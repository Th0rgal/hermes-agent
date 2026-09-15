"""Webhook mission-complete events route into the origin conversation.

Regression: a start_mission from a desktop chat used to finish in a
throwaway ``webhook:mission-complete:<delivery>`` session (Coldcard
``acfb03d2``, TAP ``35a245``). After HMAC the adapter must append a
callback into the origin session and skip the isolated path.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.webhook import WebhookAdapter, _INSECURE_NO_AUTH


class _FakeSessionDB:
    def __init__(self, sessions, messages=None):
        self.sessions = sessions
        self.messages = messages or {}
        self.appended = []

    def get_session(self, sid):
        return self.sessions.get(sid)

    def resolve_resume_session_id(self, sid):
        return sid

    def get_messages(self, sid):
        return self.messages.get(sid, [])

    def append_message(self, session_id, role, content, **_kw):
        self.appended.append((session_id, role, content))
        self.messages.setdefault(session_id, []).append(
            {"role": role, "content": content}
        )
        return 1


class _FakeRunner:
    def __init__(self, session_db):
        self._session_db = session_db
        self.adapters = {}
        self.session_store = None

    def _profile_name_for_source(self, *_args, **_kwargs):
        return "default"


def _make_adapter() -> WebhookAdapter:
    config = PlatformConfig(
        enabled=True,
        extra={
            "host": "127.0.0.1",
            "port": 0,
            "routes": {
                "mission-complete": {
                    "secret": _INSECURE_NO_AUTH,
                    "prompt": "Mission {title} is {status}",
                    "deliver": "log",
                    "events": [
                        "completed",
                        "failed",
                        "not_feasible",
                        "blocked",
                        "awaiting_user",
                    ],
                }
            },
        },
    )
    return WebhookAdapter(config)


def _mock_request(payload: dict, route: str = "mission-complete"):
    body = json.dumps(payload).encode()
    req = MagicMock()
    req.headers = {}
    req.content_length = len(body)
    req.match_info = {"route_name": route}
    req.method = "POST"

    async def _read():
        return body

    req.read = _read
    return req


ORIGIN = "20260815_123853_35a245"
MISSION = "498546da-14b7-48af-afd5-db16a14f5900"


@pytest.mark.asyncio
async def test_compression_defers_callback_without_consuming_retry(monkeypatch):
    db = _FakeSessionDB(
        {ORIGIN: {"source": "desktop"}},
        messages={ORIGIN: [{"content": f"started {MISSION}"}]},
    )
    locked = True
    db.get_compression_lock_holder = lambda sid: "compressor" if locked else None
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(db)
    api = MagicMock()
    api.supports_async_delivery = False
    adapter.gateway_runner.adapters[Platform.API_SERVER] = api
    wake = AsyncMock()
    monkeypatch.setattr("gateway.wake.deliver_wake", wake)
    payload = {"mission_id": MISSION, "status": "completed", "type": "completed",
               "origin_session": ORIGIN, "event_id": "compression-retry"}
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 503
    assert db.appended == []
    locked = False
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 202
    await asyncio.gather(*adapter._background_tasks)
    assert wake.await_count == 1
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 200
    assert wake.await_count == 1


@pytest.mark.asyncio
async def test_routed_notice_uses_trusted_replacement_readback(monkeypatch):
    db = _FakeSessionDB({ORIGIN: {"source": "desktop"}},
                        messages={ORIGIN: [{"content": f"started {MISSION}"}]})
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(db)
    api = MagicMock()
    api.supports_async_delivery = False
    adapter.gateway_runner.adapters[Platform.API_SERVER] = api
    monkeypatch.setattr("gateway.wake.deliver_wake", AsyncMock())
    evidence = {"mission_id": "22222222-2222-4222-8222-222222222222", "verified_live": True,
                "run_id": "run-2", "state": "running", "observed_at": "2026-09-15T10:00:00+00:00"}
    monkeypatch.setattr("gateway.platforms.mission_status_route.read_replacement_evidence", lambda payload: evidence)
    response = await adapter._handle_webhook(_mock_request({
        "mission_id": MISSION, "status": "failed", "type": "failed", "origin_session": ORIGIN,
        "terminal_evidence": "old attempt failed", "event_id": "verified-replacement",
    }))
    assert response.status == 202
    text = db.appended[-1][2]
    assert "Replacement execution verified live" in text
    assert evidence["mission_id"] in text and "old attempt failed" in text


@pytest.mark.asyncio
async def test_mission_complete_routes_into_origin_and_skips_throwaway(monkeypatch):
    db = _FakeSessionDB(
        {ORIGIN: {"source": "desktop"}},
        messages={ORIGIN: [{"content": f"started {MISSION}"}]},
    )
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(db)
    api = MagicMock()
    api.supports_async_delivery = False
    adapter.gateway_runner.adapters[Platform.API_SERVER] = api
    monkeypatch.setattr("gateway.wake.deliver_wake", AsyncMock())
    adapter.handle_message = AsyncMock()

    payload = {
        "mission_id": MISSION,
        "status": "completed",
        "type": "completed",
        "title": "TAP app Miles&Go API documentation",
        "origin_session": ORIGIN,
        "short_description": "Report written to /root/work/tap-api-research/REPORT.md",
        "event_id": "evt-tap-1",
    }
    resp = await adapter._handle_webhook(_mock_request(payload))
    body = json.loads(resp.body)
    assert resp.status == 202
    assert body["status"] == "routed"
    assert body["session_id"] == ORIGIN
    assert body["mission_id"] == MISSION
    assert adapter.handle_message.await_count == 0
    assert len(db.appended) == 1
    _sid, role, content = db.appended[0]
    assert _sid == ORIGIN
    assert role == "assistant"
    assert "[Mission callback:" in content
    assert MISSION in content
    assert "REPORT.md" in content


@pytest.mark.asyncio
async def test_unrelated_origin_is_stashed_not_injected():
    db = _FakeSessionDB(
        {ORIGIN: {"source": "desktop"}},
        messages={ORIGIN: [{"content": "never mentioned this mission"}]},
    )
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(db)
    adapter.handle_message = AsyncMock()

    payload = {
        "mission_id": MISSION,
        "status": "failed",
        "type": "failed",
        "origin_session": ORIGIN,
        "title": "stray",
    }
    resp = await adapter._handle_webhook(_mock_request(payload))
    body = json.loads(resp.body)
    # Origin present but no ownership proof — do not inject into that
    # session, and do not mint a throwaway webhook session either. Enroll
    # will fold if this was a beat-the-transcript race.
    assert resp.status == 503
    assert body["status"] == "pending_enrollment"
    assert body["evidence_stashed"] is True
    assert adapter.handle_message.await_count == 0
    assert db.appended == []


@pytest.mark.asyncio
async def test_delegated_mission_folds_instead_of_routing(monkeypatch, tmp_path):
    """A backend='mission' delegation is claimed by the ledger first."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import importlib

    import tools.async_delegation as ad
    import tools.process_registry as pr

    importlib.reload(ad)

    class _Q:
        def put(self, evt):
            captured.append(evt)

    captured = []
    monkeypatch.setattr(pr, "process_registry", type("PR", (), {"completion_queue": _Q()})())

    reg = ad.register_mission_delegation(
        goal="do X",
        session_key="sk",
        parent_session_id=ORIGIN,
        origin_session_id=ORIGIN,
    )
    ad.set_delegation_mission_id(reg["delegation_id"], MISSION)

    db = _FakeSessionDB(
        {ORIGIN: {"source": "desktop"}},
        messages={ORIGIN: [{"content": f"started {MISSION}"}]},
    )
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(db)
    adapter.handle_message = AsyncMock()

    payload = {
        "mission_id": MISSION,
        "status": "completed",
        "type": "completed",
        "origin_session": ORIGIN,
        "title": "delegated",
        "result_summary": "done",
    }
    resp = await adapter._handle_webhook(_mock_request(payload))
    body = json.loads(resp.body)
    assert body["status"] == "delivered"
    assert body["outcome"] == "folded"
    assert adapter.handle_message.await_count == 0
    # Fold owns delivery; origin-route must not also append.
    assert db.appended == []
    assert captured and captured[-1]["results"][0]["mission_id"] == MISSION


