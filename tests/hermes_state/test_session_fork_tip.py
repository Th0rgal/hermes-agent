"""Anti-fork lineage guards: resume/reopen/recovery must converge on the tip.

Prod incident shape (verity/lido lineages): a conversation chain
root → live continuation, plus stale ``YYYYMMDD_*`` SIBLINGS minted whenever
a writer ran a turn against the root while a continuation already existed.
Three code paths conspired:

1. ``resolve_resume_session_id`` required the parent's
   ``end_reason='compression'`` marker to project forward, and its fallback
   walk picked children by ``started_at DESC`` — a stale empty sibling
   started later bounced the resume back to the root.
2. ``session.resume`` then called ``reopen_session`` on that root, clearing
   the marker and making ``publish_compression_child`` accept the root as a
   live parent again — the next compression published a NEW sibling.
3. Turn-start recovery (``recover_rotated_compression_session``) failed
   closed on sibling ambiguity and on marker-stripped parents, so every
   subsequent stale writer forked again.

These tests pin the fixes: delivery-order tip resolution for resume,
reopen refusal on rotated parents with a continuation child, and
deterministic (delivery-order) adoption in recovery.
"""

import time
from typing import Any, Dict, List, Optional

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _set_times(db: SessionDB, sid: str, started_at: float, ended_at=None) -> None:
    conn = db._conn
    conn.execute(
        "UPDATE sessions SET started_at = ?, ended_at = COALESCE(?, ended_at) "
        "WHERE id = ?",
        (started_at, ended_at, sid),
    )
    conn.commit()


def _forked_lineage(db: SessionDB) -> None:
    """root (compression-ended, has messages) with two siblings:

    - ``live_cont``: the real continuation — live, has messages.
    - ``stale_sib``: an empty sibling STARTED LATER (the shape that used to
      capture the started_at-DESC walk and bounce resume back to root).
    """
    base = time.time() - 10_000
    db.create_session("root", source="cli")
    db.append_message("root", role="user", content="pre-compression")
    db.end_session("root", "compression")
    db.create_session("live_cont", source="cli", parent_session_id="root")
    db.append_message("live_cont", role="assistant", content="post-compression")
    db.create_session("stale_sib", source="cli", parent_session_id="root")
    _set_times(db, "root", base, base + 50)
    _set_times(db, "live_cont", base + 100)
    _set_times(db, "stale_sib", base + 200)  # later-started, but empty


# ---------------------------------------------------------------------------
# resolve_resume_session_id: delivery-order walk, has-messages as tiebreak
# ---------------------------------------------------------------------------


def test_resume_forked_lineage_resolves_to_live_sibling_not_root(db):
    _forked_lineage(db)
    # The prod bug: started_at DESC descended into the empty stale sibling,
    # found no messages, and returned root — re-arming the fork loop.
    assert db.resolve_resume_session_id("root") == "live_cont"


def test_resume_resolves_forward_without_compression_marker(db):
    # A reopened (or end-marker-raced) parent lacks end_reason='compression'
    # but its continuation still holds the post-compression turns. The walk
    # must not depend on the marker.
    base = time.time() - 10_000
    db.create_session("root", source="cli")
    db.append_message("root", role="user", content="pre")
    db.create_session("cont", source="cli", parent_session_id="root")
    db.append_message("cont", role="assistant", content="post")
    _set_times(db, "root", base)
    _set_times(db, "cont", base + 100)
    assert db.resolve_resume_session_id("root") == "cont"


def test_resume_ignores_branch_delegate_and_tool_children(db):
    base = time.time() - 10_000
    db.create_session("root", source="cli")
    db.append_message("root", role="user", content="hi")
    db.create_session(
        "branch", source="cli", parent_session_id="root",
        model_config={"_branched_from": "root"},
    )
    db.append_message("branch", role="user", content="branched turn")
    db.create_session(
        "delegate", source="cli", parent_session_id="root",
        model_config={"_delegate_from": "root"},
    )
    db.append_message("delegate", role="user", content="delegate turn")
    db.create_session("toolchild", source="tool", parent_session_id="root")
    db.append_message("toolchild", role="user", content="tool turn")
    _set_times(db, "root", base)
    for i, sid in enumerate(("branch", "delegate", "toolchild")):
        _set_times(db, sid, base + 100 * (i + 1))
    assert db.resolve_resume_session_id("root") == "root"


def test_resume_matches_delivery_resolver_on_forked_lineage(db):
    # Interactive resume and deliveries must land on the SAME leaf.
    _forked_lineage(db)
    assert (
        db.resolve_resume_session_id("root")
        == db.resolve_delivery_session_id("root")
        == "live_cont"
    )


# ---------------------------------------------------------------------------
# reopen_session: refuse to resurrect a rotated parent with a continuation
# ---------------------------------------------------------------------------


def test_reopen_refuses_rotated_parent_with_continuation_child(db):
    _forked_lineage(db)
    db.reopen_session("root")
    row = db.get_session("root")
    assert row["ended_at"] is not None
    assert row["end_reason"] == "compression"


