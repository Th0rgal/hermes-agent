"""Durable run replay through the /v1/runs adapter: the JSON event page, journal capacity at
admission, slow or failing journals, and journal slots that must never leak.

Shared app/adapter helpers live in ``tests/gateway/test_api_server_runs.py``.
"""

import asyncio
import hashlib
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_api_server_runs import (
    _claim_run,
    _create_runs_app,
    _make_adapter,
    _use_idempotency_db,
)


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.mark.asyncio
async def test_json_event_replay_survives_gateway_reopen_and_validates_cursor(tmp_path):
    path = tmp_path / "replay.db"
    adapter = _make_adapter()
    _use_idempotency_db(adapter, path)
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        with patch.object(adapter, "_create_agent") as create:
            agent = MagicMock()
            agent.run_conversation.return_value = {"final_response": "replayed answer"}
            agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
            create.return_value = agent
            started = await cli.post("/v1/runs", json={"input": "hello"}, headers={"Idempotency-Key": "replay"})
            run_id = (await started.json())["run_id"]
            for _ in range(40):
                status = await cli.get(f"/v1/runs/{run_id}")
                if (await status.json()).get("status") == "completed":
                    break
                await asyncio.sleep(0.05)
            first = await cli.get(f"/v1/runs/{run_id}/events?format=json&after=0")
            original = await first.json()
            assert any(e["data"]["event"] == "run.completed" for e in original["events"])
    adapter._run_idempotency_store.close()
    restarted = _make_adapter()
    _use_idempotency_db(restarted, path)
    async with TestClient(TestServer(_create_runs_app(restarted))) as cli:
        replay = await cli.get(f"/v1/runs/{run_id}/events?format=json&after=0")
        assert await replay.json() == original
        after = await cli.get(f"/v1/runs/{run_id}/events?format=json&after={original['cursor']}")
        assert (await after.json())["events"] == []
        for cursor in ["-1", str(1 << 63), "not-a-number"]:
            invalid = await cli.get(f"/v1/runs/{run_id}/events?format=json&after={cursor}")
            assert invalid.status == 400
        unknown = await cli.get("/v1/runs/run_unknown/events?format=json")
        assert unknown.status == 404
    restarted._run_idempotency_store.close()


@pytest.mark.asyncio
async def test_journal_capacity_is_retryable_when_api_concurrency_is_unlimited(adapter):
    adapter._max_concurrent_runs = 0
    adapter._run_idempotency_store.MAX_JOURNAL_RUNS = 0
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        with patch.object(adapter, '_create_agent') as create:
            response = await cli.post('/v1/runs', json={'input': 'hello'}, headers={'Idempotency-Key': 'journal-capacity'})
            assert response.status == 429
            assert response.headers['Retry-After'] == '5'
            assert (await response.json())['error']['code'] == 'run_journal_capacity'
            create.assert_not_called()
            assert not adapter._run_streams
            assert not adapter._run_statuses


@pytest.mark.asyncio
async def test_json_replay_rejects_unjournaled_live_run(adapter):
    run_id = 'run_unjournaled'
    _claim_run(adapter, run_id)
    adapter._run_statuses[run_id] = {'status': 'running'}
    adapter._run_streams[run_id] = asyncio.Queue()
    adapter._run_streams[run_id].put_nowait({'event': 'message.delta', 'delta': 'live only'})
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        response = await cli.get(f'/v1/runs/{run_id}/events?format=json')
        assert response.status == 409
        assert (await response.json())['error']['code'] == 'run_replay_unsupported'
        assert adapter._run_streams[run_id].qsize() == 1


@pytest.mark.asyncio
async def test_json_replay_rejects_legacy_reservation_but_preserves_status(adapter):
    run_id = 'run_legacy'
    request = MagicMock(headers={})
    scope = adapter._run_idempotency_scope(request)
    store = adapter._run_idempotency_store
    store.reserve(scope, 'legacy', 'fp', run_id, {'status': 'completed', 'output': 'preserved'})
    store._conn.execute('UPDATE run_idempotency SET journal_enabled=0 WHERE run_id=?', (run_id,))
    store._conn.commit()
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        response = await cli.get(f'/v1/runs/{run_id}/events?format=json')
        assert response.status == 409
        assert (await response.json())['error']['code'] == 'run_replay_unsupported'
        status = await cli.get(f'/v1/runs/{run_id}')
        assert status.status == 200
        assert (await status.json())['output'] == 'preserved'


