"""A row loaded from the database must not be written back to it.

Measured 2026-08-05, session `20260803_161851_073bb5`:

    messages: 259
    tool_call_id duplicated: 69 / 69
    adjacent duplicates:      0 / 69      <- NOT a per-tool double write
    constant offset:        127           (2->129, 3->130, 4->131)
    identical at offset:    126 / 127
    ts[127] == ts[0]        exactly

The copied timestamps are the proof: `_flush_messages_to_session_db` reuses a
message's own `timestamp` when it writes, so a re-written row keeps the
original. These were rewrites of loaded rows, not fresh calls. The session then
passed the context limit and could no longer compress — an absorbing state, and
the reason it had to be abandoned rather than repaired.

The flush writes anything not carrying `_DB_PERSISTED_MARKER`. A cold resume
seeds the conversation from `get_messages_as_conversation`, whose rows carried
no marker, so every one of them looked new.

The marker is OPT-IN (`mark_persisted=True`), mirroring `include_row_ids`:
every live-replay caller — the ones whose loaded list becomes an agent's
working conversation_history and therefore reaches the flush — passes it,
while default consumers (ACP restore, export, inspection) get the transcript
in its historical shape with no synthetic underscore keys (see
test_message_reactions.test_row_id_is_opt_in_and_never_reaches_the_provider
and tests/acp/test_session.py::test_assistant_reasoning_fields_persisted,
which pin that clean default shape).
"""

import pytest

from hermes_state import SessionDB, _DB_PERSISTED_MARKER


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    handle = SessionDB(db_path=tmp_path / "state.db")
    handle.create_session("s1", source="cli")
    yield handle
    handle.close()


class TestTheMarker:
    def test_the_spelling_matches_the_flush(self):
        # Three copies of this string exist (hermes_state, run_agent,
        # context_compressor). If they drift, the marker silently stops
        # matching and the duplication comes straight back — with no error
        # anywhere, which is exactly how it went unnoticed.
        from run_agent import _DB_PERSISTED_MARKER as flush_marker
        from agent.context_compressor import _DB_PERSISTED_MARKER as compressor_marker

        assert _DB_PERSISTED_MARKER == flush_marker == compressor_marker

    def test_it_is_underscore_prefixed(self):
        # Transports strip underscore-prefixed keys before the wire, which is
        # what makes it safe to attach to every replayed message.
        assert _DB_PERSISTED_MARKER.startswith("_")


class TestRestoredRows:
    def test_default_consumers_see_the_historical_shape(self, db):
        # The marker is opt-in: without mark_persisted=True the loader must
        # not attach it (nor any other synthetic underscore key) — ACP
        # restore, export and inspection get the transcript verbatim.
        db.append_message("s1", "user", "hello")
        [restored] = db.get_messages_as_conversation("s1")
        assert _DB_PERSISTED_MARKER not in restored

    def test_a_restored_message_is_marked(self, db):
        db.append_message("s1", "user", "hello")
        [restored] = db.get_messages_as_conversation("s1", mark_persisted=True)
        assert restored.get(_DB_PERSISTED_MARKER) is True

    def test_every_role_is_marked(self, db):
        db.append_message("s1", "user", "hello")
        db.append_message("s1", "assistant", "hi")
        db.append_message("s1", "tool", "result", tool_call_id="call_1")
        restored = db.get_messages_as_conversation("s1", mark_persisted=True)
        assert len(restored) == 3
        assert all(m.get(_DB_PERSISTED_MARKER) is True for m in restored), restored

    def test_repair_alternation_preserves_the_marker(self, db):
        # The live-replay callers pass repair_alternation=True. If the repair
        # dropped the marker, the very paths that resume a session would be
        # the ones still duplicating.
        db.append_message("s1", "user", "one")
        db.append_message("s1", "user", "two")
        restored = db.get_messages_as_conversation(
            "s1", repair_alternation=True, mark_persisted=True
        )
        assert restored
        assert all(m.get(_DB_PERSISTED_MARKER) is True for m in restored), restored

    def test_a_reframed_delivery_is_still_marked(self, db):
        # Delivery reframing rewrites role and content on the way out; it must
        # not cost the row its persistence marker, or deliveries alone would
        # duplicate on every resume.
        db.append_message(
            "s1", "assistant", "[Cron delivery: watcher]\nDone.", observed=True
        )
        [restored] = db.get_messages_as_conversation("s1", mark_persisted=True)
        assert restored["role"] == "user"
        assert restored.get(_DB_PERSISTED_MARKER) is True

    def test_ancestors_are_marked_too(self, db):
        # include_ancestors walks a compression chain. Those rows are just as
        # durable as the tip's, and a continuation is precisely where a cold
        # resume happens.
        db.append_message("s1", "user", "parent turn")
        db.create_session("s2", source="cli", parent_session_id="s1")
        db.append_message("s2", "user", "child turn")
        restored = db.get_messages_as_conversation(
            "s2", include_ancestors=True, mark_persisted=True
        )
        assert len(restored) >= 2
        assert all(m.get(_DB_PERSISTED_MARKER) is True for m in restored), restored


class TestTheIncidentShape:
    def test_a_reloaded_transcript_reports_nothing_new_to_write(self, db):
        """The whole bug in one assertion.

        The flush writes messages lacking the marker. Reload a transcript and
        count them: before the fix this was the entire history — 127 messages
        in the measured session — and each was appended again, carrying its
        original timestamp.
        """
        for i in range(10):
            db.append_message("s1", "user", f"turn {i}")
            db.append_message("s1", "assistant", f"reply {i}")

        reloaded = db.get_messages_as_conversation(
            "s1", repair_alternation=True, mark_persisted=True
        )
        unwritten = [m for m in reloaded if not m.get(_DB_PERSISTED_MARKER)]

        assert len(reloaded) == 20
        assert unwritten == [], (
            f"{len(unwritten)} restored messages would be written back to the "
            "database they were just read from"
        )
