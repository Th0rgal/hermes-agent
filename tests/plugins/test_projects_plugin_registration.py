"""The projects plugin registers the six project_* tools byte-for-byte the same
as the old builtin registry.register calls did (name, toolset, schema shape).

This is the parity guard for moving tool registration out of
tools/project_tools.py into plugins/projects/register(ctx).
"""

from plugins.projects import register


class _RecordingCtx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, *, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "kwargs": kwargs,
        }


def test_registers_all_six_project_tools():
    ctx = _RecordingCtx()
    register(ctx)

    assert set(ctx.tools) == {
        "project_list",
        "project_create",
        "project_switch",
        "project_route_set",
        "project_route_get",
        "project_route_clear",
    }


def test_every_tool_is_in_the_project_toolset_with_a_matching_schema_name():
    ctx = _RecordingCtx()
    register(ctx)

    for name, spec in ctx.tools.items():
        assert spec["toolset"] == "project"
        assert spec["schema"]["name"] == name
        assert "parameters" in spec["schema"]
        assert callable(spec["handler"])


def test_route_and_create_tools_require_expected_params():
    ctx = _RecordingCtx()
    register(ctx)

    assert ctx.tools["project_create"]["schema"]["parameters"]["required"] == ["name"]
    for routed in ("project_route_set", "project_route_get", "project_route_clear", "project_switch"):
        assert ctx.tools[routed]["schema"]["parameters"]["required"] == ["project"]
