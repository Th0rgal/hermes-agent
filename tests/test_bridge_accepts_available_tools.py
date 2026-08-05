"""The tool_call bridge must not refuse a tool the session already has.

Measured on prod, 2026-08-05, three hours of hermes-assistant logs:

    13x  tool_call requires a 'name' argument
     4x  tool_call to 'mcp__sandboxed_assistant__get_mission_digest' is
         missing required argument(s): mission_id
     2x  tool_call to 'mcp__sandboxed_assistant__start_mission' is missing
         required argument(s): title, prompt
     2x  tool_call cannot invoke 'tool_call'
     3x  [Tool loop warning: same_tool_failure_warning]

and, earlier the same day:

    'mcp_sandboxed_assistant__get_mission' is not a deferrable tool. If it
    appears in the model-facing tools list already, call it directly instead
    of via tool_call.

Meanwhile the Verity and Lido controllers ticked every ten minutes with
`last_status: ok` and moved no missions for hours. The turns were being spent
arguing with the bridge.

Refusing a directly-available tool adds no safety — the model can call it
straight — and costs a whole turn.
"""

from tools import tool_search as ts


def _defs(*names):
    return [{"type": "function", "function": {"name": n}} for n in names]


class TestResolutionDoesNotAuthorise:
    def test_a_directly_available_tool_resolves(self):
        # The old branch returned an error here and the turn was over.
        name, args, err = ts.resolve_underlying_call(
            {"name": "terminal", "arguments": {"command": "ls"}}
        )
        assert err is None, err
        assert name == "terminal"
        assert args == {"command": "ls"}

    def test_a_bridge_tool_is_still_refused(self):
        # tool_call recursing into itself is a loop, not a capability.
        for bridge in ts.BRIDGE_TOOL_NAMES:
            _, _, err = ts.resolve_underlying_call({"name": bridge})
            assert err and "bridge tool" in err

    def test_malformed_arguments_are_still_refused(self):
        _, _, err = ts.resolve_underlying_call({"name": "x", "arguments": "{not json"})
        assert err and "not valid JSON" in err

        _, _, err = ts.resolve_underlying_call({"name": "x", "arguments": [1, 2]})
        assert err and "must be an object" in err

    def test_absent_arguments_default_to_empty(self):
        name, args, err = ts.resolve_underlying_call({"name": "x"})
        assert err is None and name == "x" and args == {}


class TestTheMissingNameMessage:
    def test_it_still_fails(self):
        _, _, err = ts.resolve_underlying_call({"mission_id": "abc"})
        assert err is not None

    def test_it_echoes_the_keys_that_did_arrive(self):
        # The model sent the target's arguments at the top level. Naming them
        # back is what makes the mistake visible on the first attempt.
        _, _, err = ts.resolve_underlying_call({"mission_id": "abc", "verbose": True})
        assert "mission_id" in err and "verbose" in err

    def test_it_shows_the_correct_shape(self):
        _, _, err = ts.resolve_underlying_call({"mission_id": "abc"})
        assert 'tool_call(name=' in err

    def test_no_keys_at_all_is_handled(self):
        _, _, err = ts.resolve_underlying_call({})
        assert err and "none" in err


class TestTheScopeGate:
    def test_a_granted_tool_is_reachable(self):
        # The gate's purpose is to stop a session reaching a tool it was never
        # granted. A tool in the session's own defs was granted.
        assert "terminal" in ts.scoped_deferrable_names(_defs("terminal"))

    def test_a_deferrable_tool_is_still_reachable(self):
        names = ts.scoped_deferrable_names(_defs("mcp__sandboxed_assistant__start_mission"))
        assert "mcp__sandboxed_assistant__start_mission" in names

    def test_a_tool_outside_the_session_scope_is_not(self):
        # The property that actually matters: absence from tool_defs still
        # means unreachable. Widening the gate must not have widened it to
        # everything.
        names = ts.scoped_deferrable_names(_defs("terminal"))
        assert "mcp__secret_server__drop_database" not in names

    def test_bridge_tools_never_enter_the_gate(self):
        names = ts.scoped_deferrable_names(_defs(*ts.BRIDGE_TOOL_NAMES, "terminal"))
        assert names == frozenset({"terminal"})

    def test_an_empty_scope_grants_nothing(self):
        assert ts.scoped_deferrable_names([]) == frozenset()

    def test_a_nameless_definition_is_skipped(self):
        assert ts.scoped_deferrable_names([{"type": "function", "function": {}}]) == frozenset()
