"""A broken MCP server must be visible to the agent, not only to a human.

Measured 2026-08-05. `notion` failed its handshake on every session for days:

    WARNING tools.mcp_tool: Failed to connect to MCP server 'notion'
      (command=/usr/local/bin/notion-mcp-wrapper.sh): CancelledError

Cause: the wrapper ran `npx -y`, which re-resolved the package under the
service HOME on every launch — 89s against a `connect_timeout` of 60. Tested by
hand as root, with a warm npm cache, the same handshake took 1s.

Every consumer of `get_mcp_status` is human-facing (CLI banner, startup check,
TUI panel). The agent was told nothing, so it read the missing Notion tools as
an ordinary absence and improvised through the terminal. It was right about the
absence and had nothing at all to reason about the cause.
"""

from unittest.mock import patch

from tools.mcp_tool import unavailable_mcp_notice


def _status(name, **overrides):
    entry = {
        "name": name,
        "transport": "stdio",
        "tools": 0,
        "connected": False,
        "disabled": False,
        "status": "failed",
    }
    entry.update(overrides)
    return entry


def _with(statuses):
    return patch("tools.mcp_tool.get_mcp_status", return_value=statuses)


class TestHealthyIsFree:
    def test_all_connected_produces_nothing(self):
        # The notice sits in the volatile band of the system prompt. On a
        # healthy deployment — which is nearly always — it must cost zero
        # tokens and zero prefix-cache churn.
        with _with([_status("notion", connected=True, status="connected")]):
            assert unavailable_mcp_notice() == ""

    def test_a_deliberately_disabled_server_is_not_a_fault(self):
        with _with([_status("notion", disabled=True, status="disabled")]):
            assert unavailable_mcp_notice() == ""

    def test_a_server_still_connecting_is_not_reported(self):
        # Reporting an in-flight connection as broken would teach the agent to
        # give up on a server that is about to work.
        with _with([_status("notion", status="connecting")]):
            assert unavailable_mcp_notice() == ""

    def test_no_servers_at_all(self):
        with _with([]):
            assert unavailable_mcp_notice() == ""


class TestBrokenIsVisible:
    def test_a_failed_server_is_named(self):
        with _with([_status("notion", error="CancelledError")]):
            assert "notion" in unavailable_mcp_notice()

    def test_the_reason_is_carried(self):
        # The name alone says "gone". The reason is what lets the agent report
        # a blocker instead of inventing one.
        with _with([_status("notion", error="CancelledError")]):
            assert "CancelledError" in unavailable_mcp_notice()

    def test_it_says_the_tools_exist(self):
        # The distinction the agent could not make: configured-and-broken
        # versus never-configured.
        with _with([_status("notion", error="CancelledError")]):
            notice = unavailable_mcp_notice()
        assert "not because they do not exist" in notice

    def test_it_asks_for_a_blocker_not_a_workaround(self):
        with _with([_status("notion", error="CancelledError")]):
            assert "blocker" in unavailable_mcp_notice()

    def test_every_broken_server_gets_a_line(self):
        with _with([
            _status("notion", error="CancelledError"),
            _status("linear", error="connection refused"),
            _status("sandboxed_assistant", connected=True, status="connected"),
        ]):
            notice = unavailable_mcp_notice()
        assert "- notion:" in notice
        assert "- linear:" in notice
        assert "sandboxed_assistant" not in notice, "a healthy server is not listed"

    def test_a_missing_error_still_yields_a_line(self):
        # Some failures record only a status. A line with a weak reason still
        # beats silence.
        with _with([_status("notion", status="failed")]):
            notice = unavailable_mcp_notice()
        assert "- notion: failed" in notice


class TestItNeverBreaksATurn:
    def test_a_raising_status_call_yields_no_notice(self):
        # This runs inside system-prompt assembly. A diagnostic that can take
        # down every turn is worse than the problem it reports.
        with patch("tools.mcp_tool.get_mcp_status", side_effect=RuntimeError("boom")):
            assert unavailable_mcp_notice() == ""

    def test_a_none_status_is_tolerated(self):
        with _with(None):
            assert unavailable_mcp_notice() == ""
