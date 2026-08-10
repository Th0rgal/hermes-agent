import sqlite3
import threading
import time

from hermes_state import (
    SessionDB,
    replay_session_delivery_spool,
    spool_session_delivery,
)


def test_observed_deliveries_since_lists_only_new_delivery_rows(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("desk-1", source="tui")
        db.append_message("desk-1", role="user", content="start the watch")
        db.append_message("desk-1", role="assistant", content="Watching.")
        baseline = db.latest_message_id()
        assert baseline > 0

        db.append_message(
            "desk-1",
            role="assistant",
            content="[Cron delivery: watcher]\nBuild finished.",
            observed=True,
            delivery_id="cron:tui:desk-1:j:r1",
        )
        # Ordinary turns after the baseline are not deliveries.
        db.append_message("desk-1", role="assistant", content="Anything else?")

        rows = db.list_observed_deliveries_since(baseline)
        assert [row["session_id"] for row in rows] == ["desk-1"]
        assert rows[0]["content"].startswith("[Cron delivery: watcher]")
        assert rows[0]["id"] > baseline

        # The returned highwater excludes nothing on the next pass.
        assert db.list_observed_deliveries_since(rows[-1]["id"] + 1) == []
    finally:
        db.close()


def test_delivery_id_deduplicates_transcript_append(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("api-1", source="api_server")
        first = db.append_message(
            "api-1", role="assistant", content="done", delivery_id="cron:api-1:j:r1"
        )
        second = db.append_message(
            "api-1", role="assistant", content="done", delivery_id="cron:api-1:j:r1"
        )
        assert second == first
        assert [message["content"] for message in db.get_messages("api-1")] == ["done"]
    finally:
        db.close()


def test_legacy_delivery_receipts_migrate_and_cascade(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_session("api-1", source="api_server")
        db.append_message(
            "api-1", role="assistant", content="done", delivery_id="cron:api-1:j:r1"
        )
    finally:
        db.close()

    legacy = sqlite3.connect(path, isolation_level=None)
    try:
        legacy.execute("PRAGMA foreign_keys=OFF")
        legacy.execute("ALTER TABLE delivery_receipts RENAME TO delivery_receipts_v23")
        legacy.execute(
            """CREATE TABLE delivery_receipts (
                   delivery_id TEXT PRIMARY KEY,
                   message_id INTEGER,
                   created_at REAL NOT NULL,
                   FOREIGN KEY (message_id) REFERENCES messages(id)
               )"""
        )
        legacy.execute(
            """INSERT INTO delivery_receipts (delivery_id, message_id, created_at)
               SELECT delivery_id, message_id, created_at FROM delivery_receipts_v23"""
        )
        legacy.execute("DROP TABLE delivery_receipts_v23")
        legacy.execute("UPDATE schema_version SET version = 23")
    finally:
        legacy.close()

    migrated = SessionDB(db_path=path)
    try:
        foreign_keys = migrated._conn.execute(
            "PRAGMA foreign_key_list('delivery_receipts')"
        ).fetchall()
        assert any(row[2] == "messages" and row[6] == "CASCADE" for row in foreign_keys)
        migrated.clear_messages("api-1")
        receipt_count = migrated._conn.execute(
            "SELECT COUNT(*) FROM delivery_receipts"
        ).fetchone()[0]
        assert receipt_count == 0
        assert migrated.delete_session("api-1") is True
    finally:
        migrated.close()


def test_spool_survives_and_replays_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_session("api-1", source="api_server")
    finally:
        db.close()

    spool_session_delivery("cron:api-1:j:r1", "api-1", "assistant", "done")
    assert replay_session_delivery_spool(db_path=path) == {"replayed": 1, "failed": 0}
    # Recreating the same deterministic delivery after a crash is harmless.
    spool_session_delivery("cron:api-1:j:r1", "api-1", "assistant", "done")
    assert replay_session_delivery_spool(db_path=path) == {"replayed": 1, "failed": 0}

    verify = SessionDB(db_path=path)
    try:
        assert [message["content"] for message in verify.get_messages("api-1")] == ["done"]
    finally:
        verify.close()


def test_spool_preserves_observed_delivery_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_session("desktop-1", source="desktop")
    finally:
        db.close()

    spool_session_delivery(
        "cron:desktop:desktop-1:j:r1",
        "desktop-1",
        "assistant",
        "[Cron delivery: j]\nDone.",
        observed=True,
    )
    assert replay_session_delivery_spool(db_path=path) == {"replayed": 1, "failed": 0}

    verify = SessionDB(path)
    try:
        # Storage keeps the assistant role: the dashboard delivery watcher
        # filters on it in SQL, and the desktop divider checks it too.
        [stored] = verify.get_messages("desktop-1")
        assert stored["role"] == "assistant"
        assert stored["observed"]

        # The replay projection reframes it as input, because the model must
        # not read another conversation's report as its own turn.
        [replayed] = verify.get_messages_as_conversation("desktop-1")
        assert replayed["role"] == "user"
        assert replayed["observed"] is True
    finally:
        verify.close()


def test_spool_replay_follows_unique_compression_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_session("parent", source="api_server")
        db.create_session(
            "continuation-1", source="api_server", parent_session_id="parent"
        )
        db.end_session("parent", end_reason="compression")
        db.create_session(
            "continuation-2",
            source="api_server",
            parent_session_id="continuation-1",
        )
        db.end_session("continuation-1", end_reason="compression")
    finally:
        db.close()

    spool_session_delivery(
        "cron:parent:j:r1", "parent", "assistant", "delivered to the live tip"
    )
    assert replay_session_delivery_spool(db_path=path) == {"replayed": 1, "failed": 0}

    verify = SessionDB(db_path=path)
    try:
        assert verify.get_messages("parent") == []
        assert verify.get_messages("continuation-1") == []
        assert [
            message["content"] for message in verify.get_messages("continuation-2")
        ] == ["delivered to the live tip"]
    finally:
        verify.close()


def test_spool_replay_forked_lineage_lands_on_live_leaf(tmp_path, monkeypatch):
    """A forked lineage replays deterministically onto the live/newest leaf.

    Historical behavior kept the spool entry forever on sibling ambiguity —
    which meant a delivery whose target had forked (compression races do
    this) was never delivered at all, while the human kept the conversation
    going on one of the leaves. Replay now routes through the shared
    ``resolve_delivery_session_id`` live-tip resolver: the live (non-ended)
    leaf wins, else the newest, deterministically — never a new branch.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_session("parent", source="api_server")
        db.create_session("child-a", source="api_server", parent_session_id="parent")
        db.create_session("child-b", source="api_server", parent_session_id="parent")
        db.end_session("parent", end_reason="compression")
        # The human abandoned child-a (reaped); child-b is the live leaf.
        db.end_session("child-a", end_reason="ws_orphan_reap")
    finally:
        db.close()

    spool_path = spool_session_delivery(
        "cron:parent:j:r2", "parent", "assistant", "lands on the live leaf"
    )
    assert replay_session_delivery_spool(db_path=path) == {"replayed": 1, "failed": 0}
    assert not spool_path.exists()

    verify = SessionDB(db_path=path)
    try:
        assert verify.get_messages("parent") == []
        assert verify.get_messages("child-a") == []
        assert [
            message["content"] for message in verify.get_messages("child-b")
        ] == ["lands on the live leaf"]
    finally:
        verify.close()


def test_concurrent_writer_waits_for_lock_without_losing_delivery(tmp_path, monkeypatch):
    # NB: this used to also assert a configurable `write_retry_deadline_s`.
    # Upstream dropped that knob in the refactor the fork series was rebased
    # onto (no `retry_deadline` remains anywhere in the source), so the
    # assertion outlived its feature. What it guarded — a writer blocked on a
    # SQLite lock must wait and still land its delivery — is exercised below
    # against the shipped defaults.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "state.db"
    setup = SessionDB(path)
    setup.create_session("api-1", source="api_server")
    setup.close()

    blocker = sqlite3.connect(path, timeout=0.1, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    result = []

    def writer():
        db = SessionDB(path)
        try:
            result.append(
                db.append_message(
                    "api-1",
                    role="assistant",
                    content="eventually committed",
                    delivery_id="cron:api-1:j:r2",
                )
            )
        finally:
            db.close()

    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.25)
    blocker.rollback()
    blocker.close()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert len(result) == 1
    verify = SessionDB(path)
    try:
        assert verify.get_messages("api-1")[-1]["content"] == "eventually committed"
    finally:
        verify.close()
