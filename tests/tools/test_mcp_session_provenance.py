"""MCP tools can be told which conversation is calling them.

A tool that starts long-running work elsewhere (a sandboxed.sh mission) needs
to know where to report back.  Asking the model to pass its own session id is
unreliable in both directions: it can forget, and nothing stops it naming
someone else's conversation.  Instead the tool declares an
``origin_session_id`` argument in its input schema and the dispatcher fills it
from the live session — the value is the gateway's, not the model's.
"""

from tools.mcp_tool import (
    SESSION_PROVENANCE_ARG,
    _declares_session_provenance,
    _with_session_provenance,
)


def _schema(properties: dict) -> dict:
    return {
        "name": "mcp__sandboxed_assistant__start_mission",
        "description": "start a mission",
        "parameters": {"type": "object", "properties": properties},
    }


def test_schema_declaring_the_argument_opts_in():
    assert _declares_session_provenance(
        _schema({"title": {"type": "string"}, SESSION_PROVENANCE_ARG: {"type": "string"}})
    )


def test_schema_without_the_argument_stays_opted_out():
    assert not _declares_session_provenance(_schema({"title": {"type": "string"}}))
    assert not _declares_session_provenance(_schema({}))
    assert not _declares_session_provenance({"name": "x", "description": "y"})


def test_live_session_overwrites_anything_the_model_supplied():
    """The security property: provenance is stated by the gateway, not chosen."""
    effective = _with_session_provenance(
        {"title": "t", SESSION_PROVENANCE_ARG: "someone-elses-session"},
        "20260803_150605_59ab72",
    )
    assert effective[SESSION_PROVENANCE_ARG] == "20260803_150605_59ab72"
    assert effective["title"] == "t"


def test_model_cannot_opt_out_by_omitting_the_argument():
    effective = _with_session_provenance({"title": "t"}, "20260803_150605_59ab72")
    assert effective[SESSION_PROVENANCE_ARG] == "20260803_150605_59ab72"


def test_without_a_live_session_arguments_pass_through_untouched():
    """No session (cron/CLI paths) must not forge an empty provenance."""
    for missing in (None, "", "   "):
        effective = _with_session_provenance({"title": "t"}, missing)
        assert effective == {"title": "t"}


def test_caller_arguments_are_never_mutated_in_place():
    original = {"title": "t"}
    _with_session_provenance(original, "20260803_150605_59ab72")
    assert original == {"title": "t"}
