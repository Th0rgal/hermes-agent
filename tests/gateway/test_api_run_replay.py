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
    write = store._write_event_batch
    def slow_write(*args):
        entered.set()
        release.wait(3)
        write(*args)
    monkeypatch.setattr(store, '_write_event_batch', slow_write)
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
        store._event_errors['run_one'] = OSError('disk full')
    monkeypatch.setattr(store, '_write_event_batch', failed_write)
    queue.put_nowait({'event': 'run.completed'})
    assert queue.get_nowait()['event'] == 'run.completed'
    with pytest.raises(RuntimeError, match='replay is unavailable'):
        store.events('alice', 'run_one', 0)
    store.close()


def test_terminal_status_and_event_commit_together_and_do_not_regress(tmp_path):
    path = str(tmp_path / 'runs.db')
    store = RunIdempotencyStore(path)
    store.reserve('alice', 'key', 'fingerprint', 'run_one', {'status': 'running'})
    event = {'event': 'run.completed', 'run_id': 'run_one', 'output': 'done'}
    store.finish_run('run_one', {'status': 'completed', 'output': 'done'}, event).result()
    late = store.finish_run('run_one', {'status': 'cancelled'}, {'event': 'run.cancelled'}).result()
    assert late['event']['event'] == 'run.completed'
    assert late['status']['status'] == 'completed'
    # A worker callback that entered update_status before cancellation may commit late.
    store.update_status('run_one', {'status': 'waiting_for_approval'})
    store.close()
    reopened = RunIdempotencyStore(path)
    assert reopened.status_for_run('alice', 'run_one')['status']['status'] == 'completed'
    assert [e['data'] for e in reopened.events('alice', 'run_one', 0)] == [event]
    reopened.close()


def test_failed_terminal_frame_rolls_back_the_status(tmp_path):
    import pytest
    import sqlite3
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    store.reserve('alice', 'key', 'fingerprint', 'run_one', {'status': 'running'})
    store._conn.execute("CREATE TRIGGER fail_events BEFORE INSERT ON run_events BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END")
    store._conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_run('run_one', {'status': 'completed'}, {'event': 'run.completed'}).result()
    assert store.status_for_run('alice', 'run_one')['status']['status'] == 'running'
    assert store._conn.execute('SELECT count(*) FROM run_events').fetchone()[0] == 0
    store.close()


