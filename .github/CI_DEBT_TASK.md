# Task: clear the latent production CI debt

`production` carries pre-existing test/lint failures that per-area path-gating
hid on single-area pushes. PR #64 (fleet) was the first cross-cutting change to
run all suites together and exposed them. None are caused by fleet; all live in
files fleet never touched. This branch exists to fix them so `production` is
green end-to-end.

## Definition of done
`gh pr checks` on this PR shows every required check green:
`Python tests / *`, `JS & TS checks / apps/desktop / *`, `check-attribution`.

## Failures to fix (verified 2026-08-08 on PR #64)

1. **`tests/test_message_reactions.py::test_row_id_is_opt_in_and_never_reaches_the_provider`**
   Assertion `all(not k.startswith("_") or k == "_row_id" for k in message)` is
   False — a `_`-prefixed key other than `_row_id` leaks out of
   `get_messages_as_conversation()` (hermes_state.py / SessionSearchMixin). Find
   the stray underscore key and stop it reaching the default consumer.

2. **`tests/acp/test_session.py::test_assistant_reasoning_fields_persisted`**
   `restored.history` no longer equals the expected shape — reasoning-field
   persistence/restore drifted. Reconcile serialize/restore so the round-trip
   matches (or update the test if the shape change is intentional and correct).

3. **`tests/gateway/test_session_api.py`** (session API) — failing assertions on
   the chat-completion response/headers. Diagnose and fix the regression.

4. **`apps/desktop/src/app/chat/index.test.tsx` > "does not re-render chat
   history when an unrelated parent idle tick updates"** — render-isolation
   regression (memoization). Restore the isolation the test guards.

5. **ESLint (`apps/desktop`, `eslint src/ electron/`)** — remaining
   `perfectionist/sort-imports` + `sort-named-imports` errors in
   `src/lib/state-signature.test.ts` and `src/store/session-states.test.ts`.
   `npm run lint -- --fix` resolves these mechanically.

## Rules
- Fix root causes; do not delete/skip tests to go green. If a test asserts
  intended new behavior the impl lacks, implement the behavior.
- Keep changes scoped to these failures — no unrelated refactors.
- `cargo`-free repo; Python via the repo venv, desktop via `npm --workspace apps/desktop`.
- Follow FORK.md: commits authored so `check-attribution` stays green.