@pytest.mark.asyncio
async def test_slow_journal_does_not_block_status_updates_or_unrelated_requests(adapter, monkeypatch):
    import os
    run_id = 'run_slow_journal'
    request = MagicMock(headers={})
    store = adapter._run_idempotency_store
    store.reserve(adapter._run_idempotency_scope(request), 'slow', 'fp', run_id,
                  {'status': 'running'}, owner_pid=os.getpid())
    adapter._run_idempotency_ids.add(run_id)
    entered, release = threading.Event(), threading.Event()
    write = store._write_event_batch
    def blocked_write(*args):
        with store._lock:
            entered.set()
            release.wait(3)
        return write(*args)
    monkeypatch.setattr(store, '_write_event_batch', blocked_write)
    owns_run, reading = store.owns_run, threading.Event()
    def observed_owns_run(*args):
        reading.set()
        return owns_run(*args)
    monkeypatch.setattr(store, 'owns_run', observed_owns_run)
    app = _create_runs_app(adapter)
    async def probe(request):
        return web.json_response({'ok': True})
    app.router.add_get('/probe', probe)
    async with TestClient(TestServer(app)) as cli:
        try:
            store.append_event(run_id, {'event': 'message.delta', 'delta': 'first'})
            assert await asyncio.to_thread(entered.wait, 1)
            before = time.monotonic()
            adapter._set_run_status(run_id, 'waiting_for_approval', approval={'request_id': 'one'})
            assert time.monotonic() - before < 0.25
            adapter._run_statuses.pop(run_id)  # cold ownership and status must read SQLite
            status_task = asyncio.create_task(cli.get(f'/v1/runs/{run_id}'))
            assert await asyncio.to_thread(reading.wait, 1)  # the cold read now waits on the journal
            probe_response = await asyncio.wait_for(cli.get('/probe'), timeout=1)
            assert probe_response.status == 200
            assert not status_task.done()
        finally:
            release.set()
        assert (await status_task).status == 200
        store.events(adapter._run_idempotency_scope(request), run_id, 0)
        assert store.status_for_run(adapter._run_idempotency_scope(request), run_id)['status']['status'] == 'waiting_for_approval'


@pytest.mark.asyncio
async def test_disconnect_during_reservation_keeps_one_executor_and_drain_slot(adapter, monkeypatch):
    store = adapter._run_idempotency_store
    entered, release = threading.Event(), threading.Event()
    reserve = store.reserve
    handlers = []
    original_handle = adapter._handle_runs
    async def tracked_handle(request):
        handlers.append(asyncio.current_task())
        return await original_handle(request)
    def blocked_reserve(*args, **kwargs):
        entered.set()
        release.wait(3)
        return reserve(*args, **kwargs)
    monkeypatch.setattr(store, 'reserve', blocked_reserve)
    monkeypatch.setattr(adapter, '_handle_runs', tracked_handle)
    agent = MagicMock()
    agent.run_conversation.return_value = {'final_response': 'done'}
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        with patch.object(adapter, '_create_agent', return_value=agent) as create:
            first = asyncio.create_task(cli.post('/v1/runs', json={'input': 'hello'},
                                                 headers={'Idempotency-Key': 'disconnect-at-reserve'}))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                original_run_id = next(iter(adapter._run_statuses))
                handlers[0].cancel()
                await asyncio.sleep(0)
                assert adapter._pending_agent_requests == 1
            finally:
                release.set()
            await asyncio.gather(first, return_exceptions=True)
            replay = await cli.post('/v1/runs', json={'input': 'hello'},
                                    headers={'Idempotency-Key': 'disconnect-at-reserve'})
            assert replay.status == 202
            assert (await replay.json())['run_id'] == original_run_id
            for _ in range(40):
                if adapter._run_statuses[original_run_id]['status'] == 'completed':
                    break
                await asyncio.sleep(0.025)
            assert adapter._run_statuses[original_run_id]['status'] == 'completed'
            assert create.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "approval"])
