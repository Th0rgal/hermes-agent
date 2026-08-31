"""Tests for durable explicit project-to-session routes (hermes_cli/project_routes).

Covers the workstream invariants:

- routes are explicit-or-nothing: no route → LookupError, never a fallback
  to the active project / current desktop session;
- project validation (unknown, archived) and session validation (missing,
  non-local source) reject bad bindings;
- compression/continuation migrates the stored route to the live tip
  atomically, and readers only ever see the old or the new binding.
"""

from __future__ import annotations

import pytest

from hermes_cli import project_routes as routes
from hermes_cli import projects_db as pdb
from hermes_state import SessionDB


@pytest.fixture
def conn(tmp_path):
    c = pdb.connect(db_path=tmp_path / "projects.db")
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def session_db(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def project_id(conn):
    return pdb.create_project(conn, name="Aurora", folders=["/www/aurora"])


def _mk_session(session_db, sid, source="desktop", **kwargs):
    session_db.create_session(session_id=sid, source=source, **kwargs)
    return sid


# ---------------------------------------------------------------------------
# bind / get / unbind / list
# ---------------------------------------------------------------------------


class TestBindRoute:
    def test_bind_and_get(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-1")
        route = routes.bind_route(conn, project_id, "sess-1", session_db=session_db)
        assert route.project_id == project_id
        assert route.session_id == "sess-1"

        got = routes.get_route(conn, project_id)
        assert got is not None
        assert got.session_id == "sess-1"

    def test_bind_by_slug(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-1")
        proj = pdb.get_project(conn, project_id)
        route = routes.bind_route(conn, proj.slug, "sess-1", session_db=session_db)
        assert route.project_id == project_id

    def test_bind_is_upsert(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-1")
        _mk_session(session_db, "sess-2")
        routes.bind_route(conn, project_id, "sess-1", session_db=session_db)
        routes.bind_route(conn, project_id, "sess-2", session_db=session_db)
        assert routes.get_route(conn, project_id).session_id == "sess-2"
        assert len(routes.list_routes(conn)) == 1

    def test_bind_unknown_project_rejected(self, conn, session_db):
        _mk_session(session_db, "sess-1")
        with pytest.raises(LookupError, match="unknown project"):
            routes.bind_route(conn, "nope", "sess-1", session_db=session_db)

    def test_bind_archived_project_rejected(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-1")
        pdb.archive_project(conn, project_id)
        with pytest.raises(LookupError, match="archived"):
            routes.bind_route(conn, project_id, "sess-1", session_db=session_db)

    def test_bind_missing_session_rejected(self, conn, session_db, project_id):
        with pytest.raises(LookupError, match="not found"):
            routes.bind_route(conn, project_id, "ghost", session_db=session_db)

    def test_bind_gateway_source_session_rejected(self, conn, session_db, project_id):
        """Routes may only target local surfaces — a Telegram-sourced session
        must not be silently re-targetable via a project route."""
        _mk_session(session_db, "tg-1", source="telegram")
        with pytest.raises(ValueError, match="source 'telegram'"):
            routes.bind_route(conn, project_id, "tg-1", session_db=session_db)

    def test_bind_compression_parent_binds_live_tip(self, conn, session_db, project_id):
        """Binding a compressed-away parent must land on the continuation
        child that actually receives messages."""
        _mk_session(session_db, "parent")
        session_db.append_message("parent", "user", "before compression")
        session_db.end_session("parent", end_reason="compression")
        _mk_session(session_db, "child", parent_session_id="parent")
        session_db.append_message("child", "user", "after compression")

        route = routes.bind_route(conn, project_id, "parent", session_db=session_db)
        assert route.session_id == "child"

    def test_unbind(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-1")
        routes.bind_route(conn, project_id, "sess-1", session_db=session_db)
        assert routes.unbind_route(conn, project_id) is True
        assert routes.get_route(conn, project_id) is None
        assert routes.unbind_route(conn, project_id) is False

    def test_unbind_clears_authoritative_sandboxed_binding(
        self, conn, session_db, project_id, tmp_path, monkeypatch
    ):
        import sqlite3

        roster = tmp_path / "sandboxed-projects.db"
        sdb = sqlite3.connect(roster)
        sdb.execute(
            "CREATE TABLE project_bindings "
            "(slug TEXT PRIMARY KEY, control_session_id TEXT, bound_at TEXT, bound_by TEXT)"
        )
        sdb.commit()
        sdb.close()
        monkeypatch.setenv("SANDBOXED_PROJECTS_DB", str(roster))

        _mk_session(session_db, "sess-1")
        routes.bind_route(conn, project_id, "sess-1", session_db=session_db)
        assert routes.lookup_sandboxed_binding("aurora") == "sess-1"

        assert routes.unbind_route(conn, project_id) is True
        assert routes.lookup_sandboxed_binding("aurora") is None
        with pytest.raises(LookupError, match="no explicit session route"):
            routes.resolve_route_target(conn, project_id, session_db=session_db)

    def test_route_cascades_on_project_delete(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-1")
        routes.bind_route(conn, project_id, "sess-1", session_db=session_db)
        pdb.delete_project(conn, project_id)
        assert routes.list_routes(conn) == []


# ---------------------------------------------------------------------------
# resolution: explicit-or-nothing (no current-Desktop fallback)
# ---------------------------------------------------------------------------


class TestResolveRouteTarget:
    def test_resolves_exact_bound_session(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-1")
        # A decoy "currently open" desktop session that a naive fallback
        # would pick (newer, same source). Resolution must ignore it.
        _mk_session(session_db, "current-desktop-window")
        routes.bind_route(conn, project_id, "sess-1", session_db=session_db)

        target = routes.resolve_route_target(conn, project_id, session_db=session_db)
        assert target.session_id == "sess-1"
        assert target.source == "desktop"

    def test_no_route_raises_instead_of_desktop_fallback(
        self, conn, session_db, project_id
    ):
        _mk_session(session_db, "current-desktop-window")
        with pytest.raises(LookupError, match="no explicit session route"):
            routes.resolve_route_target(conn, project_id, session_db=session_db)

    def test_unknown_project_raises(self, conn, session_db):
        with pytest.raises(LookupError, match="unknown project"):
            routes.resolve_route_target(conn, "ghost", session_db=session_db)

    def test_sandboxed_binding_repairs_a_missing_hermes_route(
        self, conn, session_db, tmp_path, monkeypatch
    ):
        import sqlite3

        project_id = pdb.create_project(
            conn, name="Verity core", folders=["/tmp/verity-core"]
        )
        _mk_session(session_db, "sess-1299f6")
        roster = tmp_path / "sandboxed-projects.db"
        sdb = sqlite3.connect(roster)
        sdb.execute(
            "CREATE TABLE project_bindings "
            "(slug TEXT PRIMARY KEY, control_session_id TEXT, bound_at TEXT, bound_by TEXT)"
        )
        sdb.execute(
            "INSERT INTO project_bindings VALUES ('verity-core', 'sess-1299f6', 'now', 'test')"
        )
        sdb.commit()
        sdb.close()
        monkeypatch.setenv("SANDBOXED_PROJECTS_DB", str(roster))

        target = routes.resolve_route_target(
            conn, "verity-core", session_db=session_db
        )
        assert target.session_id == "sess-1299f6"
        assert routes.get_route(conn, project_id).session_id == "sess-1299f6"

    def test_old_sandboxed_schema_is_treated_as_unavailable(
        self, conn, session_db, project_id, tmp_path, monkeypatch
    ):
        import sqlite3

        roster = tmp_path / "old-sandboxed-projects.db"
        sdb = sqlite3.connect(roster)
        sdb.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        sdb.commit()
        sdb.close()
        monkeypatch.setenv("SANDBOXED_PROJECTS_DB", str(roster))

        assert routes.lookup_sandboxed_binding("aurora") is None
        with pytest.raises(LookupError, match="no explicit session route"):
            routes.resolve_route_target(conn, project_id, session_db=session_db)

    def test_routes_json_alias_resolves_to_the_bound_slug(
        self, conn, session_db, tmp_path, monkeypatch
    ):
        """Cron deliver=project:verity-core must use the `verity` route."""
        project_id = pdb.create_project(conn, name="Verity", folders=["/tmp/verity"])
        _mk_session(session_db, "sess-verity")
        routes.bind_route(conn, project_id, "sess-verity", session_db=session_db)
        (tmp_path / "routes.json").write_text('{"verity": "verity-core"}\n')
        monkeypatch.setenv("HERMES_PROJECTS_DIR", str(tmp_path))

        target = routes.resolve_route_target(
            conn, "verity-core", session_db=session_db
        )
        assert target.project_id == project_id
        assert target.session_id == "sess-verity"

    def test_canonical_slug_resolves_when_db_row_uses_alias(
        self, conn, session_db, tmp_path, monkeypatch
    ):
        """deliver=project:verity-lido finds the bound lido-audit row."""
        project_id = pdb.create_project(
            conn, name="Lido audit", folders=["/tmp/lido"]
        )
        _mk_session(session_db, "sess-lido")
        routes.bind_route(conn, project_id, "sess-lido", session_db=session_db)
        (tmp_path / "routes.json").write_text(
            '{"lido-audit": "verity-lido", "lido": "verity-lido"}\n'
        )
        monkeypatch.setenv("HERMES_PROJECTS_DIR", str(tmp_path))

        target = routes.resolve_route_target(
            conn, "verity-lido", session_db=session_db
        )
        assert target.project_id == project_id
        assert target.session_id == "sess-lido"

    def test_resolve_reopens_ws_orphan_reaped_session(
        self, conn, session_db, project_id
    ):
        _mk_session(session_db, "sess-1")
        routes.bind_route(conn, project_id, "sess-1", session_db=session_db)
        session_db.end_session("sess-1", end_reason="ws_orphan_reap")
        assert session_db.get_session("sess-1")["ended_at"] is not None

        target = routes.resolve_route_target(
            conn, project_id, session_db=session_db
        )
        assert target.session_id == "sess-1"
        row = session_db.get_session("sess-1")
        assert row["ended_at"] is None

    def test_archived_project_raises(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-1")
        routes.bind_route(conn, project_id, "sess-1", session_db=session_db)
        pdb.archive_project(conn, project_id)
        with pytest.raises(LookupError, match="archived"):
            routes.resolve_route_target(conn, project_id, session_db=session_db)

    def test_vanished_session_raises_instead_of_fallback(
        self, conn, session_db, project_id, tmp_path
    ):
        _mk_session(session_db, "sess-1")
        routes.bind_route(conn, project_id, "sess-1", session_db=session_db)
        # Simulate the routed session disappearing (fresh state.db).
        empty_db = SessionDB(db_path=tmp_path / "empty.db")
        try:
            empty_db.create_session(
                session_id="current-desktop-window", source="desktop"
            )
            with pytest.raises(LookupError, match="not found"):
                routes.resolve_route_target(conn, project_id, session_db=empty_db)
        finally:
            empty_db.close()


# ---------------------------------------------------------------------------
# compression/continuation migration
# ---------------------------------------------------------------------------


class TestRouteMigration:
    def test_migrate_session_routes_is_atomic_update(self, conn, session_db, project_id):
        _mk_session(session_db, "old")
        routes.bind_route(conn, project_id, "old", session_db=session_db)
        moved = routes.migrate_session_routes(conn, "old", "new")
        assert moved == 1
        assert routes.get_route(conn, project_id).session_id == "new"

    def test_migrate_moves_every_route_bound_to_old_id(self, conn, session_db):
        p1 = pdb.create_project(conn, name="One", folders=[])
        p2 = pdb.create_project(conn, name="Two", folders=[])
        _mk_session(session_db, "shared")
        routes.bind_route(conn, p1, "shared", session_db=session_db)
        routes.bind_route(conn, p2, "shared", session_db=session_db)

        assert routes.migrate_session_routes(conn, "shared", "tip") == 2
        assert routes.get_route(conn, p1).session_id == "tip"
        assert routes.get_route(conn, p2).session_id == "tip"

    def test_migrate_noop_for_unknown_or_same_id(self, conn):
        assert routes.migrate_session_routes(conn, "a", "a") == 0
        assert routes.migrate_session_routes(conn, "", "b") == 0
        assert routes.migrate_session_routes(conn, "ghost", "b") == 0

    def test_resolution_migrates_route_after_compression(
        self, conn, session_db, project_id
    ):
        """When compression forks a continuation child, resolving the route
        must (a) return the child and (b) durably repoint the stored route."""
        _mk_session(session_db, "parent")
        session_db.append_message("parent", "user", "hi")
        routes.bind_route(conn, project_id, "parent", session_db=session_db)

        session_db.end_session("parent", end_reason="compression")
        _mk_session(session_db, "child", parent_session_id="parent")
        session_db.append_message("child", "user", "continued")

        target = routes.resolve_route_target(conn, project_id, session_db=session_db)
        assert target.session_id == "child"
        # Durable: the stored route moved, not just the returned value.
        assert routes.get_route(conn, project_id).session_id == "child"

    def test_resolution_follows_multi_hop_continuation(
        self, conn, session_db, project_id
    ):
        _mk_session(session_db, "gen0")
        session_db.append_message("gen0", "user", "m0")
        routes.bind_route(conn, project_id, "gen0", session_db=session_db)

        session_db.end_session("gen0", end_reason="compression")
        _mk_session(session_db, "gen1", parent_session_id="gen0")
        session_db.append_message("gen1", "user", "m1")
        session_db.end_session("gen1", end_reason="compression")
        _mk_session(session_db, "gen2", parent_session_id="gen1")
        session_db.append_message("gen2", "user", "m2")

        target = routes.resolve_route_target(conn, project_id, session_db=session_db)
        assert target.session_id == "gen2"
        assert routes.get_route(conn, project_id).session_id == "gen2"


class TestBindRouteSourceOverride:
    """The source allowlist is a heuristic; an operator may override it.

    It exists to stop a project being routed at a machine-created session —
    on the production host there are 4364 `webhook` and 5634 `cron` rows,
    nearly all throwaway. But a human does occasionally adopt one as a real
    working conversation, and no property of the row distinguishes that case
    (98% of webhook sessions carry user messages, because the inbound payload
    is itself recorded as one). Hence an explicit escape hatch rather than a
    cleverer predicate.
    """

    def test_an_unroutable_source_is_refused_by_default(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-hook", source="webhook")
        with pytest.raises(ValueError) as caught:
            routes.bind_route(conn, project_id, "sess-hook", session_db=session_db)
        assert "webhook" in str(caught.value)
        assert "override deliberately" in str(caught.value)
        assert routes.get_route(conn, project_id) is None

    def test_the_override_binds_it(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-hook", source="webhook")
        route = routes.bind_route(
            conn, project_id, "sess-hook",
            session_db=session_db, allow_unroutable_source=True,
        )
        assert route.session_id == "sess-hook"
        assert routes.get_route(conn, project_id).session_id == "sess-hook"

    def test_the_override_bypasses_only_the_source_check(self, conn, session_db, project_id):
        # A missing session is still an error, or the override would become a
        # way to bind a project to anything at all.
        with pytest.raises(LookupError):
            routes.bind_route(
                conn, project_id, "no-such-session",
                session_db=session_db, allow_unroutable_source=True,
            )

    def test_a_routable_source_needs_no_override(self, conn, session_db, project_id):
        _mk_session(session_db, "sess-desktop", source="desktop")
        route = routes.bind_route(conn, project_id, "sess-desktop", session_db=session_db)
        assert route.session_id == "sess-desktop"
