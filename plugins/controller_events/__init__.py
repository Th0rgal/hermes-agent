"""Controller-event delivery, relocated out of the core gateway.

The structured controller-event *delivery* logic (batching, dedupe, structured
silence) used to live as ``DeliveryRouter.deliver_events`` patched into
``gateway/delivery.py`` — an upstream file, so every upstream refactor of the
delivery router risked a conflict. It has no production caller yet (the sink at
``gateway.controller_events.set_controller_event_sink`` is unwired; emission
just logs), so it is pure fork infrastructure. Housing it here keeps the delta
off the upstream file; the event *contract* (dataclasses, batcher, render,
suppression predicates) stays in ``gateway/controller_events.py`` — a fork-added
leaf module that never conflicts.

``deliver_controller_events(router, …)`` is the same code as the old method,
taking the ``DeliveryRouter`` explicitly instead of ``self``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def register(ctx) -> None:  # noqa: ARG001 - no runtime wiring yet
    """No-op: this plugin currently only houses the delivery helper.

    When controller-event delivery is wired to a target, install the sink here
    via ``gateway.controller_events.set_controller_event_sink(...)``.
    """


async def deliver_controller_events(
    router: Any,
    events: List[Any],
    targets: List[Any],
    job_id: Optional[str] = None,
    job_name: Optional[str] = None,
    batcher: Optional[Any] = None,
) -> Dict[str, Any]:
    """Deliver structured controller events (see gateway/controller_events).

    Semantics layered on top of ``router.deliver``:

    * **Dedupe** — duplicate events (same ``dedupe_key``) within the batcher
      window collapse to a single rendering. Callers that want cross-call
      dedupe pass a shared ``batcher``; otherwise a fresh per-call batcher
      still dedupes within the call.
    * **Batching** — accepted events render as one digest message per flush
      (at most ``batcher.max_batch`` per digest) instead of one platform send
      per event.
    * **Structured silence** — events with ``silent=True`` NEVER reach a
      platform adapter. They are still written through the local delivery path
      so the local-origin audit trail is preserved.

    Event payloads carry only local identifiers (job/project/session ids) by
    construction, so the digest and audit metadata leak no platform routing
    information.
    """
    from gateway.controller_events import (
        ControllerEventBatcher,
        event_to_payload,
        partition_suppressed,
        render_batch,
        ui_role_for,
    )

    active_batcher = batcher or ControllerEventBatcher()
    accepted = active_batcher.accept_many(events)
    results: Dict[str, Any] = {
        "accepted": len(accepted),
        "deduped": len(events) - len(accepted),
        "suppressed_local_only": 0,
        "deliveries": [],
    }

    for offset in range(0, len(accepted), active_batcher.max_batch):
        batch = accepted[offset : offset + active_batcher.max_batch]
        deliverable, suppressed = partition_suppressed(batch)

        if suppressed:
            # Audit-only: local delivery, never a platform adapter.
            router._deliver_local(
                render_batch(suppressed),
                job_id,
                job_name,
                {
                    "suppressed": True,
                    "controller_events": [event_to_payload(e) for e in suppressed],
                },
            )
            results["suppressed_local_only"] += len(suppressed)

        if deliverable:
            roles = {ui_role_for(e) for e in deliverable}
            metadata = {
                "job_id": job_id,
                "controller_events": [event_to_payload(e) for e in deliverable],
                "ui_role": "assistant" if "assistant" in roles else "system",
                "ui_roles": [ui_role_for(e) for e in deliverable],
            }
            results["deliveries"].append(
                await router.deliver(
                    render_batch(deliverable),
                    targets,
                    job_id=job_id,
                    job_name=job_name,
                    metadata=metadata,
                )
            )

    return results
