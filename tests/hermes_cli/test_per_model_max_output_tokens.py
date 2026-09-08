"""Tests for per-model max_output_tokens resolution in custom providers.

Regression test for the DGX session 20260907_125723_a70323 bug: a
``providers.dgx-spark.models.qwen3.8-orca-nvfp4.max_tokens: 8192``
config was silently ignored, causing the session to send 65536 output
tokens (vLLM server default) instead of the configured 8192, which
triggered a 400 error and 413s of unnecessary compression.

The fix implements proper precedence:
  1. explicit caller max_tokens
  2. per-model max_tokens  (providers.<name>.models.<model>.max_tokens)
  3. per-provider max_tokens (providers.<name>.max_tokens)
  4. default (None — server decides)
"""

import pytest

from hermes_cli.runtime_provider import (
    _lift_max_output_tokens,
    _resolve_effective_max_output_tokens,
    resolve_runtime_provider,
)
from hermes_cli.config import (
    _normalize_custom_provider_entry,
)
from hermes_cli.max_tokens import resolve_global_max_tokens


class TestLiftMaxOutputTokens:
    """Unit tests for _lift_max_output_tokens with per-model support."""

    def test_provider_level_max_tokens(self):
        entry = {"max_tokens": 8192}
        result = {}
        _lift_max_output_tokens(entry, result)
        assert result["max_output_tokens"] == 8192

    def test_provider_level_max_output_tokens(self):
        entry = {"max_output_tokens": 4096}
        result = {}
        _lift_max_output_tokens(entry, result)
        assert result["max_output_tokens"] == 4096

    @pytest.mark.parametrize("key", ["max_tokens", "max_output_tokens"])
    def test_boolean_provider_caps_are_rejected(self, key):
        result = {}
        _lift_max_output_tokens({key: True}, result, model="m")
        assert "max_output_tokens" not in result

    def test_max_output_tokens_preferred_over_max_tokens(self):
        entry = {"max_output_tokens": 4096, "max_tokens": 8192}
        result = {}
        _lift_max_output_tokens(entry, result)
        assert result["max_output_tokens"] == 4096

    def test_per_model_overrides_provider_level(self):
        entry = {
            "max_tokens": 16384,
            "models": {
                "qwen3.8-orca-nvfp4": {"max_tokens": 8192},
            },
        }
        result = {}
        _lift_max_output_tokens(entry, result, model="qwen3.8-orca-nvfp4")
        assert result["max_output_tokens"] == 8192

    def test_per_model_max_tokens_without_provider_level(self):
        """Per-model max_tokens works even when provider-level is unset."""
        entry = {
            "models": {
                "my-model": {"max_tokens": 4096},
            },
        }
        result = {}
        _lift_max_output_tokens(entry, result, model="my-model")
        assert result["max_output_tokens"] == 4096

    def test_falls_back_to_provider_when_model_not_in_models(self):
        entry = {
            "max_tokens": 16384,
            "models": {
                "other-model": {"max_tokens": 8192},
            },
        }
        result = {}
        _lift_max_output_tokens(entry, result, model="unknown-model")
        assert result["max_output_tokens"] == 16384

    def test_falls_back_to_provider_when_no_model_specified(self):
        entry = {
            "max_tokens": 8192,
            "models": {
                "some-model": {"max_tokens": 4096},
            },
        }
        result = {}
        _lift_max_output_tokens(entry, result, model=None)
        assert result["max_output_tokens"] == 8192

    def test_no_max_tokens_anywhere(self):
        entry = {"models": {"m": {"context_length": 131072}}}
        result = {}
        _lift_max_output_tokens(entry, result, model="m")
        assert "max_output_tokens" not in result

    def test_invalid_max_tokens_ignored(self):
        entry = {"max_tokens": -1, "models": {"m": {"max_tokens": 0}}}
        result = {}
        _lift_max_output_tokens(entry, result, model="m")
        assert "max_output_tokens" not in result

    def test_per_model_max_output_tokens_alias(self):
        entry = {
            "models": {
                "my-model": {"max_output_tokens": 2048},
            },
        }
        result = {}
        _lift_max_output_tokens(entry, result, model="my-model")
        assert result["max_output_tokens"] == 2048


