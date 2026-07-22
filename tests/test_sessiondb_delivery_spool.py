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


def test_spool_survives_and_replays_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    db = SessionDB()
    try:
        db.create_session("api-1", source="api_server")
    finally:
        db.close()

    spool_session_delivery("cron:api-1:j:r1", "api-1", "assistant", "done")
    assert replay_session_delivery_spool() == {"replayed": 1, "failed": 0}
    # Recreating the same deterministic delivery after a crash is harmless.
    spool_session_delivery("cron:api-1:j:r1", "api-1", "assistant", "done")
    assert replay_session_delivery_spool() == {"replayed": 1, "failed": 0}

    verify = SessionDB()
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
