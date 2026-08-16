"""Missions Board dashboard plugin — backend API routes.

Mounted at /api/plugins/projects-board/ by the dashboard plugin system.

Intentionally thin: every handler forwards to sandboxed.sh through the gateway's
credential-free relay (``hermes_cli.web_routers.missions._sandboxed_request``),
so the desktop stays credential-free and there is exactly one place that mints a
token for sandboxed.sh. The plugin's REST door is namespace-scoped to
``/api/plugins/projects-board`` by construction, so these paths are relative.

Reads: the project roster (board overview), one project's detail, and a
project's state timeline. Writes: steering a running mission, a project
lifecycle action (pause/resume/archive/unarchive — the board's column drags),
and the autonomy-grant editor.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect, status as http_status
from starlette.concurrency import run_in_threadpool

from hermes_cli.web_deps import late
from plugins.projects.dashboard.plugin_api import _mint_token, _sandboxed_config, _sandboxed_request

_open_session_db_for_profile = late("_open_session_db_for_profile")

_log = logging.getLogger("hermes_cli.web_server")

router = APIRouter()

# Mission events that should refresh the board surface. The stream carries more
# (text deltas, tool calls) but the surface only needs to know a mission's
# lifecycle moved — the refetch pulls the rest.
_REFRESH_EVENTS = frozenset(
    {
        "mission_status_changed",
        "mission_title_changed",
        "mission_metadata_updated",
        "mission_settings_updated",
    }
)

# The full set of lifecycle actions sandboxed.sh accepts on a project. Column
# drags map source→target buckets onto exactly these.
_PROJECT_ACTIONS = frozenset({"pause", "resume", "archive", "unarchive", "delete"})


def _ws_upgrade_authorized(ws: WebSocket) -> bool:
    """Delegate to the dashboard's canonical WS auth gate, exactly like the
    kanban plugin: browsers can't set Authorization on a WS upgrade, so the
    credential rides in the query string and the shared gate validates it in
    every mode (loopback token / OAuth ticket / server-internal). Imported
    lazily so the plugin still loads in the bare-FastAPI test harness, where we
    accept so the relay stays testable.
    """
    try:
        from hermes_cli import web_server as _ws
    except Exception:
        return True
    return bool(_ws._ws_auth_ok(ws))


def _clean_slug(slug: str) -> str:
    slug = (slug or "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")
    return slug


async def publish_bound_session_goal(session_id: str, goal: str) -> bool:
    """Write a Hermes /goal onto the bound project's next_action.

    Returns False when the session is not a project conversation or the
    backend write fails — callers must not treat that as a goal-set error.
    """
    sid = (session_id or "").strip()
    text = (goal or "").strip()
    if not sid or not text:
        return False
    try:
        resolved = await _sandboxed_request("GET", f"/api/projects/by-session/{sid}")
        slug = resolved.get("slug") if isinstance(resolved, dict) else None
        if not slug:
            return False
        detail = await _sandboxed_request("GET", f"/api/projects/{slug}")
        project = detail.get("project") if isinstance(detail, dict) else None
        current = (project or {}).get("mode") if isinstance(project, dict) else None
        mode = current if current in {"active", "blocked", "paused"} else "active"
        await _sandboxed_request(
            "POST",
            f"/api/projects/{slug}/status",
            body={"mode": mode, "next_action": text[:500]},
        )
        return True
    except Exception:
        _log.debug("publish_bound_session_goal failed", exc_info=True)
        return False


@router.get("/projects")
async def list_projects() -> Dict[str, Any]:
    """The project roster: one entry per project with mode, health, and mission
    chips, from the backend board overview.
    """
    body = await _sandboxed_request("GET", "/api/projects/overview")
    projects: Optional[List[Dict[str, Any]]] = (
        body.get("projects") if isinstance(body, dict) else None
    )
    if not isinstance(projects, list):
        projects = []
    return {"projects": projects}


@router.get("/projects/{slug}")
async def get_project(slug: str) -> Dict[str, Any]:
    """One project's structured object: record (mode/blocker), autonomy grant,
    tracks, open decisions, and its bound control conversation.
    """
    slug = _clean_slug(slug)
    body = await _sandboxed_request("GET", f"/api/projects/{slug}")
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502, detail="sandboxed.sh returned an unexpected project shape"
        )
    return body


@router.get("/projects/{slug}/state")
async def get_project_state(slug: str, limit: int = 20) -> Dict[str, Any]:
    """The project's state timeline (newest-first signatures with headlines and
    observation counts), for the drawer.
    """
    slug = _clean_slug(slug)
    body = await _sandboxed_request(
        "GET", f"/api/projects/{slug}/state", params={"limit": limit}
    )
    states = body.get("states") if isinstance(body, dict) else None
    if not isinstance(states, list):
        states = []
    return {"states": states}


@router.post("/projects/{slug}/action")
async def project_action(
    slug: str, payload: Dict[str, Any] = Body(default_factory=dict)
) -> Dict[str, Any]:
    """A project lifecycle action — what a board column drag means. Validated
    against the closed action set so a renderer bug can't smuggle an arbitrary
    verb to the backend.
    """
    slug = _clean_slug(slug)
    action = str(payload.get("action") or "").strip().lower()
    if action not in _PROJECT_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of {sorted(_PROJECT_ACTIONS)}",
        )
    await _sandboxed_request(
        "POST", f"/api/projects/{slug}/action", body={"action": action}
    )
    return {"ok": True, "slug": slug, "action": action}


@router.post("/projects/{slug}/rename")
async def rename_project(
    slug: str, payload: Dict[str, Any] = Body(default_factory=dict)
) -> Dict[str, Any]:
    """Rename a project (its display title). Relays to the sandboxed.sh
    upsert, which COALESCEs the title — passing only slug+title leaves the
    project's objective/repository/controller binding untouched.
    """
    slug = _clean_slug(slug)
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title must be a non-empty string")
    # NB: no trailing slash — sandboxed.sh serves the upsert at
    # `PUT /api/projects` (a trailing-slash `/api/projects/` 404s), so the rename
    # relay must match it exactly or the desktop sees a 502.
    await _sandboxed_request(
        "PUT", "/api/projects", body={"slug": slug, "title": title}
    )
    return {"ok": True, "slug": slug, "title": title}


@router.post("/projects/{slug}/grant")
async def update_grant(
    slug: str, payload: Dict[str, Any] = Body(default_factory=dict)
) -> Dict[str, Any]:
    """Edit the project's autonomy grant (merge authority, budget per tick,
    parallel missions, …). Forwarded as-is; sandboxed.sh owns the field
    semantics and returns the updated grant.
    """
    slug = _clean_slug(slug)
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(status_code=400, detail="a non-empty grant patch is required")
    body = await _sandboxed_request("POST", f"/api/projects/{slug}/grant", body=payload)
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502, detail="sandboxed.sh returned an unexpected grant shape"
        )
    return body


@router.post("/projects/{slug}/status")
async def set_project_status(
    slug: str, payload: Dict[str, Any] = Body(default_factory=dict)
) -> Dict[str, Any]:
    """Write the controller/session objective onto the project row.

    A bound-session ``/goal`` is the project's live next_action. We keep the
    current mode (blocked stays blocked) so a Ralph loop cannot launder a
    stall into ``active``.
    """
    slug = _clean_slug(slug)
    next_action = str(payload.get("next_action") or "").strip()
    if not next_action:
        raise HTTPException(status_code=400, detail="next_action is required")
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in {"active", "blocked", "paused"}:
        detail = await _sandboxed_request("GET", f"/api/projects/{slug}")
        project = detail.get("project") if isinstance(detail, dict) else None
        current = (project or {}).get("mode") if isinstance(project, dict) else None
        mode = current if current in {"active", "blocked", "paused"} else "active"
    body = await _sandboxed_request(
        "POST",
        f"/api/projects/{slug}/status",
        body={"mode": mode, "next_action": next_action[:500]},
    )
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502, detail="sandboxed.sh returned an unexpected status shape"
        )
    return body


@router.get("/projects/{slug}/tasks")
async def get_project_tasks(slug: str) -> Dict[str, Any]:
    """The project's roadmap: board tasks aggregated across its boss missions
    (status, dependencies, result digest, PR link, worker mission), plus a
    done/running/failed summary — the drawer's checklist source.
    """
    slug = _clean_slug(slug)
    body = await _sandboxed_request("GET", f"/api/projects/{slug}/tasks")
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502, detail="sandboxed.sh returned an unexpected tasks shape"
        )
    return body


@router.get("/sessions/{session_id}/resolve")
async def resolve_session(session_id: str) -> Dict[str, Any]:
    """Follow continuation/resume pointers to the LIVE session id.

    Project bindings store a session id frozen at bind time; Hermes
    compressions move the conversation to new ids. This is the desktop's
    canonicalization door — read-only, local SessionDB, no sandboxed.sh call.
    404 = the id resolves to nothing (deleted lineage), which callers treat
    as "keep the stored id".
    """

    def _read() -> Optional[str]:
        db = _open_session_db_for_profile(None, read_only=True)
        try:
            sid = db.resolve_session_id(session_id)
            if not sid:
                return None
            return db.resolve_resume_session_id(sid) or sid
        finally:
            db.close()

    live = await run_in_threadpool(_read)
    if not live:
        raise HTTPException(status_code=404, detail="unknown session")
    return {"session_id": session_id, "live_session_id": live}


@router.put("/projects/{slug}/conversation")
async def rebind_conversation(
    slug: str, payload: Dict[str, Any] = Body(default_factory=dict)
) -> Dict[str, Any]:
    """Re-point the project's bound conversation (the self-heal write).

    The desktop calls this when the stored binding resolved to a newer
    continuation, so every consumer of the binding — selection, dedup,
    Hermes-side mission-callback routing — follows the live conversation.
    """
    slug = _clean_slug(slug)
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    await _sandboxed_request(
        "PUT", f"/api/projects/{slug}/conversation", body={"session_id": session_id}
    )
    return {"ok": True, "slug": slug, "session_id": session_id}


def _inject_owner_answer(session_key: str, text: str) -> bool:
    """Deliver the owner's answer into the bound Hermes session, in-process.

    The dashboard runs the tui_gateway dispatcher in this very process
    (`/api/ws` → `tui_gateway.ws.handle_ws` → `server.dispatch`), so no WS
    client or token is needed. `queued=True` makes the note the session's NEXT
    turn instead of hijacking an in-flight one (server-side queue semantics —
    prompt.submit is never rejected). Only live sessions are addressed: waking
    a cold session from here would need a capture transport for the LONG
    `session.resume` handler; the ledger keeps the answer either way, so we
    degrade to injected=false rather than half-resume.
    """
    try:
        from tui_gateway import server as gw
    except Exception:
        return False
    try:
        hit = gw._find_live_session_by_key(session_key)
        if hit is None:
            return False
        sid, session = hit
        response = gw.dispatch(
            {
                "id": f"projects-board-{uuid.uuid4().hex[:8]}",
                "method": "prompt.submit",
                "params": {"session_id": sid, "text": text, "queued": True},
            },
            transport=session.get("transport"),
        )
        return isinstance(response, dict) and "error" not in response
    except Exception as error:  # pragma: no cover - defensive
        _log.warning("projects-board: owner-answer injection failed: %s", error)
        return False


@router.post("/projects/{slug}/decisions/answer")
async def answer_decision(
    slug: str, payload: Dict[str, Any] = Body(default_factory=dict)
) -> Dict[str, Any]:
    """Answer a pending owner decision from the project card.

    Two effects: (1) the ledger row flips to answered in sandboxed.sh —
    authoritative; (2) best-effort, the answer is queued into the bound
    control conversation so the controller acts on it next turn without
    waiting for its own poll.
    """
    slug = _clean_slug(slug)
    at = str(payload.get("at") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if not at or not answer:
        raise HTTPException(status_code=400, detail="at and answer are required")
    question = str(payload.get("question") or "").strip()

    await _sandboxed_request(
        "POST", f"/api/projects/{slug}/decision/answer", body={"at": at, "answer": answer}
    )

    # The ledger flip above is the authoritative effect. Everything below is
    # best-effort delivery: a transient failure here must not surface as an
    # error, or the renderer would prompt the user to re-answer a decision
    # that is already answered.
    injected = False
    try:
        detail = await _sandboxed_request("GET", f"/api/projects/{slug}")
    except Exception as error:
        _log.warning("projects-board: post-answer lookup failed for %s: %s", slug, error)
        return {"ok": True, "slug": slug, "at": at, "injected": False}
    conversation = detail.get("conversation") if isinstance(detail, dict) else None
    if isinstance(conversation, dict) and conversation.get("source") == "binding":
        session_key = str(conversation.get("session_id") or "")
        if session_key:
            excerpt = (question[:120] + "…") if len(question) > 120 else question
            prefix = f"[Owner decision re: {excerpt}] " if excerpt else "[Owner decision] "
            injected = await run_in_threadpool(
                _inject_owner_answer, session_key, prefix + answer
            )

    return {"ok": True, "slug": slug, "at": at, "injected": injected}


@router.post("/missions/{mission_id}/message")
async def steer_mission(
    mission_id: str, payload: Dict[str, Any] = Body(default_factory=dict)
) -> Dict[str, Any]:
    """Send a steering message to a running mission — nudge a background agent
    inline from a card's mission row.
    """
    mission_id = (mission_id or "").strip()
    if not mission_id:
        raise HTTPException(status_code=400, detail="mission_id is required")
    content = (payload.get("content") or payload.get("message") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    await _sandboxed_request(
        "POST",
        "/api/control/message",
        body={"mission_id": mission_id, "content": content},
    )
    return {"ok": True, "mission_id": mission_id}


@router.websocket("/events")
async def stream_events(ws: WebSocket) -> None:
    """Relay sandboxed.sh mission lifecycle events to the plugin so the surface
    updates on push, not just the poll. We open the backend SSE stream
    (`/api/control/stream`) with a minted token — the credential stays in the
    gateway, never the renderer — and forward a compact `{invalidate,
    mission_id}` frame whenever a mission's lifecycle moves. The client turns
    that into a targeted React Query invalidation.

    The poll remains the fallback: if this stream drops or the backend is
    absent, the surface still refreshes on its interval.
    """
    if not _ws_upgrade_authorized(ws):
        await ws.close(code=http_status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()

    try:
        base_url, secret, subject = _sandboxed_config()
    except HTTPException:
        # No sandboxed.sh here — nothing to relay. Close cleanly; the client
        # falls back to polling (which will itself 503 and hide the surface).
        await ws.close(code=http_status.WS_1000_NORMAL_CLOSURE)
        return

    token = _mint_token(secret, subject)
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET",
                f"{base_url}/api/control/stream",
                headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 400:
                    await ws.close(code=http_status.WS_1011_INTERNAL_ERROR)
                    return
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") in _REFRESH_EVENTS:
                        await ws.send_json(
                            {"type": "invalidate", "mission_id": event.get("mission_id")}
                        )
    except WebSocketDisconnect:
        return
    except httpx.HTTPError as error:
        _log.warning("projects-board event relay lost the sandboxed.sh stream: %s", error)
    finally:
        try:
            await ws.close()
        except Exception:
            pass
