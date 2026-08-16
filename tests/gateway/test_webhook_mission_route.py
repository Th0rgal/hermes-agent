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
async def test_mission_complete_routes_into_origin_and_skips_throwaway():
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
    assert body["status"] == "pending_enrollment"
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
