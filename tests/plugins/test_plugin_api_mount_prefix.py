"""_plugin_api_mount_prefix — bundled plugins may opt into a custom mount
prefix; user/project plugins always get the forced /api/plugins/<name>."""

from hermes_cli.web_server import _plugin_api_mount_prefix


def test_default_namespace_when_no_prefix():
    assert _plugin_api_mount_prefix({"name": "foo", "source": "bundled"}) == "/api/plugins/foo"


def test_bundled_can_mount_at_root():
    assert _plugin_api_mount_prefix({"name": "projects", "source": "bundled", "api_prefix": ""}) == ""


def test_bundled_custom_prefix():
    assert (
        _plugin_api_mount_prefix({"name": "projects", "source": "bundled", "api_prefix": "/api/"})
        == "/api"
    )


def test_user_plugin_prefix_is_ignored():
    # A user/project plugin cannot escape its namespace, whatever the manifest asks.
    assert (
        _plugin_api_mount_prefix({"name": "evil", "source": "user", "api_prefix": ""})
        == "/api/plugins/evil"
    )
    assert (
        _plugin_api_mount_prefix({"name": "evil", "source": "user", "api_prefix": "/api"})
        == "/api/plugins/evil"
    )


def test_non_absolute_custom_prefix_falls_back():
    assert (
        _plugin_api_mount_prefix({"name": "projects", "source": "bundled", "api_prefix": "api"})
        == "/api/plugins/projects"
    )
