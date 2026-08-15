"""Rotate to a parent-linked child after compression exhaustion."""

from __future__ import annotations

import types

from agent.conversation_compression import fork_session_after_compression_exhaustion


class _FakeDB:
    def __init__(self):
        self.created = []
        self.ended = []
        self.titles = {"parent-1": "Verity Benchmark #6"}
        self.child_titles = {}

    def create_session(self, session_id, source, **kwargs):
        self.created.append((session_id, source, kwargs))
        return session_id

    def get_session_title(self, session_id):
        return self.titles.get(session_id)

    def set_session_title(self, session_id, title):
        self.child_titles[session_id] = title

    def end_session(self, session_id, reason):
        self.ended.append((session_id, reason))


def test_fork_creates_parent_linked_child_and_ends_parent():
    db = _FakeDB()
    transitions = []
    agent = types.SimpleNamespace(
        _session_db=db,
        session_id="parent-1",
        platform="desktop",
        model="gpt-5.6-sol",
        _session_init_model_config={"x": 1},
        working_directory="/tmp/ws",
        _transition_context_engine_session=lambda **kw: transitions.append(kw),
    )

    new_id = fork_session_after_compression_exhaustion(agent)

    assert new_id
    assert new_id != "parent-1"
    assert agent.session_id == new_id
    assert db.created[0][0] == new_id
    assert db.created[0][2]["parent_session_id"] == "parent-1"
    assert db.ended == [("parent-1", "compression_exhausted")]
    assert db.child_titles[new_id] == "Verity Benchmark #6"
    assert transitions[0]["old_session_id"] == "parent-1"
    assert transitions[0]["new_session_id"] == new_id


def test_fork_returns_none_without_session_db():
    agent = types.SimpleNamespace(session_id="x")
    assert fork_session_after_compression_exhaustion(agent) is None
