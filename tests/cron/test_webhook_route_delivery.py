"""A project route explicitly bound to a webhook-sourced session (via
allow_unroutable_source) must still deliver — the resolver used to drop every
delivery whose session source wasn't desktop/webui, which made active projects
look dead in the app.
"""

import contextlib

from cron import scheduler
from hermes_cli import project_routes as routes


def _patch_route(monkeypatch, source: str, session_id: str = "20260810_183941_2a3bd4"):
    @contextlib.contextmanager
    def _fake_connect():
        yield object()

    monkeypatch.setattr(scheduler, "_resolve_project_route_target", scheduler._resolve_project_route_target)
    # Patch the lazily-imported helpers used inside the resolver.
    import hermes_cli.projects_db as pdb

    monkeypatch.setattr(pdb, "connect_closing", _fake_connect, raising=False)
    monkeypatch.setattr(
        routes,
        "resolve_route_target",
        lambda conn, token: routes.RouteTarget(project_id="p_x", session_id=session_id, source=source),
    )


def test_webhook_source_delivers_as_local_session(monkeypatch):
    _patch_route(monkeypatch, "webhook")
    target = scheduler._resolve_project_route_target({"id": "j1"}, "lean-silicon")
    assert target is not None
    assert target["kind"] == scheduler._LOCAL_SESSION_TARGET_KIND
    assert target["session_id"] == "20260810_183941_2a3bd4"


def test_desktop_source_still_delivers(monkeypatch):
    _patch_route(monkeypatch, "desktop", session_id="20260812_133335_64bf17")
    target = scheduler._resolve_project_route_target({"id": "j2"}, "coldcard")
    assert target is not None
    assert target["kind"] == scheduler._LOCAL_SESSION_TARGET_KIND


def test_unknown_source_still_dropped(monkeypatch):
    # A source that backs no appendable local session is still dropped.
    _patch_route(monkeypatch, "telegram")
    target = scheduler._resolve_project_route_target({"id": "j3"}, "some-project")
    assert target is None
