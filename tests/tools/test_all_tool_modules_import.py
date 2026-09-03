"""Every module under ``tools/`` must import.

The tool registry only *warns* when a module fails to import and silently
drops its tools, so a broken import surfaces much later as "agent init
failed" in a real turn (2026-09-03: ``tools/project_tools.py`` lost its
``registry`` import in an upstream merge). Make it a hard test instead.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import tools

_MODULES = sorted(
    f"tools.{module.name}"
    for module in pkgutil.iter_modules(tools.__path__)
    if not module.name.startswith("_")
)


@pytest.mark.parametrize("module_name", _MODULES)
def test_tool_module_imports(module_name: str) -> None:
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        # Optional third-party deps are allowed to be absent in a dev venv;
        # a name error or syntax error inside the module never is.
        pytest.skip(f"{module_name}: optional dependency missing ({error.name})")
