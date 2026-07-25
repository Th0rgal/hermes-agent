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

## Syncing with upstream

```bash
git fetch upstream            # NousResearch/hermes-agent
git rebase upstream/main production
# resolve conflicts; drop any delta commit upstream has absorbed
git push --force-with-lease origin production
```

The production checkout hard-resets onto the fetched branch, so a
force-push of `production` is expected and safe. `--force-with-lease`
protects against clobbering a concurrent sync.

After the rebase, review the series with
`git log --oneline upstream/main..production` — it should stay short. If a
delta stops applying because upstream shipped the same fix, delete the
commit rather than resolving it forward.

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

Production (`agent-core:/usr/local/lib/hermes-agent`) tracks
`origin/production`:

```bash
git fetch origin && git reset --hard origin/production
systemctl restart hermes-assistant hermes-dashboard
```
