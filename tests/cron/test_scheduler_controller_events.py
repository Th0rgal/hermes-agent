"""Scheduler wiring for structured controller events.

The scheduler's beats — a cron fire, a ``[SILENT]`` suppression, an explicit
project route resolving — are recorded as typed controller events
(``gateway/controller_events``). These tests pin:

* ``run_one_job`` emits ``controller_wakeup`` (silent) when a job fires;
* the legacy ``[SILENT]`` marker path additionally records a structured
  ``reconciliation_event(kind="silent_suppression")`` without changing the
  existing skip-delivery behavior;
* ``deliver=project:<token>`` resolution emits ``project_progress``
  (stage=``route_resolved``) referencing only local identifiers;
* emission is best-effort — a broken sink can never break job firing.
"""

from __future__ import annotations

import pytest

import cron.scheduler as s
import hermes_state
from gateway.controller_events import set_controller_event_sink
from hermes_cli import projects_db as pdb
from hermes_cli import project_routes as routes


@pytest.fixture
def captured_events():
    seen = []
    set_controller_event_sink(seen.append)
    try:
        yield seen
    finally:
        set_controller_event_sink(None)


def _patch_pipeline(monkeypatch, *, final="final response"):
    calls = []

    def fake_run_job(job, *, defer_agent_teardown=None):
        calls.append("run_job")
        return (True, "out", final, None)

    monkeypatch.setattr(s, "run_job", fake_run_job)
    monkeypatch.setattr(s, "save_job_output", lambda jid, out: f"/tmp/{jid}.txt")
    monkeypatch.setattr(s, "_deliver_result", lambda job, content, adapters=None, loop=None: calls.append("deliver"))
    monkeypatch.setattr(s, "mark_job_run", lambda jid, ok, err=None, delivery_error=None: None)
    return calls


class TestWakeupEmission:
    def test_cron_fire_emits_silent_wakeup(self, monkeypatch, captured_events):
        _patch_pipeline(monkeypatch)

        assert s.run_one_job({"id": "j1", "name": "nightly"}) is True

        wakeups = [e for e in captured_events if e.event_type == "controller_wakeup"]
        assert len(wakeups) == 1
        assert wakeups[0].reason == "cron_fire"
        assert wakeups[0].job_id == "j1"
        assert wakeups[0].job_name == "nightly"
        assert wakeups[0].silent is True
        assert wakeups[0].ts > 0

    def test_broken_sink_does_not_break_firing(self, monkeypatch):
        calls = _patch_pipeline(monkeypatch)

        def explode(_event):
            raise RuntimeError("sink down")

        set_controller_event_sink(explode)
        try:
            assert s.run_one_job({"id": "j1", "name": "nightly"}) is True
        finally:
            set_controller_event_sink(None)
        assert "run_job" in calls and "deliver" in calls


class TestStructuredSilentSuppression:
    def test_silent_marker_records_reconciliation_event(self, monkeypatch, captured_events):
        calls = _patch_pipeline(monkeypatch, final="[SILENT]")

        s.run_one_job({"id": "j2", "name": "watcher"})

        assert "deliver" not in calls  # legacy behavior unchanged
        recs = [e for e in captured_events if e.event_type == "reconciliation_event"]
        assert len(recs) == 1
        assert recs[0].kind == "silent_suppression"
        assert recs[0].subject == "j2"
        assert recs[0].silent is True

    def test_loud_response_records_no_suppression_event(self, monkeypatch, captured_events):
        calls = _patch_pipeline(monkeypatch, final="real report")

        s.run_one_job({"id": "j3", "name": "watcher"})

        assert "deliver" in calls
        assert [e for e in captured_events if e.event_type == "reconciliation_event"] == []

    def test_quoted_marker_mid_sentence_is_not_suppressed(self, monkeypatch, captured_events):
        calls = _patch_pipeline(
            monkeypatch,
            final="I considered staying [SILENT] but here is the summary anyway.",
        )

        s.run_one_job({"id": "j4", "name": "watcher"})

        assert "deliver" in calls
        assert [e for e in captured_events if e.event_type == "reconciliation_event"] == []


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    """Hermetic projects.db + state.db (pattern from test_scheduler_project_routes)."""
    projects_path = tmp_path / "projects.db"
    state_path = tmp_path / "state.db"

    monkeypatch.setattr(pdb, "projects_db_path", lambda: projects_path)

    real_session_db = hermes_state.SessionDB

    def _session_db_factory(*args, **kwargs):
        if not args and "db_path" not in kwargs:
            kwargs["db_path"] = state_path
        return real_session_db(*args, **kwargs)

    monkeypatch.setattr(hermes_state, "SessionDB", _session_db_factory)

    session_db = real_session_db(db_path=state_path)
    try:
        yield {"projects_path": projects_path, "session_db": session_db}
    finally:
        session_db.close()


def _bind(route_env, project_name, session_id, *, source="desktop"):
    session_db = route_env["session_db"]
    if session_db.get_session(session_id) is None:
        session_db.create_session(session_id=session_id, source=source)
    with pdb.connect_closing(db_path=route_env["projects_path"]) as conn:
        project_id = pdb.create_project(conn, name=project_name, folders=[])
        routes.bind_route(conn, project_id, session_id, session_db=session_db)
    return project_id


class TestProjectProgressEmission:
    def test_route_resolution_emits_project_progress(self, route_env, captured_events):
        _bind(route_env, "Alpha", "sess-alpha")

        target = s._resolve_project_route_target({"id": "j1"}, "alpha")

        assert target is not None
        progress = [e for e in captured_events if e.event_type == "project_progress"]
        assert len(progress) == 1
        assert progress[0].project == "alpha"
        assert progress[0].stage == "route_resolved"
        assert progress[0].session_id == "sess-alpha"
        assert progress[0].silent is True

    def test_progress_event_references_only_local_identifiers(self, route_env, captured_events):
        from gateway.controller_events import event_to_payload

        _bind(route_env, "Alpha", "sess-alpha")
        s._resolve_project_route_target({"id": "j1"}, "alpha")

        payload = event_to_payload(captured_events[-1])
        assert not ({"chat_id", "thread_id", "platform"} & set(payload))

    def test_unresolved_route_emits_no_progress(self, route_env, captured_events):
        target = s._resolve_project_route_target({"id": "j1"}, "ghost")

        assert target is None
        assert [e for e in captured_events if e.event_type == "project_progress"] == []