def test_reopen_still_reopens_rotated_parent_without_continuation(db):
    db.create_session("solo", source="cli")
    db.end_session("solo", "compression")
    db.reopen_session("solo")
    row = db.get_session("solo")
    assert row["ended_at"] is None
    assert row["end_reason"] is None


def test_reopen_ignores_branch_delegate_tool_children(db):
    # Branch/delegate/tool children are not continuations; the parent must
    # stay reopenable (session.branch and delegations are legitimate).
    db.create_session("p", source="cli")
    db.end_session("p", "compression")
    db.create_session(
        "b", source="cli", parent_session_id="p",
        model_config={"_branched_from": "p"},
    )
    db.create_session(
        "d", source="cli", parent_session_id="p",
        model_config={"_delegate_from": "p"},
    )
    db.create_session("t", source="tool", parent_session_id="p")
    db.reopen_session("p")
    row = db.get_session("p")
    assert row["ended_at"] is None


def test_reopen_non_compression_end_reason_unaffected(db):
    db.create_session("p", source="cli")
    db.create_session("c", source="cli", parent_session_id="p")
    db.end_session("p", "ws_orphan_reap")
    db.reopen_session("p")
    assert db.get_session("p")["ended_at"] is None


# ---------------------------------------------------------------------------
# Turn-start recovery: marker-independent, deterministic on sibling forks
# ---------------------------------------------------------------------------


class _RecoveryAgent:
    """Minimal stale-agent double for recover_rotated_compression_session."""

    def __init__(self, db: SessionDB, session_id: str):
        self._session_db = db
        self.session_id = session_id
        self.platform = "cli"
        self._memory_manager = None
        self._session_db_created = True
        self._last_flushed_db_idx = 0
        self._flushed_db_message_session_id = session_id
        self._flushed_db_message_ids: set = set()
        self._cached_system_prompt = None
        self.context_compressor = object()  # no on_session_start / bind hooks


def _recover(agent) -> Optional[List[Dict[str, Any]]]:
    from agent.conversation_compression import recover_rotated_compression_session

    return recover_rotated_compression_session(agent)


def test_stale_writer_adopts_tip_on_forked_lineage(db):
    # THE prod scenario: an agent still bound to root while a live
    # continuation and a stale sibling exist. Fail-closed adoption used to
    # return None here (sibling ambiguity), so the turn ran on root and the
    # next compression forked another sibling. Now it adopts the
    # delivery-order leaf deterministically.
    _forked_lineage(db)
    agent = _RecoveryAgent(db, "root")
    recovered = _recover(agent)
    assert recovered is not None
    assert agent.session_id == "live_cont"
    assert [m["content"] for m in recovered] == ["post-compression"]


def test_recovery_is_marker_independent(db):
    # Parent reopened by an old build (marker stripped) but the continuation
    # exists: the marker-based gate said "not rotated" and let the turn write
    # to the parent. The delivery-tip gate must still trigger adoption.
    base = time.time() - 10_000
    db.create_session("root", source="cli")
    db.append_message("root", role="user", content="pre")
    db.create_session("cont", source="cli", parent_session_id="root")
    db.append_message("cont", role="assistant", content="post")
    _set_times(db, "root", base)
    _set_times(db, "cont", base + 100)
    assert db.get_session("root")["end_reason"] is None  # no marker
    agent = _RecoveryAgent(db, "root")
    recovered = _recover(agent)
    assert recovered is not None
    assert agent.session_id == "cont"


def test_recovery_noop_on_actual_tip(db):
    _forked_lineage(db)
    agent = _RecoveryAgent(db, "live_cont")
    assert _recover(agent) is None
    assert agent.session_id == "live_cont"


def test_recovery_noop_without_continuation(db):
    db.create_session("solo", source="cli")
    db.append_message("solo", role="user", content="hi")
    agent = _RecoveryAgent(db, "solo")
    assert _recover(agent) is None
    assert agent.session_id == "solo"


def test_recovery_prefers_unambiguous_live_child_via_strict_finder(db):
    # Single live child with the marker present: the strict
    # find_live_compression_child path still resolves it (unchanged
    # fast path), and recovery adopts it.
    base = time.time() - 10_000
    db.create_session("root", source="cli")
    db.append_message("root", role="user", content="pre")
    db.end_session("root", "compression")
    db.create_session("cont", source="cli", parent_session_id="root")
    db.append_message("cont", role="assistant", content="post")
    _set_times(db, "root", base, base + 50)
    _set_times(db, "cont", base + 100)
    assert db.find_live_compression_child("root")["id"] == "cont"
    agent = _RecoveryAgent(db, "root")
    recovered = _recover(agent)
    assert recovered is not None
    assert agent.session_id == "cont"


def test_no_new_sibling_after_stale_writer_recovery(db):
    # End-to-end invariant for the fork loop: after recovery, root has the
    # same two children it started with — adoption never mints a row.
    _forked_lineage(db)
    before = db._conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE parent_session_id = 'root'"
    ).fetchone()[0]
    agent = _RecoveryAgent(db, "root")
    assert _recover(agent) is not None
    after = db._conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE parent_session_id = 'root'"
    ).fetchone()[0]
    assert (before, after) == (2, 2)
