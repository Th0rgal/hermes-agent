"""Durable idempotency reservations for API server runs."""

import hmac
import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

from hermes_cli.sqlite_util import add_column_if_missing


# Keep the extracted store's log records on the API server logger.
logger = logging.getLogger("gateway.platforms.api_server")

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})

_SELECT_BY_KEY = (
    "SELECT fingerprint, run_id, status_json, owner_pid, owner_started, updated_at "
    "FROM run_idempotency WHERE scope=? AND idempotency_key=?")
_EXTEND_RETENTION_BY_KEY = (
    "UPDATE run_idempotency SET retention_until=MAX(retention_until, ?) "
    "WHERE scope=? AND idempotency_key=? AND fingerprint=?")
_EXTEND_RETENTION_BY_RUN = (
    "UPDATE run_idempotency SET retention_until=MAX(retention_until, ?) "
    "WHERE scope=? AND run_id=?")
# Columns added after the first schema shipped; applied when missing.
_MIGRATIONS = {
    "owner_pid": "INTEGER NOT NULL DEFAULT 0",
    "owner_started": "INTEGER NOT NULL DEFAULT 0",
    "retention_until": "REAL NOT NULL DEFAULT 0",
    "acknowledged_at": "REAL",
    "journal_enabled": "INTEGER NOT NULL DEFAULT 0"}


def _encode_status(status: Dict[str, Any]) -> str:
    return json.dumps(status, sort_keys=True, separators=(",", ":"))


def _record(run_id, status_json, owner_pid, owner_started, updated_at) -> dict[str, Any]:
    return {
        "run_id": run_id, "status": json.loads(status_json), "owner_pid": int(owner_pid or 0),
        "owner_started": int(owner_started or 0), "updated_at": float(updated_at or 0)}


def _outcome(row, fingerprint):
    """Classify a stored ``(scope, key)`` row against the caller's fingerprint."""
    return ("reused" if hmac.compare_digest(row[0], fingerprint) else "conflict"), _record(*row[1:])


def _owner_alive(owner_pid: int, owner_started: int) -> bool:
    """True when the recorded owner pid still exists and is the same process incarnation."""
    try:
        from gateway.status import _pid_exists, get_process_start_time
        return owner_pid > 0 and bool(_pid_exists(owner_pid)) and (
            not owner_started or int(get_process_start_time(owner_pid) or 0) == owner_started)
    except Exception:
        return False



