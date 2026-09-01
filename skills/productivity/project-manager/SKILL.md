---
name: project-manager
description: Steer sandboxed.sh projects, roadmaps, decisions from chat.
version: 1.0.0
author: thomas
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  tools: [mcp]
metadata:
  hermes:
    tags: [Projects, Roadmap, Orchestration, Autonomy]
---

# Project Manager — projects, roadmaps, decisions

## When to Use

When the owner asks about their projects ("how is X going?", "what's on the
roadmap?", "what needs me?"), wants roadmap items added/edited/cancelled from
conversation, answers an escalated decision, or asks to change a project's
autonomy grant. Requires the `sandboxed_assistant` MCP server.

Use the `sandboxed_assistant` MCP tool family to talk about the owner's
projects with real data, never from memory. Every project is identified by its
`slug`.

## Reading state (do this before opining)

1. `list_projects` — the roster with buckets (attention/active/paused) and
   health. Start here when the owner asks "how are my projects doing?".
2. `get_project <slug>` — objective, status/mode, blocker, next action, grant,
   open decisions, recent decisions (the decision ledger), tracks.
3. `get_project_tasks <slug>` — the server-authoritative roadmap projection.
   `tasks` contains only declared, non-cancelled tracks; its
   `declared_total` denominator never includes ad-hoc missions.
   `unplanned_attempts` lists live undeclared work separately, while
   `inconsistencies` explains evidence/lifecycle debt.

When summarizing, lead with what needs the owner (open decisions, blockers,
failed tasks), then progress (summary done/total), then what is running.

## Shaping the roadmap from conversation

- `plan_project_tasks` — declare tracks. Keys are stable kebab-case, unique per
  project (`task_key`). Replanning preserves satisfied/cancelled lifecycle;
  it cannot silently reopen terminal work.
- `update_project_task` — revise a non-terminal track's title, desired state,
  acceptance criteria, or dependencies.
- `cancel_project_task` — cancel a non-terminal declared track.
- Project mission launches include `project`, `track`, acceptance criteria,
  and one stable `idempotency_key`. The backend atomically declares/revises
  the track, owns/links the attempt, and supersedes the prior owner.

Prefer small, verifiable items with acceptance criteria over vague epics.
Completion is evidence-derived. Use `accept_project_track_evidence` only for
an immutable receipt that satisfies the exact current criterion at the
governed artifact version. Mission self-report is not evidence. Reopen a
satisfied track only with `reopen_project_track`, recording the owner's real
reason; this advances the revision so old evidence cannot satisfy it.

## Decisions and autonomy

- `answer_project_decision` — resolve a pending escalation with the owner's
  verdict (only when the owner has actually decided in the conversation).
- `record_project_decision` — declare an act or escalate a question yourself
  when you are operating as the project's controller.
- `get_project_grant` / `set_project_grant` — the autonomy grant: level
  (observe/propose/act_reversible/act_full), merge authority
  (`full | repo:a,b | review-first`), budget per tick, parallel missions.
  Change it only on the owner's explicit request; it is guidance the
  controller follows, not a scheduler-enforced limit.

## Cross-project etiquette

These tools reach every project on the box. When the conversation is about
one project, do not mutate another one's state without naming it and getting
the owner's confirmation first. Reads are always fine.
