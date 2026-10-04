"""Durable public event replay survives transport and process loss."""
from types import SimpleNamespace

from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
from gateway.platforms.api_server_runs import _ReplayQueue, _publish_run_event


def test_replay_survives_reopen_and_is_principal_scoped(tmp_path):
    path = str(tmp_path / 'runs.db')
    store = RunIdempotencyStore(path)
    store.reserve('alice', 'key', 'fingerprint', 'run_one', {'status': 'running'})
    owner = SimpleNamespace(_run_idempotency_ids={'run_one'}, _run_idempotency_store=store)
    queue = _ReplayQueue(owner, 'run_one')
    queue.put_nowait({'event': 'message.delta', 'delta': 'Hello'})
    queue.put_nowait({'event': 'tool.started', 'tool': 'safe_tool'})
    first = store.events('alice', 'run_one', 0)
    assert len(first) == 2
    assert store.events('bob', 'run_one', 0) == []
    assert queue.get_nowait()['delta'] == 'Hello'
    store.close()
    reopened = RunIdempotencyStore(path)
    assert reopened.events('alice', 'run_one', 0) == first
    assert reopened.events('alice', 'run_one', int(first[0]['id'])) == first[1:]
    reopened.close()


def test_unreserved_run_does_not_write_replay(tmp_path):
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    queue = _ReplayQueue(SimpleNamespace(_run_idempotency_ids=set(), _run_idempotency_store=store), 'unknown')
    queue.put_nowait({'event': 'message.delta', 'delta': 'Hello'})
    queue.put_nowait(None)
    assert store.events('alice', 'unknown', 0) == []
    store.close()


def test_replay_continues_after_transport_retirement(tmp_path):
    store = RunIdempotencyStore(str(tmp_path / "runs.db"))
    store.reserve("alice", "key", "fingerprint", "run_one", {"status": "running"})
    owner = SimpleNamespace(_run_idempotency_ids={"run_one"}, _run_idempotency_store=store, _run_streams={})
    owner._run_streams["run_one"] = _ReplayQueue(owner, "run_one")
    _publish_run_event(owner, "run_one", {"event": "message.delta", "delta": "before"})
    del owner._run_streams["run_one"]
    _publish_run_event(owner, "run_one", {"event": "message.delta", "delta": "after"})
    _publish_run_event(owner, "run_one", {"event": "run.completed"})
    assert [e["data"]["event"] for e in store.events("alice", "run_one", 0)] == [
        "message.delta", "message.delta", "run.completed"]
    store.close()


def test_slow_journal_does_not_block_live_delivery(tmp_path, monkeypatch):
    import threading
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    store.reserve('alice', 'key', 'fingerprint', 'run_one', {'status': 'running'})
    entered, release = threading.Event(), threading.Event()
    write = store._write_event
    def slow_write(*args):
        entered.set()
        release.wait(3)
        write(*args)
    monkeypatch.setattr(store, '_write_event', slow_write)
    queue = _ReplayQueue(SimpleNamespace(_run_idempotency_ids={'run_one'}, _run_idempotency_store=store), 'run_one')
    try:
        queue.put_nowait({'event': 'message.delta', 'delta': 'live'})
        assert entered.wait(1)
        assert queue.get_nowait()['delta'] == 'live'
    finally:
        release.set()
    assert store.events('alice', 'run_one', 0)[0]['data']['delta'] == 'live'
    store.close()


def test_journal_failure_preserves_live_frame_and_fails_replay(tmp_path, monkeypatch):
    import pytest
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    store.reserve('alice', 'key', 'fingerprint', 'run_one', {'status': 'running'})
    queue = _ReplayQueue(SimpleNamespace(_run_idempotency_ids={'run_one'}, _run_idempotency_store=store), 'run_one')
    def failed_write(*args):
        store._event_error = OSError('disk full')
    monkeypatch.setattr(store, '_write_event', failed_write)
    queue.put_nowait({'event': 'run.completed'})
    assert queue.get_nowait()['event'] == 'run.completed'
    with pytest.raises(RuntimeError, match='replay is unavailable'):
        store.events('alice', 'run_one', 0)
    store.close()