def test_failed_run_does_not_disable_healthy_run_replay(tmp_path):
    import pytest
    import sqlite3
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    for run in ['bad', 'good']:
        store.reserve('alice', run, 'fingerprint', run, {'status': 'running'})
    store._conn.execute("CREATE TRIGGER fail_bad BEFORE INSERT ON run_events WHEN NEW.run_id='bad' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    store._conn.commit()
    store.append_event('good', {'event': 'message.delta', 'delta': 'healthy'})
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_run('bad', {'status': 'completed'}, {'event': 'run.completed'}).result()
    assert store.events('alice', 'good', 0)[0]['data']['delta'] == 'healthy'
    with pytest.raises(RuntimeError):
        store.events('alice', 'bad', 0)
    store.finish_run('good', {'status': 'completed'}, {'event': 'run.completed'}).result()
    assert len(store.events('alice', 'good', 0)) == 2
    store.close()


def test_terminal_enqueue_closes_journal_before_commit(tmp_path, monkeypatch):
    import threading
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    store.reserve('alice', 'key', 'fingerprint', 'run_one', {'status': 'running'})
    entered, release = threading.Event(), threading.Event()
    write = store._write_terminal
    def blocked_terminal(*args):
        entered.set()
        assert release.wait(3)
        return write(*args)
    monkeypatch.setattr(store, '_write_terminal', blocked_terminal)
    try:
        terminal = store.finish_run('run_one', {'status': 'cancelled'}, {'event': 'run.cancelled'})
        assert entered.wait(1)
        for _ in range(100):
            store.append_event('run_one', {'event': 'message.delta', 'delta': 'late'})
        assert store._pending_events == 1
    finally:
        release.set()
    terminal.result()
    assert [e['data']['event'] for e in store.events('alice', 'run_one', 0)] == ['run.cancelled']
    store.close()


def test_delta_burst_is_batched_without_invalidating_replay(tmp_path, monkeypatch):
    import threading
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    for run in ['bad', 'good']:
        store.reserve('alice', run, 'fingerprint', run, {'status': 'running'})
    store.MAX_RUN_PENDING_BATCHES = 2
    entered, release = threading.Event(), threading.Event()
    write = store._write_event_batch
    def blocked_write(*args):
        with store._enqueue_lock:
            store._open_batches.pop(args[0], None)
        entered.set()
        assert release.wait(3)
        return write(*args)
    monkeypatch.setattr(store, '_write_event_batch', blocked_write)
    try:
        store.append_event('bad', {'event': 'message.delta', 'delta': 'first'})
        assert entered.wait(1)
        for _ in range(1000):
            store.append_event('bad', {'event': 'message.delta', 'delta': 'more'})
        assert store._pending_events == 2
        store.append_event('good', {'event': 'message.delta', 'delta': 'healthy'})
        assert store._pending_events == 3
    finally:
        release.set()
    replay = store.events('alice', 'bad', 0)
    assert ''.join(e['data']['delta'] for e in replay) == 'first' + 'more' * 1000
    store.finish_run('bad', {'status': 'completed'}, {'event': 'run.completed'}).result()
    assert store.status_for_run('alice', 'bad')['status']['status'] == 'completed'
    assert store.events('alice', 'good', 0)[0]['data']['delta'] == 'healthy'
    store.close()
    assert store._pending_events == store._pending_bytes == 0


def test_journal_rejects_oversized_frame_without_queuing(tmp_path):
    import pytest
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    store.reserve('alice', 'key', 'fingerprint', 'run_one', {'status': 'running'})
    store.MAX_RUN_PENDING_BYTES = 32
    store.append_event('run_one', {'event': 'message.delta', 'delta': 'x' * 100})
    assert store._pending_events == store._pending_bytes == 0
    with pytest.raises(RuntimeError, match='replay is unavailable'):
        store.events('alice', 'run_one', 0)
    store.close()


def test_replay_wait_timeout_does_not_poison_writer(tmp_path, monkeypatch):
    import threading
    import pytest
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    store.reserve('alice', 'key', 'fingerprint', 'run_one', {'status': 'running'})
    store.REPLAY_WAIT_SECONDS = 0.01
    entered, release = threading.Event(), threading.Event()
    write = store._write_event_batch
    def blocked_write(*args):
        entered.set()
        assert release.wait(3)
        return write(*args)
    monkeypatch.setattr(store, '_write_event_batch', blocked_write)
    try:
        store.append_event('run_one', {'event': 'message.delta', 'delta': 'hello'})
        assert entered.wait(1)
        with pytest.raises(RuntimeError, match='temporarily unavailable'):
            store.events('alice', 'run_one', 0)
        assert 'run_one' not in store._event_errors
    finally:
        release.set()
    store.finish_run('run_one', {'status': 'completed'}, {'event': 'run.completed'}).result()
    assert [e['data']['event'] for e in store.events('alice', 'run_one', 0)] == ['message.delta', 'run.completed']
    store.close()


def test_noisy_run_cannot_consume_another_runs_byte_budget(tmp_path, monkeypatch):
    import threading
    import pytest
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    for run in ['noisy', 'healthy']:
        store.reserve('alice', run, 'fingerprint', run, {'status': 'running'})
    store.MAX_RUN_PENDING_BYTES = 512
    entered, release = threading.Event(), threading.Event()
    write = store._write_event_batch
    def blocked_write(*args):
        entered.set()
        assert release.wait(3)
        return write(*args)
    monkeypatch.setattr(store, '_write_event_batch', blocked_write)
    try:
        store.append_event('noisy', {'event': 'message.delta', 'delta': 'x' * 400})
        assert entered.wait(1)
        store.append_event('noisy', {'event': 'message.delta', 'delta': 'x' * 400})
        store.append_event('healthy', {'event': 'message.delta', 'delta': 'hello'})
        terminal = store.finish_run('healthy', {'status': 'completed'}, {'event': 'run.completed'})
        assert 'healthy' not in store._event_errors
    finally:
        release.set()
    terminal.result()
    with pytest.raises(RuntimeError, match='replay is unavailable'):
        store.events('alice', 'noisy', 0)
    assert [e['data']['event'] for e in store.events('alice', 'healthy', 0)] == ['message.delta', 'run.completed']
    store.close()


def test_adapter_disconnect_does_not_block_event_loop():
    import asyncio
    import threading
    from gateway.platforms.api_server import APIServerAdapter
    entered, release = threading.Event(), threading.Event()
    def close():
        entered.set()
        release.wait(3)
    owner = SimpleNamespace(
        _mark_disconnected=lambda: None, _response_store=None,
        _run_idempotency_store=SimpleNamespace(close=close),
        _site=None, _runner=None, _app=None, name='test',
        _close_cached_session_dbs=lambda: None)
    async def exercise():
        task = asyncio.create_task(APIServerAdapter.disconnect(owner))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), timeout=1.5)
            assert not task.done()  # timer ran while the writer was still draining
        finally:
            release.set()
            await task
    asyncio.run(exercise())


