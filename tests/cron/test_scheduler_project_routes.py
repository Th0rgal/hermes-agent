"""Cron delivery through explicit project-to-session routes.

Exact-origin guarantees for ``deliver=project:<id|slug>``:

- the target is the *exact* explicitly bound session — never a decoy
  "currently open" Desktop session, the active project, or a home channel;
- a missing/broken route drops the target and surfaces a delivery error
  instead of any fallback;
- compression/continuation re-points the durable route to the live tip and
  the delivery follows it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import hermes_state
from cron.scheduler import (
    _deliver_result,
    _resolve_delivery_target,
    _resolve_delivery_targets,
)
from hermes_cli import project_routes as routes
from hermes_cli import projects_db as pdb


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    """Hermetic projects.db + state.db for the scheduler's lazy opens."""
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


class TestProjectTargetResolution:
    def test_resolves_exact_bound_desktop_session(self, route_env):
        _bind(route_env, "Aurora", "sess-1")
        # Decoy: a newer desktop session that a current-Desktop fallback
        # would grab. The route must win, exactly.
        route_env["session_db"].create_session(
            session_id="current-desktop-window", source="desktop"
        )

        target = _resolve_delivery_target({"id": "j", "deliver": "project:aurora"})
        assert target == {
            "kind": "local_session",
            "platform": "desktop",
            "chat_id": "sess-1",
            "session_id": "sess-1",
            "thread_id": None,
        }

    def test_resolves_by_project_id_token(self, route_env):
        project_id = _bind(route_env, "Aurora", "sess-1")
        target = _resolve_delivery_target(
            {"id": "j", "deliver": f"project:{project_id}"}
        )
        assert target["session_id"] == "sess-1"

    def test_webui_source_routes_to_webui_local_target(self, route_env):
        _bind(route_env, "Aurora", "web-1", source="webui")
        target = _resolve_delivery_target({"id": "j", "deliver": "project:aurora"})
        assert target["kind"] == "local_session"
        assert target["platform"] == "webui"
        assert target["session_id"] == "web-1"

    def test_explicit_webhook_uses_adapter_but_bound_webhook_is_local(self, route_env):
        session_db = route_env["session_db"]
        session_db.create_session(session_id="hook-session", source="webhook")
        with pdb.connect_closing(db_path=route_env["projects_path"]) as conn:
            project_id = pdb.create_project(conn, name="Aurora", folders=[])
            routes.bind_route(
                conn,
                project_id,
                "hook-session",
                session_db=session_db,
                allow_unroutable_source=True,
            )

        explicit = _resolve_delivery_target(
            {"id": "j", "deliver": "webhook:external-hook"}
        )
        assert explicit == {
            "platform": "webhook",
            "chat_id": "external-hook",
            "thread_id": None,
        }
        bound = _resolve_delivery_target(
            {"id": "j", "deliver": "project:aurora"}
        )
        assert bound["kind"] == "local_session"
        assert bound["session_id"] == "hook-session"

    def test_api_server_source_routes_as_local_session_target(self, route_env):
        _bind(route_env, "Aurora", "api-1", source="api_server")
        target = _resolve_delivery_target({"id": "j", "deliver": "project:aurora"})
        assert target == {
            "kind": "local_session",
            "platform": "api_server",
            "chat_id": "api-1",
            "session_id": "api-1",
            "thread_id": None,
        }

    def test_subagent_source_routes_as_local_session_target(self, route_env):
        _bind(route_env, "Aurora", "sub-1", source="subagent")
        target = _resolve_delivery_target({"id": "j", "deliver": "project:aurora"})
        assert target["kind"] == "local_session"
        assert target["platform"] == "subagent"
        assert target["session_id"] == "sub-1"

    def test_unbound_project_yields_no_target(self, route_env, monkeypatch):
        """No route → no target. A configured home channel and an open
        desktop session must NOT be substituted."""
        with pdb.connect_closing(db_path=route_env["projects_path"]) as conn:
            pdb.create_project(conn, name="Aurora", folders=[])
        route_env["session_db"].create_session(
            session_id="current-desktop-window", source="desktop"
        )
        monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "-100999")

        assert _resolve_delivery_target({"id": "j", "deliver": "project:aurora"}) is None

    def test_unknown_project_yields_no_target(self, route_env):
        assert _resolve_delivery_target({"id": "j", "deliver": "project:ghost"}) is None

    def test_archived_project_yields_no_target(self, route_env):
        project_id = _bind(route_env, "Aurora", "sess-1")
        with pdb.connect_closing(db_path=route_env["projects_path"]) as conn:
            pdb.archive_project(conn, project_id)
        assert _resolve_delivery_target({"id": "j", "deliver": "project:aurora"}) is None

    def test_empty_project_token_yields_no_target(self, route_env):
        assert _resolve_delivery_target({"id": "j", "deliver": "project:"}) is None

    def test_combines_with_other_targets(self, route_env, monkeypatch):
        _bind(route_env, "Aurora", "sess-1")
        monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "-100999")
        monkeypatch.delenv("TELEGRAM_HOME_CHANNEL_THREAD_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_CRON_THREAD_ID", raising=False)

        targets = _resolve_delivery_targets(
            {"id": "j", "deliver": "project:aurora,telegram"}
        )
        assert len(targets) == 2
        assert targets[0]["session_id"] == "sess-1"
        assert targets[1]["platform"] == "telegram"


