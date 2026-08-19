#!/usr/bin/env python3
"""Project tools — the agent's INTENTIONAL handle on first-class Projects.

Projects (per-profile ``projects.db``) are the named workspaces the desktop
sidebar groups sessions into. Creating / switching a project is a deliberate act
expressed as explicit tools — never a side effect of a terminal ``cd``.

Exposed only on GUI sessions: the tools live in the `project` toolset (kept off
``_HERMES_CORE_TOOLS``) which the desktop/TUI gateway folds into its resolved
toolsets, so no CLI/messaging/cron schema carries them. The GUI also wires
``set_project_workspace_callback`` so a create/switch re-anchors the live
session's cwd and the sidebar follows the move; the DB write is the durable part.
"""

import json
import os
from typing import Callable, Optional


# Set by the GUI gateway (tui_gateway) at session wiring. Receives
# ``(task_id, primary_path, project_name)`` and re-anchors that session's
# workspace + refreshes the sidebar. ``None`` in CLI / messaging contexts — the
# DB write still happens; there's just no live GUI session to move.
_workspace_callback: Optional[Callable[[str, str, str], None]] = None


def set_project_workspace_callback(fn: Optional[Callable[[str, str, str], None]]) -> None:
    global _workspace_callback
    _workspace_callback = fn


def _primary_path(proj) -> Optional[str]:
    if getattr(proj, "primary_path", None):
        return proj.primary_path
    for folder in proj.folders:
        if folder.is_primary:
            return folder.path
    return proj.folders[0].path if proj.folders else None


def _apply_workspace(task_id: Optional[str], path: Optional[str], name: str) -> None:
    cb = _workspace_callback
    if cb and task_id and path:
        try:
            cb(task_id, path, name)
        except Exception:
            pass


def _resolve(conn, token: str):
    from hermes_cli import projects_db as pdb

    token = (token or "").strip()
    if not token:
        return None
    projects = pdb.list_projects(conn, include_archived=True)
    # Exact id / slug / name first, then case-insensitive slug / name.
    for proj in projects:
        if token in (proj.id, proj.slug) or proj.name == token:
            return proj
    low = token.lower()
    for proj in projects:
        if proj.slug.lower() == low or proj.name.lower() == low:
            return proj
    return None


def project_list(task_id: Optional[str] = None) -> str:
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing() as conn:
        active = pdb.get_active_id(conn)
        projects = pdb.list_projects(conn)

    return json.dumps({
        "active_id": active,
        "projects": [
            {
                "id": p.id,
                "slug": p.slug,
                "name": p.name,
                "primary_path": _primary_path(p),
                "active": p.id == active,
            }
            for p in projects
        ],
    })


def project_create(name: str, path: Optional[str] = None, task_id: Optional[str] = None) -> str:
    name = (name or "").strip()
    if not name:
        return json.dumps({"success": False, "error": "name is required"})

    from hermes_cli import projects_db as pdb

    folder = (path or "").strip()
    if folder:
        folder = os.path.abspath(os.path.expanduser(folder))

    try:
        with pdb.connect_closing() as conn:
            existing = pdb.find_by_primary_path(conn, folder) if folder else None
            if existing is not None:
                # Idempotent create: the folder already belongs to a project.
                # Re-activating it beats minting a duplicate — duplicated
                # projects render N identical sidebar subtrees (#75820).
                pdb.set_active(conn, existing.id)
                proj = existing
            else:
                pid = pdb.create_project(conn, name=name, folders=[folder] if folder else [], primary_path=folder or None)
                pdb.set_active(conn, pid)
                proj = pdb.get_project(conn, pid)
    except ValueError as exc:
        return json.dumps({"success": False, "error": str(exc)})

    if proj is None:
        return json.dumps({"success": False, "error": "project vanished after create"})

    primary = _primary_path(proj)
    _apply_workspace(task_id, primary, proj.name)

    return json.dumps({"success": True, "id": proj.id, "slug": proj.slug, "name": proj.name, "primary_path": primary})


def project_switch(project: str, task_id: Optional[str] = None) -> str:
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing() as conn:
        proj = _resolve(conn, project)
        if proj is None:
            return json.dumps({"success": False, "error": f"no project matching '{project}'"})
        pdb.set_active(conn, proj.id)

    primary = _primary_path(proj)
    _apply_workspace(task_id, primary, proj.name)

    return json.dumps({"success": True, "id": proj.id, "slug": proj.slug, "name": proj.name, "primary_path": primary})


def _caller_durable_session_id() -> str:
    """The caller's durable SessionDB id, or "" when there is none.

    Explicit context only (ContextVar set by the gateway, or the
    HERMES_SESSION_ID the agent subprocess inherits). Deliberately does NOT
    scan SessionDB for the newest Desktop session — a route bind must name a
    real, intentional target, never "whatever window is open".
    """
    sid = ""
    try:
        from gateway.session_context import get_session_env

        sid = get_session_env("HERMES_SESSION_ID", "")
    except Exception:
        sid = ""
    return (sid or os.environ.get("HERMES_SESSION_ID", "")).strip()


def project_route_set(project: str, session_id: Optional[str] = None, task_id: Optional[str] = None) -> str:
    from hermes_cli import project_routes as routes
    from hermes_cli import projects_db as pdb

    sid = (session_id or "").strip() or _caller_durable_session_id()
    if not sid:
        return json.dumps({
            "success": False,
            "error": (
                "no session_id given and no durable session in context — "
                "explicit routes are never inferred from the current desktop"
            ),
        })
    try:
        with pdb.connect_closing() as conn:
            proj = _resolve(conn, project)
            if proj is None:
                return json.dumps({"success": False, "error": f"no project matching '{project}'"})
            route = routes.bind_route(conn, proj.id, sid)
    except (LookupError, ValueError) as exc:
        return json.dumps({"success": False, "error": str(exc)})
    return json.dumps({
        "success": True,
        "project": proj.slug,
        "session_id": route.session_id,
    })


def project_route_get(project: str, task_id: Optional[str] = None) -> str:
    from hermes_cli import project_routes as routes
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing() as conn:
        proj = _resolve(conn, project)
        if proj is None:
            return json.dumps({"success": False, "error": f"no project matching '{project}'"})
        route = routes.get_route(conn, proj.id)
    if route is None:
        return json.dumps({
            "success": True,
            "project": proj.slug,
            "route": None,
        })
    return json.dumps({
        "success": True,
        "project": proj.slug,
        "route": route.to_dict(),
    })


def project_route_clear(project: str, task_id: Optional[str] = None) -> str:
    from hermes_cli import project_routes as routes
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing() as conn:
        proj = _resolve(conn, project)
        if proj is None:
            return json.dumps({"success": False, "error": f"no project matching '{project}'"})
        removed = routes.unbind_route(conn, proj.id)
    return json.dumps({"success": True, "project": proj.slug, "removed": removed})
