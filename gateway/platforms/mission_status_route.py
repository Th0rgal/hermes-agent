"""Route a sandboxed.sh mission-status webhook into a live conversation.

HMAC already authenticated the payload. origin_session is still a hint:
the session must exist (continuations followed). If it does not, the
explicit project route is the only fallback. An unroutable payload returns
None so the webhook adapter can reject delivery without an owner.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Optional, Tuple
from uuid import UUID, uuid4
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_PENDING_DIRNAME = "pending_mission_callbacks"
_SAFE_MISSION_RE = re.compile(r"[^A-Za-z0-9._:-]+")

_TERMINAL = {
    "completed",
    "failed",
    "not_feasible",
    "notfeasible",
    "blocked",
    "interrupted",
    "awaiting_user",
    "awaitinguser",
}

# Legacy cap kept for tests/import compatibility. The wake prompt is now a
# one-or-two-sentence notice that forbids tools, so a large operator session
# must still be woken — otherwise callbacks append silently and the chat
# looks dead (Lido/EIP-8282, 2026-08-24). Only a compression lock skips.
PROJECT_OPERATOR_WAKE_MESSAGE_CAP = 80

# Display typing for the rows this route writes. The desktop renders
# `mission_callback` under a "mission finished" divider, `mission_callback_wake`
# as a timeline line (never the prompt text), and `hidden` not at all.
MISSION_CALLBACK_DISPLAY_KIND = "mission_callback"
MISSION_CALLBACK_WAKE_DISPLAY_KIND = "mission_callback_wake"
MISSION_CALLBACK_SEPARATOR_DISPLAY_KIND = "hidden"

MISSION_CALLBACK_WAKE_PROMPT = (
    "A routed mission-complete callback was just appended to this conversation. "
    "In one or two sentences, report the attempt outcome and any verified follow-up. "
    "Only when the callback declares a successor, distinguish a superseded attempt "
    "from that replacement. A supersession tag does not prove it is executing: name "
    "a live replacement only with current native execution evidence already available "
    "in this conversation; otherwise say that declared replacement is unverified. "
    "Do not mention replacement execution when no successor is declared. Never promise rerouting "
    "or say no action is needed merely because a controller exists. Preserve "
    "actionable failures and unresolved questions. Do not inspect the mission, "
    "do not run tools, and do not dispatch or continue project work in this notice."
)


def extract_superseded_by(payload: dict) -> str | None:
    """Native mission_horizon tag: relationship evidence, never liveness proof."""
    tags = payload.get("tags")
    if not isinstance(tags, list):
        return None
    # Match the producer's last valid tag precedence.
    for tag in reversed(tags):
        if isinstance(tag, str) and tag.startswith("superseded_by:"):
            try:
                successor = str(UUID(tag.split(":", 1)[1].strip()))
            except ValueError:
                continue
            if successor != str(payload.get("mission_id", "")).strip():
                return successor
    return None


# A wedged connection can outlive the deadline. Bound outstanding reads too.
_REPLACEMENT_READ_SLOT = threading.BoundedSemaphore(1)
_REPLACEMENT_READ_TIMEOUT = 5.0


async def bounded_replacement_evidence(payload: dict) -> dict | None:
    import asyncio

    if not _REPLACEMENT_READ_SLOT.acquire(blocking=False):
        return None

    def read():
        try:
            return read_replacement_evidence(payload)
        finally:
            _REPLACEMENT_READ_SLOT.release()

    task = asyncio.create_task(asyncio.to_thread(read))
    try:
        return await asyncio.wait_for(asyncio.shield(task), _REPLACEMENT_READ_TIMEOUT)
    except Exception:
        return None


def read_replacement_evidence(payload: dict) -> dict | None:
    """Read current native identity/execution through the existing MCP server.

    No server name, project authority or verified result comes from callback
    prose. Missing tools/readback preserve the explicit unverified notice.
    """
    from tools.registry import registry
    from tools.mission_delegation import _MCP_SERVER_NAME
    from cron.controller_scope import _readback_object

    name = f"mcp__{_MCP_SERVER_NAME}__get_mission_digest"
    if registry.get_entry(name) is None:
        return None
    try:
        from tools.mcp_tool import _make_tool_handler

        read = _make_tool_handler(_MCP_SERVER_NAME, "get_mission_digest", 5.0)
        mission_id = str(UUID(str(payload.get("mission_id") or "")))
        project = extract_project_slug(payload)
        if not project:
            return None

        def verified_identity(mid):
            row = _readback_object(read({"mission_id": mid}))
            if (row.get("id") or row.get("mission_id")) != mid or extract_project_slug(row) != project:
                raise ValueError("Native replacement identity/project mismatch")
            return row

        prior = verified_identity(mission_id)
        tags = prior.get("tags")
        if tags is None and isinstance(prior.get("project"), dict):
            tags = prior["project"].get("tags")
        successor = extract_superseded_by({"mission_id": mission_id, "tags": tags})
        if not successor:
            return None
        replacement = verified_identity(successor)
        result = {"mission_id": successor, "verified_live": False}
        execution = replacement.get("execution")
        if not isinstance(execution, dict):
            return result
        # Queued/starting/status=active alone do not prove execution. These
        # states are the native durable runner's executing states. A stale
        # heartbeat is unknown, never proof of termination or permission to
        # dispatch another writer.
        state = execution.get("state")
        run_id = execution.get("run_id")
        try:
            heartbeat = datetime.fromisoformat(execution.get("heartbeat_at") or "")
        except (TypeError, ValueError):
            return result
        if heartbeat.tzinfo is None:
            return result
        now = datetime.now(timezone.utc)
        if state in {"running", "waiting_tool"} and run_id and 0 <= (now - heartbeat).total_seconds() <= 60:
            result.update(verified_live=True, run_id=str(run_id), state=state, observed_at=now.isoformat())
        return result
    except Exception:
        logger.debug("Native replacement readback unavailable", exc_info=True)
        return None


def extract_origin_session(payload: dict) -> str:
    return str(
        payload.get("origin_session") or payload.get("origin_session_id") or ""
    ).strip()


def extract_project_slug(payload: dict) -> str:
    project = payload.get("project")
    if isinstance(project, dict):
        project = project.get("project") or project.get("slug")
    return str(project or "").strip()


def extract_status(payload: dict) -> str:
    return str(
        payload.get("status") or payload.get("type") or payload.get("event_type") or ""
    ).strip().lower()


def is_routable_mission_status(payload: dict) -> bool:
    if not str(payload.get("mission_id") or "").strip():
        return False
    return extract_status(payload) in _TERMINAL


def sync_session_db(session_db: Any) -> Any:
    """Prefer the underlying sync SessionDB.

    The gateway runner exposes ``AsyncSessionDB``, whose ``__getattr__``
    wraps every method in ``asyncio.to_thread``. Calling those from this
    sync router yields a coroutine (truthy!) instead of a session row —
    that is how origin-route crashed in production (TAP smoke, 2026-08-15)
    and fell through to a throwaway webhook session.
    """
    inner = getattr(session_db, "_db", None)
    return inner if inner is not None else session_db


def resolve_live_session_id(session_id: str, session_db: Any) -> Optional[str]:
    """Follow continuation / resume pointers; None if the row is gone."""
    sid = (session_id or "").strip()
    if not sid:
        return None
    resolve_resume = getattr(session_db, "resolve_resume_session_id", None)
    resolve_id = getattr(session_db, "resolve_session_id", None)
    if callable(resolve_resume):
        sid = resolve_resume(sid) or sid
    elif callable(resolve_id):
        sid = resolve_id(sid) or sid
    row = session_db.get_session(sid)
    if not row:
        return None
    return sid


def _recent_message_texts(session_db: Any, session_id: str, limit: int = 400):
    """Best-effort read of a session's recent message bodies.

    Feature-detected: SessionDB variants expose ``get_messages`` /
    ``list_messages``. Returns None (not []) when no reader exists, so
    callers can tell "cannot inspect" apart from "inspected, found nothing".
    """
    reader = getattr(session_db, "get_messages", None) or getattr(
        session_db, "list_messages", None
    )
    if not callable(reader):
        return None
    try:
        rows = reader(session_id) or []
    except Exception:
        logger.debug("message read failed for %s", session_id, exc_info=True)
        return None
    texts = []
    for row in rows[-limit:]:
        content = row.get("content") if isinstance(row, dict) else getattr(row, "content", "")
        if content:
            texts.append(str(content))
    return texts


def _parent_session_id(session_db: Any, session_id: str) -> str:
    getter = getattr(session_db, "get_session", None)
    if not callable(getter):
        return ""
    try:
        row = getter(session_id)
    except Exception:
        return ""
    if not row:
        return ""
    parent = (
        row.get("parent_session_id")
        if isinstance(row, dict)
        else getattr(row, "parent_session_id", None)
    )
    return str(parent or "").strip()


def session_references_mission(session_db: Any, session_id: str, mission_id: str) -> bool:
    """Ownership proof for origin routing: the session must already mention
    the mission (the dispatch tool result and prior callbacks embed its id).

    HMAC authenticates the *payload*, not the origin hint inside it — any
    mission creator could name an unrelated conversation. Fail-open only when
    the store exposes no message reader (legacy DBs), fail-closed otherwise.

    Walks ``parent_session_id`` so a compression continuation still owns a
    mission started in the pre-compression parent.
    """
    mission = (mission_id or "").strip()
    if not mission:
        return False
    current = (session_id or "").strip()
    seen: set[str] = set()
    inspected = False
    while current and current not in seen:
        seen.add(current)
        texts = _recent_message_texts(session_db, current)
        if texts is None:
            if not inspected:
                return True  # cannot inspect — legacy store, keep prior behaviour
            break
        inspected = True
        if any(mission in text for text in texts):
            return True
        current = _parent_session_id(session_db, current)
    return False


def extract_event_id(payload: dict) -> str:
    return str(payload.get("event_id") or payload.get("delivery_id") or "").strip()


def _identity_line_value(value: object) -> str:
    """Keep externally supplied callback identity fields on one header line."""
    return " ".join(str(value or "").split())


def _callback_revision(
    payload: dict, replacement_evidence: dict | None = None,
) -> tuple[str | None, bool]:
    """Return the structured relationship revision represented by a callback.

    The human-readable body can contain arbitrary worker output, including a
    quoted callback or relationship sentence. Dedupe must compare only this
    explicit envelope state, never prose in a prior callback.
    """
    successor = (
        (replacement_evidence or {}).get("mission_id")
        or extract_superseded_by(payload)
    )
    successor = str(successor).strip() if successor else None
    verified = bool(
        successor
        and replacement_evidence
        and replacement_evidence.get("verified_live") is True
    )
    return successor, verified


def resolve_project_session_id(project: str, session_db: Any = None) -> Optional[str]:
    slug = (project or "").strip()
    if not slug:
        return None
    try:
        from hermes_cli import project_routes as routes
        from hermes_cli import projects_db as pdb
    except Exception:
        logger.debug("project route store unavailable", exc_info=True)
        return None
    try:
        with pdb.connect_closing() as conn:
            target = routes.resolve_route_target(
                conn, slug, session_db=session_db
            )
        return str(getattr(target, "session_id", "") or "") or None
    except Exception as exc:
        logger.info("project route %s did not resolve: %s", slug, exc)
        return None


def extract_wake_session(payload: dict) -> str:
    return str(payload.get("wake_session") or "").strip()


def resolve_mission_delivery_session(payload: dict, session_db: Any) -> Optional[str]:
    """Pick the dedicated conversation for this mission-status event."""
    session_db = sync_session_db(session_db)
    if not is_routable_mission_status(payload):
        return None
    # Producer-resolved tip (project binding, else origin). Use the id as-is
    # when the row exists: walking resume children is how a project click
    # used to land on a live review thread instead of the bound session.
    hinted = extract_wake_session(payload)
    if hinted:
        row = session_db.get_session(hinted)
        if row:
            return hinted
        live = resolve_live_session_id(hinted, session_db)
        if live:
            return live
    origin = extract_origin_session(payload)
    if origin:
        live = resolve_live_session_id(origin, session_db)
        if live:
            mission_id = str(payload.get("mission_id") or "").strip()
            if session_references_mission(session_db, live, mission_id):
                return live
            logger.info(
                "origin %s does not reference mission %s — falling back to the project route",
                live,
                mission_id,
            )
    project = extract_project_slug(payload)
    if project:
        return resolve_project_session_id(project, session_db)
    return None


def format_mission_callback(payload: dict, *, replacement_evidence: dict | None = None) -> str:
    """Human + machine trailer written into the dedicated session."""
    mission_id = _identity_line_value(payload.get("mission_id"))
    status = _identity_line_value(extract_status(payload))
    # This line is part of the machine-readable callback envelope.  A title
    # comes from an external producer, so it must not be allowed to split the
    # header and defeat exact event-id deduplication.
    title = _identity_line_value(payload.get("title") or "mission")
    project = extract_project_slug(payload) or "unknown"
    workspace = _identity_line_value(payload.get("workspace_name"))
    bits = [
        payload.get("result_summary"),
        payload.get("short_description"),
        payload.get("terminal_reason"),
        payload.get("terminal_evidence") if status != "completed" else None,
    ]
    body = "\n".join(str(b).strip() for b in bits if b and str(b).strip())
    event_id = _identity_line_value(extract_event_id(payload))
    successor, verified = _callback_revision(payload, replacement_evidence)
    identity = f"status={status} mission={mission_id}"
    if event_id:
        identity += f" event={event_id}"
    # Keep revision fields before arbitrary workspace text. The append path
    # reads only this structured portion of the identity line, so quoted
    # worker prose and workspace values cannot impersonate revision evidence.
    if successor:
        identity += f" superseded_by={successor}"
    if verified:
        identity += " replacement_verified=1"
    if workspace:
        identity += f" workspace={workspace}"
    lines = [f"[Mission callback: {title}]", identity]
    if body:
        lines.append(body)
    if successor:
        lines.append(f"Superseded attempt; declared successor={successor}.")
        if verified:
            lines.append(
                f"Replacement execution verified live at {replacement_evidence['observed_at']}: "
                f"mission={successor} run={replacement_evidence['run_id']} state={replacement_evidence['state']}. "
                "This observation does not accept project evidence or resolve unrelated failures."
            )
        else:
            lines.append("Current replacement execution is not verified.")
    lines.append(
        f"Attempt evidence for project={project}. "
        "The controller must check current native execution and evidence before claiming recovery "
        "or dispatching more work. Retain actionable failures until resolved."
    )
    # Callbacks are attempt evidence. They must not write project mode/decisions
    # through the native transcript ingestor, including markers quoted by a
    # worker's result. Only subsequent controller judgment owns that action.
    return re.sub(
        r"\[(CTRL|STATE_SIGNATURE|DECISION|Cron delivery)\s*:",
        lambda match: "[Mission evidence " + match[1] + ":",
        "\n".join(lines), flags=re.IGNORECASE,
    )


def mission_callback_display_metadata(payload: dict) -> dict:
    """Compact, renderable facts about the callback for ``display_metadata``.

    Lets a client show "mission finished · title · status" and open the mission
    without parsing the prose — and even when the woken model ignores the
    one-or-two-sentence instruction.
    """
    metadata = {
        "mission_id": str(payload.get("mission_id") or "").strip(),
        "status": extract_status(payload),
        "title": str(payload.get("title") or "mission").strip(),
        "project": extract_project_slug(payload) or None,
    }
    event_id = extract_event_id(payload)
    if event_id:
        metadata["event_id"] = event_id
    workspace = str(payload.get("workspace_name") or "").strip()
    if workspace:
        metadata["workspace"] = workspace
    summary = str(
        payload.get("result_summary") or payload.get("short_description") or ""
    ).strip()
    if summary:
        metadata["summary"] = summary[:400]
    return {k: v for k, v in metadata.items() if v is not None}


def _append_typed(session_db: Any, **kwargs: Any) -> None:
    """``append_message`` with display typing, tolerating older DB shims."""
    try:
        session_db.append_message(**kwargs)
    except TypeError:
        kwargs.pop("display_kind", None)
        kwargs.pop("display_metadata", None)
        kwargs.pop("turn_lease_holder", None)
        session_db.append_message(**kwargs)


class MissionCallbackTurnActive(RuntimeError):
    """A live agent turn owns the transcript; webhook delivery must retry."""


def _acquire_callback_turn_lease(session_db: Any, session_id: str) -> str | None:
    """Fence callback transcript writes behind a live agent turn when supported.

    Older DB shims lack durable turn leases and retain their existing behaviour.
    On the real SessionDB, failing to acquire means a turn is actively loading
    or flushing this conversation, so callers must leave native delivery
    unacknowledged rather than inserting a callback into its transcript.
    """
    acquire = getattr(session_db, "try_acquire_session_turn_lease", None)
    if not callable(acquire):
        return None
    holder = f"mission-callback:{uuid4().hex}"
    if not acquire(session_id, holder, ttl_seconds=30.0, patience_s=0.5):
        raise MissionCallbackTurnActive(session_id)
    return holder


def _release_callback_turn_lease(session_db: Any, session_id: str, holder: str | None) -> None:
    if not holder:
        return
    release = getattr(session_db, "release_session_turn_lease", None)
    if callable(release):
        release(session_id, holder)


def _last_message_role(session_db: Any, session_id: str) -> Optional[str]:
    reader = getattr(session_db, "get_messages", None) or getattr(
        session_db, "list_messages", None
    )
    if not callable(reader):
        return None
    try:
        rows = reader(session_id) or []
    except Exception:
        return None
    if not rows:
        return None
    last = rows[-1]
    role = last.get("role") if isinstance(last, dict) else getattr(last, "role", None)
    return str(role).strip().lower() if role else None


def should_wake_mission_callback(session_db: Any, live_id: str) -> bool:
    """Whether to start an agent turn after appending a mission callback.

    Append always happens. The wake prompt is a one-or-two-sentence notice
    that forbids tools. Skip only while another writer holds the compression
    lock — a large operator session must still be told that a mission finished.
    """
    sid = (live_id or "").strip()
    if not sid:
        return False
    db = sync_session_db(session_db)
    holder_fn = getattr(db, "get_compression_lock_holder", None)
    if callable(holder_fn):
        try:
            if holder_fn(sid):
                logger.info(
                    "skip mission wake for %s: compression lock held", sid
                )
                return False
        except Exception:
            logger.debug("compression-lock check failed for %s", sid, exc_info=True)
    return True


def append_mission_callback(
    session_id: str, payload: dict, session_db: Any, *, replacement_evidence: dict | None = None,
) -> Tuple[str, bool]:
    """Persist the callback on the live session.

    Returns ``(live_id, appended)``. ``appended`` is False when this exact
    delivery is already in the transcript — callers must not schedule a
    second wake.

    Idempotent per delivery: the producer retries at-least-once on a lost
    HTTP response, so an ``event=<id>`` already present in the transcript
    means this exact delivery landed.

    Role-safe: never writes assistant→assistant. If the tip is already an
    assistant turn, a user separator is inserted first so
    ``repair_message_sequence`` cannot merge the callback into the previous
    model answer.
    """
    session_db = sync_session_db(session_db)
    live = resolve_live_session_id(session_id, session_db) or session_id
    lease_holder = _acquire_callback_turn_lease(session_db, live)
    try:
        event_id = extract_event_id(payload)
        if event_id:
            texts = _recent_message_texts(session_db, live)
            if texts is not None:
                mission_id = _identity_line_value(payload.get("mission_id"))
                event_id = _identity_line_value(event_id)
                header = re.compile(
                    rf"status=\S+ mission={re.escape(mission_id)} event={re.escape(event_id)}"
                    r"(?: superseded_by=[0-9a-f-]{36})?(?: replacement_verified=1)?"
                    r"(?: workspace=.*)?"
                )
                # Match the producer identity line, never a prefix or quoted
                # evidence further down the callback body.
                def matches(text):
                    lines = text.splitlines()
                    return (len(lines) >= 2 and lines[0].startswith("[Mission callback:")
                            and header.fullmatch(lines[1]) is not None)

                existing_headers = [text.splitlines()[1] for text in texts if matches(text)]
                if existing_headers:
                    # Native retry delivery may enrich a terminal event with its
                    # supersession relationship after the original callback was
                    # stored.  Keep unchanged retries idempotent, but append the
                    # new attempt evidence so the owning conversation is woken
                    # with the same revision the controller inbox receives.
                    successor, verified_now = _callback_revision(
                        payload, replacement_evidence
                    )
                    # Only the latest generated identity line is evidence for
                    # this event's current relationship. Searching every
                    # callback body made a quoted successor look authoritative
                    # and treated A -> B -> A as an unchanged retry.
                    latest_header = existing_headers[-1].split(" workspace=", 1)[0]
                    successor_match = re.search(
                        r"(?:^| )superseded_by=([0-9a-f-]{36})(?: |$)",
                        latest_header,
                    )
                    latest_successor = (
                        successor_match.group(1) if successor_match else None
                    )
                    latest_verified = " replacement_verified=1" in latest_header
                    if not successor or (
                        successor == latest_successor
                        and (not verified_now or latest_verified)
                    ):
                        logger.info(
                            "duplicate mission callback event %s for %s — skipping append",
                            event_id,
                            live,
                        )
                        return live, False
        content = format_mission_callback(payload, replacement_evidence=replacement_evidence)
        metadata = mission_callback_display_metadata(payload)
        if _last_message_role(session_db, live) == "assistant":
            _append_typed(
                session_db,
                session_id=live,
                role="user",
                content="A mission you started has finished. The result follows.",
                display_kind=MISSION_CALLBACK_SEPARATOR_DISPLAY_KIND,
                display_metadata=metadata,
                turn_lease_holder=lease_holder,
            )
        _append_typed(
            session_db,
            session_id=live,
            role="assistant",
            content=content,
            display_kind=MISSION_CALLBACK_DISPLAY_KIND,
            display_metadata=metadata,
            turn_lease_holder=lease_holder,
        )
        return live, True
    finally:
        _release_callback_turn_lease(session_db, live, lease_holder)


def append_mission_wake_failure(session_id: str, payload: dict, session_db: Any) -> None:
    """Do not write an ambiguous wake receipt into a live conversation.

    A self-post timeout does not cancel the underlying model turn.  Appending
    an assistant receipt here can race that turn's final assistant message and
    corrupt strict role alternation.  The callback itself is already durable;
    retain the ambiguity in logs until the turn has a terminal receipt.
    """
    logger.warning("mission wake delivery outcome unknown for %s event=%s; no transcript receipt appended",
                   session_id, extract_event_id(payload))


def _pending_callback_path(mission_id: str) -> Path:
    from hermes_constants import get_hermes_home

    safe = _SAFE_MISSION_RE.sub("_", (mission_id or "").strip())[:128] or "unknown"
    return get_hermes_home() / _PENDING_DIRNAME / f"{safe}.json"


# Existing early-enrollment evidence only: never project ownership. Refuse
# overflow/conflicting events rather than silently replacing accepted evidence.
_PENDING_MAX_RECORDS = 128
_PENDING_MAX_BYTES = 65_536


def stash_unroutable_callback(mission_id: str, payload: dict) -> bool:
    """Bounded, fail-closed backup; HTTP acceptance must await a real owner."""
    mid = (mission_id or "").strip()
    if not mid or not isinstance(payload, dict):
        return False
    path = _pending_callback_path(mid)
    lock = path.parent / ".mutation-lock"
    acquired = False
    handle = None
    try:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _PENDING_MAX_BYTES:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the lock inode: the OS releases ownership even on process death.
        # A legacy directory at this path fails closed; never steal its lock.
        from gateway.status import _try_acquire_file_lock

        handle = lock.open("a+", encoding="utf-8")
        acquired = _try_acquire_file_lock(handle)
        if not acquired:
            return False
        if path.exists():
            if path.stat().st_size > _PENDING_MAX_BYTES:
                return False
            return json.loads(path.read_text(encoding="utf-8")) == payload
        if sum(1 for _ in path.parent.glob("*.json")) >= _PENDING_MAX_RECORDS:
            return False
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_bytes(encoded)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return True
    except Exception:
        logger.debug("pending callback backup refused for %s", mid, exc_info=True)
        return False
    finally:
        if handle is not None:
            if acquired:
                from gateway.status import _release_file_lock

                _release_file_lock(handle)
            handle.close()


def peek_stashed_callback(mission_id: str) -> Optional[dict]:
    """Read backup evidence without consuming it before durable reconciliation."""
    return _read_stashed_callback(mission_id, consume=False)


def take_stashed_callback(mission_id: str, *, expected_payload: Optional[dict] = None) -> Optional[dict]:
    """Pop only the accepted evidence under the nonblocking mutation lock."""
    return _read_stashed_callback(mission_id, consume=True, expected_payload=expected_payload)


def _read_stashed_callback(
    mission_id: str, *, consume: bool, expected_payload: Optional[dict] = None,
) -> Optional[dict]:
    mid = (mission_id or "").strip()
    if not mid:
        return None
    path = _pending_callback_path(mid)
    lock = path.parent / ".mutation-lock"
    acquired = False
    handle = None
    try:
        from gateway.status import _try_acquire_file_lock

        handle = lock.open("a+", encoding="utf-8")
        acquired = _try_acquire_file_lock(handle)
        if not acquired:
            return None
        if not path.exists() or path.stat().st_size > _PENDING_MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if expected_payload is not None and data != expected_payload:
            return None
        if consume:
            path.unlink()
        return data
    except Exception:
        logger.debug("pending callback backup unavailable for %s", mid, exc_info=True)
        return None
    finally:
        if handle is not None:
            if acquired:
                from gateway.status import _release_file_lock

                _release_file_lock(handle)
            handle.close()