class TestProjectRouteContinuation:
    def test_delivery_follows_compression_continuation(self, route_env):
        """Compression forks a child; the delivery target and the durable
        route must both move to the live tip — atomically, via the store."""
        session_db = route_env["session_db"]
        _bind(route_env, "Aurora", "parent")
        session_db.append_message("parent", "user", "hi")
        session_db.end_session("parent", end_reason="compression")
        session_db.create_session(
            session_id="child", source="desktop", parent_session_id="parent"
        )
        session_db.append_message("child", "user", "continued")

        target = _resolve_delivery_target({"id": "j", "deliver": "project:aurora"})
        assert target["session_id"] == "child"

        with pdb.connect_closing(db_path=route_env["projects_path"]) as conn:
            assert routes.get_route(conn, "aurora").session_id == "child"

    def test_route_migration_survives_repeat_resolution(self, route_env):
        session_db = route_env["session_db"]
        _bind(route_env, "Aurora", "parent")
        session_db.append_message("parent", "user", "hi")
        session_db.end_session("parent", end_reason="compression")
        session_db.create_session(
            session_id="child", source="desktop", parent_session_id="parent"
        )
        session_db.append_message("child", "user", "continued")

        first = _resolve_delivery_target({"id": "j", "deliver": "project:aurora"})
        second = _resolve_delivery_target({"id": "j", "deliver": "project:aurora"})
        assert first["session_id"] == second["session_id"] == "child"

    def test_canonical_slug_resolves_when_db_row_uses_alias(
        self, route_env, tmp_path, monkeypatch
    ):
        """Sandboxed routes.json maps lido-audit -> verity-lido while the
        Hermes Project row stays slug=lido-audit. Cron deliver=project:verity-lido
        must find that bound session, not drop the target."""
        _bind(route_env, "Lido audit", "sess-lido")
        (tmp_path / "routes.json").write_text(
            '{"lido-audit": "verity-lido", "lido": "verity-lido"}\n'
        )
        monkeypatch.setenv("HERMES_PROJECTS_DIR", str(tmp_path))

        target = _resolve_delivery_target(
            {"id": "j", "deliver": "project:verity-lido"}
        )
        assert target is not None
        assert target["session_id"] == "sess-lido"
        assert target["kind"] == "local_session"

    def test_delivery_reopens_ws_orphan_reaped_session(self, route_env):
        """Desktop websocket teardown stamps ws_orphan_reap. Delivery must
        still append and reopen so the operator chat is live again."""
        session_db = route_env["session_db"]
        _bind(route_env, "Aurora", "sess-1")
        session_db.append_message("sess-1", "user", "hi")
        session_db.end_session("sess-1", end_reason="ws_orphan_reap")
        assert session_db.get_session("sess-1")["ended_at"] is not None

        job = {"id": "j", "name": "aurora-report", "deliver": "project:aurora"}
        assert _deliver_result(job, "back online") is None

        row = session_db.get_session("sess-1")
        assert row["ended_at"] is None
        assert row.get("end_reason") in (None, "")
        msgs = session_db.get_messages("sess-1")
        assert any("back online" in str(m.get("content", "")) for m in msgs)

    def test_delivery_requires_explicit_adoption_of_subagent_child(self, route_env):
        """A newer worker child cannot hijack the bound control conversation."""
        session_db = route_env["session_db"]
        _bind(route_env, "Aurora", "parent", source="api_server")
        session_db.create_session(
            session_id="child",
            source="subagent",
            parent_session_id="parent",
            model_config={"_delegate_from": "parent"},
        )
        session_db.append_message("child", "user", "operator is here")

        target = _resolve_delivery_target({"id": "j", "deliver": "project:aurora"})
        assert target["session_id"] == "parent"
        assert target["kind"] == "local_session"

        job = {"id": "j", "name": "aurora-report", "deliver": "project:aurora"}
        assert _deliver_result(job, "hello from cron") is None
        parent_msgs = session_db.get_messages("parent")
        assert any("hello from cron" in str(m.get("content", "")) for m in parent_msgs)
        child_msgs = session_db.get_messages("child")
        assert not any("hello from cron" in str(m.get("content", "")) for m in child_msgs)

        with pdb.connect_closing(db_path=route_env["projects_path"]) as conn:
            routes.bind_route(conn, "aurora", "child", session_db=session_db)
        adopted = _resolve_delivery_target(
            {"id": "j", "deliver": "project:aurora"}
        )
        assert adopted["session_id"] == "child"


