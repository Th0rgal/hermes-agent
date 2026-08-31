"""Durable explicit project-to-session routes.

A **route** binds a first-class Project (``hermes_cli/projects_db.py``) to
exactly one durable SessionDB conversation. It is the *explicit* counterpart
to the desktop's inferred cwd grouping: a route only exists because someone
deliberately bound it (``project_route_set`` tool, dashboard, or API) — it is
never derived from "whatever Desktop session happens to be open right now".

Consumers (cron ``deliver=project:<id|slug>``, notification routing) resolve
through :func:`resolve_route_target`, which is **explicit-or-nothing**: a
missing route raises :class:`LookupError` instead of falling back to the
current/most-recent Desktop session, the active project, or a home channel.

Compression/continuation: context compression ends a session and forks a
continuation child, so a stored ``session_id`` can go stale. Resolution walks
the continuation chain forward via the public
``SessionDB.resolve_resume_session_id`` API and, when the live tip differs,
repoints every route bound to the old id in a single IMMEDIATE transaction
(:func:`migrate_session_routes`) — the route follows the conversation
atomically, and readers only ever observe the old-or-new id, never a half
state.

Storage: a small additive table inside the per-profile ``projects.db`` (same
file the Project rows live in, so ``ON DELETE CASCADE`` keeps routes from
outliving their project). SessionDB internals are never touched — only the
public ``get_session`` / ``resolve_session_id`` / ``resolve_resume_session_id``
API is used.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from hermes_cli.sqlite_util import write_txn

ROUTES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS project_session_routes (
    project_id  TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL,
    bound_at    INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_session_routes_session
    ON project_session_routes(session_id);
"""

# Session sources a route may target: local GUI/API surfaces whose durable
# delivery surface is the SessionDB transcript itself. Gateway chat platforms
# (telegram/discord/...) have their own origin routing and are rejected at
# bind time so a route can never silently re-target a messaging thread.
ROUTABLE_SESSION_SOURCES = frozenset({"desktop", "webui", "api_server", "subagent"})

