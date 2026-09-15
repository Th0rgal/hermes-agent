"""Real cron storage, imports and temp-home callback replay; no live services."""
import importlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from cron import jobs
from cron import controller_callbacks as relay


def controller(project="verity-lido", **patch):
    job = jobs.create_job("Check project", "0 * * * *", deliver=f"project:{project}")
    with jobs._jobs_lock():
        records = jobs.load_jobs()
        row = next(j for j in records if j["id"] == job["id"])
        row["controller"] = {"project": project, "callback_relay": True,
                             "repositories": ["example/proof"], "permissions": [],
                             "reserved_local_areas": []}
        row.update(patch)
        jobs.save_jobs(records)
    return job["id"]


def event(index=1, **patch):
    return {"project": "verity-lido", "mission_id": "mission-1", "run_id": "run-1",
            "event_id": f"event-{index}", "status": "completed", "summary": "review ready", **patch}


def test_duplicate_concurrent_callback_has_one_durable_dispatch_identity():
    job_id = controller()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: relay.enqueue_mission_callback(event()), range(8)))
    assert sum(not r["duplicate"] for r in results) == 1
    snapshot = relay.pending_callbacks(job_id)
    assert len(snapshot["event_ids"]) == 1
    assert f"controller:{job_id}:{snapshot['event_ids'][0]}" in snapshot["prompt"]
    assert len(jobs.load_jobs()) == 1


def test_running_arrival_survives_snapshot_ack_and_module_restart():
    job_id = controller()
    relay.enqueue_mission_callback(event())
    first = relay.pending_callbacks(job_id)
    relay.enqueue_mission_callback(event(2))
    relay.acknowledge_callbacks(job_id, first["event_ids"], success=True)
    reloaded = importlib.reload(relay)
    second = reloaded.pending_callbacks(job_id)
    assert len(second["event_ids"]) == 1
    assert second["event_ids"] != first["event_ids"]
    assert reloaded.wake_pending_controllers() == 1
    assert reloaded.enqueue_mission_callback(event())["duplicate"]


@pytest.mark.parametrize("patch", [
    {"enabled": False}, {"state": "paused", "paused_at": "2026-09-09T10:00:00+00:00"},
    {"state": "completed"},
])
def test_explicit_disabled_paused_terminal_never_reactivated(patch):
    job_id = controller(**patch)
    before = jobs.load_jobs()[0]
    relay.enqueue_mission_callback(event())
    assert relay.wake_pending_controllers() == 0
    after = jobs.load_jobs()[0]
    for field in ("enabled", "state", "paused_at", "next_run_at"):
        assert after.get(field) == before.get(field)
    assert relay.pending_callbacks(job_id)["event_ids"] == []
    assert len(after["controller_callbacks"]) == 1


def test_failed_handling_retains_input_without_a_second_job():
    job_id = controller()
    relay.enqueue_mission_callback(event())
    snapshot = relay.pending_callbacks(job_id)
    relay.acknowledge_callbacks(job_id, snapshot["event_ids"], success=False)
    assert relay.pending_callbacks(job_id) == snapshot
    assert len(jobs.load_jobs()) == 1


def test_other_projects_and_nonterminal_events_keep_interactive_route():
    controller()
    assert relay.enqueue_mission_callback(event(project="other")) is None
    assert relay.enqueue_mission_callback(event(status="active")) is None
    assert "controller_callbacks" not in jobs.load_jobs()[0]


def test_ambiguous_binding_refuses_double_owner():
    controller()
    controller()
    with pytest.raises(ValueError, match="Ambiguous"):
        relay.enqueue_mission_callback(event())
    assert all("controller_callbacks" not in job for job in jobs.load_jobs())


def test_callback_due_marker_survives_real_schedule_validation():
    job_id = controller()
    relay.enqueue_mission_callback(event())
    assert job_id in [job["id"] for job in jobs.get_due_jobs()]