def test_journal_admission_preserves_accepted_runs_and_retry_keys(tmp_path):
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    store.MAX_JOURNAL_RUNS = 1
    assert store.reserve('alice', 'first', 'fp1', 'run_one', {'status': 'running'})[0] == 'created'
    assert store.reserve('alice', 'next', 'fp2', 'run_two', {'status': 'running'}) == ('capacity', None)
    assert store.lookup('alice', 'next', 'fp2') == ('missing', None)
    assert store.reserve('alice', 'first', 'fp1', 'ignored', {'status': 'running'})[0] == 'reused'
    store.append_event('run_one', {'event': 'message.delta', 'delta': 'still healthy'})
    store.finish_run('run_one', {'status': 'completed'}, {'event': 'run.completed'}).result()
    assert len(store.events('alice', 'run_one', 0)) == 2
    assert store.reserve('alice', 'next', 'fp2', 'run_two', {'status': 'running'})[0] == 'created'
    store.close()


def test_migration_does_not_claim_legacy_reservations_have_journals(tmp_path):
    import sqlite3
    import time
    path = str(tmp_path / 'legacy.db')
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE run_idempotency (
        scope TEXT, idempotency_key TEXT, fingerprint TEXT, run_id TEXT,
        status_json TEXT, created_at REAL, updated_at REAL,
        PRIMARY KEY (scope, idempotency_key))''')
    conn.execute('INSERT INTO run_idempotency VALUES (?,?,?,?,?,?,?)',
                 ('alice', 'legacy', 'fp', 'old', '{"status":"completed"}', time.time(), time.time()))
    conn.commit()
    conn.close()
    store = RunIdempotencyStore(path)
    assert store.status_for_run('alice', 'old')['journal_enabled'] is False
    assert store.reserve('alice', 'legacy', 'fp', 'ignored', {'status': 'queued'})[0] == 'reused'
    assert store.status_for_run('alice', 'old')['journal_enabled'] is False
    store.reserve('alice', 'new', 'fp2', 'new', {'status': 'queued'})
    assert store.status_for_run('alice', 'new')['journal_enabled'] is True
    store.close()


def test_completed_receipts_are_not_retained_in_memory(tmp_path):
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    store.reserve('alice', 'key', 'fp', 'run_one', {'status': 'running'})
    event = {'event': 'run.completed', 'output': 'x' * 10000}
    receipt = store.finish_run('run_one', {'status': 'completed', 'output': event['output']}, event).result()
    store._event_writer.submit(lambda: None).result()  # completion callbacks have run
    assert 'run_one' not in store._event_writes
    assert 'run_one' not in store._terminal_writes
    assert 'run_one' in store._closed_journals
    store.append_event('run_one', {'event': 'message.delta', 'delta': 'late'})
    late = store.finish_run('run_one', {'status': 'cancelled'}, {'event': 'run.cancelled'}).result()
    assert late == receipt
    assert len(store.events('alice', 'run_one', 0)) == 1
    store.close()


def test_retention_prunes_dead_owner_journals_without_client_reconnect(tmp_path, monkeypatch):
    import time
    import gateway.platforms.api_server_run_idempotency as module
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    for run, pid in [('dead', 101), ('live', 102)]:
        store.reserve('alice', run, run, run, {'status': 'running'}, owner_pid=pid, owner_started=1)
        store.append_event(run, {'event': 'message.delta', 'delta': run})
        assert store.events('alice', run, 0)
    expiry = time.time() + store.RETENTION_SECONDS + 1
    monkeypatch.setattr(module, '_owner_alive', lambda pid, started: pid == 102)
    monkeypatch.setattr(module.time, 'time', lambda: expiry)
    assert store.lookup('alice', 'dead', 'dead') == ('missing', None)
    assert store.lookup('alice', 'live', 'live')[0] == 'reused'
    assert store._conn.execute("SELECT count(*) FROM run_events WHERE run_id='dead'").fetchone()[0] == 0
    assert store.events('alice', 'live', 0)
    store.close()


def test_read_only_clients_cannot_replay_expired_terminal_journals(tmp_path, monkeypatch):
    import time
    import gateway.platforms.api_server_run_idempotency as module

    for access in ("events", "status"):
        store = RunIdempotencyStore(str(tmp_path / f"{access}.db"))
        try:
            for run in ("expired", "retained"):
                store.reserve("alice", run, run, run, {"status": "running"},
                              retention_until=time.time() + 3 * store.RETENTION_SECONDS if run == "retained" else 0)
                store.finish_run(run, {"status": "completed"}, {"event": "run.completed"}).result()
            store._event_writer.submit(lambda: None).result()
            expiry = time.time() + store.RETENTION_SECONDS + 1
            with monkeypatch.context() as patch:
                patch.setattr(module.time, "time", lambda: expiry)
                # No POST/lookup/reserve after the clock advances: reading alone expires data.
                if access == "events":
                    assert store.events("alice", "expired", 0) == []
                else:
                    assert store.status_for_run("alice", "expired") is None
                assert store.events("alice", "retained", 0)
                assert store.status_for_run("alice", "retained") is not None
                assert store._conn.execute("SELECT count(*) FROM run_events WHERE run_id='expired'").fetchone()[0] == 0
                assert not store.owns_run("alice", "expired")
        finally:
            store.close()


def test_new_retention_horizon_does_not_revive_expired_cached_room_run(tmp_path, monkeypatch):
    import asyncio
    import time
    import gateway.platforms.api_server_run_idempotency as module
    from gateway.platforms.api_server_runs import _durable_run_status

    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    expiry = time.time() + 10
    store.reserve('room-member', 'key', 'fp', 'run_one', {'status': 'running'}, retention_until=expiry)
    store.finish_run('run_one', {'status': 'completed'}, {'event': 'run.completed'}).result()
    store._event_writer.submit(lambda: None).result()
    owner = SimpleNamespace(
        _run_idempotency_store=store, _run_idempotency_ids={'run_one'},
        _run_statuses={'run_one': {'status': 'completed'}}, _run_owners={'run_one': 'room-member'},
        _run_idempotency_scope=lambda request: 'room-member', _release_run_owner_if_forgotten=lambda run: None)
    request = SimpleNamespace(_hermes_room_run_retention_until=expiry + 1000)
    monkeypatch.setattr(module.time, 'time', lambda: expiry + 1)
    try:
        assert asyncio.run(_durable_run_status(owner, request, 'run_one')) is None
        assert store.events('room-member', 'run_one', 0) == []
        assert 'run_one' not in owner._run_statuses
    finally:
        store.close()


def test_lookup_with_new_horizon_does_not_revive_expired_journal(tmp_path, monkeypatch):
    import time
    import gateway.platforms.api_server_run_idempotency as module
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    expiry = time.time() + 10
    store.reserve('alice', 'key', 'fp', 'run_one', {'status': 'completed'}, retention_until=expiry)
    monkeypatch.setattr(module.time, 'time', lambda: expiry + 1)
    try:
        assert store.lookup('alice', 'key', 'fp', retention_until=expiry + 1000) == ('missing', None)
    finally:
        store.close()


def test_storage_contention_does_not_exhaust_agent_executor_after_disconnects(tmp_path):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    release = threading.Event()
    started = []

    def blocked_read(number):
        started.append(number)
        assert release.wait(5)
        return number

    async def exercise():
        # Even a tiny agent executor remains free while many storage clients reconnect.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        tasks = [asyncio.create_task(store.call_async(blocked_read, i)) for i in range(32)]
        try:
            for _ in range(100):
                if len(started) == 4:
                    break
                await asyncio.sleep(0.01)
            assert len(started) == 4
            for task in tasks[:4]:
                task.cancel()
            await asyncio.sleep(0)
            assert await asyncio.wait_for(asyncio.to_thread(lambda: 'agent-ready'), 0.5) == 'agent-ready'
            # Cancellation cannot free a submission slot while its worker still waits.
            assert len(started) == 4
            assert store._io_workers._work_queue.qsize() == 0
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        store.close()