@pytest.mark.asyncio
async def test_duplicate_event_id_does_not_reschedule_wake(monkeypatch):
    db = _FakeSessionDB(
        {ORIGIN: {"source": "desktop"}},
        messages={ORIGIN: [{"content": f"started {MISSION}"}]},
    )
    adapter = _make_adapter()
    runner = _FakeRunner(db)
    api = MagicMock()
    api.supports_async_delivery = False
    runner.adapters = {Platform.API_SERVER: api}
    adapter.gateway_runner = runner
    wakes = []

    async def _fake_wake(target, **kwargs):
        wakes.append((target, kwargs))

    monkeypatch.setattr("gateway.wake.deliver_wake", _fake_wake)

    payload = {
        "mission_id": MISSION,
        "status": "completed",
        "type": "completed",
        "origin_session": ORIGIN,
        "title": "dup",
        "event_id": "evt-dup-1",
    }
    first = await adapter._handle_webhook(_mock_request(payload))
    second = await adapter._handle_webhook(_mock_request(payload))
    assert json.loads(first.body)["status"] == "routed"
    assert json.loads(second.body)["status"] == "duplicate"
    if adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks), return_exceptions=True)
    assert len(wakes) == 1
    assert wakes[0][0] is api
    assert len(db.appended) == 1


