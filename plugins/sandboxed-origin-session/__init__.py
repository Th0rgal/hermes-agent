"""Stamp the originating Hermes conversation onto sandboxed.sh missions.

A mission started from a conversation has to carry that conversation's id, or
nothing downstream can find its way home: the sandboxed.sh clients group
missions as workers of their session, and the mission-status webhook uses
`origin_session` to deliver the result back where it was asked for.

Asking the model to pass its own session id does not work reliably — it does
not always know it, and a wrong id is worse than none. So this plugin injects
it deterministically, from the gateway's own session context, on every
`start_mission` call.

After a successful conversational start_mission it also enrolls the mission
in the async-delegation ledger. That is what makes the terminal webhook fold
back into this turn as a builtin worker, instead of opening a throwaway
`webhook:mission-complete:` session. Controller/cron ticks are not enrolled:
those sessions already report on their own cadence.

Seam: a ``tool_request`` middleware (rewrite args) plus a ``post_tool_call``
hook (enroll after success). ``pre_tool_call`` cannot rewrite arguments.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# MCP tools register as mcp__<server>__<tool>; this is the sandboxed.sh
# assistant server's mission starter.
TARGET_TOOLS = frozenset(
    {
        "mcp__sandboxed_assistant__start_mission",
        "mcp__sandboxed-assistant__start_mission",
        # adopt_mission re-points an existing mission at the calling
        # conversation; it needs the same stamp for the same reason, and the
        # MCP server refuses an unstamped call rather than clearing the origin.
        "mcp__sandboxed_assistant__adopt_mission",
        "mcp__sandboxed-assistant__adopt_mission",
    }
)

START_MISSION_TOOLS = frozenset(
    name for name in TARGET_TOOLS if name.endswith("start_mission")
)

# Mirrors the server-side validator in assistant-mcp: anything it would reject
# is not worth sending, and a malformed id must never become a routing hint.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Sessions that die with the work that created them. A cron job opens one per
# tick and ends it, so stamping one files the mission under a corpse: the
# conversation cannot be opened, replied to, or delivered into. Measured on
# Verity, that shape produced 52 one-tick "conversations" for one project.
_EPHEMERAL_SESSION_PREFIXES = ("cron_",)

# Published by cron/scheduler.py when the job's ``deliver`` names a project
# with an explicit route. Empty means "no explicit binding" — never a hint to
# guess from.
_CONTROL_SESSION_VAR = "HERMES_CRON_AUTO_DELIVER_CONTROL_SESSION"


def _session_env(name: str, default: str = "") -> str:
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env(name, default) or default)
    except Exception:  # pragma: no cover - context module is optional
        return default


def _is_ephemeral_session_id(session_id: str) -> bool:
    return session_id.startswith(_EPHEMERAL_SESSION_PREFIXES)


def _durable_session_id(fallback: str = "") -> str:
    """Best available id for the conversation this tool call belongs to.

    Prefers the api_server request's chat id: that is literally the
    ``state.db sessions.id`` the clients list, and unlike ``HERMES_SESSION_ID``
    it survives sub-agent construction (building a child agent overwrites the
    global session id, so a delegated call would otherwise stamp the child's
    synthetic id).
    """
    if _session_env("HERMES_SESSION_PLATFORM") == "api_server":
        chat_id = _session_env("HERMES_SESSION_CHAT_ID")
        if chat_id:
            return chat_id
    # Non-api_server surfaces (CLI, cron, desktop): the executor's own
    # session_id, then the process env. Both are best-effort.
    return str(fallback or os.environ.get("HERMES_SESSION_ID", "") or "")


def _resolve_origin_session(fallback: str = "") -> Tuple[str, str]:
    """(session_id, lane). An empty session_id means "do not stamp".

    Lanes, first hit wins:

    ``control``     the project's durable conversation, resolved by the cron
                    scheduler through the explicit route store. This is what
                    makes a tick's missions belong to the project instead of
                    to the tick.
    ``api_server``  an interactive turn: the request's own chat id.
    ``fallback``    anything else, best-effort — but never an ephemeral id.
    """
    control = _session_env(_CONTROL_SESSION_VAR).strip()
    if control:
        return control, "control"

    candidate = _durable_session_id(fallback).strip()
    if not candidate:
        return "", "none"
    if (
        _session_env("HERMES_SESSION_PLATFORM") == "api_server"
        and _session_env("HERMES_SESSION_CHAT_ID").strip() == candidate
    ):
        return candidate, "api_server"
    if _is_ephemeral_session_id(candidate):
        # Refusing is the point. An unstamped mission is honestly
        # unattributed and can be adopted later; one stamped with a dead
        # per-tick id is filed under a conversation nobody will read again.
        return "", "ephemeral"
    return candidate, "fallback"


def stamp_origin_session(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Add `origin_session_id` to a start_mission call. Never overwrites."""
    if kwargs.get("tool_name") not in TARGET_TOOLS:
        return None
    args = kwargs.get("args")
    if not isinstance(args, dict):
        return None
    # An explicit value wins: the caller may be re-homing a mission on purpose.
    existing = args.get("origin_session_id")
    if isinstance(existing, str) and existing.strip():
        return None

    session_id, lane = _resolve_origin_session(str(kwargs.get("session_id") or ""))
    if not session_id:
        if lane == "ephemeral":
            logger.warning(
                "start_mission from an ephemeral session; leaving the mission "
                "unstamped rather than filing it under a per-tick conversation"
            )
        return None
    if not _SESSION_ID_RE.match(session_id):
        logger.warning("refusing malformed origin_session_id from lane %s", lane)
        return None

    logger.debug("stamping origin_session_id=%s (lane=%s)", session_id, lane)
    return {
        "args": {**args, "origin_session_id": session_id},
        "source": "sandboxed-origin-session",
        "reason": f"origin_session_id injected from the {lane} lane",
    }


