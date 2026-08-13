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
3. `get_project_tasks <slug>` — the roadmap: board tasks planned by the
   project's boss missions plus chat-planned proposals (`status: "proposed"`),
   with result digests, PR links, attempts, and a done/total summary.

When summarizing, lead with what needs the owner (open decisions, blockers,
failed tasks), then progress (summary done/total), then what is running.

## Shaping the roadmap from conversation

- `plan_project_tasks` — add items. Keys are stable kebab-case, unique per
  project (`task_key`). A proposal is a *plan*, not dispatched work: the
  project's controller adopts it by planning a real board task under the same
  key, at which point the proposal drops out automatically.
- `update_project_task` — edit an open proposal (title, prompt, acceptance
  criteria, dependencies). Board tasks already adopted belong to their boss
  mission — steer the mission instead.
- `cancel_project_task` — remove an open proposal.

Prefer small, verifiable items with acceptance criteria over vague epics.
Re-planning an existing key updates it in place (idempotent), and revives it
if it was cancelled.

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
