"""Desktop Projects tools, registered from a plugin.

The ``project_*`` MCP tools used to self-register at import time via
``registry.register(...)`` calls at the top of ``tools/project_tools.py`` (found
by ``discover_builtin_tools``). Their *implementations* stay in that fork-added
module (a new file, conflict-free); this plugin owns the *registration* so the
tool wiring lives behind ``register(ctx)`` — the plugin-native seam — instead of
the builtin registry.

``set_project_workspace_callback`` is intentionally left importable from
``tools.project_tools`` so ``tui_gateway/server.py`` keeps its existing import
unchanged (no edit to that upstream-heavy file).
"""

from __future__ import annotations

from tools.project_tools import (
    project_create,
    project_list,
    project_route_clear,
    project_route_get,
    project_route_set,
    project_switch,
)


def register(ctx) -> None:
    ctx.register_tool(
        name="project_list",
        toolset="project",
        schema={
            "name": "project_list",
            "description": "List the desktop Projects (named workspaces) and which one is active.",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=lambda args, **kw: project_list(task_id=kw.get("task_id")),
    )
    ctx.register_tool(
        name="project_create",
        toolset="project",
        schema={
            "name": "project_create",
            "description": (
                "Create a desktop Project (a named workspace) and switch this chat into it. "
                "Pass `path` to anchor it to a repo/folder — this chat's workspace moves there "
                "and the sidebar follows. Use when starting work in a new repo/folder; this is "
                "the intentional way to move the session, not `cd`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human name, e.g. 'Aurora Demo'"},
                    "path": {"type": "string", "description": "Primary repo/folder to anchor the project to"},
                },
                "required": ["name"],
            },
        },
        handler=lambda args, **kw: project_create(
            name=args.get("name", ""), path=args.get("path"), task_id=kw.get("task_id")
        ),
    )
    ctx.register_tool(
        name="project_switch",
        toolset="project",
        schema={
            "name": "project_switch",
            "description": (
                "Switch this chat into an existing desktop Project (by name, slug, or id). "
                "Moves the session's workspace to the project's primary folder and the sidebar "
                "follows. The intentional way to move between projects, not `cd`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name, slug, or id"},
                },
                "required": ["project"],
            },
        },
        handler=lambda args, **kw: project_switch(project=args.get("project", ""), task_id=kw.get("task_id")),
    )
    ctx.register_tool(
        name="project_route_set",
        toolset="project",
        schema={
            "name": "project_route_set",
            "description": (
                "Explicitly bind a Project to a durable session so cron deliveries "
                "(deliver=project:<slug>) and notifications land in that exact "
                "conversation. Defaults to THIS session when session_id is omitted. "
                "Routes are explicit-or-nothing: without one, project-targeted "
                "deliveries fail instead of guessing the current desktop window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name, slug, or id"},
                    "session_id": {"type": "string", "description": "Durable session id (defaults to the calling session)"},
                },
                "required": ["project"],
            },
        },
        handler=lambda args, **kw: project_route_set(
            project=args.get("project", ""),
            session_id=args.get("session_id"),
            task_id=kw.get("task_id"),
        ),
    )
    ctx.register_tool(
        name="project_route_get",
        toolset="project",
        schema={
            "name": "project_route_get",
            "description": "Show the explicit session route bound to a Project (or null when unbound).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name, slug, or id"},
                },
                "required": ["project"],
            },
        },
        handler=lambda args, **kw: project_route_get(project=args.get("project", ""), task_id=kw.get("task_id")),
    )
    ctx.register_tool(
        name="project_route_clear",
        toolset="project",
        schema={
            "name": "project_route_clear",
            "description": (
                "Remove a Project's explicit session route. Project-targeted "
                "deliveries will fail until a new route is bound — they never fall "
                "back to the current desktop session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name, slug, or id"},
                },
                "required": ["project"],
            },
        },
        handler=lambda args, **kw: project_route_clear(project=args.get("project", ""), task_id=kw.get("task_id")),
    )
