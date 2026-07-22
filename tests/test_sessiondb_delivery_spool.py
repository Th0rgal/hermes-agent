import sqlite3
import threading
import time

from hermes_state import (
    SessionDB,
    replay_session_delivery_spool,
    spool_session_delivery,
)


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


def test_concurrent_writer_waits_for_lock_without_losing_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "session_db:\n  write_retry_deadline_seconds: 2\n",
        encoding="utf-8",
    )
    path = tmp_path / "state.db"
    setup = SessionDB(path)
    assert setup._write_retry_deadline_s == 2.0
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