# Accidental SessionDB closures that must not kill a project control chat.
# Desktop websocket teardown stamps ``ws_orphan_reap`` even while cron and
# mission callbacks still have to land in that transcript (EIP-8282, 2026-08-24).
RECLAIMABLE_SESSION_END_REASONS = frozenset(
    {"ws_orphan_reap", "agent_close", "idle_timeout", "lru_evict"}
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the routes table if missing (idempotent, additive)."""
    conn.executescript(ROUTES_SCHEMA_SQL)


def _now() -> int:
    return int(time.time())


@dataclass(frozen=True)
class ProjectRoute:
    project_id: str
    session_id: str
    bound_at: int
    updated_at: int

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "bound_at": self.bound_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class RouteTarget:
    """A resolved, validated delivery target for a project route."""

    project_id: str
    session_id: str
    source: str


def _route_from_row(row: sqlite3.Row) -> ProjectRoute:
    return ProjectRoute(
        project_id=row["project_id"],
        session_id=row["session_id"],
        bound_at=int(row["bound_at"]),
        updated_at=int(row["updated_at"]),
    )


def _require_project(conn: sqlite3.Connection, project: str):
    """Validate a project token (id / slug) and return the Project row."""
    from hermes_cli import projects_db as pdb

    token = str(project or "").strip()
    if not token:
        raise LookupError("project id or slug is required")
    proj = pdb.get_project(conn, token)
    if proj is None:
        raise LookupError(f"unknown project '{token}'")
    if proj.archived:
        raise LookupError(f"project '{proj.slug}' is archived")
    return proj


class _OwnedSessionDB:
    """Open a SessionDB only when the caller did not inject one."""

    def __init__(self, session_db=None):
        self._injected = session_db
        self._owned = None

    def __enter__(self):
        if self._injected is not None:
            return self._injected
        from hermes_state import SessionDB

        self._owned = SessionDB()
        return self._owned

    def __exit__(self, *exc):
        if self._owned is not None:
            try:
                self._owned.close()
            except Exception:
                pass
        return False


def reopen_reclaimable_session(db, session_id: str, row: Optional[dict] = None) -> Optional[dict]:
    """Reopen a project-bound session ended only by a recoverable accident.

    Compression / ``session_reset`` / ``session_switch`` stay closed — those
    are real conversation boundaries. Returns the (possibly refreshed) row.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return row
    current = row if row is not None else (db.get_session(sid) if hasattr(db, "get_session") else None)
    if not current or not current.get("ended_at"):
        return current
    reason = str(current.get("end_reason") or "").strip().lower()
    if reason not in RECLAIMABLE_SESSION_END_REASONS:
        return current
    reopen = getattr(db, "reopen_session", None)
    if not callable(reopen):
        return current
    reopen(sid)
    refreshed = db.get_session(sid) if hasattr(db, "get_session") else current
    return refreshed or current


def _live_session(db, session_id: str) -> tuple[str, dict]:
    """Resolve ``session_id`` to its live continuation tip + session row.

    Uses only public SessionDB API: exact/prefix resolution, then the
    compression-continuation walk. Raises LookupError when the session (or
    its tip) does not exist — callers must surface the error, not fall back.
    A ``ws_orphan_reap`` (or similar accidental) end is reopened so cron
    delivery keeps using the operator chat instead of looking dead.
    """
    sid = str(session_id or "").strip()
    if not sid:
        raise LookupError("session_id is required")
    resolved = db.resolve_session_id(sid) or sid
    resolved = db.resolve_resume_session_id(resolved) or resolved
    delivery = getattr(db, "resolve_delivery_session_id", None)
    if callable(delivery):
        resolved = delivery(resolved) or resolved
    row = db.get_session(resolved)
    if not row:
        raise LookupError(f"session '{session_id}' not found")
    row = reopen_reclaimable_session(db, resolved, row) or row
    return resolved, row


def bind_route(
    conn: sqlite3.Connection,
    project: str,
    session_id: str,
    *,
    session_db=None,
    allow_unroutable_source: bool = False,
) -> ProjectRoute:
    """Explicitly bind ``project`` to a durable session (upsert).

    Validates both endpoints: the project must exist and be unarchived; the
    session must exist in SessionDB and have a routable local source
    (:data:`ROUTABLE_SESSION_SOURCES`). Binds the *live continuation tip*, so
    binding a compression parent lands on the session that actually receives
    messages. Raises LookupError/ValueError on validation failure.

    ``allow_unroutable_source`` overrides only the *source* check, and exists
    because the source allowlist is a heuristic, not a fact. It guards against
    routing a project at a machine-created session — there are 4364 ``webhook``
    and 5634 ``cron`` sessions on this host, nearly all throwaway — but a human
    does occasionally adopt one of those as a real working conversation, and no
    property of the row distinguishes that case (98% of webhook sessions carry
    user messages, because the payload itself is recorded as one).

    So the override is deliberately not automatic: only an operator asserting
    "this one is real" may set it. Everything else is still validated.
    """
    ensure_schema(conn)
    proj = _require_project(conn, project)
    with _OwnedSessionDB(session_db) as db:
        live_id, row = _live_session(db, session_id)
        source = str(row.get("source") or "").strip().lower()
    if source not in ROUTABLE_SESSION_SOURCES and not allow_unroutable_source:
        raise ValueError(
            f"session '{live_id}' has source '{source or 'unknown'}' — project "
            f"routes may only target {sorted(ROUTABLE_SESSION_SOURCES)} sessions "
            f"(pass allow_unroutable_source=True to override deliberately)"
        )
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO project_session_routes "
            "(project_id, session_id, bound_at, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "session_id = excluded.session_id, updated_at = excluded.updated_at",
            (proj.id, live_id, now, now),
        )
    route = get_route(conn, proj.id)
    assert route is not None
    write_sandboxed_binding(proj.slug, live_id)
    return route