async def test_delayed_agent_callback_cannot_resurrect_terminal_run(adapter, kind):
    from types import SimpleNamespace
    from gateway.platforms import api_server, api_server_runs

    run_id = "run_delayed_callback"
    queue = adapter._run_streams[run_id] = asyncio.Queue()
    adapter._run_statuses[run_id] = {"status": "running"}
    if kind == "tool":
        callback = adapter._make_run_event_callback(run_id, asyncio.get_running_loop())
        callback("tool.completed", "terminal", result="done")
    else:
        run = SimpleNamespace(run_id=run_id, queue=queue, put_event=queue.put_nowait)
        callback = api_server_runs._make_approval_notify(adapter, run, _api_server=api_server)
        callback({"command": "echo harmless", "request_id": "request_delayed"})
    # The executor finished before the queued worker notification reached the loop.
    adapter._run_statuses[run_id] = {"status": "completed"}
    await asyncio.sleep(0)
    assert adapter._run_statuses[run_id] == {"status": "completed"}
    assert queue.empty()


@pytest.mark.asyncio
async def test_run_that_raises_mid_run_releases_its_journal_slot(adapter, tmp_path):
    _use_idempotency_db(adapter, tmp_path / "slots.db")
    store = adapter._run_idempotency_store
    store.MAX_JOURNAL_RUNS = store.MAX_JOURNAL_RUNS_PER_SCOPE = 1
    adapter._max_concurrent_runs = 0
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        with patch.object(adapter, "_create_agent") as create:
            agent = MagicMock()
            agent.run_conversation.side_effect = RuntimeError("boom mid-run")
            agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
            create.return_value = agent
            # Worst case: the terminal journal write itself fails, so finish_run never frees the slot.
            store.finish_run = MagicMock(side_effect=RuntimeError("journal down"))
            for i in range(4):  # each run crashes; the next must still be admitted
                response = await cli.post(
                    "/v1/runs", json={"input": f"crash {i}"}, headers={"Idempotency-Key": f"leak-{i}"})
                assert response.status == 202, await response.text()
                run_id = (await response.json())["run_id"]
                for _ in range(200):
                    if adapter._run_statuses.get(run_id, {}).get("status") == "failed" and not store.journal_slots():
                        break
                    await asyncio.sleep(0.02)
                assert adapter._run_statuses[run_id]["status"] == "failed"
                assert store.journal_slots() == {}


@pytest.mark.asyncio
async def test_dead_owner_nonterminal_status_becomes_interrupted(tmp_path):
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore

    path = tmp_path / "idem.db"
    scope = hashlib.sha256(
        b"default\0unauthenticated-test-listener"
    ).hexdigest()
    store = RunIdempotencyStore(str(path))
    store.reserve(
        scope,
        "stale-run",
        "fingerprint",
        "run_stale",
        {"run_id": "run_stale", "status": "running"},
        owner_pid=999_999_999,
        owner_started=1,
    )
    store.close()

    restarted = _make_adapter()
    _use_idempotency_db(restarted, path)
    app = _create_runs_app(restarted)
    async with TestClient(TestServer(app)) as cli:
        first = await cli.get("/v1/runs/run_stale/events?format=json")
        frames = (await first.json())["events"]
        assert [e["data"]["event"] for e in frames] == ["run.interrupted"]
        response = await cli.get("/v1/runs/run_stale")
        body = await response.json()
        again = await cli.get("/v1/runs/run_stale/events?format=json")
        assert (await again.json())["events"] == frames
    assert response.status == 200
    assert body["status"] == "interrupted"
    assert body["last_event"] == "run.interrupted"


def test_progress_event_does_not_fsync_unchanged_running_status(adapter):
    adapter._run_statuses["run_progress"] = {
        "run_id": "run_progress",
        "status": "running",
    }
    adapter._run_idempotency_ids.add("run_progress")
    adapter._run_idempotency_store.queue_status = MagicMock()

    adapter._set_run_status(
        "run_progress", "running", last_event="tool.completed"
    )

    adapter._run_idempotency_store.queue_status.assert_not_called()
