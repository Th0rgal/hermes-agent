"""A skill body must be sent once per conversation, not once per invocation.

Measured on production, 2026-08-05:

  Verity dev #28        sandboxed-sh-missions injected 5x  (~435 KB)
  Audit Lean de Lido #25              injected 4x  (3 copies = 75% of context)
  Lean Silicon #8                     compression timed out, 418k tokens

The bodies are large — `sandboxed-sh-missions` is 87 KB — so a handful of
re-invocations is enough to push a conversation past the context limit. Once
there, compression times out and the session cannot shrink again: an absorbing
state.
"""

from unittest.mock import MagicMock, patch

from agent import skill_commands


class _DB:
    def __init__(self, messages):
        self._messages = messages
        self.closed = False

    def get_messages(self, session_id):
        return self._messages

    def close(self):
        self.closed = True


def _body_message(skill_name):
    return {
        "role": "user",
        "content": (
            f'{skill_commands._SKILL_INVOCATION_PREFIX}"{skill_name}" skill, '
            f"indicating they want you to follow its instructions. "
            f"{skill_commands._SINGLE_SKILL_MARKER}\n\n# the whole body"
        ),
    }


class TestSkillAlreadyLoaded:
    def test_a_previous_full_body_counts_as_loaded(self):
        db = _DB([_body_message("sandboxed-sh-missions")])
        with patch("hermes_state.SessionDB", return_value=db):
            assert skill_commands._skill_already_loaded("sess-1", "sandboxed-sh-missions")
        assert db.closed, "the database handle must be released"

    def test_a_different_skill_does_not_count(self):
        db = _DB([_body_message("paloma-projects")])
        with patch("hermes_state.SessionDB", return_value=db):
            assert not skill_commands._skill_already_loaded("sess-1", "sandboxed-sh-missions")

    def test_the_short_reference_does_not_count_as_a_body(self):
        # The reference keeps the invocation prefix so the UI still renders the
        # command. If that alone counted as "loaded", a session that only ever
        # saw references would never receive the body at all.
        reference = skill_commands._SKILL_ALREADY_LOADED_TEMPLATE.format(
            skill_name="sandboxed-sh-missions"
        )
        db = _DB([{"role": "user", "content": reference}])
        with patch("hermes_state.SessionDB", return_value=db):
            assert not skill_commands._skill_already_loaded("sess-1", "sandboxed-sh-missions")

    def test_an_assistant_echo_does_not_count(self):
        # Agents quote scaffolding back into their own replies; only a real
        # user-role injection is a body.
        message = _body_message("sandboxed-sh-missions")
        message["role"] = "assistant"
        db = _DB([message])
        with patch("hermes_state.SessionDB", return_value=db):
            assert not skill_commands._skill_already_loaded("sess-1", "sandboxed-sh-missions")

    def test_no_session_means_not_loaded(self):
        assert not skill_commands._skill_already_loaded(None, "x")
        assert not skill_commands._skill_already_loaded("", "x")
        assert not skill_commands._skill_already_loaded("sess-1", "")

    def test_it_fails_open(self):
        # A redundant body is recoverable; a missing one is not. Any error
        # must therefore answer "not loaded" and let the body through.
        broken = MagicMock(side_effect=RuntimeError("db down"))
        with patch("hermes_state.SessionDB", broken):
            assert not skill_commands._skill_already_loaded("sess-1", "x")


class TestReferenceForm:
    def test_it_keeps_the_prefix_the_display_projection_parses(self):
        reference = skill_commands._SKILL_ALREADY_LOADED_TEMPLATE.format(skill_name="work")
        assert reference.startswith(skill_commands._SKILL_INVOCATION_PREFIX)
        assert '"work"' in reference

    def test_it_omits_the_loaded_below_marker(self):
        # That marker is what distinguishes a body from a reference, both for
        # the check above and for anything else parsing the transcript.
        reference = skill_commands._SKILL_ALREADY_LOADED_TEMPLATE.format(skill_name="work")
        assert skill_commands._SINGLE_SKILL_MARKER not in reference

    def test_it_is_orders_of_magnitude_smaller_than_a_body(self):
        reference = skill_commands._SKILL_ALREADY_LOADED_TEMPLATE.format(
            skill_name="sandboxed-sh-missions"
        )
        assert len(reference) < 300, "the point is that it is tiny"