def get_route(conn: sqlite3.Connection, project: str) -> Optional[ProjectRoute]:
    """Return the stored route for a project token, or None when unbound."""
    ensure_schema(conn)
    from hermes_cli import projects_db as pdb

    proj = pdb.get_project(conn, str(project or "").strip())
    if proj is None:
        return None
    row = conn.execute(
        "SELECT * FROM project_session_routes WHERE project_id = ?", (proj.id,)
    ).fetchone()
    return _route_from_row(row) if row else None


def unbind_route(conn: sqlite3.Connection, project: str) -> bool:
    """Remove a route from the authoritative roster and Hermes replica."""
    ensure_schema(conn)
    from hermes_cli import projects_db as pdb

    proj = pdb.get_project(conn, str(project or "").strip())
    if proj is None:
        return False
    if not clear_sandboxed_binding(proj.slug):
        raise RuntimeError(
            f"could not clear sandboxed.sh binding for project '{proj.slug}'"
        )
    with write_txn(conn):
        cur = conn.execute(
            "DELETE FROM project_session_routes WHERE project_id = ?", (proj.id,)
        )
    return cur.rowcount > 0


def list_routes(conn: sqlite3.Connection) -> List[ProjectRoute]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM project_session_routes ORDER BY project_id ASC"
    ).fetchall()
    return [_route_from_row(r) for r in rows]


def _sandboxed_projects_db() -> Optional[Path]:
    env = (os.environ.get("SANDBOXED_PROJECTS_DB") or "").strip()
    if env:
        path = Path(env)
        if path.is_file():
            return path
    for candidate in (Path("/root/.sandboxed-sh/projects.db"),):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def lookup_sandboxed_binding(slug: str) -> Optional[str]:
    """Roster bind for ``slug`` (and routes.json nicknames). None if unavailable."""
    token = str(slug or "").strip()
    if not token:
        return None
    path = _sandboxed_projects_db()
    if path is None:
        return None
    keys = [token]
    try:
        from hermes_cli.projects_db import _slug_lookup_keys

        for key in _slug_lookup_keys(token):
            if key not in keys:
                keys.append(key)
    except Exception:
        pass
    try:
        sdb = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        try:
            for key in keys:
                row = sdb.execute(
                    "SELECT control_session_id FROM project_bindings WHERE slug = ?",
                    (key,),
                ).fetchone()
                if row and str(row[0] or "").strip():
                    return str(row[0]).strip()
        except sqlite3.Error:
            return None
    finally:
        sdb.close()
    return None


def clear_sandboxed_binding(canonical: str) -> bool:
    """Clear the roster's authoritative bind before deleting its replica.

    A missing database or pre-binding schema has nothing to clear. Other
    write failures are reported so callers do not claim an unbind that the
    next reconciliation would immediately undo.
    """
    slug = str(canonical or "").strip()
    if not slug:
        return True
    path = _sandboxed_projects_db()
    if path is None:
        return True
    try:
        sdb = sqlite3.connect(str(path))
        try:
            sdb.execute("DELETE FROM project_bindings WHERE slug = ?", (slug,))
            sdb.commit()
        finally:
            sdb.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return True
        return False
    except sqlite3.Error:
        return False
    return True


def write_sandboxed_binding(canonical: str, session_id: str) -> None:
    """Best-effort replica of a Hermes bind into the roster store."""
    slug = str(canonical or "").strip()
    sid = str(session_id or "").strip()
    if not slug or not sid:
        return
    path = _sandboxed_projects_db()
    if path is None:
        return
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    try:
        sdb = sqlite3.connect(str(path))
        try:
            sdb.execute(
                "INSERT INTO project_bindings (slug, control_session_id, bound_at, bound_by) "
                "VALUES (?, ?, ?, 'hermes') "
                "ON CONFLICT(slug) DO UPDATE SET "
                "control_session_id = excluded.control_session_id, "
                "bound_at = excluded.bound_at, "
                "bound_by = excluded.bound_by",
                (slug, sid, now),
            )
            sdb.commit()
        finally:
            sdb.close()
    except sqlite3.Error:
        return


