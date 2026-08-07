"""Fleet proxy routes: projects roster, one project, and mission steering.

These forward to sandboxed.sh through `_sandboxed_request`; the tests stub that
one seam so they exercise the route logic (shaping, validation, the single
write) without a live backend.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from hermes_cli.web_routers import missions as missions_mod


def _stub_backend(monkeypatch: pytest.MonkeyPatch, responder) -> list[Dict[str, Any]]:
    """Replace the sandboxed.sh seam; record every call for assertions."""
    calls: list[Dict[str, Any]] = []

    async def fake_request(
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        calls.append({"method": method, "path": path, "params": params, "body": body})
        return responder(method, path)

    monkeypatch.setattr(missions_mod, "_sandboxed_request", fake_request)
    return calls


@pytest.mark.asyncio
async def test_list_projects_forwards_the_overview(monkeypatch: pytest.MonkeyPatch) -> None:
    overview = {"projects": [{"slug": "verity", "bucket": "active"}], "archived": []}
    calls = _stub_backend(monkeypatch, lambda m, p: overview)

    result = await missions_mod.list_projects()

    assert result == {"projects": [{"slug": "verity", "bucket": "active"}]}
    assert calls == [{"method": "GET", "path": "/api/projects/overview", "params": None, "body": None}]


@pytest.mark.asyncio
async def test_list_projects_tolerates_a_missing_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_backend(monkeypatch, lambda m, p: {"unexpected": True})
    assert await missions_mod.list_projects() == {"projects": []}


@pytest.mark.asyncio
async def test_get_project_forwards_the_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    project = {"project": {"slug": "verity", "mode": "active"}, "grant": {"merge_authority": "full"}}
    calls = _stub_backend(monkeypatch, lambda m, p: project)

    result = await missions_mod.get_project("verity")

    assert result["project"]["mode"] == "active"
    assert calls[0]["path"] == "/api/projects/verity"


@pytest.mark.asyncio
async def test_get_project_rejects_a_blank_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_backend(monkeypatch, lambda m, p: {})
    with pytest.raises(HTTPException) as excinfo:
        await missions_mod.get_project("   ")
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_steer_mission_posts_content(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: None)

    result = await missions_mod.steer_mission("abc-123", {"content": "keep going"})

    assert result == {"ok": True, "mission_id": "abc-123"}
    assert calls[0] == {
        "method": "POST",
        "path": "/api/control/message",
        "params": None,
        "body": {"mission_id": "abc-123", "content": "keep going"},
    }


@pytest.mark.asyncio
async def test_steer_mission_requires_content(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_backend(monkeypatch, lambda m, p: None)
    with pytest.raises(HTTPException) as excinfo:
        await missions_mod.steer_mission("abc-123", {"content": "   "})
    assert excinfo.value.status_code == 400