def test_large_callback_burst_is_batched_without_acknowledging_unsent_entries():
    job_id = controller()
    for index in range(9):
        relay.enqueue_mission_callback(event(index, summary="x" * 2000))
    snapshot = relay.pending_callbacks(job_id)
    assert 0 < len(snapshot["event_ids"]) < 9
    assert len(snapshot["prompt"]) < 7000
    relay.acknowledge_callbacks(job_id, snapshot["event_ids"], success=True)
    remaining = [item for item in jobs.load_jobs()[0]["controller_callbacks"] if not item.get("handled_at")]
    assert len(remaining) == 9 - len(snapshot["event_ids"])


def test_callback_snapshot_fits_actual_remaining_prompt_budget():
    job_id = controller()
    for index in range(3):
        relay.enqueue_mission_callback(event(index, summary="x" * 2000))
    snapshot = relay.pending_callbacks(job_id, max_chars=3200)
    assert len(snapshot["prompt"]) <= 3200
    assert len(snapshot["event_ids"]) == 1
    assert len(relay.pending_callbacks(job_id)["event_ids"]) > 1


def test_callback_that_cannot_fit_is_explicit_and_remains_pending():
    job_id = controller()
    receipt = relay.enqueue_mission_callback(event(summary="x" * 2000))
    with pytest.raises(ValueError, match="callback.*budget"):
        relay.pending_callbacks(job_id, max_chars=100)
    assert relay.pending_callbacks(job_id)["event_ids"] == [receipt["event_id"]]


def test_failed_controller_callback_replay_preserves_scheduled_retry():
    job_id = controller()
    relay.enqueue_mission_callback(event())
    jobs.mark_job_run(job_id, success=False, error="Controller prompt exceeds budget")
    retry_at = jobs.get_job(job_id)["next_run_at"]
    for _ in range(3):
        relay.wake_pending_controllers()
        relay.enqueue_mission_callback(event())
        assert jobs.get_job(job_id)["next_run_at"] == retry_at
    assert relay.pending_callbacks(job_id)["event_ids"]
    # Fresh native evidence can wake the controller; replay cannot.
    relay.enqueue_mission_callback(event(2))
    assert jobs.get_job(job_id)["next_run_at"] < retry_at


def test_replayed_receipt_absorbs_native_supersession_without_second_dispatch_identity():
    job_id = controller()
    first = relay.enqueue_mission_callback(event())
    before = jobs.get_job(job_id)["controller_callbacks"][0]
    successor = "f43e7dec-7143-4902-8b00-968a2b715dae"
    replay = relay.enqueue_mission_callback(event(tags=["superseded_by:" + successor]))
    assert replay["duplicate"]
    assert replay["event_id"] == first["event_id"]
    entries = jobs.get_job(job_id)["controller_callbacks"]
    assert len(entries) == 1
    assert entries[0]["superseded_by"] == successor
    assert entries[0]["received_at"] == before["received_at"]
    assert entries[0]["dispatch_idempotency_key"] == before["dispatch_idempotency_key"]
    assert successor in relay.pending_callbacks(job_id)["prompt"]


def test_late_supersession_reopens_an_acknowledged_receipt():
    job_id = controller()
    first = relay.enqueue_mission_callback(event())
    relay.acknowledge_callbacks(job_id, [first["event_id"]], success=True)
    dispatch_key = jobs.get_job(job_id)["controller_callbacks"][0]["dispatch_idempotency_key"]
    successor = "f43e7dec-7143-4902-8b00-968a2b715dae"
    relay.enqueue_mission_callback(event(tags=["superseded_by:" + successor]))
    entry = jobs.get_job(job_id)["controller_callbacks"][0]
    assert not entry.get("handled_at")
    assert entry["dispatch_idempotency_key"] == dispatch_key
    assert successor in relay.pending_callbacks(job_id)["prompt"]


