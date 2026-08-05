"""A resumed CLI session must publish its id, or its work is unattributable.

Measured 2026-08-05. The Verity controller conversation was resumed from the
CLI and dispatched three missions. One of them:

    GET /api/control/missions/57c1dfb4-…
    status=active | project=None | track=None | origin_session_id=None

The mission was real and running. It was also invisible: absent from
`?project_prefix=verity`, absent from `?origin_session_id=…`, absent from the
board. The controller reported a dispatch and every inventory showed none —
which is what "the controller is spinning" looked like from outside.

The chain: the `sandboxed-origin-session` plugin stamps `origin_session_id` from
`HERMES_SESSION_ID`; the sandboxed.sh server fills a mission's `project` from
the conversation binding keyed on that id. With no id, neither happens.

`gateway.session_context.set_current_session_id` exists for precisely this —
its docstring names the CLI and `/resume`. The CLI reassigned `self.session_id`
in four places and never called it.
"""

from unittest.mock import patch

from hermes_cli.cli_agent_setup_mixin import _publish_active_session_id


class TestPublishing:
    def test_it_publishes_the_id(self):
        with patch("gateway.session_context.set_current_session_id") as setter:
            _publish_active_session_id("20260805_093524_88112a")
        setter.assert_called_once_with("20260805_093524_88112a")

    def test_an_empty_id_publishes_nothing(self):
        # Clearing the process-wide session identity with "" would be worse
        # than leaving the previous one: tools would resolve to nothing.
        with patch("gateway.session_context.set_current_session_id") as setter:
            _publish_active_session_id("")
        setter.assert_not_called()

    def test_it_never_raises(self):
        # This sits on the resume path. A session that cannot publish its id is
        # still a usable session; failing the resume over it would trade an
        # attribution gap for a dead conversation.
        with patch(
            "gateway.session_context.set_current_session_id",
            side_effect=RuntimeError("boom"),
        ):
            _publish_active_session_id("s1")

    def test_a_missing_gateway_module_is_tolerated(self):
        with patch.dict("sys.modules", {"gateway.session_context": None}):
            _publish_active_session_id("s1")


class TestEveryRotationPointCallsIt:
    """Four assignment sites; a new one that forgets is the bug coming back."""

    def _source(self, module):
        import inspect

        return inspect.getsource(module)

    def test_the_setup_mixin_publishes_after_each_assignment(self):
        from hermes_cli import cli_agent_setup_mixin

        source = self._source(cli_agent_setup_mixin)
        assignments = source.count("self.session_id = resolved_id")
        publishes = source.count("_publish_active_session_id(self.session_id)")
        assert assignments >= 2
        assert publishes >= assignments, (
            f"{assignments} session rotations, {publishes} published"
        )

    def test_the_commands_mixin_publishes_after_each_assignment(self):
        from hermes_cli import cli_commands_mixin

        source = self._source(cli_commands_mixin)
        assignments = source.count("self.session_id = target_id") + source.count(
            "self.session_id = new_session_id"
        )
        publishes = source.count("_publish_active_session_id(self.session_id)")
        assert assignments >= 2
        assert publishes >= assignments, (
            f"{assignments} session rotations, {publishes} published"
        )


class TestTheSanctionedMechanism:
    def test_the_helper_it_delegates_to_still_exists(self):
        # If set_current_session_id is ever renamed, this fails loudly here
        # rather than silently degrading to "no id published" at runtime --
        # the exact failure mode being fixed.
        from gateway.session_context import set_current_session_id

        assert callable(set_current_session_id)

    def test_hermes_session_id_is_a_bridged_var(self):
        # It must stay one: bridged vars are injected per command and stripped
        # from the shared bash snapshot, which is what keeps one session's id
        # from leaking into another's tools.
        from gateway.session_context import _VAR_MAP

        assert "HERMES_SESSION_ID" in _VAR_MAP
