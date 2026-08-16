"""Conversational start_mission auto-enroll into the mission-delegation ledger."""

import json

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

    monkeypatch.setattr(pr, "process_registry", type("PR", (), {"completion_queue": _Q()})())
    _ad._captured = captured  # type: ignore[attr-defined]
    return _ad


def test_enroll_binds_mission_from_wrapped_mcp_result(ad):
    from tools.mission_delegation import enroll_conversational_start_mission

    inner = json.dumps(
        {
            "mission": {
                "id": "498546da-14b7-48af-afd5-db16a14f5900",
                "title": "TAP",
            }
        }
    )
    wrapped = (
        '<untrusted_tool_result source="mcp__sandboxed_assistant__start_mission">\n'
        + json.dumps({"result": inner})
        + "\n</untrusted_tool_result>"
    )
    out = enroll_conversational_start_mission(
        result=wrapped,
        origin_session_id="20260815_123853_35a245",
        parent_session_id="20260815_123853_35a245",
        goal="document TAP Miles&Go APIs",
    )
    assert out["status"] == "enrolled"
    assert out["mission_id"] == "498546da-14b7-48af-afd5-db16a14f5900"
    row = ad.find_delegation_by_mission_id(out["mission_id"])
    assert row and row["origin_session_id"] == "20260815_123853_35a245"


def test_enroll_is_idempotent(ad):
    from tools.mission_delegation import enroll_conversational_start_mission

    first = enroll_conversational_start_mission(
        result=json.dumps({"mission_id": "m-1"}),
        origin_session_id="sess-1",
        goal="g",
    )
    again = enroll_conversational_start_mission(
        result=json.dumps({"mission_id": "m-1"}),
        origin_session_id="sess-1",
        goal="g",
    )
    assert first["status"] == "enrolled"
    assert again["status"] == "already_enrolled"
    assert again["delegation_id"] == first["delegation_id"]


def test_enroll_skips_ephemeral_and_missing(ad):
    from tools.mission_delegation import enroll_conversational_start_mission

    assert enroll_conversational_start_mission(
        result=json.dumps({"mission_id": "m-2"}),
        origin_session_id="cron_tick_dead",
    )["status"] == "skipped"
    assert enroll_conversational_start_mission(
        result="not-json",
        origin_session_id="sess-1",
    )["status"] == "skipped"


def test_enroll_existing_backfills(ad):
    from tools.mission_delegation import enroll_existing_mission

    out = enroll_existing_mission(
        mission_id="498546da-14b7-48af-afd5-db16a14f5900",
        origin_session_id="20260815_123853_35a245",
        goal="TAP Miles&Go",
    )
    assert out["status"] == "enrolled"
    assert (
        ad.find_delegation_by_mission_id(out["mission_id"])["origin_session_id"]
        == "20260815_123853_35a245"
    )


def test_enrolled_mission_folds_into_origin(ad):
    from tools.mission_delegation import enroll_conversational_start_mission

    enroll_conversational_start_mission(
        result=json.dumps({"mission_id": "m-fold"}),
        origin_session_id="origin-1",
        parent_session_id="origin-1",
        goal="g",
    )
    outcome = ad.fold_mission_completion(
        mission_id="m-fold", status="completed", summary="the report"
    )
    assert outcome == "folded"
    evt = ad._captured[-1]
    assert evt["origin_session_id"] == "origin-1"
    assert evt["results"][0]["summary"] == "the report"


def test_enroll_folds_callback_that_beat_the_ledger(ad, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.platforms.mission_status_route import stash_unroutable_callback
    from tools.mission_delegation import enroll_conversational_start_mission

    stash_unroutable_callback(
        "m-race",
        {
            "mission_id": "m-race",
            "status": "completed",
            "result_summary": "finished first",
        },
    )
    out = enroll_conversational_start_mission(
        result=json.dumps({"mission_id": "m-race"}),
        origin_session_id="origin-race",
        parent_session_id="origin-race",
        goal="g",
    )
    assert out["status"] == "enrolled"
    assert ad._captured[-1]["results"][0]["summary"] == "finished first"
    row = ad.find_delegation_by_mission_id("m-race")
    assert row["delivery_state"] != "pending" or ad._captured
