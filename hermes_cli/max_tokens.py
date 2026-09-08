"""Shared output-token cap resolution."""

import os
from typing import Any, Optional


def resolve_global_max_tokens(model_config: Any = None) -> Optional[int]:
    """Return the effective global output cap, including its env override."""
    env_value = os.environ.get("HERMES_MAX_TOKENS")
    if env_value:
        try:
            value = int(env_value)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None
    if isinstance(model_config, dict):
        value = model_config.get("max_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None
