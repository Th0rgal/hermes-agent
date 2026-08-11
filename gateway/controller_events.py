"""Structured controller events — typed wakeup/progress/notification vocabulary.

The cron scheduler and gateway controller historically narrated their internal
beats (a job firing, a delivery suppressed by ``[SILENT]``, a project route
resolving) as free-form log lines and ad-hoc delivery strings. This module
gives those beats a small, typed vocabulary so downstream consumers (delivery
batching, local audit, dashboard rendering) can switch on *what happened*
instead of re-parsing prose:

  * ``ControllerWakeup``     — the controller woke to fire scheduled work.
  * ``ReconciliationEvent``  — internal bookkeeping settled state (a silent
                               suppression, a route migration, a spool replay).
  * ``ProjectProgress``      — progress on a Project routed through the durable
                               explicit project-to-session routes
                               (``hermes_cli/project_routes.py``).
  * ``UserNotification``     — an explicitly user-facing message.

Design constraints (mirrors ``gateway/stream_events.py``):

  * Plain frozen dataclasses — no behavior beyond derived keys, no I/O, cheap
    to construct on any thread.
  * **Structured SILENT suppression.** Suppression is a typed ``silent`` field
    decided at emission time, not a string marker parsed out of model output.
    ``should_suppress_delivery()`` is the single gate; suppressed events are
    still eligible for local-origin audit (local delivery is never filtered —
    the same invariant ``gateway/delivery.py`` documents for silence
    narration). The legacy ``[SILENT]`` marker contract in ``cron/scheduler``
    is unchanged; when it fires, the scheduler records the fact as a
    structured ``ReconciliationEvent(kind="silent_suppression")``.
  * **Privacy.** Events never carry platform chat ids, thread ids, tokens or
    message bodies destined for other chats. They reference local identifiers
    only (job ids/names, project tokens, local SessionDB session ids), so a
    rendered digest can be delivered anywhere without leaking routing
    information, and the local audit trail stays scrubbable.
  * Events describe *transport-level facts*, never conversation context.
    Nothing here is persisted to agent message history.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# ── Event types ──────────────────────────────────────────────────────────────

RECONCILIATION_EVENT = "reconciliation_event"
CONTROLLER_WAKEUP = "controller_wakeup"
PROJECT_PROGRESS = "project_progress"
USER_NOTIFICATION = "user_notification"


@dataclass(frozen=True)
class ReconciliationEvent:
    """Internal bookkeeping settled state.

    ``kind`` is a stable machine-readable string, e.g.
    ``"silent_suppression"`` (a cron turn elected structured silence),
    ``"route_migrated"`` (a project route followed a session continuation).
    ``subject`` names the local entity reconciled (job id, project token).
    Silent by default: reconciliation is bookkeeping, not a user-facing beat.
    """
    kind: str
    subject: str = ""
    detail: str = ""
    silent: bool = True
    ts: float = 0.0

    event_type = RECONCILIATION_EVENT


@dataclass(frozen=True)
class ControllerWakeup:
    """The controller woke up to run scheduled/callback work.

    ``reason`` is stable and machine-readable (``"cron_fire"``,
    ``"callback"``, ``"retry"``). Silent by default — the wakeup itself is
    not a user-facing message; whatever the woken job *produces* travels its
    own delivery path.
    """
    reason: str
    job_id: str = ""
    job_name: str = ""
    silent: bool = True
    ts: float = 0.0

    event_type = CONTROLLER_WAKEUP


@dataclass(frozen=True)
class ProjectProgress:
    """Progress on a Project bound via a durable explicit route.

    ``project`` is the project token (id or slug) as supplied to
    ``deliver=project:<token>``; ``session_id`` is the *local* SessionDB id
    the route resolved to (never a platform chat id). ``stage`` is stable
    (``"route_resolved"``, ``"delivered"``, ``"blocked"``).
    """
    project: str
    stage: str
    session_id: str = ""
    summary: str = ""
    silent: bool = False
    ts: float = 0.0

    event_type = PROJECT_PROGRESS


@dataclass(frozen=True)
class UserNotification:
    """An explicitly user-facing notification.

    The only event type whose *primary purpose* is to reach the user;
    ``silent`` therefore defaults to False and flipping it True is the
    structured way to demote a notification to audit-only.
    """
    text: str
    title: str = ""
    priority: str = "normal"  # "low" | "normal" | "high"
    silent: bool = False
    ts: float = 0.0

    event_type = USER_NOTIFICATION


ControllerEvent = Union[
    ReconciliationEvent,
    ControllerWakeup,
    ProjectProgress,
    UserNotification,
]

_EVENT_CLASSES: Dict[str, type] = {
    RECONCILIATION_EVENT: ReconciliationEvent,
    CONTROLLER_WAKEUP: ControllerWakeup,
    PROJECT_PROGRESS: ProjectProgress,
    USER_NOTIFICATION: UserNotification,
}


# ── Structured SILENT suppression ────────────────────────────────────────────

def should_suppress_delivery(event: ControllerEvent) -> bool:
    """Single gate for structured silence.

    True → the event must not reach any platform adapter. Suppressed events
    remain eligible for local-origin audit delivery; suppression is about the
    user's chat surface, not about forgetting the fact.
    """
    return bool(getattr(event, "silent", False))


def partition_suppressed(
    events: List[ControllerEvent],
) -> Tuple[List[ControllerEvent], List[ControllerEvent]]:
    """Split ``events`` into (deliverable, suppressed), preserving order."""
    deliverable: List[ControllerEvent] = []
    suppressed: List[ControllerEvent] = []
    for event in events:
        (suppressed if should_suppress_delivery(event) else deliverable).append(event)
    return deliverable, suppressed


# ── UI roles ─────────────────────────────────────────────────────────────────

# Rendering roles understood by the dashboard/local transcript surfaces
# (web/src/lib/api.ts: "user" | "assistant" | "system" | "tool").  Controller
# events are never "user": they originate locally, not from the human.
UI_ROLES = frozenset({"user", "assistant", "system", "tool"})

_EVENT_UI_ROLE: Dict[str, str] = {
    RECONCILIATION_EVENT: "system",
    CONTROLLER_WAKEUP: "system",
    PROJECT_PROGRESS: "assistant",
    USER_NOTIFICATION: "assistant",
}


def ui_role_for(event: ControllerEvent) -> str:
    """Rendering role for local/dashboard surfaces. Total over event types."""
    return _EVENT_UI_ROLE[event.event_type]


# ── Serialization ────────────────────────────────────────────────────────────

def event_to_payload(event: ControllerEvent) -> Dict[str, Any]:
    """Serialize to a plain dict with an ``event_type`` discriminator."""
    payload: Dict[str, Any] = {"event_type": event.event_type}
    for name in event.__dataclass_fields__:
        payload[name] = getattr(event, name)
    return payload


def event_from_payload(payload: Dict[str, Any]) -> ControllerEvent:
    """Inverse of :func:`event_to_payload`. Raises ValueError on unknown type."""
    if not isinstance(payload, dict):
        raise ValueError("controller event payload must be a dict")
    event_type = payload.get("event_type")
    cls = _EVENT_CLASSES.get(event_type)
    if cls is None:
        raise ValueError(f"unknown controller event_type: {event_type!r}")
    fields = {
        name: payload[name]
        for name in cls.__dataclass_fields__
        if name in payload
    }
    return cls(**fields)


# ── Rendering ────────────────────────────────────────────────────────────────

def render_event(event: ControllerEvent) -> str:
    """Compact single-event text. Built only from typed fields — an event can
    never smuggle arbitrary routing metadata into a rendered digest."""
    if isinstance(event, UserNotification):
        return f"{event.title}: {event.text}" if event.title else event.text
    if isinstance(event, ProjectProgress):
        head = f"project {event.project}: {event.stage}"
        return f"{head} — {event.summary}" if event.summary else head
    if isinstance(event, ControllerWakeup):
        label = event.job_name or event.job_id or "scheduled work"
        return f"controller wakeup ({event.reason}): {label}"
    head = f"reconciled {event.kind}"
    if event.subject:
        head += f" [{event.subject}]"
    return f"{head} — {event.detail}" if event.detail else head


def render_batch(events: List[ControllerEvent]) -> str:
    """Render a batch as one digest, one line per event, arrival order.

    A single event renders bare (no bullet chrome) so the common case reads
    as a normal message.
    """
    if not events:
        return ""
    if len(events) == 1:
        return render_event(events[0])
    return "\n".join(f"• {render_event(e)}" for e in events)


# ── Dedupe + batching ────────────────────────────────────────────────────────

def dedupe_key(event: ControllerEvent) -> Tuple[str, ...]:
    """Stable identity for duplicate collapse.

    Excludes ``ts`` (re-emissions of the same fact within the window differ
    only by timestamp) and ``silent`` (a silent re-emission of an already
    delivered fact is still the same fact).
    """
    parts: List[str] = [event.event_type]
    for name in event.__dataclass_fields__:
        if name in ("ts", "silent"):
            continue
        parts.append(str(getattr(event, name)))
    return tuple(parts)


class ControllerEventBatcher:
    """Collects controller events, collapsing duplicates within a window.

    Thread-safe: emission happens on scheduler worker threads while flushes
    run on the delivery side. ``add`` returns True when the event was
    accepted, False when it deduplicated against an identical event seen
    within ``window_seconds``. ``flush`` returns accepted events in arrival
    order, at most ``max_batch`` per call (the remainder stays queued).

    Dedupe memory intentionally survives flushes: a fact re-emitted right
    after a flush is still the same fact until the window expires.
    """

    def __init__(self, window_seconds: float = 300.0, max_batch: int = 20):
        if window_seconds < 0:
            raise ValueError("window_seconds must be >= 0")
        if max_batch < 1:
            raise ValueError("max_batch must be >= 1")
        self.window_seconds = float(window_seconds)
        self.max_batch = int(max_batch)
        self._lock = threading.Lock()
        self._pending: List[ControllerEvent] = []
        self._seen: Dict[Tuple[str, ...], float] = {}

    def add(self, event: ControllerEvent, now: Optional[float] = None) -> bool:
        ts = time.time() if now is None else float(now)
        key = dedupe_key(event)
        with self._lock:
            self._evict(ts)
            if key in self._seen:
                return False
            self._seen[key] = ts
            self._pending.append(event)
            return True

    def accept_many(
        self,
        events: List[ControllerEvent],
        now: Optional[float] = None,
    ) -> List[ControllerEvent]:
        """Atomically deduplicate a call-local group without queueing it.

        The ``controller_events`` plugin's ``deliver_controller_events`` uses
        this path so two concurrent callers can share the dedupe window without
        sharing pending delivery state. Otherwise one caller could flush
        another caller's event and
        send it to the wrong targets.
        """
        ts = time.time() if now is None else float(now)
        accepted: List[ControllerEvent] = []
        with self._lock:
            self._evict(ts)
            for event in events:
                key = dedupe_key(event)
                if key in self._seen:
                    continue
                self._seen[key] = ts
                accepted.append(event)
        return accepted

    def flush(self, now: Optional[float] = None) -> List[ControllerEvent]:
        with self._lock:
            batch = self._pending[: self.max_batch]
            self._pending = self._pending[self.max_batch:]
            return batch

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _evict(self, now: float) -> None:
        if self.window_seconds == 0:
            self._seen.clear()
            return
        cutoff = now - self.window_seconds
        stale = [k for k, seen_at in self._seen.items() if seen_at < cutoff]
        for k in stale:
            del self._seen[k]


# ── Emission ─────────────────────────────────────────────────────────────────

# Optional process-wide sink, injectable for tests and for a gateway that
# wants to route emissions into a live batcher. Best-effort by contract:
# emit_controller_event never raises into its caller — a broken sink must not
# break job scheduling or delivery.
_sink: Optional[Callable[[ControllerEvent], None]] = None
_sink_lock = threading.Lock()


def set_controller_event_sink(
    sink: Optional[Callable[[ControllerEvent], None]],
) -> None:
    global _sink
    with _sink_lock:
        _sink = sink


def emit_controller_event(event: ControllerEvent) -> None:
    """Record a structured controller event (best-effort, never raises).

    Default behavior is a structured log line — local-origin, no platform
    I/O. Suppressed events log at DEBUG so routine bookkeeping does not spam
    INFO logs.
    """
    try:
        with _sink_lock:
            sink = _sink
        if sink is not None:
            sink(event)
            return
        level = logging.DEBUG if should_suppress_delivery(event) else logging.INFO
        logger.log(level, "controller_event %s", event_to_payload(event))
    except Exception:  # pragma: no cover - emission must never break callers
        logger.debug("controller event emission failed", exc_info=True)


__all__ = [
    "RECONCILIATION_EVENT",
    "CONTROLLER_WAKEUP",
    "PROJECT_PROGRESS",
    "USER_NOTIFICATION",
    "ReconciliationEvent",
    "ControllerWakeup",
    "ProjectProgress",
    "UserNotification",
    "ControllerEvent",
    "should_suppress_delivery",
    "partition_suppressed",
    "UI_ROLES",
    "ui_role_for",
    "event_to_payload",
    "event_from_payload",
    "render_event",
    "render_batch",
    "dedupe_key",
    "ControllerEventBatcher",
    "set_controller_event_sink",
    "emit_controller_event",
]
