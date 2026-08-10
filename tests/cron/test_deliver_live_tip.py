"""Cron deliveries land on the LIVE TIP of the target session's lineage.

A cron job stores the delivery session id captured at creation time. When the
conversation rolls over via compression continuations — and especially when a
race FORKS it into sibling branches — the delivery must follow the lineage to
the freshest continuation (the same leaf interactive resume shows the human),
never append to the stale stored id, and never create a new branch.
"""

from __future__ import annotations

import time

import pytest

import hermes_state
from cron.scheduler import _deliver_to_local_session

BASE = int(time.time()) - 10_000


@pytest.fixture
def state_db(tmp_path, monkeypatch):
    """Hermetic state.db for the scheduler's lazy SessionDB() opens."""
    state_path = tmp_path / "state.db"
    real_session_db = hermes_state.SessionDB

    def _session_db_factory(*args, **kwargs):
        if not args and "db_path" not in kwargs:
            kwargs["db_path"] = state_path
        return real_session_db(*args, **kwargs)

    monkeypatch.setattr(hermes_state, "SessionDB", _session_db_factory)
    db = real_session_db(db_path=state_path)
    try:
        yield db
    finally:
        db.close()


def _mk(db, sid, parent=None, *, started, ended=None, end_reason=None):
    db.create_session(sid, source="desktop", parent_session_id=parent)
    db._conn.execute(
        "UPDATE sessions SET started_at = ?, ended_at = ?, end_reason = ?, "
        "last_activity_at = ? WHERE id = ?",
        (started, ended, end_reason, started, sid),
    )
    db._conn.commit()


def _delivered_texts(db, sid):
    return [str(m.get("content") or "") for m in db.get_messages(sid)]


JOB = {"id": "job-1", "name": "nightly report"}


def test_no_rollover_delivers_into_the_stored_session(state_db):
    _mk(state_db, "solo", started=BASE)
    err = _deliver_to_local_session(JOB, "desktop", "solo", "hello solo")
    assert err is None
    assert any("hello solo" in t for t in _delivered_texts(state_db, "solo"))


def test_chain_root_delivery_lands_in_the_tip(state_db):
    # root -(compression)-> a -(compression)-> b: deliver to root → lands in b.
    _mk(state_db, "root", started=BASE, ended=BASE + 50, end_reason="compression")
    _mk(state_db, "a", "root", started=BASE + 100,
        ended=BASE + 150, end_reason="compression")
    _mk(state_db, "b", "a", started=BASE + 200)

    err = _deliver_to_local_session(JOB, "desktop", "root", "report text")
    assert err is None
    assert any("report text" in t for t in _delivered_texts(state_db, "b"))
    assert _delivered_texts(state_db, "root") == []
    assert _delivered_texts(state_db, "a") == []


def test_forked_chain_delivers_to_the_live_leaf_deterministically(state_db):
    # The verity split: deliveries were landing on one sibling while the
    # human interacted on another. The live leaf must win even though the
    # stale sibling is newer by started_at.
    _mk(state_db, "root", started=BASE, ended=BASE + 50, end_reason="compression")
    _mk(state_db, "live_leaf", "root", started=BASE + 100)
    _mk(state_db, "stale_leaf", "root", started=BASE + 200,
        ended=BASE + 250, end_reason="ws_orphan_reap")

    for i in range(2):  # deterministic across repeated deliveries
        job = {**JOB, "_delivery_run_id": f"run-{i}"}
        err = _deliver_to_local_session(job, "desktop", "root", f"delivery {i}")
        assert err is None

    live = _delivered_texts(state_db, "live_leaf")
    assert any("delivery 0" in t for t in live)
    assert any("delivery 1" in t for t in live)
    assert _delivered_texts(state_db, "root") == []
    assert _delivered_texts(state_db, "stale_leaf") == []


def test_delivery_never_creates_a_new_branch(state_db):
    _mk(state_db, "root", started=BASE, ended=BASE + 50, end_reason="compression")
    _mk(state_db, "tip", "root", started=BASE + 100)
    before = {
        r["id"]
        for r in state_db._conn.execute("SELECT id FROM sessions").fetchall()
    }
    assert _deliver_to_local_session(JOB, "desktop", "root", "no new branch") is None
    after = {
        r["id"]
        for r in state_db._conn.execute("SELECT id FROM sessions").fetchall()
    }
    assert after == before
