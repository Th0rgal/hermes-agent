"""The control conversation a cron tick stamps its missions with.

A cron job opens a throwaway session per tick and ends it, so stamping that id
onto a mission files the work under a corpse. `_resolve_control_route` answers
"which durable conversation does this job's project report into", resolved
through the same explicit-or-nothing route store as `deliver=project:`.

The refusal path matters as much as the happy one: returning "" means the
missions go out unstamped, which is honest. Returning the wrong session would
route someone else's results into a live conversation.
"""

import contextlib
from unittest.mock import patch

from cron import scheduler


class _Target:
    def __init__(self, session_id, source="desktop", project_id="p_verity"):
        self.session_id = session_id
        self.source = source
        self.project_id = project_id


@contextlib.contextmanager
def _resolve_returns(target):
    """Make the route store answer with `target`, or raise it if it's an error.

    Patches the real modules' attributes rather than sys.modules: the scheduler
    does `from hermes_cli import project_routes`, which resolves the package
    ATTRIBUTE once the submodule has been imported by any other test — so a
    sys.modules fake is silently bypassed when the suite runs as a whole.
    """
    from hermes_cli import project_routes as routes
    from hermes_cli import projects_db as pdb

    def _resolve(_conn, _token, **_kw):
        if isinstance(target, Exception):
            raise target
        return target

    class _Project:
        slug = "verity"

    @contextlib.contextmanager
    def _conn():
        yield None

    with patch.object(routes, "resolve_route_target", _resolve), patch.object(
        pdb, "connect_closing", _conn
    ), patch.object(pdb, "get_project", lambda _c, _t: _Project()):
        yield


def test_bound_project_yields_its_control_session():
    job = {"id": "j1", "deliver": "project:verity"}
    with _resolve_returns(_Target("20260804_103847_86ca5c")):
        assert scheduler._resolve_control_route(job) == (
            "verity",
            "20260804_103847_86ca5c",
        )


def test_unbound_project_refuses_rather_than_guessing():
    job = {"id": "j1", "deliver": "project:lido-audit"}
    with _resolve_returns(LookupError("no route")):
        assert scheduler._resolve_control_route(job) == ("", "")


def test_only_the_project_part_is_read():
    """A Telegram chat id must never land in a session-id slot."""
    job = {"id": "j1", "deliver": "telegram,project:verity"}
    with _resolve_returns(_Target("20260804_103847_86ca5c")):
        slug, session = scheduler._resolve_control_route(job)
    assert (slug, session) == ("verity", "20260804_103847_86ca5c")


def test_non_project_deliver_declares_no_control_session():
    for deliver in ("telegram", "origin", "local", "desktop:20260804_103847_86ca5c"):
        assert scheduler._resolve_control_route({"id": "j", "deliver": deliver}) == (
            "",
            "",
        )


def test_resolution_failure_is_not_fatal():
    job = {"id": "j1", "deliver": "project:verity"}
    with _resolve_returns(RuntimeError("db gone")):
        assert scheduler._resolve_control_route(job) == ("", "")


class TestAgainstTheRealRouteStore:
    """Exercise the actual projects.db / SessionDB, not a mocked resolver.

    A mock cannot show that `deliver=project:<uuid>` and
    `deliver=project:<MixedCase>` both publish the SAME canonical slug — and
    that is exactly the identity downstream grouping keys on.
    """

    @staticmethod
    def _patch_store(monkeypatch, conn):
        import contextlib

        from hermes_cli import projects_db as pdb

        @contextlib.contextmanager
        def _conn():
            yield conn

        monkeypatch.setattr(pdb, "connect_closing", _conn)

    def test_uuid_and_mixed_case_both_publish_the_canonical_slug(
        self, monkeypatch, tmp_path
    ):
        import pytest as _pytest  # noqa: F401  (fixtures come from the module)
        from hermes_cli import project_routes as routes
        from hermes_cli import projects_db as pdb
        from hermes_state import SessionDB

        conn = pdb.connect(db_path=tmp_path / "projects.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            project_id = pdb.create_project(conn, name="Verity", folders=[])
            proj = pdb.get_project(conn, project_id)
            session_db.create_session(
                session_id="20260804_103847_86ca5c", source="desktop"
            )
            routes.bind_route(
                conn, project_id, "20260804_103847_86ca5c", session_db=session_db
            )
            self._patch_store(monkeypatch, conn)
            # resolve_route_target opens the default state.db unless handed one;
            # pin it to this test's SessionDB.
            _real_resolve = routes.resolve_route_target
            monkeypatch.setattr(
                routes,
                "resolve_route_target",
                lambda c, t, **kw: _real_resolve(c, t, session_db=session_db),
            )

            for token in (project_id, proj.slug, proj.slug.upper()):
                slug, session = scheduler._resolve_control_route(
                    {"id": "j", "deliver": f"project:{token}"}
                )
                assert session == "20260804_103847_86ca5c", token
                assert slug == proj.slug, (
                    f"token {token!r} must publish the canonical slug, got {slug!r}"
                )
        finally:
            session_db.close()
            conn.close()
