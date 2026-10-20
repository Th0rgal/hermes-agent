"""Journal slot caps for ``/v1/runs``: a slot is taken (cap-checked) when a run starts executing
or resumes from an approval, and is held until the executor exits.

Shared app/adapter helpers live in ``tests/gateway/test_api_server_runs.py``.
"""

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
from tests.gateway.test_api_server_runs import _create_runs_app, _make_adapter, _use_idempotency_db
from tests.waiting import async_wait_until
from tools import approval as approval_mod
from tools import approval_gateway_wait


@pytest.fixture
def adapter(tmp_path):
    adapter = _make_adapter()
    _use_idempotency_db(adapter, tmp_path / "slots.db")
    adapter._max_concurrent_runs = 0  # unlimited: the journal cap alone must bound execution
    yield adapter
    adapter._run_idempotency_store.close()


def _gated_agent_factory(release: threading.Event):
    """Agents that block until *release*; ``stats`` records how many executed at once."""
    lock, stats = threading.Lock(), {"active": 0, "peak": 0, "started": 0}

    def run_conversation(**kwargs):
        with lock:
            stats["active"] += 1
            stats["started"] += 1
            stats["peak"] = max(stats["peak"], stats["active"])
        try:
            release.wait(timeout=10)
            return {"final_response": "done"}
        finally:
            with lock:
                stats["active"] -= 1

    def create_agent(*args, **kwargs):
        agent = MagicMock()
        agent.run_conversation.side_effect = run_conversation
        agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
        return agent

    return create_agent, stats


async def _start(cli, key):
    response = await cli.post("/v1/runs", json={"input": key}, headers={"Idempotency-Key": key})
    return response.status, (await response.json()).get("run_id")


def test_acquire_refuses_over_global_and_scope_caps(tmp_path):
    store = RunIdempotencyStore(str(tmp_path / "runs.db"))
    store.MAX_JOURNAL_RUNS, store.MAX_JOURNAL_RUNS_PER_SCOPE = 3, 2
    try:
        assert store.acquire_journal("a1", "alice") and store.acquire_journal("a2", "alice")
        assert store.acquire_journal("a1", "alice")  # the holder re-checks without a second slot
        assert not store.acquire_journal("a3", "alice")  # per-scope cap
        assert store.acquire_journal("b1", "bob")
        assert not store.acquire_journal("b2", "bob")  # global cap
        store.release_journal("a1", final=True)
        assert not store.acquire_journal("a1", "alice")  # a closed journal never reopens
        assert store.acquire_journal("b2", "bob")
        assert store.journal_slots() == {"a2": "alice", "b1": "bob", "b2": "bob"}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_concurrent_starts_never_execute_past_the_journal_cap(adapter):
    store = adapter._run_idempotency_store
    store.MAX_JOURNAL_RUNS_PER_SCOPE = 2
    real_reserve, admitted_together = store.reserve, threading.Barrier(4)

    def reserve_together(*args, **kwargs):
        # Every admission sees zero executing runs before any executor starts.
        result = real_reserve(*args, **kwargs)
        admitted_together.wait(timeout=5)
        return result

    store.reserve = reserve_together
    release = threading.Event()
    create_agent, stats = _gated_agent_factory(release)
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        with patch.object(adapter, "_create_agent", side_effect=create_agent):
            started = await asyncio.gather(*(_start(cli, f"burst-{i}") for i in range(4)))
            assert [status for status, _ in started] == [202] * 4
            run_ids = [run_id for _, run_id in started]
            await async_wait_until(lambda: stats["active"] >= 2, timeout=5)
            await asyncio.sleep(0.5)  # queued runs re-poll several times meanwhile
            assert stats["peak"] == 2
            assert len(store.journal_slots()) == 2
            statuses = sorted(adapter._run_statuses[run_id]["status"] for run_id in run_ids)
            assert statuses == ["queued", "queued", "running", "running"]
            release.set()
            await async_wait_until(
                lambda: all(adapter._run_statuses.get(r, {}).get("status") == "completed" for r in run_ids)
                and not store.journal_slots(), timeout=10)
            assert stats["started"] == 4 and stats["peak"] == 2