def test_incomplete_summary_retains_input_without_immediate_replay():
    job_id = controller()
    relay.enqueue_mission_callback(event())
    snapshot = relay.pending_callbacks(job_id)
    relay.defer_callbacks(job_id, snapshot["event_ids"])
    # A max-iteration summary is still deliverable, so outer status is success.
    jobs.mark_job_run(job_id, success=True)
    retry_at = jobs.get_job(job_id)["next_run_at"]
    relay.wake_pending_controllers()
    assert jobs.get_job(job_id)["next_run_at"] == retry_at
    assert relay.pending_callbacks(job_id)["event_ids"] == snapshot["event_ids"]
    relay.enqueue_mission_callback(event(2))
    assert jobs.get_job(job_id)["next_run_at"] < retry_at


def test_incomplete_bounded_batch_defers_its_unselected_tail(monkeypatch):
    job_id = controller()
    first = relay.enqueue_mission_callback(event(summary="first"))
    relay.enqueue_mission_callback(event(2, summary="second"))
    snapshot = relay.pending_callbacks(job_id, max_chars=1000)
    assert snapshot["event_ids"] == [first["event_id"]]
    relay.defer_callbacks(job_id, snapshot["event_ids"])
    assert all(entry.get("retry_after") for entry in jobs.get_job(job_id)["controller_callbacks"])


def test_alias_callback_matches_canonical_job(monkeypatch):
    import hermes_cli.projects_db as projects
    monkeypatch.setattr(projects, "_project_alias_map", lambda: {"lido": "verity-lido"})
    job_id = controller()
    assert relay.enqueue_mission_callback(event(project="lido"))["job_id"] == job_id


def test_corrupt_binding_does_not_abort_other_jobs_or_consume_its_inbox():
    damaged = controller()
    relay.enqueue_mission_callback(event())
    good = controller("other-project")
    with jobs._jobs_lock():
        rows = jobs.load_jobs()
        next(j for j in rows if j["id"] == damaged)["deliver"] = "project:eip-8282"
        jobs.save_jobs(rows)
    assert relay.enqueue_mission_callback(event(project="other-project"))["job_id"] == good
    assert relay.wake_pending_controllers() == 1
    damaged_row = next(j for j in jobs.load_jobs() if j["id"] == damaged)
    assert not damaged_row["controller_callbacks"][0].get("handled_at")
    assert good in [job["id"] for job in jobs.get_due_jobs()]
    for affected in ("verity-lido", "eip-8282"):
        with pytest.raises(ValueError, match="Malformed controller binding"):
            relay.enqueue_mission_callback(event(project=affected))


def test_nonobject_binding_quarantines_its_known_delivery_only():
    damaged = controller()
    with jobs._jobs_lock():
        rows = jobs.load_jobs()
        rows[0]["controller"] = "broken"
        jobs.save_jobs(rows)
    assert relay.wake_pending_controllers() == 0
    with pytest.raises(ValueError, match="Malformed controller binding"):
        relay.enqueue_mission_callback(event())
    assert relay.enqueue_mission_callback(event(project="other-project")) is None


def test_native_nested_execution_keeps_distinct_same_status_runs_without_event_id():
    job_id = controller()
    payload = {"project": "verity-lido", "mission_id": "mission-1", "status": "completed",
               "type": "completed", "execution": {"run_id": "native-run-1", "generation": 1,
               "state": "completed", "heartbeat_at": None, "scope_unit": None}}
    first = relay.enqueue_mission_callback(payload)
    assert relay.enqueue_mission_callback(payload)["duplicate"]
    second = relay.enqueue_mission_callback({**payload, "execution": {
        **payload["execution"], "run_id": "native-run-2", "generation": 2}})
    assert second["event_id"] != first["event_id"]
    row = next(j for j in jobs.load_jobs() if j["id"] == job_id)
    assert [entry["run_id"] for entry in row["controller_callbacks"]] == ["native-run-1", "native-run-2"]


