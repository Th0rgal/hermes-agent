"""Real ledger/plugin/webhook contract for repeated runs of one mission."""

import importlib
import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest

MISSION = "77777777-7777-4777-8777-777777777777"
RUN1 = "11111111-1111-4111-8111-111111111111"
RUN2 = "22222222-2222-4222-8222-222222222222"
PARENT = "canonical-project-parent"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import tools.async_delegation as ad
    import tools.process_registry as pr
    from gateway.config import PlatformConfig
    from gateway.platforms.webhook import WebhookAdapter

    importlib.reload(ad)
    queue = Queue()
    monkeypatch.setattr(pr, "process_registry", SimpleNamespace(completion_queue=queue))
    path = Path(__file__).resolve().parents[2] / "plugins/sandboxed-origin-session/__init__.py"
    spec = importlib.util.spec_from_file_location("resume_origin_plugin", path)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    adapter = WebhookAdapter(PlatformConfig(enabled=True, extra={}))
    return SimpleNamespace(ad=ad, queue=queue, plugin=plugin, adapter=adapter)


def start(runtime):
    runtime.plugin.enroll_after_start_mission(
        tool_name="mcp__sandboxed_assistant__start_mission",
        status="ok", session_id=PARENT, args={"prompt": "Keep the same objective"},
        result=json.dumps({"mission_id": MISSION}),
    )
    return runtime.ad.find_delegation_by_mission_id(MISSION)


def resume(runtime, *, call_id="resume-one", origin=PARENT):
    runtime.plugin.enroll_after_start_mission(
        tool_name="mcp__sandboxed_assistant__resume_mission",
        status="ok", session_id=origin, args={"mission_id": MISSION}, tool_call_id=call_id,
        result=json.dumps({"mission_id": MISSION, "resume_accepted": True}),
    )


def callback(runtime, run_id, generation, *, event_id=None):
    payload = {
        "mission_id": MISSION, "status": "completed",
        "event_id": event_id or "event-" + run_id,
        "execution": {"run_id": run_id, "generation": generation, "state": "terminal"},
        "summary": "Candidate from generation " + str(generation),
        "origin_session": "untrusted-callback-parent",
    }
    response = runtime.adapter._maybe_fold_mission_delegation(payload)
    assert response is not None
    return response.status, json.loads(response.body)


def test_parent_receives_second_run_and_old_replay_remains_duplicate(runtime):
    first = start(runtime)
    assert callback(runtime, RUN1, 1)[1]["outcome"] == "folded"
    assert runtime.ad.claim_completion_delivery(first["delegation_id"], "delivery-one")
    assert runtime.ad.complete_completion_delivery(first["delegation_id"], "delivery-one")
    resume(runtime)
    assert callback(runtime, RUN1, 1)[1]["outcome"] == "duplicate"
    assert callback(runtime, RUN2, 2)[1]["outcome"] == "folded"
    events = [runtime.queue.get_nowait(), runtime.queue.get_nowait()]
    assert events[0]["delegation_id"] != events[1]["delegation_id"]
    assert all(e["parent_session_id"] == PARENT for e in events)
    assert all(e["origin_session_id"] == PARENT for e in events)
    assert runtime.queue.empty()


def rows(runtime):
    with runtime.ad._transaction() as conn:
        return conn.execute(
            "SELECT delegation_id, state, event_json, delivery_state, delivery_claim, "
            "mission_run_id, mission_generation FROM async_delegations ORDER BY dispatched_at"
        ).fetchall()


def test_parent_resume_during_delivery_claim_keeps_previous_receipt(runtime):
    first = start(runtime)
    callback(runtime, RUN1, 1)
    assert runtime.ad.claim_completion_delivery(first["delegation_id"], "parent-handling")
    old = rows(runtime)[0]
    resume(runtime)
    new = runtime.ad.find_delegation_by_mission_id(MISSION)
    assert new["mission_generation"] == 2 and new["mission_run_id"] is None
    assert callback(runtime, RUN1, 1)[1]["outcome"] == "duplicate"
    assert rows(runtime)[0] == old
    assert runtime.ad.active_count() == 1
    assert callback(runtime, RUN2, 2)[1]["outcome"] == "folded"
    assert runtime.ad.complete_completion_delivery(first["delegation_id"], "parent-handling")
    assert runtime.ad.active_count() == 0


def test_same_resume_call_replayed_after_new_completion_does_not_arm_again(runtime):
    start(runtime)
    callback(runtime, RUN1, 1)
    resume(runtime)
    resume(runtime)
    assert len(rows(runtime)) == 2
    callback(runtime, RUN2, 2)
    before = rows(runtime)
    resume(runtime)
    assert rows(runtime) == before
    assert runtime.ad.active_count() == 0


def test_callback_before_resume_hook_is_stashed_then_folded(runtime):
    from gateway.platforms.mission_status_route import take_stashed_callback

    start(runtime)
    callback(runtime, RUN1, 1)
    status, body = callback(runtime, RUN2, 2)
    assert status == 503 and body["status"] == "awaiting_enrollment"
    assert runtime.queue.qsize() == 1
    resume(runtime)
    assert runtime.queue.qsize() == 2
    assert runtime.ad.find_delegation_by_mission_id(MISSION)["mission_run_id"] == RUN2
    assert take_stashed_callback(MISSION) is None
    assert callback(runtime, RUN2, 2)[1]["outcome"] == "duplicate"