@pytest.mark.asyncio
async def test_run_waiting_for_a_slot_can_be_stopped_without_executing(adapter):
    store = adapter._run_idempotency_store
    store.MAX_JOURNAL_RUNS_PER_SCOPE = 1
    real_reserve = store.reserve

    def reserve_then_fill(scope, *args, **kwargs):
        result = real_reserve(scope, *args, **kwargs)
        store.acquire_journal("run_other", scope)  # the slot fills after admission
        return result

    store.reserve = reserve_then_fill
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        with patch.object(adapter, "_create_agent") as create:
            status, run_id = await _start(cli, "waits")
            assert status == 202
            await asyncio.sleep(0.3)
            assert adapter._run_statuses[run_id]["status"] == "queued"
            assert (await cli.post(f"/v1/runs/{run_id}/stop")).status == 200
            await async_wait_until(lambda: adapter._run_statuses[run_id]["status"] == "cancelled", timeout=5)
            create.assert_not_called()
            assert set(store.journal_slots()) == {"run_other"}


@pytest.mark.asyncio
async def test_approval_resume_rechecks_journal_capacity(adapter):
    store = adapter._run_idempotency_store
    store.MAX_JOURNAL_RUNS_PER_SCOPE = 1
    release = threading.Event()
    create_agent, stats = _gated_agent_factory(release)
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        with patch.object(adapter, "_create_agent", side_effect=create_agent):
            status, run_id = await _start(cli, "parks")
            assert status == 202
            await async_wait_until(lambda: stats["active"] == 1, timeout=5)
            scope = store.journal_slots()[run_id]
            pending = approval_gateway_wait._ApprovalEntry({"command": "rm -rf build", "pattern_keys": ["rm"]})
            with approval_mod._lock:
                approval_mod._gateway_queues[run_id] = [pending]
            try:
                adapter._set_run_status(run_id, "waiting_for_approval", last_event="approval.request")
                # Parked, the executor is still alive (a timeout resumes it unannounced): it keeps its slot.
                assert store.journal_slots() == {run_id: scope}
                assert (await _start(cli, "while-parked"))[0] == 429

                # A parked run without a slot cannot resume past a full budget.
                store.release_journal(run_id)
                assert store.acquire_journal("run_other", scope)
                refused = await cli.post(f"/v1/runs/{run_id}/approval", json={"choice": "once"})
                assert refused.status == 429
                assert (await refused.json())["error"]["code"] == "run_journal_capacity"
                assert not pending.event.is_set()
                assert adapter._run_statuses[run_id]["status"] == "waiting_for_approval"

                store.release_journal("run_other")
                accepted = await cli.post(f"/v1/runs/{run_id}/approval", json={"choice": "once"})
                assert accepted.status == 200
                assert pending.event.is_set() and pending.result == "once"
                assert store.journal_slots() == {run_id: scope}
                assert adapter._run_statuses[run_id]["status"] == "running"
            finally:
                with approval_mod._lock:
                    approval_mod._gateway_queues.pop(run_id, None)
                release.set()
            await async_wait_until(lambda: not store.journal_slots(), timeout=10)


@pytest.mark.asyncio
async def test_stopping_run_keeps_its_slot_until_the_executor_exits(adapter):
    store = adapter._run_idempotency_store
    store.MAX_JOURNAL_RUNS_PER_SCOPE = 1
    release = threading.Event()
    create_agent, stats = _gated_agent_factory(release)  # ignores the interrupt until released
    async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
        with patch.object(adapter, "_create_agent", side_effect=create_agent):
            status, run_id = await _start(cli, "stubborn")
            assert status == 202
            await async_wait_until(lambda: stats["active"] == 1, timeout=5)
            stop = await cli.post(f"/v1/runs/{run_id}/stop")
            assert (await stop.json())["status"] == "stopping"
            await asyncio.sleep(0.2)
            assert adapter._run_statuses[run_id]["status"] == "stopping"
            assert run_id in store.journal_slots()
            assert (await _start(cli, "during-stop"))[0] == 429
            release.set()
            await async_wait_until(
                lambda: adapter._run_statuses[run_id]["status"] in {"cancelled", "completed"}
                and not store.journal_slots(), timeout=10)
            assert (await _start(cli, "after-stop"))[0] == 202
