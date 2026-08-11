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

## Minimizing maintenance: keep deltas in plugins

Every line we patch into an upstream core file is a line that can conflict on
the next rebase — the recurring `fix(fork): reconcile delta series with
upstream refactors` commits are that tax. The way to shrink it is to move
fork behaviour behind Hermes's **plugin seams** instead of editing core, so an
upstream refactor of core leaves our code untouched.

Roughly ~40% of the fork already lives in plugins (the whole **projects-board**
desktop UI + its `plugins/projects-board/dashboard/` proxy). That is the model
to replicate. Rough split of the remaining ~5.9k patched lines across ~77 core
files, and where they should go:

**Server (Python) — move behind `plugins/<name>/` + `register(ctx)` hooks:**
- **Project routing** — `hermes_cli/project_routes.py`, `hermes_cli/web_routers/missions.py`, `tools/project_tools.py` (~715 lines). Already cohesive new modules bolted onto core routers; should register their routes/tools through `PluginContext` from a `plugins/projects/` server plugin instead of being imported by core.
- **Structured controller events** — `gateway/controller_events.py` (~402, essentially a new file). Emit over an `invoke_hook` seam rather than a core gateway module.
- **Durable cron/callback delivery** — `cron/scheduler.py`, `cron/jobs.py`, `gateway/delivery.py`, `gateway/platforms/webhook.py`. Highest-conflict cron/gateway edits; lift the additive parts (delivery replay, session-origin) into `cron_providers` / a delivery plugin behind hooks.
- **MCP classification** (`tools/mcp_tool.py`, `agent/error_classifier.py`) and the **command secret source** (`agent/secret_sources/command.py`) — small, hook- or entry-point-plugin candidates.

**Desktop (renderer) — collapse into the `contrib` extension API:**
- `store/session-unread.ts`, `app/chat/mission-tag.tsx`, `store/session-color.ts`, `app/chat/session-status-dot.tsx` (~350 lines) are missions-board presentation leaking into core stores/components. Where the plugin can't reach them yet, add SDK/contrib extension points (the fork already added `$sessionColorById`, the `SESSIONS_SECTIONS_AREA` slot, `SessionStatusDot`) and move the logic into `apps/desktop/src/plugins/projects-board/`.

**Accept as irreducible core deltas — keep them tight and well-commented:**
- `hermes_state.py` (~687), `hermes_state_schema.py`, `hermes_state_common.py` — session lineage/resume/trim invariants live deep in the state monolith; no plugin seam exists.
- `tui_gateway/server.py` (~305), `agent/conversation_loop.py`, `agent/system_prompt.py`.

Do this incrementally, one cluster per fork-delta PR, verifying prod parity
after each — not as one big-bang refactor of the live `production` branch.