class RunIdempotencyStore:
    """Durable, tenant-scoped reservations for ``POST /v1/runs``: a unique ``(scope, key)`` row
    inserted inside ``BEGIN IMMEDIATE`` so separate workers cannot both admit one request. Only
    fingerprints and public run status are stored — never request bodies or credentials."""

    RETENTION_SECONDS = 24 * 60 * 60
    ACKNOWLEDGED_RETENTION_SECONDS = 24 * 60 * 60
    # Reserve a journal allowance before admitting work, even if API concurrency is unlimited.
    # Existing runs retain their budget when admission is saturated.
    MAX_JOURNAL_RUNS = 32
    MAX_RUN_PENDING_BYTES = 8 * 1024 * 1024
    MAX_RUN_PENDING_BATCHES = 64
    MAX_BATCH_EVENTS = 128
    MAX_COALESCED_BYTES = 64 * 1024
    REPLAY_WAIT_SECONDS = 10

    @property
    def durable(self) -> bool:
        """Whether reservations survive this process."""
        return self._db_path is not None
    def __init__(self, db_path: str = None):
        if db_path is None:
            try:
                from hermes_cli.config import get_hermes_home
                db_path = str(get_hermes_home() / "runs_idempotency.db")
            except Exception:
                db_path = ":memory:"
        self._db_path = None if db_path == ":memory:" else db_path
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        except Exception as exc:
            # Docker may create the container object before `docker run` fails to start it (e.g. exit code
            # 125 when the daemon isn't ready, or a timeout mid-pull). That orphan is left in "Created"
            # state — which the exited-only orphan reaper (reap_orphan_containers, status=exited) never
            # catches, so it leaks permanently. Remove it by its known name before re-raising. See #7439.
            logger.warning(
                "Run idempotency storage is unavailable; falling back to "
                "process memory, so replay will not survive a restart: %s", exc)
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._db_path = None
        from hermes_state_wal import apply_wal_with_fallback
        apply_wal_with_fallback(self._conn, db_label="runs_idempotency.db")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS run_idempotency (
                scope TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                run_id TEXT NOT NULL,
                status_json TEXT NOT NULL,
                owner_pid INTEGER NOT NULL DEFAULT 0,
                owner_started INTEGER NOT NULL DEFAULT 0,
                retention_until REAL NOT NULL DEFAULT 0,
                acknowledged_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (scope, idempotency_key)
            )"""
        )
        columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(run_idempotency)")}
        for column, ddl in _MIGRATIONS.items():
            if column not in columns:
                add_column_if_missing(self._conn, "run_idempotency", column, f"{column} {ddl}")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS run_idempotency_run_id ON run_idempotency(run_id)")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS run_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL, payload TEXT NOT NULL)""")
        self._conn.execute("CREATE INDEX IF NOT EXISTS run_events_run ON run_events(run_id, sequence)")
        self._conn.commit()
        self._lock = threading.Lock()
        self._event_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hermes-run-events")
        self._event_writes = {}
        self._event_errors = {}
        self._enqueue_lock = threading.RLock()
        self._pending_events = 0
        self._pending_bytes = 0
        self._run_pending = {}
        self._run_pending_bytes = {}
        self._terminal_writes = {}
        self._closed_journals = set()
        self._open_batches = {}
        self._journal_runs = set()
        self._tighten_permissions()

    def _submit_event(self, run_id, payload, writer, *args):
        """Bound queued/running work and serialized bytes without blocking live SSE.

        Caller holds _enqueue_lock. Overflow fails this run's replay closed rather
        than silently dropping a frame or retaining an unbounded executor backlog.
        """
        size = (payload["size"] if isinstance(payload, dict) else len(payload.encode("utf-8")))
        size += sum(len(arg.encode("utf-8")) for arg in args)
        failure = self._event_errors.get(run_id)
        if failure is None and (
            self._run_pending_bytes.get(run_id, 0) + size > self.MAX_RUN_PENDING_BYTES
            or self._run_pending.get(run_id, 0) >= self.MAX_RUN_PENDING_BATCHES
        ):
            failure = RuntimeError("Run event journal backlog exceeded its capacity")
            self._event_errors[run_id] = failure
        if failure is not None:
            future = Future()
            future.set_exception(failure)
            return future
        self._pending_events += 1
        self._pending_bytes += size
        self._run_pending_bytes[run_id] = self._run_pending_bytes.get(run_id, 0) + size
        self._run_pending[run_id] = self._run_pending.get(run_id, 0) + 1

        def write():
            try:
                if run_id in self._event_errors:
                    raise self._event_errors[run_id]
                return writer(run_id, *args, payload)
            finally:
                with self._enqueue_lock:
                    self._pending_events -= 1
                    written_size = payload["size"] if isinstance(payload, dict) else size
                    self._pending_bytes -= written_size
                    self._run_pending_bytes[run_id] -= written_size
                    remaining = self._run_pending[run_id] - 1
                    if remaining:
                        self._run_pending[run_id] = remaining
                    else:
                        self._run_pending.pop(run_id, None)
                        self._run_pending_bytes.pop(run_id, None)
                        if run_id in self._closed_journals:
                            self._journal_runs.discard(run_id)
                    if isinstance(payload, dict):
                        payload["payloads"].clear()

        future = self._event_writer.submit(write)
        self._event_writes[run_id] = future
        future.add_done_callback(lambda done: self._write_finished(run_id, done))
        return future

    def _write_finished(self, run_id, future):
        """Keep only in-flight futures; receipts and output live durably in SQLite."""
        with self._enqueue_lock:
            if future.cancelled() or future.exception() is not None:
                # Do not retain writer tracebacks (and their full response payloads).
                self._event_errors[run_id] = RuntimeError("Run event persistence failed")
            if self._event_writes.get(run_id) is future:
                self._event_writes.pop(run_id, None)
            if self._terminal_writes.get(run_id) is future:
                self._terminal_writes.pop(run_id, None)

    def append_event(self, run_id: str, event: dict) -> None:
        """Batch queued frames and coalesce adjacent deltas; never block live SSE."""
        payload = json.dumps(event, ensure_ascii=False)
        size = len(payload.encode("utf-8"))
        with self._enqueue_lock:
            if run_id in self._event_errors or run_id in self._closed_journals:
                return
            batch = self._open_batches.get(run_id)
            if batch is not None:
                previous = json.loads(batch["payloads"][-1])
                def comparable(item):
                    return {k: v for k, v in item.items() if k not in {"delta", "timestamp"}}
                merge = (
                    event.get("event") == "message.delta"
                    and isinstance(event.get("delta"), str)
                    and isinstance(previous.get("delta"), str)
                    and comparable(previous) == comparable(event)
                    and len(batch["payloads"][-1].encode("utf-8")) + size <= self.MAX_COALESCED_BYTES
                )
                if merge:
                    payload = json.dumps({**event, "delta": previous["delta"] + event["delta"]}, ensure_ascii=False)
                    size = len(payload.encode("utf-8")) - len(batch["payloads"][-1].encode("utf-8"))
                if merge or len(batch["payloads"]) < self.MAX_BATCH_EVENTS:
                    if self._run_pending_bytes.get(run_id, 0) + size > self.MAX_RUN_PENDING_BYTES:
                        self._event_errors[run_id] = RuntimeError("Run event journal byte capacity exceeded")
                        return
                    if merge:
                        batch["payloads"][-1] = payload
                    else:
                        batch["payloads"].append(payload)
                    batch["size"] += size
                    self._pending_bytes += size
                    self._run_pending_bytes[run_id] += size
                    return
            batch = {"payloads": [payload], "size": size}
            self._open_batches[run_id] = batch
            self._submit_event(run_id, batch, self._write_event_batch)
            if run_id in self._event_errors:
                self._open_batches.pop(run_id, None)

    def _write_event_batch(self, run_id: str, batch: dict) -> None:
        with self._enqueue_lock:
            if self._open_batches.get(run_id) is batch:
                self._open_batches.pop(run_id, None)
            payloads = list(batch["payloads"])
        try:
            with self._immediate_txn():
                row = self._conn.execute(
                    "SELECT status_json FROM run_idempotency WHERE run_id=?", (run_id,)).fetchone()
                if row is not None and json.loads(row[0]).get("status") not in TERMINAL_STATUSES:
                    self._conn.executemany(
                        "INSERT INTO run_events(run_id,payload) VALUES (?,?)",
                        ((run_id, payload) for payload in payloads))
                self._conn.commit()
        except Exception as exc:
            self._event_errors[run_id] = exc
            logger.exception("Run event persistence failed; durable replay is unavailable")
            raise
        finally:
            payloads.clear()  # failed futures must not retain an entire batch through traceback locals

    def finish_run(self, run_id: str, status: dict, event: dict):
        """Queue terminal state and frame atomically; late callers read the durable winner."""
        with self._enqueue_lock:
            self._closed_journals.add(run_id)
            future = self._terminal_writes.get(run_id)
            if future is None:
                future = self._submit_event(
                    run_id, json.dumps(event, ensure_ascii=False), self._write_terminal,
                    _encode_status(status))
                self._terminal_writes[run_id] = future
                future.add_done_callback(lambda done: self._write_finished(run_id, done))
            if not self._run_pending.get(run_id):
                self._journal_runs.discard(run_id)
            return future

    def _write_terminal(self, run_id: str, status_json: str, payload: str) -> dict:
        try:
            with self._immediate_txn():
                row = self._conn.execute(
                    "SELECT status_json FROM run_idempotency WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise KeyError("Run reservation no longer exists")
                current = json.loads(row[0])
                if current.get("status") in TERMINAL_STATUSES:
                    # A concurrent shutdown cannot overwrite an already-committed completion.
                    terminal = self._conn.execute(
                        "SELECT payload FROM run_events WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
                        (run_id,)).fetchone()
                    self._conn.commit()
                    event = json.loads(terminal[0]) if terminal else {
                        **current, "event": "run." + current["status"], "run_id": run_id}
                    return {"status": current, "event": event}
                self._conn.execute(
                    "UPDATE run_idempotency SET status_json=?, updated_at=? WHERE run_id=?",
                    (status_json, time.time(), run_id))
                self._conn.execute("INSERT INTO run_events(run_id,payload) VALUES (?,?)", (run_id, payload))
                self._conn.commit()
                return {"status": json.loads(status_json), "event": json.loads(payload)}
        except Exception as exc:
            self._event_errors[run_id] = exc
            logger.exception("Terminal run persistence failed")
            raise

    def events(self, scope: str, run_id: str, after: int, limit: int = 500) -> list[dict]:
        """Replay only events whose run belongs to this authenticated principal."""
        pending = self._event_writes.get(run_id)
        if pending is not None:
            try:
                pending.result(timeout=self.REPLAY_WAIT_SECONDS)  # callers use to_thread; includes this run's preceding frames
            except TimeoutError as exc:
                raise RuntimeError("Run event replay is temporarily unavailable; retry") from exc
            except Exception as exc:
                self._event_errors.setdefault(run_id, exc)
        if run_id in self._event_errors:
            raise RuntimeError("Run event persistence failed; replay is unavailable") from self._event_errors[run_id]
        with self._lock:
            rows = self._conn.execute(
                """SELECT e.sequence,e.payload FROM run_events e
                   JOIN run_idempotency r ON r.run_id=e.run_id
                   WHERE r.scope=? AND e.run_id=? AND e.sequence>?
                   ORDER BY e.sequence LIMIT ?""", (scope, run_id, after, limit)).fetchall()
        return [{"id": str(seq), "data": json.loads(payload)} for seq, payload in rows]

    def _tighten_permissions(self) -> None:
        for suffix in ("", "-wal", "-shm") if self._db_path else ():
            candidate = Path(self._db_path + suffix)
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                logger.debug("Failed to restrict run idempotency store permissions", exc_info=True)

    @contextmanager
    def _immediate_txn(self):
        """Hold the lock inside ``BEGIN IMMEDIATE``; the body commits, errors roll back."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._conn.rollback()
                raise

    def reserve(self, scope: str, key: str, fingerprint: str, run_id: str, status: Dict[str, Any], *,
                owner_pid: int = 0, owner_started: int = 0, retention_until: float = 0):
        """Atomically reserve a key; return ``(outcome, stored_record)``."""
        now = time.time()
        retention_until = max(0.0, float(retention_until or 0))
        encoded = _encode_status(status)
        with self._immediate_txn():
            self._prune_stale_terminal_locked(now)
            row = self._conn.execute(_SELECT_BY_KEY, (scope, key)).fetchone()
            if row is not None:
                if retention_until:
                    self._conn.execute(_EXTEND_RETENTION_BY_KEY, (retention_until, scope, key, fingerprint))
                self._conn.commit()
                return _outcome(row, fingerprint)
            with self._enqueue_lock:
                if len(self._journal_runs) >= self.MAX_JOURNAL_RUNS:
                    self._conn.commit()
                    return "capacity", None
            self._conn.execute(
                "INSERT INTO run_idempotency("
                "scope,idempotency_key,fingerprint,run_id,status_json,"
                "owner_pid,owner_started,retention_until,created_at,updated_at,journal_enabled"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,1)",
                (scope, key, fingerprint, run_id, encoded, int(owner_pid or 0), int(owner_started or 0),
                 retention_until, now, now))
            self._conn.commit()
            if status.get("status") not in TERMINAL_STATUSES:
                with self._enqueue_lock:
                    self._journal_runs.add(run_id)
            return "created", _record(run_id, encoded, owner_pid, owner_started, now) | {"status": status}

    def lookup(self, scope: str, key: str, fingerprint: str, *, retention_until: float = 0):
        """Return ``missing``, ``reused`` or ``conflict`` without reserving."""
        now = time.time()
        retention_until = max(0.0, float(retention_until or 0))
        with self._immediate_txn():
            if retention_until:
                self._conn.execute(_EXTEND_RETENTION_BY_KEY, (retention_until, scope, key, fingerprint))
            self._prune_stale_terminal_locked(now)
            row = self._conn.execute(_SELECT_BY_KEY, (scope, key)).fetchone()
            self._conn.commit()
        return ("missing", None) if row is None else _outcome(row, fingerprint)

    def _prune_stale_terminal_locked(self, now: float) -> None:
        """Prune expired terminal/dead-owner records while preserving live or unknown owners.

        Caller holds the DB lock/transaction. Long live turns may outlive retention.
        """
        stale = self._conn.execute(
            """SELECT scope, idempotency_key, status_json, run_id, owner_pid, owner_started
                 FROM run_idempotency
                WHERE acknowledged_at <= ?
                   OR (retention_until > 0 AND retention_until <= ?)
                   OR (retention_until <= 0 AND updated_at < ?)""",
            (now - self.ACKNOWLEDGED_RETENTION_SECONDS, now, now - self.RETENTION_SECONDS),
        ).fetchall()
        for stale_scope, stale_key, stale_status, stale_run, owner_pid, owner_started in stale:
            try:
                terminal = json.loads(stale_status).get("status") in TERMINAL_STATUSES
            except Exception:
                terminal = False
            abandoned = int(owner_pid or 0) > 0 and not _owner_alive(int(owner_pid), int(owner_started or 0))
            if terminal or abandoned:
                with self._enqueue_lock:
                    if self._run_pending.get(stale_run):
                        continue
                    self._open_batches.pop(stale_run, None)
                    self._terminal_writes.pop(stale_run, None)
                    self._closed_journals.discard(stale_run)
                    self._event_writes.pop(stale_run, None)
                    self._event_errors.pop(stale_run, None)
                    self._journal_runs.discard(stale_run)
                self._conn.execute(
                    "DELETE FROM run_events WHERE run_id IN (SELECT run_id FROM run_idempotency WHERE scope=? AND idempotency_key=?)",
                    (stale_scope, stale_key))
                self._conn.execute(
                    "DELETE FROM run_idempotency WHERE scope=? AND idempotency_key=?", (stale_scope, stale_key))

    def status_for_run(self, scope: str, run_id: str, *, retention_until: float = 0) -> dict[str, Any] | None:
        """Load one durable run status inside its authenticated scope."""
        retention_until = max(0.0, float(retention_until or 0))
        with self._lock:
            if retention_until:
                self._conn.execute(_EXTEND_RETENTION_BY_RUN, (retention_until, scope, run_id))
                self._conn.commit()
            row = self._conn.execute(
                "SELECT status_json, owner_pid, owner_started, updated_at, journal_enabled "
                "FROM run_idempotency WHERE scope=? AND run_id=?",
                (scope, run_id)).fetchone()
        if row is None:
            return None
        return {k: v for k, v in _record(None, *row[:-1]).items() if k != "run_id"} | {"journal_enabled": bool(row[-1])}

    def extend_retention(self, scope: str, run_id: str, until: float) -> bool:
        """Persist the latest verified recovery horizon for an active grant."""
        checked_until = max(0.0, float(until or 0))
        if not checked_until:
            return False
        with self._lock:
            changed = self._conn.execute(_EXTEND_RETENTION_BY_RUN, (checked_until, scope, run_id)).rowcount
            self._conn.commit()
        return changed == 1

    def owns_run(self, scope: str, run_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM run_idempotency WHERE scope=? AND run_id=?", (scope, run_id)).fetchone()
        return row is not None

    def interrupt_stale_run(self, scope: str, run_id: str, status: dict, event: dict) -> dict:
        """Atomically publish the recovered terminal status and its replay event, once."""
        with self._immediate_txn():
            row = self._conn.execute(
                "SELECT status_json FROM run_idempotency WHERE scope=? AND run_id=?",
                (scope, run_id)).fetchone()
            if row is None:
                self._conn.commit()
                raise KeyError("Run reservation no longer exists")
            current = json.loads(row[0])
            if current.get("status") in TERMINAL_STATUSES:
                self._conn.commit()
                return current
            self._conn.execute(
                "UPDATE run_idempotency SET status_json=?, updated_at=? WHERE scope=? AND run_id=?",
                (_encode_status(status), time.time(), scope, run_id))
            self._conn.execute("INSERT INTO run_events(run_id,payload) VALUES (?,?)",
                               (run_id, json.dumps(event, ensure_ascii=False)))
            self._conn.commit()
            return status

    def queue_status(self, run_id: str, status: Dict[str, Any]):
        """Order status snapshots with journal writes without blocking the API loop."""
        with self._enqueue_lock:
            if run_id in self._closed_journals or run_id in self._event_errors:
                return None
            return self._submit_event(run_id, _encode_status(status), self._write_status)

    def _write_status(self, run_id: str, payload: str) -> None:
        with self._immediate_txn():
            self._conn.execute(
                "UPDATE run_idempotency SET status_json=?, updated_at=? WHERE run_id=? "
                "AND json_extract(status_json, '$.status') NOT IN "
                "('completed','failed','cancelled','interrupted')",
                (payload, time.time(), run_id))
            self._conn.commit()

    def update_status(self, run_id: str, status: Dict[str, Any]) -> None:
        """Synchronous store API for callers already outside the event loop."""
        self._write_status(run_id, _encode_status(status))

    def close(self) -> None:
        self._event_writer.shutdown(wait=True)
        with self._lock:
            self._conn.close()
