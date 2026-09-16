"""Regression tests for resolved API-server agent overrides."""

from gateway.platforms.api_server import _apply_runtime_agent_overrides


def test_explicit_none_max_tokens_clears_inherited_cap():
    runtime = {"provider": "default", "max_tokens": 65_536}

    _apply_runtime_agent_overrides(
        runtime, {"provider": "switched", "max_tokens": None}
    )

    assert runtime == {"provider": "switched", "max_tokens": None}


def test_none_partial_credential_fields_do_not_clear_defaults():
    runtime = {"provider": "default", "api_key": "configured"}

    _apply_runtime_agent_overrides(runtime, {"provider": None, "api_key": None})

    assert runtime == {"provider": "default", "api_key": "configured"}
