"""Controller jobs exercise the real scheduler, registry and deferred dispatch."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from cron import scheduler
from cron.controller_scope import (
    ControllerScopeError, bind_controller_scope, controller_project,
    current_controller_scope, scope_from_job,
)
import model_tools
from tools.registry import registry


PREFIX = "mcp__sandboxed_scope_test__"
TOOLSET = "sandboxed_scope_test"
MISSIONS = {
    "11111111-1111-1111-1111-111111111111": "verity-lido",
    "22222222-2222-2222-2222-222222222222": "eip-7702",
}


def job(project="verity-lido", **updates):
    return {
        "id": "controller-" + project, "name": project, "prompt": "Inspect the project.",
        "deliver": "local", "model": "test-model", "schedule_display": "manual",
        "controller": {
            "project": project, "repositories": ["example/" + project],
            "permissions": ["sandboxed.read", "sandboxed.mutate"],
            "reserved_local_areas": ["local-proof-owner"],
        }, **updates,
    }


@pytest.fixture
def mcp_tools():
    calls = []
    overrides = {}
    names = []
    properties = {
        "slug": {"type": "string"}, "project": {"type": "string"},
        "mission_id": {"type": "string"}, "supersedes_mission_id": {"type": "string"},
        "content": {"type": "string"}, "title": {"type": "string"},
        "prompt": {"type": "string"}, "continue_identity": {"type": "object"},
    }

    def register(name):
        full = PREFIX + name

        async def handler(args, **kwargs):
            await asyncio.sleep(0)
            calls.append((name, dict(args), current_controller_scope()))
            if name == "get_mission_digest":
                if overrides:
                    result = overrides["result"]
                    if isinstance(result, Exception):
                        raise result
                    return result if isinstance(result, str) else json.dumps(result)
                mission_id = next((key for key in MISSIONS if key.startswith(args["mission_id"])), args["mission_id"])
                return json.dumps({"result": json.dumps({
                    "id": mission_id, "project": {"project": MISSIONS.get(mission_id)},
                })})
            return json.dumps({"ok": True, "args": args})

        registry.register(
            name=full, toolset=TOOLSET, handler=handler, is_async=True,
            schema={"name": full, "description": name,
                    "parameters": {"type": "object", "properties": properties}},
        )
        names.append(full)

    for name in (
        "get_situation", "plan_project_tasks", "start_mission", "get_mission_digest",
        "resume_mission", "cancel_mission", "acknowledge_mission", "send_message_to_mission",
        "ask_mission", "answer_mission_question", "update_mission_settings", "adopt_mission",
        "link_mission_to_project", "list_missions", "get_compute_fleet", "workspace_bash",
        "future_unknown_mutation", "get_mission_health", "get_project_tasks", "get_project",
        "download_shared_file", "accept_project_track", "update_project",
    ):
        register(name)
    yield calls, overrides
    for name in names:
        registry.deregister(name)


def call(name, args, deferred=False):
    return json.loads(model_tools.handle_function_call(
        "tool_call" if deferred else PREFIX + name,
        {"name": PREFIX + name, "arguments": args} if deferred else args,
        enabled_toolsets=[TOOLSET],
    ))


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("name, args", [
    ("get_situation", {"slug": "eip-7702"}),
    ("plan_project_tasks", {"slug": "eip-7702"}),
    ("start_mission", {"project": "eip-7702", "title": "x", "prompt": "x"}),
    ("resume_mission", {"mission_id": list(MISSIONS)[1]}),
    ("start_mission", {"project": "verity-lido", "supersedes_mission_id": list(MISSIONS)[1]}),
    ("resume_mission", {"mission_id": list(MISSIONS)[0], "continue_identity": {"project": "eip-7702"}}),
])
def test_cross_project_refused_before_target_handler(mcp_tools, deferred, name, args):
    with bind_controller_scope(scope_from_job(job())):
        result = call(name, args, deferred)
    assert "restricted to project 'verity-lido'" in result["error"]
    assert all(entry[0] == "get_mission_digest" for entry in mcp_tools[0])


@pytest.mark.parametrize("name", [
    "resume_mission", "cancel_mission", "acknowledge_mission", "send_message_to_mission",
    "ask_mission", "answer_mission_question", "update_mission_settings", "adopt_mission",
    "link_mission_to_project",
])
def test_each_mission_mutation_checks_actual_owner(mcp_tools, name):
    args = {"mission_id": list(MISSIONS)[1], "slug": "verity-lido"}
    with bind_controller_scope(scope_from_job(job())):
        assert "error" in call(name, args)
    assert [entry[0] for entry in mcp_tools[0]] == ["get_mission_digest"]


@pytest.mark.parametrize("readback, expected", [
    ({"id": list(MISSIONS)[0]}, "nonempty canonical project"),
    ({"id": list(MISSIONS)[0], "project": None}, "nonempty canonical project"),
    ({"id": list(MISSIONS)[0], "project": {}}, "nonempty canonical project"),
    ({"project": "verity-lido"}, "mission identity"),
    ({"id": list(MISSIONS)[1], "project": "verity-lido"}, "mission identity"),
    ({"error": "upstream unavailable"}, "upstream unavailable"),
    ({"isError": True, "result": "no access"}, "lookup failed"),
    ({"success": False, "message": "denied"}, "denied"),
    ("not JSON", "invalid JSON"),
    ([], "no object"),
    (TimeoutError("ownership timeout"), "ownership timeout"),
])
def test_unknown_ownership_and_lookup_errors_fail_closed(mcp_tools, readback, expected):
    mcp_tools[1]["result"] = readback
    with bind_controller_scope(scope_from_job(job())):
        result = call("resume_mission", {"mission_id": list(MISSIONS)[0]}, deferred=True)
    assert expected in result["error"]
    assert [entry[0] for entry in mcp_tools[0]] == ["get_mission_digest"]


def test_pins_resolved_id_and_never_caches_ownership(mcp_tools):
    args = {"mission_id": "11111111"}
    with bind_controller_scope(scope_from_job(job())):
        first = call("resume_mission", args)
        mcp_tools[1]["result"] = {"id": list(MISSIONS)[0], "project": "eip-7702"}
        second = call("resume_mission", args)
    assert first["args"]["mission_id"] == list(MISSIONS)[0]
    assert args == {"mission_id": "11111111"}
    assert "error" in second
    assert [entry[0] for entry in mcp_tools[0]] == ["get_mission_digest", "resume_mission", "get_mission_digest"]


@pytest.mark.parametrize("name, args", [
    ("start_mission", {}), ("list_missions", {}), ("resume_mission", {}),
    ("workspace_bash", {"command": "true"}), ("future_unknown_mutation", {}),
    ("list_missions", {"project": "verity-lido", "project_prefix": "verity"}),
])
def test_no_guessed_project_or_unclassified_mutation(mcp_tools, name, args):
    with bind_controller_scope(scope_from_job(job())):
        assert "error" in call(name, args)
    assert not mcp_tools[0]


def test_read_permission_and_final_middleware_arguments(mcp_tools, monkeypatch):
    config = job()
    config["controller"]["permissions"] = ["sandboxed.read"]
    with bind_controller_scope(scope_from_job(config)):
        assert call("get_situation", {"slug": "verity-lido"})["ok"]
        assert call("get_compute_fleet", {})["ok"]
        assert "sandboxed.mutate" in call("plan_project_tasks", {"slug": "verity-lido"})["error"]

    def middleware(_name, args, dispatch, **kwargs):
        return dispatch({**args, "slug": "eip-7702"})

    monkeypatch.setattr("hermes_cli.middleware.run_tool_execution_middleware", middleware)
    with bind_controller_scope(scope_from_job(job())):
        assert "restricted" in call("plan_project_tasks", {"slug": "verity-lido"})["error"]
    assert [entry[0] for entry in mcp_tools[0]] == ["get_situation", "get_compute_fleet"]


def test_normal_session_has_no_scope_or_ownership_lookup(mcp_tools):
    assert current_controller_scope() is None
    assert call("resume_mission", {"mission_id": "unknown"}, deferred=True)["ok"]
    assert call("future_unknown_mutation", {})["ok"]
    assert [entry[0] for entry in mcp_tools[0]] == ["resume_mission", "future_unknown_mutation"]


def test_canonical_config_delivery_and_explicit_fields(tmp_path, monkeypatch, mcp_tools):
    routes = tmp_path / "routes"
    routes.mkdir()
    (routes / "routes.json").write_text(json.dumps({"lido": "verity-lido"}))
    monkeypatch.setenv("HERMES_PROJECTS_DIR", str(routes))
    config = job(deliver="project:lido")
    assert controller_project(config) == "verity-lido"
    del config["controller"]["project"]
    scope = scope_from_job(config)
    with bind_controller_scope(scope):
        assert call("get_situation", {"slug": "lido"})["args"]["slug"] == "verity-lido"
    config["deliver"] = ["project:lido", "project:eip-7702"]
    with pytest.raises(ControllerScopeError, match="one project"):
        scope_from_job(config)
    config = job()
    del config["controller"]["reserved_local_areas"]
    with pytest.raises(ControllerScopeError, match="explicit array"):
        scope_from_job(config)


def test_stable_context_precedes_real_preloaded_skill_and_budget(tmp_path, monkeypatch):
    import tools.skills_tool as skills_tool
    from agent.prompt_cache_boundary import find_stable_prefix

    skills_dir = tmp_path / "skills"
    skill = skills_dir / "controller-test" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: controller-test\ndescription: Controller test\n---\n\nInspect receipts carefully.")
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", skills_dir)
    config = job(skills=["controller-test"])
    first = scheduler._build_job_prompt(config, extra_prompt="First callback")
    second = scheduler._build_job_prompt(config, extra_prompt="Next callback")
    assert first.startswith("[CONTROLLER CONTEXT]")
    assert "Inspect receipts carefully." in first
    assert first.index('"job_id"') < first.index("Inspect receipts carefully.")
    assert find_stable_prefix(first) == find_stable_prefix(second)
    assert find_stable_prefix(first) is not None
    skill.write_text(skill.read_text() + " safe context" * 1500)
    with pytest.raises(ControllerScopeError, match="maximum is 16000.*preloaded skills"):
        scheduler._build_job_prompt(config)
    ordinary = {"id": "ordinary", "prompt": "unchanged"}
    plain = scheduler._build_job_prompt(ordinary)
    assert "CONTROLLER CONTEXT" not in plain
    assert scheduler._build_job_prompt(ordinary) == plain


@pytest.fixture
def local_scheduler(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model:\n  default: test-model\ncron:\n  preflight: false\n")
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: {
        "api_key": "test-key", "provider": "test-provider", "base_url": None,
        "api_mode": None, "command": None, "args": None,
    })
    monkeypatch.setattr("tools.mcp_tool.discover_mcp_tools", lambda: [])
    monkeypatch.setattr(scheduler, "get_fallback_chain", lambda cfg: [])
    monkeypatch.setattr(scheduler, "_guard_job_credential_exfil", lambda config: None)
    # Real session DB, local provider boundary. No provider or MCP network call.
    return tmp_path


def test_two_concurrent_real_scheduler_jobs_keep_scope_through_async_tools(local_scheduler, mcp_tools, monkeypatch):
    barrier = threading.Barrier(2)

    class LocalAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs["session_id"]

        def run_conversation(self, prompt, *, task_id):
            scope = current_controller_scope()
            assert scope and scope.job_id in prompt and scope.project in prompt
            barrier.wait(timeout=10)
            other = next(project for project in MISSIONS.values() if project != scope.project)
            assert call("get_situation", {"slug": scope.project}, deferred=True)["ok"]
            assert "error" in call("plan_project_tasks", {"slug": other})
            mission = next(key for key, project in MISSIONS.items() if project == scope.project)
            assert call("resume_mission", {"mission_id": mission})["ok"]
            return {"completed": True, "failed": False, "final_response": scope.project}

        def close(self):
            pass

    monkeypatch.setattr("run_agent.AIAgent", LocalAgent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(scheduler.run_job, [job("verity-lido"), job("eip-7702")]))
    assert all(result[0] and result[3] is None for result in results), results
    assert {result[2] for result in results} == set(MISSIONS.values())
    for name, args, scope in mcp_tools[0]:
        assert scope is not None
        if name == "get_situation":
            assert args["slug"] == scope.project
        else:
            assert MISSIONS[args["mission_id"]] == scope.project
    assert current_controller_scope() is None


def test_rejected_controller_never_constructs_agent(local_scheduler, monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("Agent must not be constructed for rejected controller context")

    monkeypatch.setattr("run_agent.AIAgent", unexpected)
    for config in [job(prompt="safe context " * 1600), job(deliver="project:eip-7702")]:
        success, _, final, error = scheduler.run_job(config)
        assert success is False and error and not final
        assert current_controller_scope() is None


@pytest.mark.parametrize("agent_result, expected_success, pending_count", [
    ({"completed": True, "final_response": "Handled callback"}, True, 1),
    ({"completed": False, "final_response": "Progress summary",
      "turn_exit_reason": "max_iterations_reached(4)"}, True, 2),
    ({"completed": True, "final_response": ""}, True, 2),
    ({"completed": False, "failed": True, "error": "provider failed"}, False, 2),
])
def test_scheduler_acknowledges_only_completed_snapshot(
    local_scheduler, monkeypatch, agent_result, expected_success, pending_count,
):
    from cron import controller_callbacks as callbacks, jobs

    stored = jobs.create_job("Handle native callback", "0 * * * *", deliver="local")
    binding = {**job()["controller"], "callback_relay": True}
    config = jobs.update_job(stored["id"], {"controller": binding, "model": "test-model"})
    event = {"mission_id": list(MISSIONS)[0], "project": "verity-lido", "run_id": "run-1",
             "status": "completed", "event_id": "first", "summary": "Review candidate"}
    first = callbacks.enqueue_mission_callback(event)

    class LocalAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs["session_id"]

        def run_conversation(self, prompt, *, task_id):
            assert f"controller:{stored['id']}:{first['event_id']}" in prompt
            assert len(prompt) <= 16_000
            assert current_controller_scope().project == "verity-lido"
            callbacks.enqueue_mission_callback({**event, "event_id": "arrived-during-run"})
            return agent_result

        def close(self):
            pass

    monkeypatch.setattr("run_agent.AIAgent", LocalAgent)
    result = scheduler.run_job(config)
    assert result[0] is expected_success, result
    rows = jobs.load_jobs()
    assert len(rows) == 1
    pending = [entry for entry in rows[0]["controller_callbacks"] if not entry.get("handled_at")]
    assert len(pending) == pending_count
    assert any(entry["native_event_id"] == "arrived-during-run" for entry in pending)
    assert current_controller_scope() is None


def test_callback_store_failure_prevents_agent_and_keeps_scope_restored(local_scheduler, monkeypatch):
    from cron import controller_callbacks as callbacks

    def fail_snapshot(_job_id):
        raise OSError("inbox unavailable")

    def unexpected(**kwargs):
        pytest.fail("Callback snapshot failure must not start the agent")

    monkeypatch.setattr(callbacks, "pending_callbacks", fail_snapshot)
    monkeypatch.setattr("run_agent.AIAgent", unexpected)
    config = job()
    config["controller"]["callback_relay"] = True
    result = scheduler.run_job(config)
    assert result[0] is False and "inbox unavailable" in result[3]
    assert current_controller_scope() is None


def observer_job(project="verity-lido", **updates):
    config = job(project, **updates)
    # Deliberately retain an old operator permission list: mode must cap it.
    config["controller"]["mode"] = "observer"
    return config


@pytest.mark.parametrize("mode", [None, "observe", True, [], {}])
def test_invalid_controller_mode_is_rejected(mode):
    config = job()
    config["controller"]["mode"] = mode
    with pytest.raises(ControllerScopeError, match="controller.mode"):
        scope_from_job(config)


def test_default_operator_keeps_prompt_and_permissions():
    implicit = scope_from_job(job())
    config = job()
    config["controller"]["mode"] = "operator"
    explicit = scope_from_job(config)
    assert implicit.mode == "operator"
    assert explicit.prompt_prefix() == implicit.prompt_prefix()
    assert "sandboxed.mutate" in implicit.permissions
    observer = scope_from_job(observer_job())
    assert observer.permissions == ("sandboxed.read",)
    assert '"mode": "observer"' in observer.prompt_prefix()


@pytest.mark.parametrize("deferred", [False, True])
def test_observer_reads_status_and_resolves_mission_owner(mcp_tools, deferred):
    with bind_controller_scope(scope_from_job(observer_job())):
        assert call("get_situation", {"slug": "verity-lido"}, deferred)["ok"]
        assert call("get_project_tasks", {"slug": "verity-lido"}, deferred)["ok"]
        assert call("get_compute_fleet", {}, deferred)["ok"]
        assert call("list_missions", {"project": "verity-lido"}, deferred)["ok"]
        assert call("get_mission_health", {"mission_id": "11111111"}, deferred)["args"]["mission_id"] == list(MISSIONS)[0]
        assert "restricted" in call("get_mission_health", {"mission_id": list(MISSIONS)[1]}, deferred)["error"]
        assert "canonical project" in call("get_mission_health", {"mission_id": "unknown-id"}, deferred)["error"]
    assert [args["mission_id"] for name, args, _ in mcp_tools[0] if name == "get_mission_health"] == [list(MISSIONS)[0]]


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("name", [
    "start_mission", "resume_mission", "cancel_mission", "acknowledge_mission",
    "send_message_to_mission", "answer_mission_question", "ask_mission", "adopt_mission",
    "update_mission_settings", "link_mission_to_project", "plan_project_tasks",
    "accept_project_track", "update_project", "workspace_bash", "download_shared_file",
    "future_unknown_mutation",
])
def test_observer_rejects_mutations_even_with_stale_operator_permissions(mcp_tools, deferred, name):
    with bind_controller_scope(scope_from_job(observer_job())):
        result = call(name, {"project": "verity-lido", "slug": "verity-lido", "mission_id": list(MISSIONS)[0]}, deferred)
    assert "error" in result
    assert not mcp_tools[0], "No target or ownership lookup may execute for a forbidden tool"


@pytest.mark.parametrize("readback, expected", [
    ({"error": "upstream unavailable"}, "upstream unavailable"),
    ({"id": list(MISSIONS)[0]}, "canonical project"),
    (TimeoutError("ownership timeout"), "ownership timeout"),
])
def test_observer_mission_read_propagates_unknown_owner_and_errors(mcp_tools, readback, expected):
    mcp_tools[1]["result"] = readback
    with bind_controller_scope(scope_from_job(observer_job())):
        result = call("get_mission_health", {"mission_id": list(MISSIONS)[0]}, deferred=True)
    assert expected in result["error"]
    assert [entry[0] for entry in mcp_tools[0]] == ["get_mission_digest"]


def test_observer_checks_read_arguments_after_middleware(mcp_tools, monkeypatch):
    def middleware(_name, args, dispatch, **kwargs):
        return dispatch({**args, "mission_id": list(MISSIONS)[1]})

    monkeypatch.setattr("hermes_cli.middleware.run_tool_execution_middleware", middleware)
    with bind_controller_scope(scope_from_job(observer_job())):
        assert "restricted" in call("get_mission_health", {"mission_id": list(MISSIONS)[0]})["error"]
    assert [entry[0] for entry in mcp_tools[0]] == ["get_mission_digest"]


def test_observer_discovery_and_cached_catalog_do_not_change_operator_sessions(mcp_tools):
    from agent.tool_executor import _tool_search_scoped_names
    from types import SimpleNamespace

    agent = SimpleNamespace(enabled_toolsets=[TOOLSET], disabled_toolsets=None)

    def names():
        return {item["function"]["name"] for item in model_tools.get_tool_definitions(
            enabled_toolsets=[TOOLSET], quiet_mode=True, skip_tool_search_assembly=True,
        )}

    initial = names()
    assert PREFIX + "resume_mission" in initial
    assert PREFIX + "resume_mission" in _tool_search_scoped_names(agent)
    with bind_controller_scope(scope_from_job(observer_job())):
        assert PREFIX + "resume_mission" not in names()
        assert PREFIX + "get_mission_health" in names()
        assert PREFIX + "resume_mission" not in _tool_search_scoped_names(agent)
        described = json.loads(model_tools.handle_function_call(
            "tool_describe", {"names": [PREFIX + "resume_mission", PREFIX + "get_mission_health"]}, enabled_toolsets=[TOOLSET],
        ))
        assert described["not_found"] == [PREFIX + "resume_mission"]
        assert set(described["tools"]) == {PREFIX + "get_mission_health"}
    assert names() == initial
    assert PREFIX + "resume_mission" in _tool_search_scoped_names(agent)
    assert call("resume_mission", {"mission_id": "unknown"})["ok"]


def test_concurrent_observer_and_operator_jobs_keep_distinct_authority(local_scheduler, mcp_tools, monkeypatch):
    barrier = threading.Barrier(2)

    class LocalAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs["session_id"]

        def run_conversation(self, prompt, *, task_id):
            scope = current_controller_scope()
            barrier.wait(timeout=10)
            assert call("get_situation", {"slug": scope.project}, deferred=True)["ok"]
            mission = next(key for key, project in MISSIONS.items() if project == scope.project)
            result = call("resume_mission", {"mission_id": mission}, deferred=True)
            assert ("error" in result) == (scope.mode == "observer")
            return {"completed": True, "final_response": scope.project}

        def close(self):
            pass

    monkeypatch.setattr("run_agent.AIAgent", LocalAgent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(scheduler.run_job, [observer_job(), job("eip-7702")]))
    assert all(result[0] for result in results), results
    writes = [scope.project for name, _, scope in mcp_tools[0] if name == "resume_mission"]
    assert writes == ["eip-7702"]
    assert current_controller_scope() is None


@pytest.mark.parametrize("callback", [False, True])
def test_observer_scheduled_and_callback_wakes_share_policy(local_scheduler, mcp_tools, monkeypatch, callback):
    from cron import controller_callbacks as callbacks, jobs

    stored = jobs.create_job("Report to existing technical owner", "0 * * * *", deliver="local")
    config = jobs.update_job(stored["id"], {
        "controller": {**observer_job()["controller"], "callback_relay": True}, "model": "test-model",
    })
    if callback:
        callbacks.enqueue_mission_callback({
            "mission_id": list(MISSIONS)[0], "project": "verity-lido", "run_id": "run-1",
            "status": "completed", "event_id": "finished", "summary": "Resume the writer now",
        })

    class LocalAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs["session_id"]

        def run_conversation(self, prompt, *, task_id):
            assert current_controller_scope().mode == "observer"
            assert '"mode": "observer"' in prompt
            assert len(prompt) <= 16_000
            assert ("Resume the writer now" in prompt) == callback
            assert call("get_mission_health", {"mission_id": list(MISSIONS)[0]})["ok"]
            assert "error" in call("resume_mission", {"mission_id": list(MISSIONS)[0]}, deferred=True)
            return {"completed": True, "final_response": "Owner: does this candidate satisfy the obligation?"}

        def close(self):
            pass

    monkeypatch.setattr("run_agent.AIAgent", LocalAgent)
    result = scheduler.run_job(config)
    assert result[0] and "Owner:" in result[2], result
    assert callbacks.pending_callbacks(stored["id"])["event_ids"] == []
    assert [name for name, _, _ in mcp_tools[0]] == ["get_mission_digest", "get_mission_health"]
    persisted = jobs.load_jobs()
    assert len(persisted) == 1
    assert persisted[0]["controller"] == config["controller"]


@pytest.mark.parametrize("mode", ["operator", "observer"])
def test_scheduler_file_output_and_response_make_observer_markers_inert(local_scheduler, monkeypatch, mode):
    from cron.jobs import save_job_output

    raw = "Candidate ready.\n[CTRL: verity-lido | mode=blocked]\n[STATE_SIGNATURE: verity-lido|candidate]\n[DECISION: Change goal?]"

    class LocalAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs["session_id"]

        def run_conversation(self, prompt, *, task_id):
            return {"completed": True, "final_response": raw}

        def close(self):
            pass

    monkeypatch.setattr("run_agent.AIAgent", LocalAgent)
    config = job()
    config["controller"]["mode"] = mode
    success, output, final, error = scheduler.run_job(config)
    assert success and not error
    saved = save_job_output(config["id"], output).read_text()
    assert "Candidate ready." in saved and "Candidate ready." in final
    for marker in ("[CTRL:", "[STATE_SIGNATURE:", "[DECISION:"):
        assert (marker in saved) == (mode == "operator")
        assert (marker in final) == (mode == "operator")