class TestTheContinuationChain:
    """A rollover must not undo the repair.

    Measured 2026-08-05. "Audit formel Lean de Lido #26" was repaired by hand at
    14:56; its continuation #27 was 85 messages old at 15:19 and already
    reporting "Context length exceeded. Cannot compress further." — the 94 345
    character `sandboxed-sh-missions` body was 46% of it.

    A continuation is a new session id and the same conversation. Checking one
    session row let the body back in at full size on every rollover, which is
    every few hours on an active project.
    """

    class _ChainDB:
        """SessionDB stand-in: ancestors carry the body, the tip does not."""

        def __init__(self, ancestor_messages, own_messages):
            self._ancestors = ancestor_messages
            self._own = own_messages
            self.closed = False
            self.asked_for_ancestors = False

        def get_messages_as_conversation(self, session_id, include_ancestors=False):
            if include_ancestors:
                self.asked_for_ancestors = True
                return self._ancestors + self._own
            return self._own

        def get_messages(self, session_id):
            return self._own

        def close(self):
            self.closed = True

    def test_a_body_in_an_ancestor_counts_as_loaded(self):
        db = self._ChainDB([_body_message("sandboxed-sh-missions")], [])
        with patch("hermes_state.SessionDB", return_value=db):
            assert skill_commands._skill_already_loaded("child", "sandboxed-sh-missions")
        assert db.asked_for_ancestors, "the chain must be walked, not just the tip"
        assert db.closed

    def test_an_ancestor_carrying_a_different_skill_does_not_count(self):
        db = self._ChainDB([_body_message("paloma-projects")], [])
        with patch("hermes_state.SessionDB", return_value=db):
            assert not skill_commands._skill_already_loaded("child", "sandboxed-sh-missions")

    def test_it_falls_back_to_the_current_session(self):
        # An ancestor walk is a nicety; losing it must not lose the
        # within-session protection that already shipped.
        class _NoAncestors(self._ChainDB.__mro__[0]):
            def get_messages_as_conversation(self, session_id, include_ancestors=False):
                raise RuntimeError("no chain support")

        db = _NoAncestors([], [_body_message("sandboxed-sh-missions")])
        with patch("hermes_state.SessionDB", return_value=db):
            assert skill_commands._skill_already_loaded("s", "sandboxed-sh-missions")

    def test_an_empty_chain_still_sends_the_body(self):
        db = self._ChainDB([], [])
        with patch("hermes_state.SessionDB", return_value=db):
            assert not skill_commands._skill_already_loaded("child", "sandboxed-sh-missions")


class TestTheGuardAnnouncesItsOwnBlindness:
    """A guard that cannot run must say so.

    Measured 2026-08-05: 12 full injections of `sandboxed-sh-missions` in four
    hours across 11 sessions — 1.1 MB, roughly 271k tokens on one document.
    Seven of those sessions were ephemeral (`cron_…` ticks and subagents), where
    a per-conversation guard cannot help by construction. The rest showed a
    guard that had simply never had anything to search.

    Nothing in the logs said "I could not check". Silence read as "checked,
    nothing found" — the same failure shape as a controller ticking
    `last_status: ok` while dispatching nothing.
    """

    def test_a_missing_session_id_is_logged(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger=skill_commands.logger.name):
            assert not skill_commands._skill_already_loaded(None, "sandboxed-sh-missions")
        assert "guard disabled" in caplog.text
        assert "sandboxed-sh-missions" in caplog.text

    def test_an_empty_session_id_is_logged_too(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger=skill_commands.logger.name):
            assert not skill_commands._skill_already_loaded("", "x")
        assert "guard disabled" in caplog.text

    def test_a_missing_skill_name_is_not_worth_a_warning(self, caplog):
        # No skill name means no injection is happening at all. Warning here
        # would be noise, and noise is what makes a real warning invisible.
        import logging

        with caplog.at_level(logging.WARNING, logger=skill_commands.logger.name):
            assert not skill_commands._skill_already_loaded("s1", "")
        assert caplog.text == ""

    def test_a_working_guard_stays_quiet(self, caplog):
        import logging

        db = _DB([_body_message("sandboxed-sh-missions")])
        with caplog.at_level(logging.WARNING, logger=skill_commands.logger.name):
            with patch("hermes_state.SessionDB", return_value=db):
                assert skill_commands._skill_already_loaded("s1", "sandboxed-sh-missions")
        assert "guard disabled" not in caplog.text
