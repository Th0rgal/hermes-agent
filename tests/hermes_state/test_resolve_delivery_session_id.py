"""The shared delivery resolver lands deliveries on the LIVE TIP of a lineage.

Cron ``deliver=`` targets, project routes, webhook session pinning, and the
durable spool all store a session id captured at creation/dispatch time. The
conversation keeps moving: context compression ends the session and forks a
continuation child, and gateway races can even fork SIBLING continuations.
``SessionDB.resolve_delivery_session_id`` is the single shared resolver every
delivery entry point routes through: follow the continuation chain to the
freshest leaf, deterministically preferring a live (non-ended) leaf, else the
newest — and never invent a new branch.
"""
import time

import pytest

from hermes_state import SessionDB, spool_session_delivery


@pytest.fixture
def db(tmp_path):
    d = SessionDB(tmp_path / "state.db")
    yield d
    d.close()


BASE = int(time.time()) - 10_000


def _mk(db, sid, parent=None, *, started, ended=None, end_reason=None, source="desktop"):
    db.create_session(sid, source=source, parent_session_id=parent)
    db._conn.execute(
        "UPDATE sessions SET started_at = ?, ended_at = ?, end_reason = ?, "
        "last_activity_at = ? WHERE id = ?",
        (started, ended, end_reason, started, sid),
    )
    db._conn.commit()


def _compression_chain(db):
    """root -(compression)-> a -(compression)-> b (live tip)."""
    _mk(db, "root", started=BASE, ended=BASE + 50, end_reason="compression")
    _mk(db, "a", "root", started=BASE + 100, ended=BASE + 150, end_reason="compression")
    _mk(db, "b", "a", started=BASE + 200)


def test_no_rollover_returns_input_unchanged(db):
    _mk(db, "solo", started=BASE)
    assert db.resolve_delivery_session_id("solo") == "solo"


def test_chain_resolves_root_to_live_tip(db):
    _compression_chain(db)
    assert db.resolve_delivery_session_id("root") == "b"
    # Mid-chain ids resolve forward too.
    assert db.resolve_delivery_session_id("a") == "b"
    assert db.resolve_delivery_session_id("b") == "b"


def test_fork_prefers_live_leaf_over_ended_sibling(db):
    # Compression race: the real continuation is live; a stale sibling was
    # reaped. Even though the stale sibling started LATER, the live leaf wins.
    _mk(db, "root", started=BASE, ended=BASE + 50, end_reason="compression")
    _mk(db, "live_leaf", "root", started=BASE + 100)
    _mk(db, "stale_leaf", "root", started=BASE + 200,
        ended=BASE + 250, end_reason="ws_orphan_reap")
    assert db.resolve_delivery_session_id("root") == "live_leaf"


def test_fork_two_live_leaves_picks_newest_deterministically(db):
    _mk(db, "root", started=BASE, ended=BASE + 50, end_reason="compression")
    _mk(db, "older", "root", started=BASE + 100)
    _mk(db, "newer", "root", started=BASE + 200)
    assert db.resolve_delivery_session_id("root") == "newer"
    # Deterministic: repeated resolution gives the same leaf.
    assert db.resolve_delivery_session_id("root") == "newer"


def test_follows_continuation_even_without_compression_marker(db):
    # Gateway + compression races can fork the continuation before the
    # parent's end marker is written; deliveries must still follow it.
    _mk(db, "root", started=BASE)
    _mk(db, "cont", "root", started=BASE + 100)
    assert db.resolve_delivery_session_id("root") == "cont"


def test_branch_delegate_and_tool_children_never_hijack(db):
    _mk(db, "root", started=BASE, ended=BASE + 50, end_reason="compression")
    _mk(db, "cont", "root", started=BASE + 100)
    db.create_session("branch", source="desktop", parent_session_id="root",
                      model_config={"_branched_from": "root"})
    db.create_session("delegate", source="desktop", parent_session_id="root",
                      model_config={"_delegate_from": "root"})
    db.create_session("toolchild", source="tool", parent_session_id="root")
    db._conn.execute(
        "UPDATE sessions SET started_at = ? WHERE id IN ('branch','delegate','toolchild')",
        (BASE + 999,),
    )
    db._conn.commit()
    assert db.resolve_delivery_session_id("root") == "cont"


def test_unknown_or_empty_id_falls_back_to_input(db):
    assert db.resolve_delivery_session_id("nope") == "nope"
    assert db.resolve_delivery_session_id("") == ""


def test_spool_replay_lands_on_live_tip_of_forked_chain(db, tmp_path, monkeypatch):
    # A delivery spooled against the root must replay into the live leaf,
    # deterministically, even when the lineage forked after spooling.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _mk(db, "root", started=BASE, ended=BASE + 50, end_reason="compression")
    _mk(db, "live_leaf", "root", started=BASE + 100)
    _mk(db, "stale_leaf", "root", started=BASE + 200,
        ended=BASE + 250, end_reason="ws_orphan_reap")

    spool_session_delivery("d-1", "root", "assistant", "spooled delivery")

    from hermes_state import replay_session_delivery_spool

    result = replay_session_delivery_spool(db_path=tmp_path / "state.db")
    assert result == {"replayed": 1, "failed": 0}
    tip_msgs = db.get_messages("live_leaf")
    assert any("spooled delivery" in str(m.get("content")) for m in tip_msgs)
    assert db.get_messages("root") == []
    assert db.get_messages("stale_leaf") == []