@pytest.mark.asyncio
async def test_telegram_origin_wakes_telegram_adapter_not_api_server(monkeypatch):
    db = _FakeSessionDB(
        {
            ORIGIN: {
                "source": "telegram",
                "chat_id": "12345",
                "chat_type": "dm",
                "user_id": "99",
            }
        },
        messages={ORIGIN: [{"content": f"started {MISSION}"}]},
    )
    adapter = _make_adapter()
    runner = _FakeRunner(db)
    telegram = MagicMock()
    telegram.supports_async_delivery = True
    api = MagicMock()
    api.supports_async_delivery = False
    runner.adapters = {Platform.TELEGRAM: telegram, Platform.API_SERVER: api}
    adapter.gateway_runner = runner
    wakes = []

    async def _fake_wake(target, **kwargs):
        wakes.append((target, kwargs))

    monkeypatch.setattr("gateway.wake.deliver_wake", _fake_wake)

    payload = {
        "mission_id": MISSION,
        "status": "completed",
        "type": "completed",
        "origin_session": ORIGIN,
        "title": "tg",
        "event_id": "evt-tg-1",
    }
    resp = await adapter._handle_webhook(_mock_request(payload))
    assert json.loads(resp.body)["status"] == "routed"
    if adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks), return_exceptions=True)
    assert len(wakes) == 1
    assert wakes[0][0] is telegram
    assert wakes[0][1].get("source") is not None
    assert wakes[0][1]["source"].platform == Platform.TELEGRAM


def test_session_db_for_profile_unwraps_the_async_door(monkeypatch):
    """The runner's AsyncSessionDB must be unwrapped for the thread-side
    routing helpers (they call SessionDB methods synchronously)."""
    from types import SimpleNamespace

    from gateway.platforms.webhook import WebhookAdapter
    from hermes_state import AsyncSessionDB

    class _Db:
        pass

    inner = _Db()
    runner = SimpleNamespace(_session_db=AsyncSessionDB(inner))
    monkeypatch.setattr(WebhookAdapter, "gateway_runner", property(lambda self: runner))
    adapter = WebhookAdapter.__new__(WebhookAdapter)
    db, owned = adapter._session_db_for_profile(None)
    assert db is inner
    assert owned is False



