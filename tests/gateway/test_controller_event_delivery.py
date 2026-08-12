"""DeliveryRouter.deliver_events — batching, dedupe, structured silence.

Invariants under test:

* a flush of N deliverable events becomes ONE adapter send (batching);
* duplicate events collapse before any send (dedupe);
* ``silent=True`` events NEVER reach a platform adapter but ARE written via
  the local delivery path (structured SILENT suppression preserves the
  local-origin audit trail);
* digest metadata exposes UI roles and event payloads containing only local
  identifiers (privacy);
* concurrent deliver_events calls sharing one batcher never double-send the
  same fact.
"""

import asyncio

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.controller_events import (
    ControllerEventBatcher,
    ControllerWakeup,
    ProjectProgress,
    ReconciliationEvent,
    UserNotification,
)
from gateway.delivery import DeliveryRouter, DeliveryTarget
from plugins.controller_events import deliver_controller_events


class RecordingAdapter:
    def __init__(self):
        self.calls = []

    async def send(self, chat_id, content, metadata=None):
        self.calls.append({"chat_id": chat_id, "content": content, "metadata": metadata})
        return {"success": True}


@pytest.fixture
def router(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.delivery.get_hermes_home", lambda: tmp_path)
    adapter = RecordingAdapter()
    r = DeliveryRouter(GatewayConfig(), adapters={Platform.TELEGRAM: adapter})
    r._test_adapter = adapter
    r._test_home = tmp_path
    return r


TELEGRAM = [DeliveryTarget.parse("telegram:123")]


def _local_outputs(router):
    out_dir = router._test_home / "cron" / "output"
    return sorted(out_dir.rglob("*.md")) if out_dir.exists() else []


@pytest.mark.asyncio
async def test_batch_of_events_is_one_platform_send(router):
    events = [
        UserNotification(text="first"),
        UserNotification(text="second"),
        ProjectProgress(project="alpha", stage="delivered"),
    ]

    result = await deliver_controller_events(router, events, TELEGRAM, job_id="j1")

    assert result["accepted"] == 3
    assert len(router._test_adapter.calls) == 1
    content = router._test_adapter.calls[0]["content"]
    assert "first" in content and "second" in content and "alpha" in content


@pytest.mark.asyncio
async def test_duplicates_collapse_before_send(router):
    event = UserNotification(text="deploy done")

    result = await deliver_controller_events(router, [event, event, event], TELEGRAM, job_id="j1")

    assert result["accepted"] == 1
    assert result["deduped"] == 2
    assert len(router._test_adapter.calls) == 1
    # A single deduped event renders bare — no bullet chrome.
    assert router._test_adapter.calls[0]["content"] == "deploy done"


@pytest.mark.asyncio
async def test_silent_events_never_reach_platform_but_audit_locally(router):
    events = [
        ControllerWakeup(reason="cron_fire", job_id="j1"),
        ReconciliationEvent(kind="silent_suppression", subject="j1"),
    ]

    result = await deliver_controller_events(router, events, TELEGRAM, job_id="j1")

    assert router._test_adapter.calls == []
    assert result["suppressed_local_only"] == 2
    outputs = _local_outputs(router)
    assert outputs, "suppressed events must still produce a local audit record"
    audit_text = "\n".join(p.read_text() for p in outputs)
    assert "cron_fire" in audit_text or "silent_suppression" in audit_text


@pytest.mark.asyncio
async def test_mixed_batch_platform_gets_only_loud_events(router):
    events = [
        ControllerWakeup(reason="cron_fire", job_id="j1"),  # silent
        UserNotification(text="visible summary"),
        ProjectProgress(project="alpha", stage="route_resolved", silent=True),
    ]

    await deliver_controller_events(router, events, TELEGRAM, job_id="j1")

    assert len(router._test_adapter.calls) == 1
    content = router._test_adapter.calls[0]["content"]
    assert content == "visible summary"
    assert "cron_fire" not in content
    assert "route_resolved" not in content


@pytest.mark.asyncio
async def test_digest_metadata_carries_ui_roles_and_payloads(router):
    events = [
        UserNotification(text="hello", title="Update"),
        ReconciliationEvent(kind="route_migrated", subject="alpha", silent=False),
    ]

    await deliver_controller_events(router, events, TELEGRAM, job_id="j1")

    metadata = router._test_adapter.calls[0]["metadata"]
    assert metadata["ui_role"] == "assistant"  # any assistant beat wins the digest
    assert metadata["ui_roles"] == ["assistant", "system"]
    payloads = metadata["controller_events"]
    assert [p["event_type"] for p in payloads] == [
        "user_notification",
        "reconciliation_event",
    ]
    # Privacy: payloads carry only typed local fields — never routing/address.
    for payload in payloads:
        assert not ({"chat_id", "thread_id", "platform"} & set(payload))


@pytest.mark.asyncio
async def test_system_only_digest_gets_system_role(router):
    await deliver_controller_events(
        router,
        [ReconciliationEvent(kind="route_migrated", subject="alpha", silent=False)],
        TELEGRAM,
        job_id="j1",
    )
    assert router._test_adapter.calls[0]["metadata"]["ui_role"] == "system"


@pytest.mark.asyncio
async def test_local_target_receives_loud_events_too(router):
    await deliver_controller_events(
        router,
        [UserNotification(text="kept locally")],
        [DeliveryTarget.parse("local")],
        job_id="j1",
    )
    assert router._test_adapter.calls == []
    outputs = _local_outputs(router)
    assert outputs and "kept locally" in outputs[-1].read_text()


@pytest.mark.asyncio
async def test_max_batch_splits_into_multiple_digests(router):
    batcher = ControllerEventBatcher(max_batch=2)
    events = [UserNotification(text=f"n{i}") for i in range(5)]

    await deliver_controller_events(router, events, TELEGRAM, job_id="j1", batcher=batcher)

    assert len(router._test_adapter.calls) == 3  # 2 + 2 + 1
    assert router._test_adapter.calls[-1]["content"] == "n4"


@pytest.mark.asyncio
async def test_shared_batcher_dedupes_across_concurrent_calls(router):
    """Concurrency: two deliver_events racing the same fact through one shared
    batcher must produce exactly one platform send of that fact."""
    batcher = ControllerEventBatcher(window_seconds=300)
    fact = UserNotification(text="the one fact")

    results = await asyncio.gather(
        deliver_controller_events(router, [fact], TELEGRAM, job_id="j1", batcher=batcher),
        deliver_controller_events(router, [fact], TELEGRAM, job_id="j1", batcher=batcher),
    )

    assert sum(r["accepted"] for r in results) == 1
    assert sum(r["deduped"] for r in results) == 1
    sends = [c for c in router._test_adapter.calls if "the one fact" in c["content"]]
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_shared_batcher_does_not_flush_another_call_pending_queue(router):
    """The shared object carries dedupe memory, never delivery ownership."""
    batcher = ControllerEventBatcher(window_seconds=300)
    queued_elsewhere = UserNotification(text="belongs to another route")
    batcher.add(queued_elsewhere)

    await deliver_controller_events(
        router,
        [UserNotification(text="this route only")],
        TELEGRAM,
        job_id="j1",
        batcher=batcher,
    )

    assert len(router._test_adapter.calls) == 1
    assert router._test_adapter.calls[0]["content"] == "this route only"
    assert batcher.flush() == [queued_elsewhere]


@pytest.mark.asyncio
async def test_empty_event_list_is_a_noop(router):
    result = await deliver_controller_events(router, [], TELEGRAM, job_id="j1")
    assert result["accepted"] == 0
    assert router._test_adapter.calls == []
    assert _local_outputs(router) == []
