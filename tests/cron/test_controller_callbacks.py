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
