"""Pure cron delivery-routing resolvers, extracted from cron/scheduler.py.

These are self-contained (no scheduler globals, no I/O) helpers for resolving a
job's routing intent. Housing them here shrinks the fork delta on the heavily
upstream-patched ``cron/scheduler.py`` — scheduler re-exports them, so
``from cron.scheduler import _resolve_origin`` still resolves.

Only the *pure* resolvers live here. The heavier ones (``_resolve_project_route_target``,
``_resolve_control_route``, ``_resolve_single_delivery_target``) stay in
scheduler.py: they are tightly coupled to scheduler internals (its logger,
``_emit_controller_event``, ``_LOCAL_SESSION_PLATFORMS``, local-session helpers)
and are not cleanly liftable.
"""

from __future__ import annotations

from typing import Optional


def _resolve_origin(job: dict) -> Optional[dict]:
    """Extract origin info from a job, preserving any extra routing metadata.

    Treats non-dict origins (free-form provenance strings, ints, lists from
    migration scripts or hand-edited jobs.json) as missing instead of
    crashing with ``AttributeError`` on ``origin.get(...)``. Without this
    guard, a job tagged with e.g. ``"combined-digest-replaces-x-and-y"``
    crashed every fire attempt with
    ``'str' object has no attribute 'get'`` — ``mark_job_run`` recorded the
    failure, but the next tick re-loaded the same poisoned origin and
    crashed identically until the field was patched manually (#18722).
    """
    origin = job.get("origin")
    if not isinstance(origin, dict):
        return None
    platform = origin.get("platform")
    chat_id = origin.get("chat_id")
    if platform and chat_id:
        return origin
    return None


def _normalize_deliver_value(deliver) -> str:
    """Normalize a stored/submitted ``deliver`` value to its canonical string form.

    The contract is that ``deliver`` is a string (``"local"``, ``"origin"``,
    ``"telegram"``, ``"telegram:-1001:17"``, or comma-separated combinations).
    Historically some callers — MCP clients passing an array, direct edits of
    ``jobs.json``, or stale code paths — have stored a list/tuple like
    ``["telegram"]``.  ``str(["telegram"])`` would serialize to the literal
    string ``"['telegram']"``, which is not a known platform and fails
    resolution silently.  Flatten lists/tuples into a comma-separated string
    so both forms work.  Returns ``"local"`` for anything falsy.
    """
    if deliver is None or deliver == "":
        return "local"
    if isinstance(deliver, (list, tuple)):
        parts = [str(p).strip() for p in deliver if str(p).strip()]
        return ",".join(parts) if parts else "local"
    return str(deliver)