@pytest.mark.parametrize("execution, legacy, expected", [
    ({"generation": 0}, {}, "0"),
    ({"generation": 7}, {}, "7"),
    ({"run_id": "nested"}, {"run_id": "legacy"}, "legacy"),
    (None, {"run_generation": 3}, "3"),
    ("malformed", {"generation": 4}, "4"),
])
def test_native_execution_fallback_preserves_legacy_identity(execution, legacy, expected):
    controller()
    payload = {"project": "verity-lido", "mission_id": "mission-1", "status": "completed",
               "execution": execution, **legacy}
    relay.enqueue_mission_callback(payload)
    assert jobs.load_jobs()[0]["controller_callbacks"][0]["run_id"] == expected


@pytest.mark.parametrize("snapshot", [True, False])
def test_arrival_during_failed_run_wakes_from_input_boundary(monkeypatch, snapshot):
    from datetime import datetime, timedelta, timezone
    from cron import scheduler

    now = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(jobs, "_hermes_now", lambda: now)
    job_id = controller()
    old = relay.enqueue_mission_callback(event())
    now += timedelta(seconds=1)

    def failed_run(*args, **kwargs):
        nonlocal now
        if snapshot:
            assert relay.pending_callbacks(job_id)["event_ids"] == [old["event_id"]]
        now += timedelta(seconds=1)
        relay.enqueue_mission_callback(event(2))
        now += timedelta(seconds=1)
        return False, "", "", "oversized prompt" if not snapshot else "model failed"

    monkeypatch.setattr(scheduler, "_run_job", failed_run)
    assert scheduler.run_job(jobs.get_job(job_id))[0] is False
    jobs.mark_job_run(job_id, success=False, error="failed")
    retry_at = jobs.get_job(job_id)["next_run_at"]
    importlib.reload(relay).wake_pending_controllers()
    assert jobs.get_job(job_id)["next_run_at"] < retry_at
    assert relay.enqueue_mission_callback(event(2))["duplicate"]
    assert len(jobs.get_job(job_id)["controller_callbacks"]) == 2

    # The next failed snapshot has seen both inputs: neither replay wakes it.
    now += timedelta(seconds=1)
    relay.begin_callback_run(job_id)
    relay.pending_callbacks(job_id)
    now += timedelta(seconds=1)
    jobs.mark_job_run(job_id, success=False, error="failed again")
    retry_at = jobs.get_job(job_id)["next_run_at"]
    relay.enqueue_mission_callback(event(2))
    relay.wake_pending_controllers()
    assert jobs.get_job(job_id)["next_run_at"] == retry_at


def test_deferred_fresh_input_does_not_authorize_replayed_early_wake(monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(jobs, "_hermes_now", lambda: now)
    job_id = controller()
    relay.enqueue_mission_callback(event())
    now += timedelta(seconds=1)
    relay.pending_callbacks(job_id)
    now += timedelta(seconds=1)
    fresh = relay.enqueue_mission_callback(event(2))
    relay.defer_callbacks(job_id, [fresh["event_id"]])
    now += timedelta(seconds=1)
    jobs.mark_job_run(job_id, success=False, error="failed")
    retry_at = jobs.get_job(job_id)["next_run_at"]
    relay.wake_pending_controllers()
    assert jobs.get_job(job_id)["next_run_at"] == retry_at


def test_completion_without_new_boundary_does_not_reuse_previous_run(monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(jobs, "_hermes_now", lambda: now)
    job_id = controller()
    relay.begin_callback_run(job_id)
    now += timedelta(seconds=1)
    jobs.mark_job_run(job_id, success=False, error="failed")
    now += timedelta(seconds=1)
    relay.enqueue_mission_callback(event())
    now += timedelta(seconds=1)
    jobs.mark_job_run(job_id, success=False, error="pre-dispatch rejection")
    retry_at = jobs.get_job(job_id)["next_run_at"]
    relay.wake_pending_controllers()
    assert jobs.get_job(job_id)["next_run_at"] == retry_at
