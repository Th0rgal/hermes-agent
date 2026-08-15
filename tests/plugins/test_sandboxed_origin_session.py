"""Unit tests for the sandboxed-origin-session plugin."""

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = (
    Path(__file__).resolve().parents[2] / "plugins" / "sandboxed-origin-session"
)
TOOL = "mcp__sandboxed_assistant__start_mission"


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "sandboxed_origin_session", PLUGIN_DIR / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin():
    return _load_plugin()


class FakeSessionContext:
    def __init__(self, env):
        self.env = env

    def install(self):
        module = types.ModuleType("gateway.session_context")
        module.get_session_env = lambda key, default="": self.env.get(key, default)
        gateway = sys.modules.setdefault("gateway", types.ModuleType("gateway"))
        gateway.session_context = module
        sys.modules["gateway.session_context"] = module

    @staticmethod
    def uninstall():
        sys.modules.pop("gateway.session_context", None)
        # Keep gateway if other tests imported the real package; only drop
        # the stub we installed when it has no real submodules loaded.
        gw = sys.modules.get("gateway")
        if gw is not None and getattr(gw, "__file__", None) is None:
            sys.modules.pop("gateway", None)


@pytest.fixture(autouse=True)
def _clean_session_env():
    yield
    FakeSessionContext.uninstall()
    os.environ.pop("HERMES_SESSION_ID", None)


def test_injects_api_server_chat_id(plugin):
    FakeSessionContext(
        {
            "HERMES_SESSION_PLATFORM": "api_server",
            "HERMES_SESSION_CHAT_ID": "20260815_123853_35a245",
        }
    ).install()
    result = plugin.stamp_origin_session(
        tool_name=TOOL, args={"title": "t", "prompt": "p"}, session_id=""
    )
    assert result["args"]["origin_session_id"] == "20260815_123853_35a245"
    assert result["args"]["title"] == "t"


def test_empty_origin_is_treated_as_absent_and_stamped(plugin):
    FakeSessionContext(
        {
            "HERMES_SESSION_PLATFORM": "api_server",
            "HERMES_SESSION_CHAT_ID": "20260815_123853_35a245",
        }
    ).install()
    result = plugin.stamp_origin_session(
        tool_name=TOOL,
        args={"title": "t", "origin_session_id": ""},
    )
    assert result["args"]["origin_session_id"] == "20260815_123853_35a245"


def test_control_lane_wins(plugin):
    FakeSessionContext(
        {
            "HERMES_CRON_AUTO_DELIVER_CONTROL_SESSION": "20260804_103847_86ca5c",
            "HERMES_SESSION_PLATFORM": "api_server",
            "HERMES_SESSION_CHAT_ID": "api-should-lose",
        }
    ).install()
    result = plugin.stamp_origin_session(
        tool_name=TOOL,
        args={"title": "t"},
        session_id="cron_e594d751447d_20260804_150000",
    )
    assert result["args"]["origin_session_id"] == "20260804_103847_86ca5c"
    assert "control" in result["reason"]


def test_ephemeral_session_is_refused(plugin):
    FakeSessionContext({}).install()
    assert (
        plugin.stamp_origin_session(
            tool_name=TOOL,
            args={"title": "t"},
            session_id="cron_e594d751447d_20260804_150000",
        )
        is None
    )


def test_never_overwrites_explicit_value(plugin):
    FakeSessionContext(
        {
            "HERMES_SESSION_PLATFORM": "api_server",
            "HERMES_SESSION_CHAT_ID": "api-current",
        }
    ).install()
    assert (
        plugin.stamp_origin_session(
            tool_name=TOOL, args={"origin_session_id": "api-chosen"}
        )
        is None
    )


def test_register_wires_middleware_and_hook(plugin):
    registered = {}

    class Ctx:
        def register_middleware(self, name, fn):
            registered[f"mw:{name}"] = fn

        def register_hook(self, name, fn):
            registered[f"hook:{name}"] = fn

    plugin.register(Ctx())
    assert registered["mw:tool_request"] is plugin.stamp_origin_session
    assert registered["hook:post_tool_call"] is plugin.enroll_after_start_mission


def test_enroll_hook_skips_controller_ticks(plugin, monkeypatch):
    called = {}

    def _fake_enroll(**kwargs):
        called.update(kwargs)
        return {"status": "enrolled"}

    import tools.mission_delegation as md

    monkeypatch.setattr(md, "enroll_conversational_start_mission", _fake_enroll)

    FakeSessionContext(
        {"HERMES_CRON_AUTO_DELIVER_CONTROL_SESSION": "20260804_103847_86ca5c"}
    ).install()
    plugin.enroll_after_start_mission(
        tool_name=TOOL,
        status="ok",
        args={
            "origin_session_id": "20260804_103847_86ca5c",
            "title": "writer",
        },
        result='{"mission_id":"m-ctrl"}',
        session_id="cron_tick",
    )
    assert called == {}


def test_enroll_hook_enrolls_desktop_start_mission(plugin, monkeypatch):
    called = {}

    def _fake_enroll(**kwargs):
        called.update(kwargs)
        return {"status": "enrolled"}

    import tools.mission_delegation as md

    monkeypatch.setattr(md, "enroll_conversational_start_mission", _fake_enroll)

    FakeSessionContext(
        {
            "HERMES_SESSION_PLATFORM": "api_server",
            "HERMES_SESSION_CHAT_ID": "20260815_123853_35a245",
        }
    ).install()
    plugin.enroll_after_start_mission(
        tool_name=TOOL,
        status="ok",
        args={
            "origin_session_id": "20260815_123853_35a245",
            "title": "TAP",
            "prompt": "document the API",
        },
        result='{"mission":{"id":"498546da-14b7-48af-afd5-db16a14f5900"}}',
        session_id="20260815_123853_35a245",
    )
    assert called["origin_session_id"] == "20260815_123853_35a245"
    assert called["title"] == "TAP"
    assert "498546da" in str(called["result"])
