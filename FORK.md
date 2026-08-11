# Production fork maintenance

This repository (`Th0rgal/hermes-agent`) is a deployment fork of
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent).
The branch that production deploys is **`production`**.

## Branch model

- **`main`** — mirrors upstream `main`. Never carries fork-only work.
- **`production`** — upstream `main` + a short linear series of fork-delta
  commits, ending with this file. Every delta commit message carries a
  `[fork-delta]` marker (and, for the initial rebuild, the historical commit
  ids it replaces).
- Historical dated branches (`prod/desktop-session-origin-20260721`, …) are
  frozen pre-rebuild history. Do not build on them.

## Remotes

The remote **names differ between checkouts** — this is the single most
common footgun, so check `git remote -v` before running any sync command.

| Checkout | `origin` | `fork` | upstream is | our fork is |
|---|---|---|---|---|
| **Dev** (a laptop) | `NousResearch/hermes-agent` (upstream) | `Th0rgal/hermes-agent` (ours) | `origin` | `fork` |
| **Prod** (`agent-core:/usr/local/lib/hermes-agent`) | `Th0rgal/hermes-agent` (ours) | — | — | `origin` |

The commands below use the **dev** layout (`origin` = upstream, `fork` =
ours). On the prod host, `origin` already points at our fork — see
[Deploying](#deploying).

## Syncing with upstream

Run from a **dev** checkout (`origin` = upstream, `fork` = ours):

```bash
git fetch origin                        # NousResearch/hermes-agent (upstream)
git rebase origin/main production
# resolve conflicts; drop any delta commit upstream has absorbed
git push --force-with-lease fork production
```

A force-push of `production` to our fork is expected and safe: the prod
checkout hard-resets onto it. `--force-with-lease` protects against
clobbering a concurrent sync.

After the rebase, review the series with
`git log --oneline origin/main..production` — it should stay short. If a
delta stops applying because upstream shipped the same fix, delete the
commit rather than resolving it forward.

> **`production` is live.** It runs the `hermes-assistant` controller that
> drives sandboxed.sh missions. A rebase + force-push + redeploy interrupts
> whatever the controller is doing, so treat a full upstream sync (currently
> ~1000 commits behind) as a scheduled, supervised operation — never a
> background force-push. Small fork deltas (below) are safe any time.

## Adding a fork delta

Branch from `production`, open a PR against `production`, land it as one
commit per functional change with a `[fork-delta]` line in the message.
If the change is upstreamable, open the upstream PR too and note it in the
commit message; drop the local commit on the sync after it merges.

Merge PRs with **"Rebase and merge"** (repo settings disallow merge
commits): the series must stay linear or the upstream rebase turns into
merge-commit archaeology. If a merge commit slips in anyway, flatten with
`git rebase --force-rebase <base>` (tree-identical) and force-push.

## Deploying

Run on the **prod** host (`agent-core:/usr/local/lib/hermes-agent`), where
`origin` is our fork:

```bash
git fetch origin && git reset --hard origin/production
systemctl restart hermes-assistant hermes-dashboard
```

## Minimizing maintenance: what actually costs us at rebase

The recurring `fix(fork): reconcile delta series with upstream refactors`
commits are the tax. But the right metric is **edits to files that also exist
upstream** — NOT "lines the fork added". A **fork-added new file never
conflicts** on rebase (upstream doesn't have it), no matter how large. So the
~5.9k "core" lines split sharply:

- **New fork files — already conflict-free, moving them buys nothing.**
  `gateway/controller_events.py`, `tools/project_tools.py`,
  `hermes_cli/project_routes.py`, `hermes_cli/web_routers/missions.py`,
  `apps/desktop/src/store/session-unread.ts`, `apps/desktop/src/app/chat/mission-tag.tsx`,
  etc. Relocating these into plugins is organizational hygiene, not conflict
  reduction — don't spend rebase-budget effort on it.
- **Edits interleaved into upstream files — the real tax.** These are what to
  attack. Ordered by pain: `hermes_state.py` (~687), `tui_gateway/server.py`
  (~305), `cron/scheduler.py` (~519 of edits), `gateway/platforms/webhook.py`
  (~198), `apps/desktop/src/lib/chat-messages.ts`, `apps/desktop/src/app/chat/sidebar/index.tsx`,
  `gateway/delivery.py` (~86), plus one-liners (`web_server.py` `include_router`,
  `toolsets.py`, `tui_gateway` import of `set_project_workspace_callback`).

### The real lever: add a seam, then upstream it

For each hot upstream file, replace the interleaved edit with **one call into a
generic seam**, then open the seam upstream. Once upstream ships the seam, our
delta on that file disappears entirely. Highest value:

- **`gateway/delivery.py`** — DONE: the dormant `deliver_events` moved to
  `plugins/controller_events/`; the event contract stays in the leaf
  `controller_events.py`. Next: route the 4 `cron/scheduler.py` emit sites
  through an `invoke_hook('controller_event', …)` seam so scheduler.py stops
  importing the module (defer — scheduler.py runs the live controller; do it on
  a supervised gateway-redeploy window).
- **`web_server.py`** — add a `register_router` seam so a plugin can mount a
  prefix-less router (the dashboard-plugin seam force-prefixes `/api/plugins/<name>/`,
  which would break the fixed `/api/projects` paths). Then `missions.py` moves
  to a plugin with zero URL change. Upstream `register_router`.
- **Desktop** — `chat-messages.ts` fork functions and `sidebar/index.tsx` edits
  are the real desktop tax. `sidebar` edits already lean on shared atoms
  (`$sessionColorById`, `$projectBoundSessionIds`, `SESSIONS_SECTIONS_AREA`); a
  titlebar slot would move `mission-tag.tsx` off `app/chat/index.tsx`. Upstream
  the slots.

### Accept as irreducible (keep tight + well-commented)

`hermes_state.py` (session lineage/resume/trim invariants — no seam), `tui_gateway/server.py`,
`agent/conversation_loop.py`, `agent/system_prompt.py`, `lib/chat-messages.ts`
(shared lib, per-function interleaving), and the interleaved cron/webhook core:
durable-replay-into-SessionDB (`scheduler.py`), cron mirror, webhook
session-reaping.

Do this incrementally, one seam per fork-delta PR, verifying prod parity after
each — never a big-bang refactor + force-push of the live `production` branch.
