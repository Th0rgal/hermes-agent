"""Dispatch a delegation to a sandboxed.sh *mission* (``backend="mission"``).

This module owns only the OUTBOUND concern: turn a ``delegate_task`` request into
a ``start_mission`` MCP call on the ``sandboxed_assistant`` server and register a
durable pending slot in the async-delegation ledger. The INBOUND concern —
folding the mission's terminal webhook back into the delegating turn as an
``async_delegation_complete`` row — lives in ``gateway/platforms/webhook.py`` and
reuses the very same ledger (see ``tools/async_delegation.py`` and the plan
``transient-rolling-volcano.md``).

Design: a mission-backed delegation has NO local runner/thread. We register the
pending ledger slot FIRST (so a racing completion is resolvable), then POST the
mission and bind the returned mission id onto the slot. The slot is the
authentication anchor: only a webhook whose ``mission_id`` resolves to a
Hermes-created pending row is folded in, and parent/origin routing is read from
the row, never from the (untrusted) webhook payload.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The sandboxed.sh MCP server + tool that create+start a mission (server-fixes
# origin="hermes"; we pass origin_session_id so the completion routes home).
_MCP_SERVER_NAME = "sandboxed_assistant"
_START_MISSION_TOOL = "start_mission"
_START_MISSION_TIMEOUT = 60.0


def _build_mission_prompt(goal: str, context: Optional[str], role: Optional[str]) -> str:
    """Frame the delegated task for a mission harness.

    A mission runs its own harness with its own tools, so there is no toolset
    inheritance to carry — only the goal plus optional parent context and a
    leaf-only directive (a delegated mission should do the task, not spawn its
    own fan-out)."""
    parts = [
        "You are a delegated worker. Complete the task below and report a "
        "concise result. Do not spawn further sub-delegations.",
    ]
    if role:
        parts.append(f"Role: {role}")
    if context:
        parts.append(f"Context from the delegating agent:\n{context}")
    parts.append(f"Task:\n{goal}")
    return "\n\n".join(parts)


def _extract_mission_id(result_str: str) -> Optional[str]:
    """Pull the mission id out of the ``start_mission`` tool result.

    The MCP tool returns a JSON blob; tolerate a few shapes: a flat
    ``{"mission_id"/"id": ...}``, a nested ``{"mission": {"id": ...}}``, or a
    string-wrapped ``{"result": "<json>"}`` (the MCP text-content convention).
    """
    if not result_str:
        return None
    try:
        data = json.loads(result_str)
    except Exception:
        return None
    if isinstance(data, dict):
        for key in ("mission_id", "id", "missionId"):
            val = data.get(key)
            if val:
                return str(val)
        mission = data.get("mission")
        if isinstance(mission, dict) and mission.get("id"):
            return str(mission["id"])
        inner = data.get("result")
        if isinstance(inner, str):
            return _extract_mission_id(inner)
    return None


_SEND_MESSAGE_TOOL = "send_message_to_mission"
_ANSWER_QUESTION_TOOL = "answer_mission_question"


def steer_mission_delegation(
    *,
    message: str,
    mission_id: Optional[str] = None,
    delegation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Forward a steering message from the delegating agent INTO a running
    mission delegation (next-turn, not mid-turn — a plain message queues behind
    the mission's current turn; a mission parked on a question needs
    answer_mission_question instead).

    Accepts either a ``mission_id`` (surfaced in the dispatch return) or a
    ``delegation_id`` (resolved via the ledger). Returns
    ``{"status":"sent"/"error", ...}``.
    """
    from tools.async_delegation import get_delegation_mission_id

    mid = (mission_id or "").strip()
    if not mid and delegation_id:
        mid = get_delegation_mission_id(delegation_id) or ""
    if not mid:
        return {
            "status": "error",
            "error": "steer requires a known mission_id or delegation_id.",
        }
    if not (message or "").strip():
        return {"status": "error", "error": "steer requires a non-empty message."}
    try:
        from tools.mcp_tool import _make_tool_handler

        handler = _make_tool_handler(
            _MCP_SERVER_NAME, _SEND_MESSAGE_TOOL, _START_MISSION_TIMEOUT
        )
        result_str = handler({"mission_id": mid, "content": message})
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "error": f"steer dispatch failed: {exc}"}
    return {"status": "sent", "mission_id": mid, "response": str(result_str)[:500]}


