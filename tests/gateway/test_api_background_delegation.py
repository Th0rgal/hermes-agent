"""Opted-in API parents receive real wake requests; ordinary clients own their turns."""
import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web

from gateway.config import Platform, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.session_context import async_delivery_supported, clear_session_vars
from gateway.wake import deliver_api_delegation
from hermes_state import SessionDB


def adapter_for(db, roots=()):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
        "key": "test-key", "background_delegation_sessions": list(roots),
    }))
    adapter._session_db = db
    return adapter


@pytest.fixture
def db(tmp_path):
    store = SessionDB(tmp_path / "sessions.db")
    store.create_session("parent", "api_server")
    store.create_session("other", "api_server")
    yield store
    store.close()


@pytest.mark.parametrize("invalid", [True, "parent", {"parent": True}, [""]])
def test_config_requires_explicit_session_list(invalid):
    with pytest.raises(ValueError, match="list of session IDs"):
        APIServerAdapter(PlatformConfig(enabled=True, extra={
            "background_delegation_sessions": invalid,
        }))


def test_binding_is_per_session_and_reconstructed_from_config(db):
    for adapter in (adapter_for(db, ["parent"]), adapter_for(db, ["parent"])):
        for sid, allowed in [("parent", True), ("other", False), ("missing", False)]:
            tokens = adapter._bind_api_server_session(chat_id=sid, session_id=sid)
            try:
                assert async_delivery_supported() is allowed
            finally:
                clear_session_vars(tokens)
    assert adapter_for(db).background_delegation_target("parent") is None


def test_only_live_compression_continuation_inherits_authorization(db):
    db.end_session("parent", "compression")
    db.create_session("branch", "api_server", parent_session_id="parent",
                      model_config={"_branched_from": "parent"})
    db.create_session("tip", "api_server", parent_session_id="parent")
    adapter = adapter_for(db, ["parent"])
    assert adapter.background_delegation_target("parent") == "tip"
    assert adapter.background_delegation_target("tip") == "tip"
    assert adapter.background_delegation_target("branch") is None
    db.end_session("tip", "session_reset")
    assert adapter.background_delegation_target("parent") is None
    assert adapter.background_delegation_target("tip") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("routed", [False, True])
async def test_real_gateway_delivery_wakes_only_opted_in_parent(db, monkeypatch, tmp_path, routed):
    from tests.gateway.test_background_process_notifications import _build_runner
    received = []

    async def capture(request):
        received.append((request.headers.get("X-Hermes-Session-Id"),
                         request.headers.get("Authorization"), await request.json()))
        return web.json_response({"choices": [{"message": {"content": "continued"}}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", capture)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    await site.start()
    adapter = adapter_for(db, ["parent"])
    adapter._port = site._server.sockets[0].getsockname()[1]
    runner = _build_runner(monkeypatch, tmp_path, "all")
    runner.adapters[Platform.API_SERVER] = adapter
    try:
        for sid in ("other", "parent"):
            evt = {"type": "async_delegation", "delegation_id": "d-" + sid,
                   "origin_session_id": sid, "parent_session_id": sid,
                   "session_key": "run-original-approval-key"}
            if routed:
                evt.update(platform="api_server", chat_id=sid, chat_type="dm")
            assert await runner._inject_watch_notification("child result", evt) is True
        assert len(received) == 1
        sid, auth, body = received[0]
        assert sid == "parent" and auth == "Bearer test-key"
        assert body["messages"] == [{"role": "user", "content": "child result"}]
        assert body["hermes"]["display_kind"] == "async_delegation_complete"
        assert body["hermes"]["display_metadata"]["delegation_id"] == "d-parent"
        assert len(db.get_messages("other")) == 1
        # The wake endpoint owns persistence: no extra synthetic delivery row.
        assert db.get_messages("parent") == []
    finally:
        await server.cleanup()


@pytest.mark.asyncio
async def test_busy_parent_defers_without_persisting_or_acknowledging(db):
    adapter = adapter_for(db, ["parent"])
    adapter._active_run_agents["run"] = SimpleNamespace(session_id="parent")
    with pytest.raises(RuntimeError, match="still running"):
        await deliver_api_delegation(adapter, text="result", session_id="parent")
    assert db.get_messages("parent") == []


@pytest.mark.asyncio
async def test_mismatched_origin_cannot_wake_another_authorized_parent(db):
    adapter = adapter_for(db, ["parent", "other"])
    with pytest.raises(RuntimeError, match="mismatch"):
        await deliver_api_delegation(adapter, text="result", session_id="parent",
                                     evt={"parent_session_id": "other"})
    assert db.get_messages("parent") == []


def test_policy_db_failure_does_not_break_ordinary_request_binding(db, monkeypatch):
    adapter = adapter_for(db, ["parent"])
    monkeypatch.setattr(adapter, "_ensure_session_db", lambda: None)
    for sid in ("other", "parent"):
        tokens = adapter._bind_api_server_session(chat_id=sid, session_id=sid)
        try:
            assert async_delivery_supported() is False
        finally:
            clear_session_vars(tokens)
    # The delivery-side lookup remains a retryable failure, not a false ack.
    with pytest.raises(RuntimeError, match="SessionDB unavailable"):
        asyncio.run(deliver_api_delegation(adapter, text="result", session_id="parent"))


@pytest.mark.asyncio
async def test_busy_batch_preserves_retry_budget_then_delivers(db, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from hermes_state import AsyncSessionDB
    from tests.gateway.test_background_process_notifications import _build_runner
    from tests.gateway.test_completion_delivery import _async_event, _persist_pending_completion
    from tools.async_delegation import get_durable_delegation
    import gateway.wake as wake

    runner = _build_runner(monkeypatch, tmp_path, "all")
    runner._session_db = AsyncSessionDB(db)
    adapter = adapter_for(db, ["parent"])
    runner.adapters[Platform.API_SERVER] = adapter
    adapter._active_run_agents["run"] = SimpleNamespace(session_id="parent")
    events = []
    for did in ("busy-first", "busy-second"):
        evt = _async_event(did)
        evt.update(origin_session_id="parent", parent_session_id="parent", session_key="run-key")
        _persist_pending_completion(evt)
        events.append(evt)
    for _ in range(12):
        assert await runner._deliver_async_delegation_group(events) is False
        assert await runner._deliver_completion_notification("result", events[0]) is False
    for evt in events:
        row = get_durable_delegation(evt["delegation_id"])
        assert row["delivery_attempts"] == 0
        assert row["delivery_state"] == "pending"
    adapter._active_run_agents.clear()
    post = AsyncMock()
    monkeypatch.setattr(wake, "_self_post_chat_completion", post)
    assert await runner._deliver_async_delegation_group(events) is True
    assert post.await_count == 1
    for evt in events:
        row = get_durable_delegation(evt["delegation_id"])
        assert row["delivery_attempts"] == 1
        assert row["delivery_state"] == "delivered"
