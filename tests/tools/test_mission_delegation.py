"""Mission-backed delegation ledger + inbound fold (plan
transient-rolling-volcano.md, Phase 1).

Covers the reuse of the async-delegation ledger to (a) register a durable pending
slot for a sandboxed.sh mission, (b) authenticate a terminal mission webhook via
the mission_id → row lookup, and (c) fold the result into the delegating turn as
an async_delegation event exactly once.
"""

import os

import pytest


@pytest.fixture
def ad(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import importlib

    import tools.async_delegation as _ad
    import tools.process_registry as pr

    importlib.reload(_ad)

    captured = []

    class _Q:
        def put(self, evt):
            captured.append(evt)

    class _PR:
        completion_queue = _Q()

    monkeypatch.setattr(pr, "process_registry", _PR())
    _ad._captured = captured  # type: ignore[attr-defined]
    return _ad


def test_register_bind_lookup(ad):
    reg = ad.register_mission_delegation(
        goal="do X", role="leaf", model="gpt-5.6-sol",
        session_key="sk", parent_session_id="p", origin_session_id="o",
    )
    assert reg["status"] == "dispatched"
    did = reg["delegation_id"]
    assert ad.find_delegation_by_mission_id("m1") is None  # not bound yet
    ad.set_delegation_mission_id(did, "m1")
    row = ad.find_delegation_by_mission_id("m1")
    assert row and row["delegation_id"] == did and row["delivery_state"] == "pending"
    assert row["parent_session_id"] == "p"


def test_fold_enqueues_from_ledger_not_payload(ad):
    reg = ad.register_mission_delegation(
        goal="build the thing", session_key="sk",
        parent_session_id="parent-123", origin_session_id="origin-123",
    )
    ad.set_delegation_mission_id(reg["delegation_id"], "m2")
    outcome = ad.fold_mission_completion(mission_id="m2", status="completed", summary="done")
    assert outcome == "folded"
    evt = ad._captured[-1]
    assert evt["type"] == "async_delegation" and evt["is_batch"] is True
    # Routing comes from the LEDGER row, never the (untrusted) webhook payload.
    assert evt["parent_session_id"] == "parent-123"
    assert evt["results"][0]["summary"] == "done"
    assert evt["results"][0]["mission_id"] == "m2"


def test_exactly_once_after_delivery(ad):
    reg = ad.register_mission_delegation(goal="g", session_key="sk", parent_session_id="p")
    ad.set_delegation_mission_id(reg["delegation_id"], "m3")
    assert ad.fold_mission_completion(mission_id="m3", status="completed") == "folded"
    # Simulate the watcher delivering: claim then complete.
    cid = f"test:{os.getpid()}"
    assert ad.claim_completion_delivery(reg["delegation_id"], cid)
    ad.complete_completion_delivery(reg["delegation_id"], cid)
    # A retry after delivery is a no-op — no second enqueue.
    before = len(ad._captured)
    assert ad.fold_mission_completion(mission_id="m3", status="completed") == "duplicate"
    assert len(ad._captured) == before


def test_unknown_mission_falls_through(ad):
    assert ad.fold_mission_completion(mission_id="ghost", status="completed") == "not_delegated"


def test_abandon_removes_phantom_slot(ad):
    reg = ad.register_mission_delegation(goal="g", session_key="sk")
    ad.set_delegation_mission_id(reg["delegation_id"], "m4")
    assert ad.find_delegation_by_mission_id("m4") is not None
    ad.abandon_pending_delegation(reg["delegation_id"])
    assert ad.find_delegation_by_mission_id("m4") is None
