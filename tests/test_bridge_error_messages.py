"""A refusal from the tool_call bridge must tell the agent what to do next.

Measured on prod, three hours of hermes-assistant logs on 2026-08-05 — the top
tool errors on the box:

    13x  tool_call requires a 'name' argument
     4x  tool_call to '...get_mission_digest' is missing mission_id
     3x  [Tool loop warning: same_tool_failure_warning]

and earlier the same day:

    'mcp_sandboxed_assistant__get_mission' is not a deferrable tool.

That last name has ONE underscore after `mcp` where the real tool has two. The
refusal was correct. The message was not actionable: it described a category
when the fault was a typo, so the agent retried, failed identically, and tripped
the loop guard.

Both refusals stay. Only what they say changes.
"""

from unittest.mock import patch

from tools import tool_search as ts


class TestMissingName:
    def test_it_still_refuses(self):
        _, _, err = ts.resolve_underlying_call({"mission_id": "abc"})
        assert err is not None

    def test_it_echoes_the_keys_that_did_arrive(self):
        # The model put the target's arguments at the top level. Naming them
        # back is what makes the shape error visible.
        _, _, err = ts.resolve_underlying_call({"mission_id": "abc", "verbose": True})
        assert "mission_id" in err and "verbose" in err

    def test_it_shows_the_shape_it_wanted(self):
        _, _, err = ts.resolve_underlying_call({"mission_id": "abc"})
        assert 'tool_call(name=' in err

    def test_it_offers_the_direct_call_as_a_way_out(self):
        _, _, err = ts.resolve_underlying_call({"mission_id": "abc"})
        assert "directly" in err

    def test_no_keys_at_all_is_handled(self):
        _, _, err = ts.resolve_underlying_call({})
        assert err and "none" in err


class TestTheSuggestion:
    """`_closest_tool_name` is what turns a typo into a fixable error."""

    def _registry(self, *names):
        class _Entry:
            def __init__(self, name):
                self.name = name

        class _Registry:
            def _snapshot_entries(self):
                return [_Entry(n) for n in names]

        return patch.dict(
            "sys.modules",
            {"tools.registry": type("M", (), {"registry": _Registry()})},
        )

    def test_a_single_underscore_typo_is_caught(self):
        # The exact name measured on prod.
        with self._registry("mcp__sandboxed_assistant__get_mission", "terminal"):
            got = ts._closest_tool_name("mcp_sandboxed_assistant__get_mission")
        assert got == "mcp__sandboxed_assistant__get_mission"

    def test_an_unrelated_name_gets_no_suggestion(self):
        # A wrong suggestion sends the next attempt somewhere else wrong, which
        # is worse than none. The cutoff is deliberately high.
        with self._registry("mcp__sandboxed_assistant__get_mission"):
            assert ts._closest_tool_name("banana") is None

    def test_an_empty_registry_gets_no_suggestion(self):
        with self._registry():
            assert ts._closest_tool_name("anything") is None

    def test_a_broken_registry_never_raises(self):
        # This runs on an error path. A hint that can raise turns a bad message
        # into a crashed turn.
        with patch.dict("sys.modules", {"tools.registry": None}):
            assert ts._closest_tool_name("x") is None

    def test_bridge_tools_are_never_suggested(self):
        with self._registry(*ts.BRIDGE_TOOL_NAMES):
            assert ts._closest_tool_name(list(ts.BRIDGE_TOOL_NAMES)[0] + "x") is None


class TestTheInvariantsAreUntouched:
    """The refusals themselves are load-bearing and stay exactly as they were."""

    def test_a_bridge_tool_is_still_refused(self):
        for bridge in ts.BRIDGE_TOOL_NAMES:
            _, _, err = ts.resolve_underlying_call({"name": bridge})
            assert err and "bridge tool" in err

    def test_a_non_deferrable_tool_is_still_refused(self):
        # Core tools must not be reachable through the bridge
        # (TestRegression_OpenClawCron84141). Only the wording gained a hint.
        with patch.object(ts, "is_deferrable_tool_name", return_value=False):
            _, _, err = ts.resolve_underlying_call({"name": "terminal"})
        assert err and "not a deferrable tool" in err

    def test_malformed_arguments_are_still_refused(self):
        _, _, err = ts.resolve_underlying_call({"name": "x", "arguments": "{nope"})
        assert err and "not valid JSON" in err
        _, _, err = ts.resolve_underlying_call({"name": "x", "arguments": [1]})
        assert err and "must be an object" in err
