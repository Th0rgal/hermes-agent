# Observer project controllers

Use observer mode when an existing technical owner, such as a durable local
agent, owns the project's goal and writers. Hermes can report status and open
questions through the existing project delivery route without managing that
owner's work. This policy uses the existing controller configuration and
roadmap; it introduces no second project state store.

Opt in on an existing agent job:

```json
{
  "deliver": "project:verity-lido",
  "controller": {
    "project": "verity-lido",
    "mode": "observer",
    "callback_relay": true,
    "permissions": ["sandboxed.read"],
    "repositories": ["lfglabs-dev/lido-srv3-proof-closure"],
    "reserved_local_areas": ["Existing technical owner's worktree and durable goal"]
  }
}
```

Omitting `mode`, or setting `"mode": "operator"`, preserves existing behavior.
Observer mode caps the permissions even if an old configuration still includes
`sandboxed.mutate`. It does not change the configured model, fallback chain,
agent profile, delivery route, or callback inbox. Keep the normal
`builtin/assistant` configuration and backups available.

Registry discovery exposes only tool discovery (`tool_search`, `tool_describe`,
`tool_call`) and the existing classified sandboxed status reads: project/task
and grant reads, exact-project mission lists, mission status/events/diagnostics
and shared-file listings, and workspace/fleet status. Mission-specific reads
resolve the actual mission project through the same MCP server and reject
unknown or different ownership. General workspace/fleet reads remain visible.
`download_shared_file` is excluded because it can materialize files.

All other model-invoked tools are refused, including mission launch/resume/
cancel/ACK, messages or answers to workers, project/task mutations, shell,
code execution, file tools, delegation, memory writes, cron changes, and other
MCP servers. The gate checks direct and deferred dispatch and the agent-owned
sequential/concurrent execution paths. An old advertised toolset cannot grant
these capabilities. Tools appended later by a trusted memory/context provider
are also refused at execution if outside this surface. Discovery caches distinguish observer and operator scopes;
the authority travels with the controller task through ContextVars.

Public assistant transcripts, saved job output and all delivery lanes also
neutralize the native `CTRL`, `STATE_SIGNATURE` and `DECISION` markers. Those
markers normally mutate the native project timeline or decision ledger without
a tool call. Observer reports preserve their text under inert labels and use
an `Observer report` delivery prefix; the native ingestor does not consume
them. Signature-based duplicate-report suppression still works locally. Tool
results, user content, reasoning, and operator reports retain their behavior.
This is a projection of new observer writes: existing stored history and live
in-memory conversation content are not rewritten, including cached prefixes.

Hourly and callback wakes use the same job policy. Callback text is data, not
authority to dispatch. A completed observer response can acknowledge the
existing callback inbox snapshot as delivered; it cannot ACK a native mission
or accept a proof track. Report material progress and questions in the final
response for normal project delivery. Keep the initial controller prompt within
the existing 16,000-character cap, including skills and callback snapshots.

The policy is bound when a job starts. Changing the configuration affects its
next run, not an already running controller. Activate at an existing idle
checkpoint and retain the existing delivery and callback configuration.
This change does not modify live jobs or services.

This is a bounded model tool policy, not a host security sandbox. Hermes core,
configured pre-run scripts, trusted plugins/middleware and MCP server
implementations remain trusted code. Do not configure an observer job with a
pre-run script that performs project work. The policy does not revoke authority
from other interactive sessions or the existing technical owner.