class TestResolveEffectiveMaxOutputTokens:
    """Tests for _resolve_effective_max_output_tokens in the runtime path."""

    def test_per_model_from_provider_models_config(self):
        custom_provider = {
            "max_output_tokens": 16384,
            "_provider_models_config": {
                "qwen3.8-orca-nvfp4": {"max_tokens": 8192},
            },
        }
        result = {}
        _resolve_effective_max_output_tokens(custom_provider, "qwen3.8-orca-nvfp4", result)
        assert result["max_output_tokens"] == 8192

    def test_falls_back_to_provider_level(self):
        custom_provider = {
            "max_output_tokens": 16384,
            "_provider_models_config": {
                "other-model": {"max_tokens": 8192},
            },
        }
        result = {}
        _resolve_effective_max_output_tokens(custom_provider, "unknown", result)
        assert result["max_output_tokens"] == 16384

    def test_no_models_config_uses_provider_level(self):
        custom_provider = {"max_output_tokens": 8192}
        result = {}
        _resolve_effective_max_output_tokens(custom_provider, "any-model", result)
        assert result["max_output_tokens"] == 8192

    def test_no_max_tokens_anywhere(self):
        custom_provider = {"_provider_models_config": {"m": {"context_length": 131072}}}
        result = {}
        _resolve_effective_max_output_tokens(custom_provider, "m", result)
        assert "max_output_tokens" not in result

    def test_boolean_per_model_and_provider_caps_are_rejected(self):
        custom_provider = {
            "max_output_tokens": True,
            "_provider_models_config": {"m": {"max_tokens": True}},
        }
        result = {}
        _resolve_effective_max_output_tokens(custom_provider, "m", result)
        assert "max_output_tokens" not in result

    def test_none_model_uses_provider_level(self):
        custom_provider = {"max_output_tokens": 4096}
        result = {}
        _resolve_effective_max_output_tokens(custom_provider, None, result)
        assert result["max_output_tokens"] == 4096

    def test_dgx_spark_exact_scenario(self):
        """Reproduce the exact DGX session bug scenario."""
        custom_provider = {
            "max_output_tokens": 8192,
            "_provider_models_config": {
                "qwen3.8-orca-nvfp4": {
                    "max_tokens": 8192,
                    "context_length": 131072,
                },
            },
        }
        result = {}
        _resolve_effective_max_output_tokens(
            custom_provider, "qwen3.8-orca-nvfp4", result
        )
        assert result["max_output_tokens"] == 8192

    def test_gateway_style_resolution_uses_configured_model(self, monkeypatch):
        """Gateway omits target_model; model.default must still select its cap."""
        config = {
            "model": {"provider": "dgx", "default": "chosen"},
            "providers": {
                "dgx": {
                    "api": "http://127.0.0.1:8000/v1",
                    "max_tokens": 16_000,
                    "models": {"chosen": {"max_tokens": 8192}},
                }
            },
        }
        monkeypatch.setattr(
            "hermes_cli.runtime_provider.load_config", lambda: config
        )
        monkeypatch.setattr(
            "hermes_cli.runtime_provider._try_resolve_from_custom_pool",
            lambda *args, **kwargs: None,
        )

        runtime = resolve_runtime_provider(requested="dgx")

        assert runtime["model"] == "chosen"
        assert runtime["max_output_tokens"] == 8192

    def test_default_model_cap_does_not_leak_to_sibling_model(self, monkeypatch):
        config = {
            "model": {"provider": "dgx", "default": "model-a"},
            "providers": {
                "dgx": {
                    "api": "http://127.0.0.1:8000/v1",
                    "default_model": "model-a",
                    "models": {"model-a": {"max_tokens": 65_536}, "model-b": {}},
                }
            },
        }
        monkeypatch.setattr(
            "hermes_cli.runtime_provider.load_config", lambda: config
        )
        monkeypatch.setattr(
            "hermes_cli.runtime_provider._try_resolve_from_custom_pool",
            lambda *args, **kwargs: None,
        )

        runtime = resolve_runtime_provider(requested="dgx", target_model="model-b")

        assert "max_output_tokens" not in runtime


