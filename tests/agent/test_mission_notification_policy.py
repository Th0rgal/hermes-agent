"""Mission notices must not dispatch follow-up work through any agent tool path."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("name", ["terminal", "delegate_task", "memory", "execute_code", "tool_call"])
def test_notice_blocks_direct_and_batch_dispatch_before_relay(monkeypatch, name):
    from agent.agent_runtime_helpers import invoke_tool
    from agent.tool_executor import _run_agent_tool_execution_middleware
    from agent import relay_tools
    agent = SimpleNamespace(_notification_only_turn=True)
    relay = Mock(side_effect=AssertionError("Notification must not enter Relay"))
    monkeypatch.setattr(relay_tools, "execute", relay)
    assert "disabled" in json.loads(invoke_tool(agent, name, {}, "notice"))["error"]
    dispatch = Mock(side_effect=AssertionError("No tool handler may execute"))
    started = Mock()
    result = _run_agent_tool_execution_middleware(
        agent, function_name=name, function_args={}, effective_task_id="notice",
        tool_call_id="call", execute=dispatch, begin_execution=started,
    )
    assert result.blocked and not result.dispatched
    assert "disabled" in json.loads(result.result)["error"]
    started.assert_called_once()
    relay.assert_not_called()
    dispatch.assert_not_called()


def test_reused_agent_resets_notice_policy_at_each_real_turn_entry(monkeypatch):
    from agent import conversation_loop
    agent = SimpleNamespace()
    class PreflightReached(Exception):
        pass
    def stop_before_inference(*args):
        raise PreflightReached
    monkeypatch.setattr(conversation_loop, "begin_fast_mode_turn", stop_before_inference)
    for kind, restricted in [("mission_callback_wake", True), (None, False),
                             ("internal_notification", False)]:
        with pytest.raises(PreflightReached):
            conversation_loop.run_conversation(agent, "notice", persist_user_display_kind=kind)
        assert agent._notification_only_turn is restricted