@pytest.mark.asyncio
@pytest.mark.parametrize("folded", [False, True])
async def test_controller_callback_persists_before_both_routing_paths(monkeypatch, folded):
    from cron import jobs, controller_callbacks as relay
    job = jobs.create_job("Check", "0 * * * *", deliver="project:verity-lido")
    with jobs._jobs_lock():
        rows = jobs.load_jobs()
        rows[0]["controller"] = {"project": "verity-lido", "callback_relay": True,
            "repositories": ["example/proof"], "permissions": [], "reserved_local_areas": []}
        jobs.save_jobs(rows)
    adapter = _make_adapter()
    adapter.handle_message = AsyncMock()
    if folded:
        import tools.async_delegation as ad
        registration = ad.register_mission_delegation(goal="Review", session_key="test", parent_session_id="parent")
        ad.set_delegation_mission_id(registration["delegation_id"], MISSION)
    payload = {"project": "verity-lido", "mission_id": MISSION, "status": "completed",
               "type": "completed", "title": "Ready", "event_id": "controller-event"}
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == (200 if folded else 202)
    assert len(relay.pending_callbacks(job["id"])["event_ids"]) == 1
    await adapter._handle_webhook(_mock_request(payload))
    assert len(relay.pending_callbacks(job["id"])["event_ids"]) == 1
    assert adapter.handle_message.await_count == 0


@pytest.mark.asyncio
async def test_controller_inbox_failure_is_retryable_before_transport_dedupe(monkeypatch):
    from cron import controller_callbacks as relay
    adapter = _make_adapter()
    adapter.handle_message = AsyncMock()
    def unavailable(payload):
        raise OSError("disk unavailable")
    monkeypatch.setattr(relay, "enqueue_mission_callback", unavailable)
    payload = {"project": "verity-lido", "mission_id": MISSION, "status": "completed",
               "type": "completed", "title": "Ready", "event_id": "retry-inbox"}
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 503
    monkeypatch.setattr(relay, "enqueue_mission_callback", lambda p:
                        {"job_id": "j", "event_id": "e", "duplicate": False})
    monkeypatch.setattr(adapter, "_maybe_fold_mission_delegation", lambda *a, **kw: None)
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 202
    assert json.loads(response.body)["status"] == "controller_queued"
    assert adapter.handle_message.await_count == 0


@pytest.mark.asyncio
async def test_orphan_callback_rejected_without_autonomous_owner(monkeypatch):
    adapter = _make_adapter()
    adapter.handle_message = AsyncMock()
    payload = {"mission_id": MISSION, "status": "failed", "type": "failed",
               "project": "sandboxed-sh-dev", "event_id": "orphan"}
    for _ in range(3):
        response = await adapter._handle_webhook(_mock_request(payload))
        assert response.status == 409
        assert json.loads(response.body)["reason"] == "missing_conversation_binding"
    assert adapter.handle_message.await_count == 0
    assert not adapter._background_tasks


@pytest.mark.asyncio
async def test_generic_route_with_mission_looking_payload_skips_mission_router(monkeypatch):
    adapter = _make_adapter()
    adapter._routes["mission-complete"]["mission_status"] = False
    adapter.handle_message = AsyncMock()
    router = AsyncMock(side_effect=AssertionError("generic route must not route missions"))
    monkeypatch.setattr(adapter, "_maybe_route_mission_status", router)
    payload = {"mission_id": MISSION, "status": "completed", "type": "completed",
               "event_id": "generic-mission-shaped"}
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 202
    assert router.await_count == 0
    await asyncio.gather(*list(adapter._background_tasks))


@pytest.mark.asyncio
async def test_missing_wake_adapter_preserves_exact_retry(monkeypatch):
    db = _FakeSessionDB({ORIGIN: {"source": "desktop"}},
                        messages={ORIGIN: [{"content": f"started {MISSION}"}]})
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(db)
    payload = {"mission_id": MISSION, "status": "failed", "type": "failed",
               "origin_session": ORIGIN, "event_id": "adapter-retry"}
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 503
    assert json.loads(response.body)["reason"] == "wake_adapter_unavailable"
    assert db.appended == []
    api = MagicMock()
    api.supports_async_delivery = False
    adapter.gateway_runner.adapters[Platform.API_SERVER] = api
    wake = AsyncMock()
    monkeypatch.setattr("gateway.wake.deliver_wake", wake)
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 202
    await asyncio.gather(*list(adapter._background_tasks))
    assert wake.await_count == 1 and len(db.appended) == 1
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 200
    assert wake.await_count == 1 and len(db.appended) == 1