def test_unarmed_generation_does_not_change_old_receipt(runtime):
    start(runtime)
    callback(runtime, RUN1, 1)
    before = rows(runtime)
    assert callback(runtime, RUN2, 2)[0] == 503
    assert rows(runtime) == before
    assert runtime.queue.qsize() == 1


def test_legacy_unknown_execution_requires_reconciliation(runtime):
    from tools.mission_delegation import enroll_conversational_resume_mission

    start(runtime)
    runtime.ad.fold_mission_completion(mission_id=MISSION, status="completed")
    before = rows(runtime)
    result = enroll_conversational_resume_mission(
        result=json.dumps({"mission_id": MISSION, "resume_accepted": True}),
        origin_session_id=PARENT, tool_call_id="legacy-resume",
    )
    assert result["status"] == "reconciliation_required"
    assert result["reason"] == "unknown_prior_execution"
    assert callback(runtime, RUN2, 2)[1]["status"] == "reconciliation_required"
    assert rows(runtime) == before


@pytest.mark.parametrize("execution", [None, {}, {"run_id": RUN2, "generation": True},
                                     {"run_id": RUN2, "generation": 0},
                                     {"run_id": RUN1, "generation": 2}])
def test_missing_or_conflicting_identity_cannot_consume_armed_row(runtime, execution):
    start(runtime)
    callback(runtime, RUN1, 1)
    resume(runtime)
    before = rows(runtime)
    response = runtime.adapter._maybe_fold_mission_delegation({
        "mission_id": MISSION, "status": "completed", "execution": execution,
    })
    assert response.status == 503
    assert rows(runtime) == before


def test_exact_old_event_cannot_masquerade_as_new_execution(runtime):
    start(runtime)
    callback(runtime, RUN1, 1, event_id="original-event")
    resume(runtime)
    assert callback(runtime, RUN2, 2, event_id="original-event")[1]["outcome"] == "duplicate"
    assert runtime.ad.find_delegation_by_mission_id(MISSION)["mission_run_id"] is None
    assert callback(runtime, RUN2, 2, event_id="new-event")[1]["outcome"] == "folded"


def test_concurrent_same_run_callbacks_publish_only_one_receipt(runtime):
    start(runtime)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: callback(runtime, RUN1, 1)[1]["outcome"], range(2)))
    assert sorted(outcomes) == ["duplicate", "folded"]
    assert runtime.queue.qsize() == 1


def test_concurrent_resume_hooks_create_one_wait(runtime):
    start(runtime)
    callback(runtime, RUN1, 1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: resume(runtime), range(2)))
    assert len(rows(runtime)) == 2
    assert runtime.ad.active_count() == 1


def test_foreign_conversation_cannot_rebind_parent(runtime):
    start(runtime)
    callback(runtime, RUN1, 1)
    resume(runtime, origin="foreign-parent")
    assert len(rows(runtime)) == 1
    assert runtime.ad.find_delegation_by_mission_id(MISSION)["parent_session_id"] == PARENT


def test_failed_resume_does_not_arm(runtime):
    start(runtime)
    callback(runtime, RUN1, 1)
    runtime.plugin.enroll_after_start_mission(
        tool_name="mcp__sandboxed_assistant__resume_mission", status="ok",
        session_id=PARENT, tool_call_id="refused", args={"mission_id": MISSION},
        result=json.dumps({"mission_id": MISSION, "resume_accepted": False, "error": "workspace occupied"}),
    )
    assert len(rows(runtime)) == 1


def test_observer_cannot_arm_resume_from_post_tool_hook(runtime):
    from cron.controller_scope import bind_controller_scope, scope_from_job

    start(runtime)
    callback(runtime, RUN1, 1)
    scope = scope_from_job({"id": "observer", "controller": {
        "project": "verity-lido", "mode": "observer", "permissions": ["sandboxed.read"],
        "repositories": [], "reserved_local_areas": [],
    }})
    with bind_controller_scope(scope):
        resume(runtime)
    assert len(rows(runtime)) == 1


@pytest.mark.parametrize("resumed", [False, True])
def test_gateway_process_exit_does_not_complete_external_execution(runtime, resumed):
    # The enrollment process really exits. The new process has an empty
    # in-memory registry and must retain the external runner's pending row.
    code = """
import json
from tools.mission_delegation import enroll_conversational_start_mission
from tools.async_delegation import fold_mission_completion, arm_mission_resume
enroll_conversational_start_mission(result=json.dumps({'mission_id': %r}),
    origin_session_id=%r, parent_session_id=%r)
if %r:
    fold_mission_completion(mission_id=%r, status='completed',
        execution={'run_id': %r, 'generation': 1}, event_id='old')
    arm_mission_resume(mission_id=%r, resume_key='process-resume')
""" % (MISSION, PARENT, PARENT, resumed, MISSION, RUN1, MISSION)
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, timeout=30)
    before = runtime.ad.find_delegation_by_mission_id(MISSION)
    assert before["state"] == "running"
    assert runtime.ad.recover_abandoned_delegations() == 0
    assert runtime.ad.find_delegation_by_mission_id(MISSION)["event_json"] is None
    assert callback(runtime, RUN2 if resumed else RUN1, 2 if resumed else 1)[1]["outcome"] == "folded"
    event = runtime.queue.get_nowait()
    assert event["parent_session_id"] == PARENT