def _repair_route_from_sandboxed(conn: sqlite3.Connection, proj, *, session_db=None) -> Optional[ProjectRoute]:
    """If the roster has a bind and Hermes does not, copy it in. Never invent."""
    sid = lookup_sandboxed_binding(proj.slug)
    if not sid:
        return None
    try:
        return bind_route(conn, proj.id, sid, session_db=session_db)
    except (LookupError, ValueError):
        return None


def session_is_project_control(session_id: str, *, session_db=None) -> bool:
    """True when ``session_id`` is (or is a continuation of) a bound control chat."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    from hermes_cli import projects_db as pdb

    chain = {sid}
    with _OwnedSessionDB(session_db) as db:
        current = sid
        for _ in range(32):
            row = db.get_session(current) if hasattr(db, "get_session") else None
            if not row:
                break
            parent = str(row.get("parent_session_id") or "").strip()
            if not parent or parent in chain:
                break
            chain.add(parent)
            current = parent
        try:
            live = _live_session(db, sid)[0]
            chain.add(live)
        except LookupError:
            pass
    try:
        with pdb.connect_closing() as conn:
            ensure_schema(conn)
            rows = conn.execute("SELECT session_id FROM project_session_routes").fetchall()
    except Exception:
        return False
    bound = {str(r[0] if not hasattr(r, "keys") else r["session_id"]) for r in rows}
    return bool(chain & bound)


def migrate_session_routes(
    conn: sqlite3.Connection,
    old_session_id: str,
    new_session_id: str,
) -> int:
    """Atomically repoint every route bound to ``old_session_id``.

    One IMMEDIATE transaction covers all affected routes, so concurrent
    readers observe either the pre-migration or post-migration binding —
    never a mix. Called when compression/continuation retires a session id
    in favour of its continuation child. Returns the number of routes moved.
    """
    old = str(old_session_id or "").strip()
    new = str(new_session_id or "").strip()
    if not old or not new or old == new:
        return 0
    ensure_schema(conn)
    with write_txn(conn):
        cur = conn.execute(
            "UPDATE project_session_routes "
            "SET session_id = ?, updated_at = ? "
            "WHERE session_id = ?",
            (new, _now(), old),
        )
    return cur.rowcount


def resolve_route_target(
    conn: sqlite3.Connection,
    project: str,
    *,
    session_db=None,
) -> RouteTarget:
    """Resolve a project token to its explicitly routed live session.

    Explicit-or-nothing: raises LookupError when the project is unknown,
    archived, has no bound route, or the routed session no longer exists.
    Callers must treat that as a delivery error — never substitute the
    currently open Desktop session or any other inferred target.

    When compression/continuation moved the conversation to a child session,
    the stored route is migrated to the live tip atomically before returning,
    so the durable binding follows the conversation.
    """
    ensure_schema(conn)
    proj = _require_project(conn, project)
    row = conn.execute(
        "SELECT * FROM project_session_routes WHERE project_id = ?", (proj.id,)
    ).fetchone()
    if row is None:
        repaired = _repair_route_from_sandboxed(conn, proj, session_db=session_db)
        if repaired is None:
            raise LookupError(
                f"project '{proj.slug}' has no explicit session route; bind one "
                f"with project_route_set (explicit routes are required — the "
                f"current Desktop session is never used as a fallback)"
            )
        row = conn.execute(
            "SELECT * FROM project_session_routes WHERE project_id = ?", (proj.id,)
        ).fetchone()
        if row is None:
            raise LookupError(
                f"project '{proj.slug}' has no explicit session route; bind one "
                f"with project_route_set (explicit routes are required — the "
                f"current Desktop session is never used as a fallback)"
            )
    route = _route_from_row(row)
    with _OwnedSessionDB(session_db) as db:
        live_id, session_row = _live_session(db, route.session_id)
        source = str(session_row.get("source") or "").strip().lower()
    if live_id != route.session_id:
        migrate_session_routes(conn, route.session_id, live_id)
    return RouteTarget(project_id=proj.id, session_id=live_id, source=source)