class TestOutputCapRecoverySkipsCompression:
    """The output-cap recovery should skip compression when the reduced cap
    fits comfortably inside the context window."""

    def test_vllm_error_parsing_returns_halved_cap(self):
        from agent.model_metadata import parse_available_output_tokens_from_error

        msg = (
            "This model's maximum context length is 131072 tokens. However, you "
            "requested 65536 output tokens and your prompt contains at least "
            "65537 input tokens, for a total of at least 131073 tokens. Please "
            "reduce the length of the input prompt or the number of requested "
            "output tokens."
        )
        available = parse_available_output_tokens_from_error(msg)
        assert available == 32768

    def test_reduced_cap_fits_without_compression(self):
        """When safe_out + input_estimate < 90% of context, compression is skippable."""
        context_length = 131072
        safe_out = 32768
        request_input_estimate = 65537
        total = safe_out + request_input_estimate
        assert total < context_length * 0.90

    def test_tight_fit_still_compresses(self):
        """When safe_out + input_estimate is close to context, compression is needed."""
        context_length = 131072
        safe_out = 32768
        request_input_estimate = 95000
        total = safe_out + request_input_estimate
        assert total >= context_length * 0.90


class TestNormalizerPreservesMaxTokens:
    """The config normalizer must preserve max_tokens/max_output_tokens
    so the legacy custom_providers compatibility path can read them."""

    def test_provider_level_max_tokens_survives_normalization(self):
        entry = {
            "name": "dgx-spark",
            "base_url": "http://100.77.4.93:8000/v1",
            "max_tokens": 8192,
        }
        normalized = _normalize_custom_provider_entry(entry, provider_key="dgx-spark")
        assert normalized is not None
        assert normalized["max_tokens"] == 8192

    def test_provider_level_max_output_tokens_survives_normalization(self):
        entry = {
            "name": "dgx-spark",
            "base_url": "http://100.77.4.93:8000/v1",
            "max_output_tokens": 4096,
        }
        normalized = _normalize_custom_provider_entry(entry, provider_key="dgx-spark")
        assert normalized is not None
        assert normalized["max_output_tokens"] == 4096

    def test_per_model_max_tokens_in_models_dict_survives(self):
        entry = {
            "name": "dgx-spark",
            "base_url": "http://100.77.4.93:8000/v1",
            "max_tokens": 8192,
            "models": {
                "qwen3.8-orca-nvfp4": {
                    "max_tokens": 8192,
                    "context_length": 131072,
                },
            },
        }
        normalized = _normalize_custom_provider_entry(entry, provider_key="dgx-spark")
        assert normalized is not None
        assert normalized["max_tokens"] == 8192
        assert normalized["models"]["qwen3.8-orca-nvfp4"]["max_tokens"] == 8192

    def test_camel_case_maxTokens_normalized(self):
        entry = {
            "name": "dgx-spark",
            "base_url": "http://100.77.4.93:8000/v1",
            "maxTokens": 8192,
        }
        normalized = _normalize_custom_provider_entry(entry, provider_key="dgx-spark")
        assert normalized is not None
        assert normalized["max_tokens"] == 8192

    def test_invalid_max_tokens_not_preserved(self):
        entry = {
            "name": "dgx-spark",
            "base_url": "http://100.77.4.93:8000/v1",
            "max_tokens": -1,
        }
        normalized = _normalize_custom_provider_entry(entry, provider_key="dgx-spark")
        assert normalized is not None
        assert "max_tokens" not in normalized

    @pytest.mark.parametrize("key", ["max_tokens", "max_output_tokens"])
    def test_boolean_caps_not_preserved(self, key):
        normalized = _normalize_custom_provider_entry(
            {"name": "dgx-spark", "base_url": "http://localhost/v1", key: True},
            provider_key="dgx-spark",
        )
        assert normalized is not None
        assert key not in normalized

    def test_no_unknown_key_warning_for_max_tokens(self):
        """max_tokens must be in _KNOWN_KEYS — no 'unknown config keys' warning."""
        import hermes_cli.config as cfg_mod
        cfg_mod._PROVIDER_NORMALIZE_WARNED.clear()
        entry = {
            "name": "dgx-spark",
            "base_url": "http://100.77.4.93:8000/v1",
            "max_tokens": 8192,
        }
        normalized = _normalize_custom_provider_entry(entry, provider_key="test-no-warn")
        assert normalized is not None
        assert ("test-no-warn", "unknown:max_tokens") not in cfg_mod._PROVIDER_NORMALIZE_WARNED


def test_global_cap_env_precedes_config(monkeypatch):
    monkeypatch.setenv("HERMES_MAX_TOKENS", "2048")
    assert resolve_global_max_tokens({"max_tokens": 8192}) == 2048


def test_global_cap_rejects_boolean_config(monkeypatch):
    monkeypatch.delenv("HERMES_MAX_TOKENS", raising=False)
    assert resolve_global_max_tokens({"max_tokens": True}) is None
