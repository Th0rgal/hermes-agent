"""Fleet dashboard plugin — backend API routes.

Mounted at /api/plugins/fleet/ by the dashboard plugin system.

Intentionally thin: every handler forwards to sandboxed.sh through the gateway's
credential-free relay (``hermes_cli.web_routers.missions._sandboxed_request``),
so the desktop stays credential-free and there is exactly one place that mints a
token for sandboxed.sh. The plugin's REST door is namespace-scoped to
``/api/plugins/fleet`` by construction, so these paths are relative.

Read-only except for one deliberate write — steering a running mission — which
is the whole point of a background-agents surface.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException

from hermes_cli.web_routers.missions import _sandboxed_request

router = APIRouter()


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
    slug = (slug or "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")
    body = await _sandboxed_request("GET", f"/api/projects/{slug}")
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502, detail="sandboxed.sh returned an unexpected project shape"
        )
    return body


@router.post("/missions/{mission_id}/message")
async def steer_mission(
    mission_id: str, payload: Dict[str, Any] = Body(default_factory=dict)
) -> Dict[str, Any]:
    """Send a steering message to a running mission — nudge a background agent
    inline. The only write this surface makes.
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