@pytest.mark.asyncio
async def test_ambiguous_wake_failure_is_recorded_without_retry(monkeypatch):
    db = _FakeSessionDB({ORIGIN: {"source": "desktop"}},
                        messages={ORIGIN: [{"content": f"started {MISSION}"}]})
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(db)
    api = MagicMock()
    api.supports_async_delivery = False
    adapter.gateway_runner.adapters[Platform.API_SERVER] = api
    wake = AsyncMock(side_effect=TimeoutError("untrusted transport details"))
    monkeypatch.setattr("gateway.wake.deliver_wake", wake)
    payload = {"mission_id": MISSION, "status": "completed", "type": "completed",
               "origin_session": ORIGIN, "event_id": "ambiguous-wake"}
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 202
    await asyncio.gather(*list(adapter._background_tasks))
    # A timeout can leave the self-posted model turn running.  No assistant
    # receipt may be appended beside it, or the eventual final races role
    # alternation; the callback evidence itself remains durable.
    assert len(db.appended) == 1
    assert "Delivery outcome is unknown" not in db.appended[-1][2]
    before = list(db.appended)
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 200
    assert db.appended == before and wake.await_count == 1
    assert payload["status"] == "completed"


@pytest.mark.asyncio
async def test_pending_origin_retries_after_ownership_without_duplicate_wake(monkeypatch):
    from gateway.platforms import mission_status_route as route
    db = _FakeSessionDB({ORIGIN: {"source": "desktop"}}, messages={ORIGIN: []})
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(db)
    adapter.handle_message = AsyncMock()
    api = MagicMock()
    api.supports_async_delivery = False
    adapter.gateway_runner.adapters[Platform.API_SERVER] = api
    wake = AsyncMock()
    monkeypatch.setattr("gateway.wake.deliver_wake", wake)
    payload = {"mission_id": MISSION, "status": "failed", "type": "failed",
               "origin_session": ORIGIN, "event_id": "late-ownership"}
    for _ in range(2):
        response = await adapter._handle_webhook(_mock_request(payload))
        assert response.status == 503
        assert json.loads(response.body)["evidence_stashed"] is True
    assert not db.appended and adapter.handle_message.await_count == 0
    # Existing owner records the mission; no new conversation/enrollment job.
    db.messages[ORIGIN].append({"role": "user", "content": f"Started {MISSION}"})
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 202
    await asyncio.gather(*list(adapter._background_tasks))
    assert wake.await_count == 1 and len(db.appended) == 1
    assert route.take_stashed_callback(MISSION) is None
    response = await adapter._handle_webhook(_mock_request(payload))
    assert response.status == 200 and wake.await_count == 1


@pytest.mark.asyncio
async def test_pending_origin_backup_failure_never_acknowledges_event(monkeypatch):
    from gateway.platforms import mission_status_route as route
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(_FakeSessionDB({}, messages={}))
    adapter.handle_message = AsyncMock()
    monkeypatch.setattr(route, "stash_unroutable_callback", lambda *args: False)
    response = await adapter._handle_webhook(_mock_request({
        "mission_id": MISSION, "status": "failed", "type": "failed", "origin_session": ORIGIN,
    }))
    assert response.status == 503
    assert json.loads(response.body)["evidence_stashed"] is False
    assert adapter.handle_message.await_count == 0


@pytest.mark.asyncio
async def test_route_lookup_failure_is_not_classified_as_missing_ownership(monkeypatch):
    from gateway.platforms import mission_status_route as route
    adapter = _make_adapter()
    adapter.gateway_runner = _FakeRunner(_FakeSessionDB({}, messages={}))
    adapter.handle_message = AsyncMock()
    def unavailable(*args):
        raise OSError("store unavailable")
    monkeypatch.setattr(route, "resolve_mission_delivery_session", unavailable)
    payload = {"mission_id": MISSION, "status": "failed", "type": "failed", "event_id": "lookup-failure"}
    for _ in range(2):
        response = await adapter._handle_webhook(_mock_request(payload))
        assert response.status == 503
        assert json.loads(response.body)["reason"] == "mission_route_unavailable"
    assert adapter.handle_message.await_count == 0
