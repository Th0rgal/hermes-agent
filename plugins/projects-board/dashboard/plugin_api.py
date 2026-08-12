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
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect, status as http_status

from plugins.projects.dashboard.plugin_api import _mint_token, _sandboxed_config, _sandboxed_request

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
_PROJECT_ACTIONS = frozenset({"pause", "resume", "archive", "unarchive"})


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
