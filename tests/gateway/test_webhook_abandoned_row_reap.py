"""A pinned delivery whose run died pre-switch must not leak its session row.

Measured 2026-08-05, ninety minutes after the switch-path deletion shipped:

    20260805_192545_d09ba6d5 | source=webhook | end=None
    20260805_193845_3d1d41b0 | source=webhook | end=None
    20260805_203731_76bf517f | source=webhook | end=None
    20260805_205740_41ac681a | source=webhook | end=None

All zero messages, never ended, never switched. The chain: a pinned delivery
mints its per-delivery row, then the run errors BEFORE the pin switch, so the
switch path's empty-shell deletion never fires — and `on_processing_complete`
returned early for pinned events without any cleanup, because the pinned
conversation itself must not be closed from there.

The reap targets exactly that gap: on pinned completion, delete the
per-delivery row only when the store entry still points at it (the switch
never happened) and it is provably empty.
"""

import asyncio

from gateway.platforms.webhook import WebhookAdapter


class _Store:
    def __init__(self, mapping):
        self._mapping = mapping

    def peek_session_id(self, session_key):
        return self._mapping.get(session_key)


class _DB:
    def __init__(self, deletable):
        self._deletable = deletable
        self.deleted = []

    def delete_session_if_empty(self, session_id):
        if session_id in self._deletable:
            self.deleted.append(session_id)
            return True
        return False


class _Runner:
    def __init__(self, store, db):
        self.session_store = store
        self._session_db = db

    def _session_key_for_source(self, source):
        return f"key:{source.chat_id}"


class _Source:
    chat_id = "webhook:mission-complete:d1"


class _Event:
    source = _Source()


def _adapter(runner):
    adapter = WebhookAdapter.__new__(WebhookAdapter)
    adapter.gateway_runner = runner
    return adapter


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestTheReap:
    def test_an_unswitched_empty_row_is_deleted(self):
        # The measured leak: entry still points at the shell.
        db = _DB(deletable={"shell-1"})
        runner = _Runner(_Store({"key:webhook:mission-complete:d1": "shell-1"}), db)
        _run(_adapter(runner)._reap_abandoned_delivery_row(_Event(), "pinned-1"))
        assert db.deleted == ["shell-1"]

    def test_a_switched_entry_is_left_alone(self):
        # The entry re-pointed at the pinned conversation: the switch path
        # already handled the shell, and the pinned session must not even be
        # OFFERED to the delete helper from here.
        db = _DB(deletable={"pinned-1"})
        runner = _Runner(_Store({"key:webhook:mission-complete:d1": "pinned-1"}), db)
        _run(_adapter(runner)._reap_abandoned_delivery_row(_Event(), "pinned-1"))
        assert db.deleted == []

    def test_a_used_row_survives_via_the_guard(self):
        # delete_session_if_empty refuses content; the reap inherits that.
        db = _DB(deletable=set())  # nothing qualifies as empty
        runner = _Runner(_Store({"key:webhook:mission-complete:d1": "used-1"}), db)
        _run(_adapter(runner)._reap_abandoned_delivery_row(_Event(), "pinned-1"))
        assert db.deleted == []

    def test_a_missing_runner_is_tolerated(self):
        _run(_adapter(None)._reap_abandoned_delivery_row(_Event(), "pinned-1"))

    def test_a_broken_store_never_raises(self):
        class _Boom:
            def peek_session_id(self, key):
                raise RuntimeError("store down")

        db = _DB(deletable={"shell-1"})
        runner = _Runner(_Boom(), db)
        _run(_adapter(runner)._reap_abandoned_delivery_row(_Event(), "pinned-1"))
        assert db.deleted == []