@pytest.mark.parametrize("mission", [None, {"id": MISSION, "status": "active"}])
def test_fenced_resume_envelope_arms_confirmed_mutation(runtime, mission):
    from tools.mission_delegation import enroll_conversational_resume_mission

    start(runtime)
    callback(runtime, RUN1, 1)
    # This is the installed assistant-mcp accepted-mutation/readback-warning
    # shape, transported through Hermes' JSON result wrapper.
    wire = {"mission_id": MISSION, "resume_accepted": True, "mission": mission,
            "steered": True, "steer_warning": None,
            "state_warning": "Resume request accepted, but current mission state could not be read"}
    result = enroll_conversational_resume_mission(
        result="<untrusted_tool_result>\n" + json.dumps({"result": json.dumps(wire)})
        + "\n</untrusted_tool_result>",
        origin_session_id=PARENT, tool_call_id="real-envelope",
    )
    assert result["status"] == "enrolled" and result["generation_floor"] == 2


def message_reply(*, accepted=True, previous=True, queued=False):
    reply = {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "mission_id": MISSION,
             "message_accepted": accepted, "queued": queued}
    if previous:
        reply["previous_execution"] = {"run_id": RUN1, "generation": 1}
    return reply


def send_message(runtime, reply, *, call_id="message-one", origin=PARENT, status="ok"):
    runtime.plugin.enroll_after_start_mission(
        tool_name="mcp__sandboxed_assistant__send_message_to_mission",
        status=status, session_id=origin, args={"mission_id": MISSION},
        tool_call_id=call_id, result=reply,
    )


@pytest.mark.parametrize("queued", [False, True])
def test_idle_message_continuation_uses_native_identity_and_same_parent(runtime, queued):
    start(runtime)
    callback(runtime, RUN1, 1)
    # Native MCP keeps the HTTP response inside a fenced JSON result.
    reply = json.dumps({"result": '<untrusted_tool_result>\n' +
                        json.dumps(message_reply(queued=queued)) + '\n</untrusted_tool_result>'})
    send_message(runtime, reply)
    assert len(rows(runtime)) == 2
    assert runtime.ad.find_delegation_by_mission_id(MISSION)["mission_generation"] == 2
    assert callback(runtime, RUN2, 2)[1]["outcome"] == "folded"
    send_message(runtime, reply)  # delayed duplicate hook cannot arm generation 3
    assert len(rows(runtime)) == 2
    events = [runtime.queue.get_nowait(), runtime.queue.get_nowait()]
    assert all(event["parent_session_id"] == PARENT for event in events)


@pytest.mark.parametrize("reply", [
    {"id": MISSION, "queued": False},  # legacy id is never a mission identifier
    message_reply(accepted=False),
    message_reply(previous=False, queued=True),  # active queued steer
    message_reply(previous=False, queued=False),  # accepted duplicate, no new run
    {**message_reply(), "error": "rejected"},
    {**message_reply(), "mission_id": None},
    {**message_reply(), "previous_execution": {"run_id": RUN1, "generation": True}},
    {**message_reply(), "previous_execution": {"run_id": RUN2, "generation": 1}},
])
def test_message_without_authentic_idle_admission_never_arms(runtime, reply):
    start(runtime)
    callback(runtime, RUN1, 1)
    before = rows(runtime)
    send_message(runtime, reply)
    assert rows(runtime) == before


def test_idle_message_callback_arriving_before_hook_is_reconciled(runtime):
    start(runtime)
    callback(runtime, RUN1, 1)
    assert callback(runtime, RUN2, 2)[0] == 503
    send_message(runtime, message_reply())
    assert runtime.ad.find_delegation_by_mission_id(MISSION)["mission_run_id"] == RUN2
    assert runtime.queue.qsize() == 2


def test_delayed_message_ack_cannot_arm_from_a_newer_ledger_generation(runtime):
    from tools.mission_delegation import enroll_conversational_message_mission

    start(runtime)
    callback(runtime, RUN1, 1)
    resume(runtime)
    callback(runtime, RUN2, 2)
    before = rows(runtime)
    outcome = enroll_conversational_message_mission(
        result=message_reply(), origin_session_id=PARENT, tool_call_id="late-message",
    )
    assert outcome["reason"] == "prior_execution_changed"
    assert rows(runtime) == before


def test_message_hook_failure_or_foreign_parent_cannot_enroll(runtime):
    start(runtime)
    callback(runtime, RUN1, 1)
    before = rows(runtime)
    send_message(runtime, message_reply(), status="error")
    send_message(runtime, message_reply(), origin="foreign-session")
    assert rows(runtime) == before
