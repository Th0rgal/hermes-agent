"""A deliberately trimmed session must not be mistaken for a corrupt one.

`_select_cached_agent_history` guards FTS write corruption (#50502): when
message writes fail silently, the next turn reloads a stale transcript while
the cached AIAgent still holds the full one. Preferring the longer in-memory
copy prevents same-session amnesia.

By length alone that is indistinguishable from a rewind, or from an operator
trimming an oversized conversation — both set `active = 0` and both make the
persisted copy legitimately shorter.

Measured 2026-08-05 on "Verity dev #32": 19 controller reports were deactivated
to bring the conversation from 43k to 27k tokens, and the desktop kept
reporting `Context length exceeded (70,870 tokens). Cannot compress further.`
because a cached agent still held all 177 rows. Only restarting the service
cleared it — the repair could not take effect while the process lived.
"""

from gateway.run import (
    _select_cached_agent_history,
    _session_was_deliberately_trimmed,
)


def _msgs(n):
    return [{"role": "user", "content": f"m{i}"} for i in range(n)]


class _DB:
    def __init__(self, all_rows, active_rows, raises=False):
        self._all = all_rows
        self._active = active_rows
        self._raises = raises

    def get_messages_as_conversation(self, session_id, include_inactive=False):
        if self._raises:
            raise RuntimeError("db down")
        return self._all if include_inactive else self._active


class TestDetectingATrim:
    def test_soft_deleted_rows_mean_a_trim(self):
        assert _session_was_deliberately_trimmed(_DB(_msgs(177), _msgs(158)), "s1")

    def test_no_soft_deleted_rows_mean_no_trim(self):
        assert not _session_was_deliberately_trimmed(_DB(_msgs(158), _msgs(158)), "s1")

    def test_a_broken_db_reports_no_trim(self):
        # The corruption guard is the safe default: losing a conversation to
        # amnesia is worse than one turn that is too large.
        assert not _session_was_deliberately_trimmed(_DB([], [], raises=True), "s1")

    def test_a_missing_db_or_session_reports_no_trim(self):
        assert not _session_was_deliberately_trimmed(None, "s1")
        assert not _session_was_deliberately_trimmed(_DB(_msgs(3), _msgs(1)), "")


class TestTheGuardStillGuards:
    def test_a_shorter_persisted_copy_is_replaced_by_the_live_one(self):
        live = _msgs(177)
        got = _select_cached_agent_history(_msgs(20), live)
        assert len(got) == 177
        assert got is not live, "must be a copy, not the agent's own list"

    def test_an_equal_or_longer_persisted_copy_wins(self):
        persisted = _msgs(177)
        assert _select_cached_agent_history(persisted, _msgs(177)) is persisted
        assert _select_cached_agent_history(persisted, _msgs(5)) is persisted

    def test_a_non_list_live_history_is_ignored(self):
        persisted = _msgs(3)
        assert _select_cached_agent_history(persisted, None) is persisted
        assert _select_cached_agent_history(persisted, "nonsense") is persisted


class TestATrimSurvivesTheGuard:
    def test_the_trimmed_transcript_is_kept(self):
        # The whole point: 158 active rows must not be replaced by the 177 the
        # cached agent remembers.
        persisted = _msgs(158)
        got = _select_cached_agent_history(
            persisted, _msgs(177), persisted_is_authoritative=True
        )
        assert got is persisted

    def test_it_holds_even_when_the_gap_is_large(self):
        persisted = _msgs(2)
        got = _select_cached_agent_history(
            persisted, _msgs(500), persisted_is_authoritative=True
        )
        assert got is persisted

    def test_the_flag_defaults_off(self):
        # Callers that never learned about trimming keep the old behaviour.
        assert len(_select_cached_agent_history(_msgs(2), _msgs(9))) == 9
