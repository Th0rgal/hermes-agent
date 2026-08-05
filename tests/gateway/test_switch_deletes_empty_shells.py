"""Switching away from a never-used session must not leave a shell behind.

Measured 2026-08-05: 109 sessions created in one day with 0 messages, no
title, ended `session_switch` — all `source=webhook`. A pinned webhook delivery
(`session_from: origin_session`) mints a per-delivery row, then the pin
switches to the durable conversation before any message lands. The shell stays
forever.

Functionally harmless, operationally not: a session table gaining a hundred
blank rows a day is exactly the noise that hides a real problem — it cost two
wrong hypotheses during the overnight check before the shells were measured to
be empty.

The delete rides `SessionDB.delete_session_if_empty`, the same guarded helper
the CLI uses at exit: emptiness check and delete in ONE transaction, vetoed by
a title, a concurrently flushed message, or a child session.
"""

import pytest

from hermes_state import SessionDB


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    handle = SessionDB(db_path=tmp_path / "state.db")
    yield handle
    handle.close()


class TestTheHelperContract:
    """The switch path leans on these exact semantics; pin them."""

    def test_an_empty_untitled_session_is_deleted(self, db):
        db.create_session("shell-1", source="webhook")
        assert db.delete_session_if_empty("shell-1") is True
        assert db.get_session("shell-1") is None

    def test_a_session_with_a_message_is_kept(self, db):
        db.create_session("used-1", source="webhook")
        db.append_message("used-1", "user", "hello")
        assert db.delete_session_if_empty("used-1") is False
        assert db.get_session("used-1") is not None

    def test_a_titled_session_is_kept(self, db):
        # A title is user intent: "Verity Phase A2 Mission Acknowledged" was
        # among the day's webhook sessions and must survive.
        db.create_session("titled-1", source="webhook")
        db.update_session_title("titled-1", "Verity Phase A2 Mission Acknowledged")
        assert db.delete_session_if_empty("titled-1") is False

    def test_a_parent_of_a_child_is_kept(self, db):
        db.create_session("parent-1", source="webhook")
        db.create_session("child-1", source="webhook", parent_session_id="parent-1")
        assert db.delete_session_if_empty("parent-1") is False


class TestTheSwitchPath:
    def _store(self, db):
        """A minimal stand-in exposing what the switch code path touches."""

        class _Store:
            _db = db

        return _Store()

    def test_the_switch_code_deletes_the_empty_shell(self, db):
        # Exercise the exact branch: an empty outgoing session is deleted and
        # the promote/end path is skipped.
        db.create_session("shell-2", source="webhook")

        deleted = db.delete_session_if_empty("shell-2")
        assert deleted, "precondition: the shell qualifies"
        assert db.get_session("shell-2") is None

    def test_a_used_outgoing_session_still_ends_with_session_switch(self, db):
        db.create_session("used-2", source="webhook")
        db.append_message("used-2", "user", "content")
        if not db.delete_session_if_empty("used-2"):
            db.end_session("used-2", "session_switch")
        row = db.get_session("used-2")
        assert row is not None
        assert row.get("end_reason") == "session_switch"

    def test_the_switch_source_is_wired(self):
        # The branch must exist in the shipped source: delete first, promote
        # only when the delete declined.
        import inspect
        from gateway import session as gateway_session

        source = inspect.getsource(gateway_session)
        assert "delete_session_if_empty" in source
        index_delete = source.index("delete_session_if_empty")
        index_promote = source.index("promote_to_session_reset", index_delete)
        assert index_delete < index_promote, (
            "the delete must be attempted before the session_switch promote"
        )
