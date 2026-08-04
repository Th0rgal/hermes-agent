"""The circuit breaker must distinguish "server is down" from "server said no".

Real incident, 2026-08-04: an autonomous controller asked
`get_mission_digest` about a mission that no longer existed. The MCP server
answered `404 Not Found` — correctly, three times. That tripped the breaker,
which then refused *every* tool on that server for 60 s, `start_mission`
included. The controller reported "MCP unreachable, cannot dispatch" and did
nothing for hours, while the server was in perfect health the whole time.
"""

import pytest

from tools import mcp_tool


class _McpError(Exception):
    """Stands in for the SDK's McpError, matched by class name."""


@pytest.fixture(autouse=True)
def _clean_breaker():
    mcp_tool._server_error_counts.clear()
    mcp_tool._server_breaker_opened_at.clear()
    yield
    mcp_tool._server_error_counts.clear()
    mcp_tool._server_breaker_opened_at.clear()


class TestIsApplicationError:
    @pytest.mark.parametrize(
        "message",
        [
            "Mission not found: 404 Not Found",
            "Get workspace job failed (404 Not Found): {}",
            "Bad request: 400",
            "Conflict: 409 already exists",
            "Unprocessable: 422",
        ],
    )
    def test_a_server_that_answered_is_not_a_failure(self, message):
        assert mcp_tool._is_application_error(_McpError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "Internal Server Error: 500",
            "Bad gateway: 502",
            "Service unavailable: 503",
            "Rate limited: 429",
        ],
    )
    def test_a_server_in_trouble_still_counts(self, message):
        # 5xx means the server is genuinely unwell; 429 means it is refusing
        # load. Backing off is the right response to both.
        assert mcp_tool._is_application_error(_McpError(message)) is False

    def test_a_transport_failure_counts(self):
        # Not an McpError at all: nothing answered.
        assert mcp_tool._is_application_error(TimeoutError("timed out")) is False
        assert mcp_tool._is_application_error(ConnectionError("refused")) is False

    def test_auth_errors_are_left_to_the_auth_path(self):
        # 401/403 have their own recovery flow upstream; they must not be
        # reclassified as benign here.
        assert mcp_tool._is_application_error(_McpError("Unauthorized: 401")) is False
        assert mcp_tool._is_application_error(_McpError("Forbidden: 403")) is False


class TestBreakerAccounting:
    def test_repeated_404s_never_open_the_breaker(self):
        # The exact shape of the incident: three 404s in a row.
        for _ in range(5):
            exc = _McpError("Mission not found: 404 Not Found")
            if not mcp_tool._is_application_error(exc):
                mcp_tool._bump_server_error("sandboxed_assistant")
            else:
                mcp_tool._reset_server_error("sandboxed_assistant")
        count = mcp_tool._server_error_counts.get("sandboxed_assistant", 0)
        assert count < mcp_tool._CIRCUIT_BREAKER_THRESHOLD
        assert count == 0

    def test_repeated_transport_failures_still_open_it(self):
        for _ in range(mcp_tool._CIRCUIT_BREAKER_THRESHOLD):
            exc = TimeoutError("timed out")
            if not mcp_tool._is_application_error(exc):
                mcp_tool._bump_server_error("sandboxed_assistant")
        count = mcp_tool._server_error_counts.get("sandboxed_assistant", 0)
        assert count >= mcp_tool._CIRCUIT_BREAKER_THRESHOLD
        assert "sandboxed_assistant" in mcp_tool._server_breaker_opened_at

    def test_a_404_clears_a_partially_armed_breaker(self):
        # Two transport blips then a successful 404 answer: the server is
        # demonstrably reachable again, so the count must not linger and
        # combine with a later blip to trip the breaker.
        mcp_tool._bump_server_error("s")
        mcp_tool._bump_server_error("s")
        exc = _McpError("Mission not found: 404 Not Found")
        if mcp_tool._is_application_error(exc):
            mcp_tool._reset_server_error("s")
        assert mcp_tool._server_error_counts.get("s", 0) == 0