def await_mission_completion(
    *,
    delegation_id: str,
    mission_id: str,
    timeout_seconds: float,
    poll_interval: float = 2.0,
) -> Optional[Dict[str, Any]]:
    """Block up to ``timeout_seconds`` for a dispatched mission delegation to
    reach a terminal state, then CLAIM its delivery and return the result inline.

    Claiming is the anti-double-delivery arbiter: if the await-loop claims, it
    owns the delivery and returns the result inline (the async watcher, seeing
    the same queued event, finds the row already delivered and skips it). On
    timeout returns None — the caller returns a "dispatched" handle and the
    watcher folds the result later.
    """
    import os
    import time
    import uuid

    from tools.async_delegation import (
        claim_completion_delivery,
        complete_completion_delivery,
        find_delegation_by_mission_id,
    )

    deadline = time.time() + max(0.0, float(timeout_seconds))
    while True:
        row = find_delegation_by_mission_id(mission_id)
        state = str((row or {}).get("state") or "running").lower()
        if row is not None and state not in ("running", "finalizing"):
            claim_id = f"await:{os.getpid()}:{uuid.uuid4().hex}"
            if claim_completion_delivery(delegation_id, claim_id):
                complete_completion_delivery(delegation_id, claim_id)
                try:
                    evt = json.loads(row.get("event_json") or "{}")
                except Exception:
                    evt = {}
                return {
                    "status": evt.get("status") or "completed",
                    "results": evt.get("results") or [],
                    "delegation_id": delegation_id,
                    "mission_id": mission_id,
                    "delivered": "inline",
                }
            # The async watcher already claimed this completion — it will inject
            # the result as a message. Don't return it inline too (double).
            return {
                "status": "delivered_async",
                "delegation_id": delegation_id,
                "mission_id": mission_id,
                "note": "The mission result was delivered to the conversation.",
            }
        now = time.time()
        if now >= deadline:
            return None
        time.sleep(min(poll_interval, max(0.05, deadline - now)))


def dispatch_mission_delegation(
    *,
    goal: str,
    context: Optional[str] = None,
    role: Optional[str] = None,
    model: Optional[str] = None,
    session_key: str,
    parent_session_id: Optional[str] = None,
    origin_ui_session_id: str = "",
    origin_session_id: str = "",
    workspace_id: Optional[str] = None,
    project: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a sandboxed.sh mission for a delegated task + register its pending
    ledger slot.

    Returns ``{"status":"dispatched","delegation_id":...,"mission_id":...}`` on
    success, or ``{"status":"rejected"/"error","error":...}``.
    """
    from tools.async_delegation import (
        register_mission_delegation,
        set_delegation_mission_id,
        abandon_pending_delegation,
    )

    # 1) Register the pending slot BEFORE the POST so a completion that races
    #    ahead is resolvable once the mission id is bound (and, if it beats the
    #    bind, the webhook's durable markers re-POST — self-healing).
    reg = register_mission_delegation(
        goal=goal,
        context=context,
        role=role,
        model=model,
        session_key=session_key,
        parent_session_id=parent_session_id,
        origin_ui_session_id=origin_ui_session_id,
        origin_session_id=origin_session_id,
        workspace_id=workspace_id,
        project=project,
    )
    if reg.get("status") != "dispatched":
        return reg
    delegation_id = reg["delegation_id"]

    # 2) POST the mission via the existing sandboxed_assistant MCP connection.
    args: Dict[str, Any] = {
        "title": title or ((goal or "Delegated task")[:80]),
        "prompt": _build_mission_prompt(goal, context, role),
        # Route the completion home. start_mission server-fixes origin="hermes".
        "origin_session_id": origin_session_id or parent_session_id or "",
        # Leaf-only: a delegated mission does the work, it is not a writer and
        # holds no merge authority.
        "writer": False,
    }
    if workspace_id:
        args["workspace_id"] = workspace_id
    if project:
        args["project"] = project
    if model:
        args["model_override"] = model

    try:
        from tools.mcp_tool import _make_tool_handler

        handler = _make_tool_handler(
            _MCP_SERVER_NAME, _START_MISSION_TOOL, _START_MISSION_TIMEOUT
        )
        result_str = handler(args)
    except Exception as exc:  # pragma: no cover - transport failures
        logger.warning(
            "mission delegation %s: start_mission call failed: %s",
            delegation_id, exc,
        )
        abandon_pending_delegation(delegation_id)
        return {"status": "error", "error": f"start_mission dispatch failed: {exc}"}

    mission_id = _extract_mission_id(result_str)
    if not mission_id:
        # POST failed / unexpected shape → drop the phantom slot so no stray
        # webhook can ever resolve to it.
        abandon_pending_delegation(delegation_id)
        return {
            "status": "error",
            "error": (
                "start_mission did not return a mission id "
                f"(response: {str(result_str)[:300]})"
            ),
        }

    # 3) Bind the mission id — the authentication anchor for the inbound fork.
    set_delegation_mission_id(delegation_id, mission_id)
    logger.info(
        "mission delegation %s dispatched → mission %s (parent=%s)",
        delegation_id, mission_id, parent_session_id,
    )
    return {
        "status": "dispatched",
        "delegation_id": delegation_id,
        "mission_id": mission_id,
        "backend": "mission",
    }
