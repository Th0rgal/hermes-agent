A mission can finish a turn, report a blocker and then be resumed while retaining
the same technical objective. Each such execution needs its own completion
receipt. The async delegation ledger now retains one row per enrolled native
execution instead of treating the first delivered result as the end of all future
callbacks from that mission.

Conversational `start_mission` enrollment still uses the existing origin plugin.
Its first terminal callback binds `execution.run_id` and `execution.generation`
from the authenticated native webhook. An accepted `resume_mission` then reserves
a new row above that known generation. The hook requires the native
`resume_accepted: true` result and the existing `tool_call_id`; replaying that same
call returns the same enrollment, even after its result was delivered. The
reserved row acquires its run id only from a corresponding newer native callback.

The old receipt, delivery claim and parent route are preserved. An old run or
exact event replay cannot consume the new row. A callback that arrives before
the post-resume hook is kept in the existing pending-callback stash and receives
HTTP503 so the native sender retries. The hook reconciles that stash after the
new row commits. Unknown or conflicting execution identity also remains explicit
and retryable rather than being delivered as a new parent result. No watcher,
polling loop, model tool or second project state store was added.

All parent routing comes from the original durable ledger. The resuming
conversation must match that owner, or its explicit continuation in SessionDB.
Model-supplied origin arguments and callback routing hints cannot rebind it.
Observer/controller ticks do not arm conversational receipts. Existing observer
tool/output restrictions are unchanged.

New native enrollments carry an additive task-metadata marker so gateway restart
does not mark their external runner as failed merely because the old Hermes PID
exited. This also covers restart before the first callback supplied a generation.
In-process delegation recovery retains its existing behavior. The four added
ledger columns are nullable; old delivered rows without execution identity remain
`reconciliation_required` when resumed. They are not reset or automatically
adopted. A coordinated migration can start a replacement native mission with a
fresh enrollment while preserving the historical mission/workspace artifacts.

This changes automatic enrollment for `resume_mission`. Other commands that can
wake a mission (`send_message_to_mission`, a settings-triggered auto-resume, or a
native automatic retry) do not independently authorize a new delegation receipt.
A newer terminal callback without an enrolled resume remains pending for explicit
reconciliation. The parent should use `resume_mission` for this continuation
contract and verify the returned mission's actual execution/health separately.

Tests use real imports, SQLite ledgers, the plugin and WebhookAdapter under
temporary HERMES_HOME profiles. They cover both generations, exact replays,
concurrent delivery claims/resume, early callbacks, legacy identity, observer
authority, foreign-parent rejection and an enrollment subprocess that exits
before the native completion. Run them with `scripts/run_tests.sh`; no inference
or live mission is required. Additive schema rollback must preserve ledger rows;
an older binary does not understand the per-execution continuation contract.
