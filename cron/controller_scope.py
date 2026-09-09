"""Opt-in project authority for scheduled controllers, carried with the task.

This bounds sandboxed MCP dispatch. Repository and reserved-local-area declarations
are prompt guidance; they do not claim to sandbox arbitrary terminal/file tools.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import re
from typing import Callable, Iterator


CONTROLLER_PROMPT_MAX_CHARS = 16_000
_PERMISSIONS = frozenset({"sandboxed.read", "sandboxed.mutate"})
_PROJECT_READS = frozenset({"get_project", "get_situation", "get_project_tasks", "get_project_grant"})
_PROJECT_WRITES = frozenset({
    "update_project_status", "set_project_track", "accept_project_track_evidence",
    "reopen_project_track", "accept_project_track", "invalidate_project_track_evidence",
    "set_project_grant", "record_project_decision", "answer_project_decision",
    "plan_project_tasks", "update_project_task", "cancel_project_task", "link_mission_to_project",
})
_MISSION_READS = frozenset({
    "get_mission", "get_mission_digest", "get_mission_events", "get_mission_health",
    "get_mission_diagnostics", "list_mission_shared_files", "download_shared_file",
})
_MISSION_WRITES = frozenset({
    "send_message_to_mission", "ask_mission", "answer_mission_question",
    "cancel_mission", "acknowledge_mission", "adopt_mission", "update_mission_settings",
    "resume_mission", "link_mission_to_project",
})
_GLOBAL_READS = frozenset({
    "get_chatgpt_ui_pool_status", "get_compute_fleet", "list_projects", "list_workspaces",
    "get_workspace", "list_workspace_templates", "get_workspace_template", "get_workspace_job",
})
_MISSION_LISTS = frozenset({"list_missions", "list_active_missions"})
_SANDBOXED_TOOL = re.compile(r"^(mcp__(?:sandboxed|sandboxed_[A-Za-z0-9_]+)__)([A-Za-z0-9_]+)$")


class ControllerScopeError(ValueError):
    """A controller configuration or tool call cannot prove its project authority."""


def _canonical_project(value: object, aliases: dict[str, str]) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value.strip().lower()):
        raise ControllerScopeError("A nonempty canonical project slug is required")
    slug = value.strip().lower()
    seen = set()
    while slug in aliases and aliases[slug] != slug:
        if slug in seen:
            raise ControllerScopeError("Controller project aliases contain a cycle")
        seen.add(slug)
        slug = aliases[slug]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        raise ControllerScopeError("Controller project alias is not a canonical slug")
    return slug


def _controller_config(job: dict) -> dict | None:
    config = job.get("controller")
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ControllerScopeError("job.controller must be an object")
    return config


def _project_for_job(job: dict, config: dict, aliases: dict[str, str]) -> str:
    projects = []
    if "project" in config:
        projects.append(_canonical_project(config["project"], aliases))
    deliver = job.get("deliver") or ""
    targets = deliver if isinstance(deliver, (list, tuple)) else str(deliver).split(",")
    for target in targets:
        if isinstance(target, str) and target.strip().startswith("project:"):
            projects.append(_canonical_project(target.strip().split(":", 1)[1], aliases))
    if not projects or len(set(projects)) != 1:
        raise ControllerScopeError("Controller project and project delivery must identify one project")
    return projects[0]


def controller_project(job: dict) -> str | None:
    """Canonical callback/run binding; no active-session or name-based fallback."""
    config = _controller_config(job)
    if config is None:
        return None
    from hermes_cli.projects_db import _project_alias_map

    return _project_for_job(job, config, _project_alias_map())


@dataclass(frozen=True)
class ControllerScope:
    job_id: str
    project: str
    repositories: tuple[str, ...]
    permissions: tuple[str, ...]
    reserved_local_areas: tuple[str, ...]
    aliases: tuple[tuple[str, str], ...]

    def canonical(self, value: object) -> str:
        return _canonical_project(value, dict(self.aliases))

    def prompt_prefix(self) -> str:
        context = {
            "job_id": self.job_id,
            "project": self.project,
            "repositories": self.repositories,
            "permissions": self.permissions,
            "reserved_local_areas": self.reserved_local_areas,
        }
        return (
            "[CONTROLLER CONTEXT]\n" + json.dumps(context, ensure_ascii=False, sort_keys=True) + "\n"
            "Act only for this project. Keep reserved local areas with their existing owners. "
            "Repository and local-area declarations guide your work; sandboxed MCP project and "
            "permission checks are enforced at dispatch. Unknown mission ownership is not authority.\n"
            "[/CONTROLLER CONTEXT]\n\n"
        )


def scope_from_job(job: dict) -> ControllerScope | None:
    config = _controller_config(job)
    if config is None:
        return None
    from hermes_cli.projects_db import _project_alias_map

    aliases = _project_alias_map()
    project = _project_for_job(job, config, aliases)
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ControllerScopeError("Controller requires a stable job id")
    fields = {}
    for name in ("repositories", "permissions", "reserved_local_areas"):
        value = config.get(name)
        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
            raise ControllerScopeError(f"controller.{name} must be an explicit array of nonempty strings")
        fields[name] = tuple(v.strip() for v in value)
    if set(fields["permissions"]) - _PERMISSIONS:
        raise ControllerScopeError("Controller permissions must be sandboxed.read and/or sandboxed.mutate")
    if job.get("no_agent"):
        raise ControllerScopeError("A scoped controller requires an agent job")
    return ControllerScope(job_id=job_id, project=project, aliases=tuple(sorted(aliases.items())), **fields)


_current_scope: ContextVar[ControllerScope | None] = ContextVar("controller_project_scope", default=None)


def current_controller_scope() -> ControllerScope | None:
    return _current_scope.get()


@contextmanager
def bind_controller_scope(scope: ControllerScope | None) -> Iterator[None]:
    token = _current_scope.set(scope)
    try:
        yield
    finally:
        _current_scope.reset(token)


def check_prompt_budget(scope: ControllerScope | None, prompt: str) -> str:
    if scope is not None and len(prompt) > CONTROLLER_PROMPT_MAX_CHARS:
        raise ControllerScopeError(
            f"Controller initial prompt is {len(prompt)} chars; maximum is "
            f"{CONTROLLER_PROMPT_MAX_CHARS} including context and preloaded skills"
        )
    return prompt


def _readback_object(result: object) -> dict:
    # Registry MCP handlers wrap JSON text as {"result": "..."}. This lookup
    # occurs before model-facing untrusted-result framing, so no HTML stripping.
    for _ in range(5):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError as exc:
                raise ControllerScopeError("Mission project lookup returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ControllerScopeError("Mission project lookup returned no object")
        if result.get("error") or result.get("isError") or result.get("is_error") or result.get("success") is False:
            detail = str(result.get("error") or result.get("message") or "unsuccessful response")[:500]
            raise ControllerScopeError(f"Mission project lookup failed: {detail}")
        if "result" in result:
            result = result["result"]
        elif "mission" in result:
            result = result["mission"]
        else:
            return result
    raise ControllerScopeError("Mission project lookup returned excessive wrapping")


def guard_sandboxed_call(
    tool_name: str,
    args: dict,
    resolve: Callable[[str, dict], object],
) -> dict:
    """Validate final dispatch arguments and pin resolved mission IDs.

    Reads used to authorize mutations go straight to the same registered MCP
    server, with no global cache and no model-supplied ownership fallback.
    """
    scope = current_controller_scope()
    match = _SANDBOXED_TOOL.fullmatch(tool_name)
    if scope is None or match is None:
        return args
    prefix, name = match.groups()
    allowed = _PROJECT_READS | _PROJECT_WRITES | _MISSION_READS | _MISSION_WRITES | _GLOBAL_READS | _MISSION_LISTS | {"start_mission"}
    if name not in allowed:
        raise ControllerScopeError(f"Sandboxed tool '{name}' has no project-scoped controller policy")
    if "sandboxed.read" not in scope.permissions:
        raise ControllerScopeError("Controller lacks sandboxed.read permission")
    if name in _PROJECT_WRITES | _MISSION_WRITES | {"start_mission"} and "sandboxed.mutate" not in scope.permissions:
        raise ControllerScopeError("Controller lacks sandboxed.mutate permission")
    checked = dict(args)

    def require_project(value: object) -> None:
        if scope.canonical(value) != scope.project:
            raise ControllerScopeError(f"Controller job '{scope.job_id}' is restricted to project '{scope.project}'")

    for key in ("project", "project_id", "project_slug", "slug"):
        if key in checked:
            require_project(checked[key])
            checked[key] = scope.project
    identity = checked.get("continue_identity")
    if isinstance(identity, dict) and "project" in identity:
        require_project(identity["project"])
        checked["continue_identity"] = {**identity, "project": scope.project}
    if "project_prefix" in checked:
        raise ControllerScopeError("Controller requires an exact project filter, not project_prefix")
    if name in _PROJECT_READS | _PROJECT_WRITES:
        require_project(checked.get("slug"))
        checked["slug"] = scope.project
    if name in _MISSION_LISTS | {"start_mission"}:
        require_project(checked.get("project"))
        checked["project"] = scope.project

    mission_fields = []
    if name in _MISSION_WRITES:
        mission_fields.append("mission_id")
    if "supersedes_mission_id" in checked:
        mission_fields.append("supersedes_mission_id")
    for field in mission_fields:
        mission_id = checked.get(field)
        if not isinstance(mission_id, str) or not mission_id.strip():
            raise ControllerScopeError(f"Controller requires {field} before a mission mutation")
        mission_id = mission_id.strip()
        try:
            mission = _readback_object(resolve(prefix + "get_mission_digest", {"mission_id": mission_id}))
        except ControllerScopeError:
            raise
        except Exception as exc:
            raise ControllerScopeError(f"Mission project lookup failed: {type(exc).__name__}: {str(exc)[:500]}") from exc
        resolved_id = mission.get("id") or mission.get("mission_id")
        if not isinstance(resolved_id, str) or not (
            resolved_id == mission_id or (len(mission_id) >= 8 and resolved_id.startswith(mission_id))
        ):
            raise ControllerScopeError("Mission project lookup returned a different or missing mission identity")
        project = mission.get("project")
        if isinstance(project, dict):
            project = project.get("project")
        require_project(project)
        checked[field] = resolved_id
    return checked
