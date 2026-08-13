"""Missions Board proxy routes: roster, project detail, state timeline,
lifecycle actions, grant edits, and mission steering.

These forward to sandboxed.sh through `_sandboxed_request`; the tests stub that
one seam so they exercise the route logic (shaping, validation, the writes)
without a live backend.

The plugin backend lives at ``plugins/projects-board/dashboard/plugin_api.py``,
which is not an importable package — load it by file path, exactly like the
kanban dashboard plugin test does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException


def _load_board_api() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    plugin_file = repo_root / "plugins" / "projects-board" / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("projects_board_plugin_api_under_test", plugin_file)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


board_api = _load_board_api()


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

    monkeypatch.setattr(board_api, "_sandboxed_request", fake_request)
    return calls


@pytest.mark.asyncio
async def test_list_projects_forwards_the_overview(monkeypatch: pytest.MonkeyPatch) -> None:
    overview = {"projects": [{"slug": "verity", "bucket": "active"}], "archived": []}
    calls = _stub_backend(monkeypatch, lambda m, p: overview)

    result = await board_api.list_projects()

    assert result == {"projects": [{"slug": "verity", "bucket": "active"}]}
    assert calls == [{"method": "GET", "path": "/api/projects/overview", "params": None, "body": None}]


@pytest.mark.asyncio
async def test_list_projects_tolerates_a_missing_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_backend(monkeypatch, lambda m, p: {"unexpected": True})
    assert await board_api.list_projects() == {"projects": []}


@pytest.mark.asyncio
async def test_get_project_forwards_the_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    project = {"project": {"slug": "verity", "mode": "active"}, "grant": {"merge_authority": "full"}}
    calls = _stub_backend(monkeypatch, lambda m, p: project)

    result = await board_api.get_project("verity")

    assert result["project"]["mode"] == "active"
    assert calls[0]["path"] == "/api/projects/verity"


@pytest.mark.asyncio
async def test_get_project_rejects_a_blank_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_backend(monkeypatch, lambda m, p: {})
    with pytest.raises(HTTPException) as excinfo:
        await board_api.get_project("   ")
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_state_forwards_the_limit_and_shapes_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    states = {"states": [{"signature": "abc", "headline": "CI green"}]}
    calls = _stub_backend(monkeypatch, lambda m, p: states)

    result = await board_api.get_project_state("verity", limit=5)

    assert result == {"states": [{"signature": "abc", "headline": "CI green"}]}
    assert calls == [
        {"method": "GET", "path": "/api/projects/verity/state", "params": {"limit": 5}, "body": None}
    ]


@pytest.mark.asyncio
async def test_state_tolerates_a_missing_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_backend(monkeypatch, lambda m, p: {"unexpected": True})
    assert await board_api.get_project_state("verity") == {"states": []}


@pytest.mark.asyncio
async def test_action_forwards_a_valid_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: None)

    result = await board_api.project_action("verity", {"action": "Pause"})

    assert result == {"ok": True, "slug": "verity", "action": "pause"}
    assert calls[0] == {
        "method": "POST",
        "path": "/api/projects/verity/action",
        "params": None,
        "body": {"action": "pause"},
    }


@pytest.mark.asyncio
async def test_action_accepts_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: None)

    result = await board_api.project_action("verity", {"action": "delete"})

    assert result == {"ok": True, "slug": "verity", "action": "delete"}
    assert calls[0]["body"] == {"action": "delete"}


@pytest.mark.asyncio
async def test_action_rejects_an_unknown_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: None)
    with pytest.raises(HTTPException) as excinfo:
        await board_api.project_action("verity", {"action": "detonate"})
    assert excinfo.value.status_code == 400
    assert calls == []


@pytest.mark.asyncio
async def test_grant_forwards_the_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: {"grant": {"merge_authority": "full"}})

    result = await board_api.update_grant("verity", {"merge_authority": "full", "parallel_missions": 2})

    assert result == {"grant": {"merge_authority": "full"}}
    assert calls[0] == {
        "method": "POST",
        "path": "/api/projects/verity/grant",
        "params": None,
        "body": {"merge_authority": "full", "parallel_missions": 2},
    }


@pytest.mark.asyncio
async def test_grant_rejects_an_empty_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: None)
    with pytest.raises(HTTPException) as excinfo:
        await board_api.update_grant("verity", {})
    assert excinfo.value.status_code == 400
    assert calls == []


@pytest.mark.asyncio
async def test_tasks_forwards_the_roadmap(monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = {"slug": "verity", "tasks": [{"task_key": "t1", "status": "accepted"}], "summary": {"total": 1}}
    calls = _stub_backend(monkeypatch, lambda m, p: roadmap)

    result = await board_api.get_project_tasks("verity")

    assert result["summary"]["total"] == 1
    assert calls[0]["path"] == "/api/projects/verity/tasks"


@pytest.mark.asyncio
async def test_answer_decision_marks_answered_and_injects_into_the_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def responder(method: str, path: str):
        if path.endswith("/decision/answer"):
            return {"ok": True}
        return {"conversation": {"session_id": "20260806_abc", "source": "binding"}}

    calls = _stub_backend(monkeypatch, responder)
    injected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        board_api,
        "_inject_owner_answer",
        lambda session, text: injected.append((session, text)) or True,
    )

    result = await board_api.answer_decision(
        "verity", {"at": "2026-08-13T10:00:00Z", "answer": "yes, ship", "question": "Ship v2?"}
    )

    assert result == {"ok": True, "slug": "verity", "at": "2026-08-13T10:00:00Z", "injected": True}
    assert calls[0] == {
        "method": "POST",
        "path": "/api/projects/verity/decision/answer",
        "params": None,
        "body": {"at": "2026-08-13T10:00:00Z", "answer": "yes, ship"},
    }
    assert injected == [("20260806_abc", "[Owner decision re: Ship v2?] yes, ship")]


@pytest.mark.asyncio
async def test_answer_decision_degrades_without_a_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def responder(method: str, path: str):
        if path.endswith("/decision/answer"):
            return {"ok": True}
        # Inferred conversation (latest_update) must never be injected into.
        return {"conversation": {"session_id": "cron_dead", "source": "latest_update"}}

    _stub_backend(monkeypatch, responder)
    monkeypatch.setattr(
        board_api,
        "_inject_owner_answer",
        lambda session, text: (_ for _ in ()).throw(AssertionError("must not inject")),
    )

    result = await board_api.answer_decision("verity", {"at": "t", "answer": "ok"})

    assert result["injected"] is False


@pytest.mark.asyncio
async def test_answer_decision_requires_at_and_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: None)
    with pytest.raises(HTTPException) as excinfo:
        await board_api.answer_decision("verity", {"answer": "  "})
    assert excinfo.value.status_code == 400
    assert calls == []


@pytest.mark.asyncio
async def test_steer_mission_posts_content(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_backend(monkeypatch, lambda m, p: None)

    result = await board_api.steer_mission("abc-123", {"content": "keep going"})

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
        await board_api.steer_mission("abc-123", {"content": "   "})
    assert excinfo.value.status_code == 400
