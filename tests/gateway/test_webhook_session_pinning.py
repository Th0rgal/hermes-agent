"""Webhook deliveries can land in the conversation that started the work.

A webhook delivery is normally one-shot: the ``delivery_id`` is baked into the
session key so every event gets its own throwaway session.  That is wrong for
completion callbacks — a mission started from a conversation must report back
*into that conversation*, otherwise the result is written somewhere nobody
reads (the failure this feature exists to fix).

Routing a delivery into an existing session is a write into someone's live
conversation, so the tests here pin the whole gate, not just the happy path:
the route must opt in (``session_from``), the value must look like a session
id, and when ``session_requires`` is set the referenced value must actually
appear in that session's transcript.
"""

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.webhook import WebhookAdapter, _INSECURE_NO_AUTH
from gateway.session import SessionStore


def _make_adapter(routes) -> WebhookAdapter:
    config = PlatformConfig(
        enabled=True, extra={"host": "127.0.0.1", "port": 0, "routes": routes}
    )
    return WebhookAdapter(config)


class _FakeRunner:
    def __init__(self, store: SessionStore):
        self.session_store = store
        self._session_db = store._db


def _make_store(tmp_path) -> SessionStore:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    config = GatewayConfig(platforms={Platform.WEBHOOK: PlatformConfig(enabled=True)})
    store = SessionStore(sessions_dir=sessions_dir, config=config)
    assert store._db is not None, "test requires a real SessionDB"
    return store


ROUTE = {
    "secret": _INSECURE_NO_AUTH,
    "prompt": "done",
    "session_from": "origin_session",
    "session_requires": "mission_id",
}

MISSION = "c5a2b1bc-fc0f-4c61-9403-3a51fae7eaca"


@pytest.mark.asyncio
async def test_pins_to_origin_session_that_dispatched_the_mission(tmp_path):
    store = _make_store(tmp_path)
    adapter = _make_adapter({"mission-complete": ROUTE})
    adapter.gateway_runner = _FakeRunner(store)

    session_id = "20260803_150605_59ab72"
    store._db.create_session(session_id, "desktop")
    store._db.append_message(
        session_id, "assistant", f"Started mission {MISSION} for you."
    )

    pinned = await adapter._resolve_pinned_session(
        "mission-complete",
        ROUTE,
        {"origin_session": session_id, "mission_id": MISSION},
    )
    assert pinned == session_id


@pytest.mark.asyncio
async def test_route_without_session_from_never_pins(tmp_path):
    """Opting in is per route: an un-opted route keeps its own session."""
    store = _make_store(tmp_path)
    adapter = _make_adapter({"mission-complete": {"secret": _INSECURE_NO_AUTH}})
    adapter.gateway_runner = _FakeRunner(store)

    session_id = "20260803_150605_59ab72"
    store._db.create_session(session_id, "desktop")
    store._db.append_message(session_id, "assistant", MISSION)

    pinned = await adapter._resolve_pinned_session(
        "mission-complete",
        {"secret": _INSECURE_NO_AUTH},
        {"origin_session": session_id, "mission_id": MISSION},
    )
    assert pinned is None


@pytest.mark.asyncio
async def test_session_that_never_referenced_the_mission_is_refused(tmp_path):
    """A mislabelled origin must not inject a turn into a bystander session."""
    store = _make_store(tmp_path)
    adapter = _make_adapter({"mission-complete": ROUTE})
    adapter.gateway_runner = _FakeRunner(store)

    bystander = "20260804_090000_aaaaaa"
    store._db.create_session(bystander, "desktop")
    store._db.append_message(bystander, "user", "unrelated conversation")

    pinned = await adapter._resolve_pinned_session(
        "mission-complete",
        ROUTE,
        {"origin_session": bystander, "mission_id": MISSION},
    )
    assert pinned is None


@pytest.mark.asyncio
async def test_malformed_or_absent_origin_is_ignored(tmp_path):
    store = _make_store(tmp_path)
    adapter = _make_adapter({"mission-complete": ROUTE})
    adapter.gateway_runner = _FakeRunner(store)

    for payload in (
        {},
        {"origin_session": ""},
        {"origin_session": "   "},
        {"origin_session": "has spaces", "mission_id": MISSION},
        {"origin_session": "x" * 129, "mission_id": MISSION},
        {"origin_session": "20260803_150605_59ab72"},  # requires field missing
    ):
        assert (
            await adapter._resolve_pinned_session("mission-complete", ROUTE, payload)
            is None
        ), payload


@pytest.mark.asyncio
async def test_pinned_delivery_does_not_close_the_operator_session(tmp_path):
    """The one-shot close must not reap a long-lived pinned conversation."""
    store = _make_store(tmp_path)
    adapter = _make_adapter({"mission-complete": ROUTE})
    adapter.gateway_runner = _FakeRunner(store)

    ended: list = []
    adapter._end_webhook_session = lambda *a, **k: ended.append(a)  # type: ignore

    class _Event:
        metadata = {"gateway_session_id": "20260803_150605_59ab72"}

        class source:
            chat_id = "webhook:mission-complete:123"

    await adapter.on_processing_complete(_Event(), None)
    assert ended == [], "pinned delivery must not end its target session"