def enroll_after_start_mission(**kwargs: Any) -> None:
    """After a successful conversational start_mission, bind the ledger row.

    Controller ticks (``HERMES_CRON_AUTO_DELIVER_CONTROL_SESSION`` set) are
    not enrolled: those missions report on the controller cadence. The
    origin-route webhook remains the safety net if a conversation still
    needs the terminal notice.
    """
    tool_name = kwargs.get("tool_name")
    if tool_name not in START_MISSION_TOOLS:
        return None
    status = str(kwargs.get("status") or "ok").lower()
    if status not in ("ok", "success", ""):
        return None

    args = kwargs.get("args") if isinstance(kwargs.get("args"), dict) else {}
    origin = str(args.get("origin_session_id") or "").strip()
    lane = ""
    if not origin:
        origin, lane = _resolve_origin_session(str(kwargs.get("session_id") or ""))
    if not origin or lane == "ephemeral":
        return None
    # A cron tick that stamped the project conversation still owns the
    # cadence — do not fold those completions into the project chat as
    # if they were a builtin worker the user is waiting on.
    if _session_env(_CONTROL_SESSION_VAR).strip():
        return None

    try:
        from tools.mission_delegation import enroll_conversational_start_mission

        enroll_conversational_start_mission(
            result=kwargs.get("result"),
            origin_session_id=origin,
            parent_session_id=str(kwargs.get("session_id") or origin),
            goal=str(args.get("prompt") or args.get("title") or ""),
            title=str(args.get("title") or ""),
            project=args.get("project"),
            workspace_id=args.get("workspace_id"),
            model=args.get("model_override"),
        )
    except Exception:
        logger.warning("start_mission enroll failed", exc_info=True)
    return None


def register(ctx) -> None:
    ctx.register_middleware("tool_request", stamp_origin_session)
    ctx.register_hook("post_tool_call", enroll_after_start_mission)