class TestProjectRouteDelivery:
    def test_deliver_result_appends_to_exact_routed_session(self, route_env):
        _bind(route_env, "Aurora", "sess-1")
        route_env["session_db"].create_session(
            session_id="current-desktop-window", source="desktop"
        )
        job = {"id": "j", "deliver": "project:aurora"}

        with patch(
            "cron.scheduler._deliver_to_local_session", return_value=None
        ) as m_deliver:
            assert _deliver_result(job, "hello") is None
        m_deliver.assert_called_once_with(job, "desktop", "sess-1", "hello")

    def test_deliver_result_reports_error_when_route_missing(self, route_env):
        """Explicit-or-nothing end to end: no route → delivery error, and no
        local-session append happens anywhere (i.e. no desktop fallback)."""
        with pdb.connect_closing(db_path=route_env["projects_path"]) as conn:
            pdb.create_project(conn, name="Aurora", folders=[])
        route_env["session_db"].create_session(
            session_id="current-desktop-window", source="desktop"
        )
        job = {"id": "j", "deliver": "project:aurora"}

        with patch("cron.scheduler._deliver_to_local_session") as m_deliver:
            err = _deliver_result(job, "hello")
        assert err is not None
        assert "no delivery target resolved" in err
        m_deliver.assert_not_called()

    def test_delivery_lands_in_routed_session_transcript(self, route_env):
        """Real append path: the message lands in the routed session, and
        only there."""
        session_db = route_env["session_db"]
        _bind(route_env, "Aurora", "sess-1")
        session_db.create_session(
            session_id="current-desktop-window", source="desktop"
        )
        job = {"id": "j", "name": "aurora-report", "deliver": "project:aurora"}

        with patch("tui_gateway.server._broadcast_global_event") as broadcast:
            assert _deliver_result(job, "route me exactly") is None
        broadcast.assert_called_once_with(
            "sessions.changed", {"session_id": "sess-1"}
        )

        routed = session_db.get_messages("sess-1")
        assert any("route me exactly" in str(m.get("content", "")) for m in routed)
        decoy = session_db.get_messages("current-desktop-window")
        assert not any(
            "route me exactly" in str(m.get("content", "")) for m in decoy
        )
