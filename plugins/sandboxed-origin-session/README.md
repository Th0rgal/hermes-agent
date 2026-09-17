# sandboxed-origin-session

Fork plugin. Two jobs:

1. **Stamp** `origin_session_id` on `start_mission` / `adopt_mission` from the
   live Hermes session (never a `cron_` tick id).
2. **Enroll** a successful conversational `start_mission` in the
   async-delegation ledger so the terminal sandboxed.sh webhook folds back
   into this chat instead of a throwaway `webhook:mission-complete:` session.

Controller ticks (`HERMES_CRON_AUTO_DELIVER_CONTROL_SESSION` set) are stamped
onto the project conversation but **not** enrolled — they keep their own
cadence. The webhook origin-route is the safety net when a ledger row is
missing.
