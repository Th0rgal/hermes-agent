#!/usr/bin/env bash
set -uo pipefail

# A label event can wait while a newer commit replaces its candidate. Rerunning
# the old candidate would cancel the current head's ref-scoped workflows.
require_current_head() {
  local live_head
  live_head=$(gh api "repos/$REPO/pulls/$PR_NUMBER" --jq '.head.sha') || {
    echo "Unable to verify the current PR head; refusing to rerun CI." >&2
    exit 1
  }
  if [ "$live_head" != "$HEAD_SHA" ]; then
    echo "PR head changed; leaving its current CI run alone."
    exit 0
  fi
}

require_current_head

# Find the latest CI run for this PR's head SHA.
RUN_INFO=$(gh run list \
  --repo "$REPO" \
  --commit "$HEAD_SHA" \
  --workflow ci.yaml \
  --limit 1 \
  --json databaseId,status \
  --jq '.[0] | "\(.databaseId) \(.status)"' 2>/dev/null || true)

if [ -z "$RUN_INFO" ]; then
  echo "No CI run found for this PR — nothing to rerun."
  exit 0
fi

# Split "RUN_ID STATUS" into two vars. Read STATUS from RUN_INFO,
# not from the truncated RUN_ID. Both values came from the same
# var before, which made STATUS the run id. Thus the wait branch
# always ran.
RUN_ID="${RUN_INFO%% *}"
STATUS="${RUN_INFO##* }"

echo "Latest CI run: $RUN_ID (status: $STATUS)"

# If the run is still in progress, wait for it to finish.
# gh run rerun only works on completed runs — if we try while it's
# running, GitHub rejects with "cannot be rerun; This workflow is
# already running".
if [ "$STATUS" != "completed" ]; then
  echo "Run is $STATUS — waiting for completion (this may take a while)..."
  # gh run watch --exit-status exits non-zero if the run fails,
  # which is expected (the label gate fails). Don't let that kill
  # the workflow — we WANT to rerun failed jobs.
  timeout 2100 gh run watch "$RUN_ID" --repo "$REPO" --interval 15 || true

  # Verify it's actually completed now.
  STATUS=$(gh run view "$RUN_ID" --repo "$REPO" --json status --jq '.status' 2>/dev/null || echo "unknown")
  if [ "$STATUS" != "completed" ]; then
    echo "Run is still $STATUS after wait — giving up."
    exit 0
  fi
fi

require_current_head

echo "Run completed. Rerunning all failed jobs..."
gh run rerun "$RUN_ID" --repo "$REPO" --failed || true
echo "Done. GitHub will rerun review-labels and all dependent jobs."
