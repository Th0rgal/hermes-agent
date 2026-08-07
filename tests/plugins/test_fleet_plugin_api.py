"""Fleet proxy routes: projects roster, one project, and mission steering.

These forward to sandboxed.sh through `_sandboxed_request`; the tests stub that
one seam so they exercise the route logic (shaping, validation, the single
write) without a live backend.

The plugin backend lives at ``plugins/fleet/dashboard/plugin_api.py``, which is
not an importable package — load it by file path, exactly like the kanban
dashboard plugin test does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException


def _load_fleet_api() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    plugin_file = repo_root / "plugins" / "fleet" / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("fleet_plugin_api_under_test", plugin_file)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fleet_api = _load_fleet_api()


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

    monkeypatch.setattr(fleet_api, "_sandboxed_request", fake_request)
    return calls


@pytest.mark.asyncio
async def test_list_projects_forwards_the_overview(monkeypatch: pytest.MonkeyPatch) -> None:
    overview = {"projects": [{"slug": "verity", "bucket": "active"}], "archived": []}
    calls = _stub_backend(monkeypatch, lambda m, p: overview)

    result = await fleet_api.list_projects()

    assert result == {"projects": [{"slug": "verity", "bucket": "active"}]}
    assert calls == [{"method": "GET", "path": "/api/projects/overview", "params": None, "body": None}]


@pytest.mark.asyncio
async def test_list_projects_tolerates_a_missing_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_backend(monkeypatch, lambda m, p: {"unexpected": True})
    assert await fleet_api.list_projects() == {"projects": []}


@pytest.mark.asyncio
async def test_get_project_forwards_the_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    project = {"project": {"slug": "verity", "mode": "active"}, "grant": {"merge_authority": "full"}}
    calls = _stub_backend(monkeypatch, lambda m, p: project)

    result = await fleet_api.get_project("verity")

    assert result["project"]["mode"] == "active"
    assert calls[0]["path"] == "/api/projects/verity"


@pytest.mark.asyncio
async def test_get_project_rejects_a_blank_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_backend(monkeypatch, lambda m, p: {})
    with pytest.raises(HTTPException) as excinfo:
        await fleet_api.get_project("   ")
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_steer_mission_posts_content(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: None)

    result = await fleet_api.steer_mission("abc-123", {"content": "keep going"})

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
        await fleet_api.steer_mission("abc-123", {"content": "   "})
    assert excinfo.value.status_code == 400
