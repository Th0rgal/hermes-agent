"""Exercise observer authority through real agent and registry entry points."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cron.controller_scope import bind_controller_scope, scope_from_job
import model_tools
from run_agent import AIAgent


def observer_scope():
    return scope_from_job({
        "id": "observer-lido", "controller": {
            "project": "verity-lido", "mode": "observer",
            "permissions": ["sandboxed.read", "sandboxed.mutate"],
            "repositories": ["example/lido"], "reserved_local_areas": ["local-owner"],
        },
    })


@pytest.fixture
def agent():
    # Real AIAgent/executors; only provider construction and advertised tools
    # are replaced. Deliberately advertise tools an old operator could use.
    names = ["execute_code", "terminal", "delegate_task", "memory", "setup_mcp", "write_file"]
    definitions = [{"type": "function", "function": {
        "name": name, "description": name,
        "parameters": {"type": "object", "properties": {}},
    }} for name in names]
    with (
        patch("run_agent.get_tool_definitions", return_value=definitions),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("hermes_cli.config.load_config_readonly", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        result = AIAgent(
            api_key="test-key", base_url="https://example.invalid/v1",
            quiet_mode=True, skip_context_files=True, skip_memory=True,
        )
    result.client = MagicMock()
    result._cached_system_prompt = "Test provider boundary."
    result._use_prompt_caching = False
    result.compression_enabled = False
    result.save_trajectories = False
    yield result
    result.close()


@pytest.mark.parametrize("execution", ["sequential", "concurrent", "direct"])
@pytest.mark.parametrize("name", ["execute_code", "terminal", "delegate_task", "memory", "setup_mcp", "write_file"])
def test_observer_blocks_agent_owned_and_executable_tools_before_any_handler(agent, tmp_path, execution, name):
    marker = tmp_path / "must-not-exist"
    args = {
        "code": f"from pathlib import Path; Path({str(marker)!r}).write_text('bypass')",
        "command": "echo forbidden", "path": str(marker), "content": "forbidden",
        "prompt": "Launch another writer", "action": "add", "target": "memory",
    }
    tc = SimpleNamespace(id="observer-block", type="function", function=SimpleNamespace(
        name=name, arguments=json.dumps(args),
    ))
    messages = []
    with (
        patch("run_agent.handle_function_call", side_effect=AssertionError("registry handler reached")),
        patch.object(agent, "_dispatch_delegate_task", side_effect=AssertionError("child launched")),
        bind_controller_scope(observer_scope()),
    ):
        if execution == "direct":
            content = agent._invoke_tool(name, args, "observer-task")
        else:
            getattr(agent, "_execute_tool_calls_" + execution)(
                SimpleNamespace(content="", tool_calls=[tc]), messages, "observer-task",
            )
            assert len(messages) == 1
            content = messages[0]["content"]
    assert "Observer controller" in content and "not permitted" in content
    assert not marker.exists()


@pytest.mark.parametrize("name", [
    "execute_code", "terminal", "write_file", "patch", "delegate_task", "cronjob",
    "memory", "setup_mcp", "send_message", "mcp__unrelated__start_mission",
])
@pytest.mark.parametrize("deferred", [False, True])
def test_registry_entry_cannot_bypass_observer_with_other_toolsets(tmp_path, name, deferred):
    marker = tmp_path / "must-not-exist"
    args = {"code": f"from pathlib import Path; Path({str(marker)!r}).touch()", "command": "true"}
    with bind_controller_scope(observer_scope()):
        result = json.loads(model_tools.handle_function_call(
            "tool_call" if deferred else name,
            {"name": name, "arguments": args} if deferred else args,
            enabled_toolsets=["terminal", "code_execution", "file", "delegate", "cronjob"],
        ))
    assert "error" in result
    assert not marker.exists()


@pytest.mark.parametrize("execution", ["sequential", "concurrent", "direct"])
def test_unscoped_agent_can_still_dispatch_executable_tool(agent, execution):
    messages = []
    with patch("run_agent.handle_function_call", return_value=json.dumps({"ok": True})) as handler:
        if execution == "direct":
            result = agent._invoke_tool("execute_code", {"code": "print(1)"}, "interactive")
        else:
            tc = SimpleNamespace(id="normal", type="function", function=SimpleNamespace(
                name="execute_code", arguments=json.dumps({"code": "print(1)"}),
            ))
            getattr(agent, "_execute_tool_calls_" + execution)(
                SimpleNamespace(content="", tool_calls=[tc]), messages, "interactive",
            )
            result = messages[0]["content"]
    handler.assert_called_once()
    assert json.loads(result)["ok"]
