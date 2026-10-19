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

## Delegating from an Orb host session

For a child created through `create_worker_mission`, keep `parent_mission_id` lineage: the server inherits the parent's actual machine and working directory. `create_worker_mission` accepts an optional existing `working_directory` and a stable `idempotency_key`; reuse the key after a lost response. Changing a workspace is not a request to move to another machine. Explicit remote execution uses `remote_node_id` (or `placement: "core"` for the API host).

Git is optional. Prepare separate Git worktrees only when concurrent repository writers need them; otherwise assign disjoint files or serialize writes in an ordinary directory. Pass context and skill source paths in the assignment, and have the worker check its available tools. Do not assume the parent's MCP inventory or loaded skills are copied automatically. `start_mission` through the assistant MCP remains an explicitly targeted executor launch, not an Orb child launch.

### Hosted cloud attempts

Discover accounts and effective capabilities with `list_cloud_accounts`. An
available account can be selected through `start_mission.cloud` with provider
`chatgpt`, `grok_bot`, or `cursor_cloud`, an account reference, optional supported
model/repository/ref, and a stable `idempotency_key`. Never send credentials or
local directory contents in that selection. Connecting an account starts no work.

Read `get_cloud_execution` for durable provider identities, turns and results.
A transport disconnect is not completion; `submission_uncertain` requires
reconciliation before a new submission. Continue the same mission using
`send_message_to_mission` and a stable `client_message_id` UUID. Response completion,
archive and track acceptance are distinct. Cancellation must be provider-confirmed.

The initial hosted route does not implement track leases, writer grants or
scheduled admission: do not use it for autonomous track dispatch. Unsupported
connectors remain unavailable until their bounded real-provider canaries pass.

### Cloud model selection and verified Grok Bot

Use `list_cloud_models` with `provider: chatgpt | cursor_cloud` before selecting a model. Creation accepts `cloud.model` and Cursor `cloud.model_params: [{id,value}]`. Follow-ups accept `cloud_model` and `cloud_model_params` in `send_message_to_mission`; these settings belong to that turn, not earlier turns. Keep the same `client_message_id` on retries, including the same model parameters. Grok Bot has no model selector or attachment capability. Its dedicated Bots share the account computer: archiving an Orb mission must not delete provider files. Never replace an uncertain submission with a new mission.
