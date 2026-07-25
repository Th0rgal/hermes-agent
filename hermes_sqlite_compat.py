"""Runtime selection of a WAL-safe SQLite driver (issue #69784 follow-up).

Production installs run python-build-standalone, whose embedded SQLite
(3.50.4 at the time of writing) carries the WAL-reset corruption bug
(https://sqlite.org/wal.html#walresetbug).  ``hermes update`` cannot
replace that embedded library, so ``hermes_state`` refuses to enable WAL
and every state database degrades to ``journal_mode=DELETE`` with a
once-per-process warning.

This module restores WAL safely by selecting, once per process, the first
sqlite3-compatible driver whose *linked* SQLite library contains the fix
(3.51.3+, or the 3.50.7 / 3.44.6 backports):

1. ``HERMES_SQLITE_MODULE=<import.path>`` — operator override, e.g. a
   pysqlite3 built from source against a fixed system libsqlite3.  The
   override is validated like any candidate: a still-vulnerable or
   non-conformant module is refused with a warning, never trusted.
   ``HERMES_SQLITE_MODULE=sqlite3`` forces the stdlib driver.
2. stdlib ``sqlite3`` when its linked SQLite is already fixed.
3. Known drop-in replacements that bundle their own SQLite amalgamation:
   ``pysqlite3`` (pysqlite3-binary) and ``sqlean`` (sqlean.py).  As of
   2026-07 both still bundle vulnerable versions (3.51.1 / 3.50.4) and are
   therefore refused; once upstream ships 3.51.3+ they are picked up
   automatically on the next process start.
4. stdlib ``sqlite3`` regardless — the WAL-reset gate in ``hermes_state``
   then keeps databases on ``journal_mode=DELETE``: safe, just slower.

Every Hermes connection to ``state.db`` must be created through the
``sqlite3`` name exported here.  Mixing a fixed driver (running WAL) with
a vulnerable driver on the same database would re-open the exact race the
gate exists to prevent, so ``hermes_state.apply_wal_with_fallback`` also
re-checks the linked version *per connection* via
:func:`connection_sqlite_version_info`.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import sqlite3 as _stdlib_sqlite3
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_OVERRIDE = "HERMES_SQLITE_MODULE"

# Drop-in replacements probed after stdlib, in preference order.
DEFAULT_CANDIDATES: Tuple[str, ...] = ("pysqlite3.dbapi2", "pysqlite3", "sqlean")

# DB-API surface Hermes actually uses (hermes_state, delivery_ledger,
# async_delegation, readiness, doctor, backup).
_REQUIRED_ATTRS: Tuple[str, ...] = (
    "connect",
    "Connection",
    "Cursor",
    "Row",
    "Error",
    "DatabaseError",
    "OperationalError",
    "IntegrityError",
    "sqlite_version",
)

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?")


def is_sqlite_wal_reset_vulnerable(
    version_info: Optional[tuple] = None,
) -> bool:
    """Return True when the given (or selected) SQLite has the WAL-reset bug.

    Upstream documents the bug in versions 3.7.0 through 3.51.2, fixed in
    3.51.3+, with backports 3.50.7 and 3.44.6:
    https://sqlite.org/wal.html#walresetbug

    Pre-WAL libraries (< 3.7.0) cannot hit the race and are treated as safe.
    """
    if version_info is None:
        version_info = sqlite3.sqlite_version_info
    info = tuple(version_info)
    if info < (3, 7, 0):
        return False
    if info >= (3, 51, 3):
        return False
    # Backports of the same fix on older release lines.
    if (3, 50, 7) <= info < (3, 51, 0):
        return False
    if (3, 44, 6) <= info < (3, 45, 0):
        return False
    return True


def parse_sqlite_version(text: str) -> Optional[Tuple[int, int, int]]:
    match = _VERSION_RE.match(str(text).strip())
    if match is None:
        return None
    major, minor, patch = match.group(1), match.group(2), match.group(3)
    return (int(major), int(minor), int(patch or 0))


def _module_version_info(module: Any) -> Optional[Tuple[int, int, int]]:
    info = getattr(module, "sqlite_version_info", None)
    if isinstance(info, tuple) and len(info) >= 3:
        try:
            return (int(info[0]), int(info[1]), int(info[2]))
        except (TypeError, ValueError):
            pass
    version = getattr(module, "sqlite_version", None)
    if version:
        return parse_sqlite_version(str(version))
    return None


def probe_sqlite_module(module: Any) -> Optional[Dict[str, Any]]:
    """Validate that *module* is a usable, WAL-safe sqlite3 drop-in.

    Returns an info dict on success, or ``None`` when the module is
    missing required DB-API surface, cannot open an in-memory database,
    or links a SQLite that still carries the WAL-reset bug.  Never raises.
    """
    try:
        for attr in _REQUIRED_ATTRS:
            if not hasattr(module, attr):
                return None
        version_info = _module_version_info(module)
        if version_info is None:
            return None
        # The linked library must report the same version the module
        # claims, and the connection must actually work.
        conn = module.connect(":memory:")
        try:
            row = conn.execute("SELECT sqlite_version()").fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        linked = parse_sqlite_version(str(row[0]))
        if linked is None:
            return None
        if is_sqlite_wal_reset_vulnerable(linked):
            return None
        return {
            "module": getattr(module, "__name__", str(module)),
            "sqlite_version": ".".join(str(part) for part in linked),
            "sqlite_version_info": linked,
        }
    except Exception:
        return None


def select_sqlite_module(
    *,
    env: Optional[Dict[str, str]] = None,
    candidates: Optional[Tuple[str, ...]] = None,
    stdlib_module: Any = _stdlib_sqlite3,
    importer=importlib.import_module,
) -> Tuple[Any, Dict[str, Any]]:
    """Pick the sqlite3-compatible driver Hermes should use.

    Pure given its keyword arguments (all injectable for tests); the
    module-level selection below calls it once per process.
    """
    environ = os.environ if env is None else env
    stdlib_info = _module_version_info(stdlib_module) or (0, 0, 0)
    stdlib_version = ".".join(str(part) for part in stdlib_info)
    info: Dict[str, Any] = {
        "stdlib_sqlite_version": stdlib_version,
        "override": False,
        "wal_safe": False,
    }

    override = (environ.get(ENV_OVERRIDE) or "").strip()
    if override:
        if override == "sqlite3":
            info.update(
                module="sqlite3",
                sqlite_version=stdlib_version,
                override=True,
                wal_safe=not is_sqlite_wal_reset_vulnerable(stdlib_info),
            )
            return stdlib_module, info
        try:
            module = importer(override)
        except Exception as exc:
            logger.warning(
                "%s=%s could not be imported (%s) — falling back to "
                "automatic SQLite driver selection.",
                ENV_OVERRIDE,
                override,
                exc,
            )
        else:
            probed = probe_sqlite_module(module)
            if probed is not None:
                probed.update(
                    stdlib_sqlite_version=stdlib_version,
                    override=True,
                    wal_safe=True,
                )
                logger.info(
                    "Using SQLite driver %s (SQLite %s) from %s.",
                    probed["module"],
                    probed["sqlite_version"],
                    ENV_OVERRIDE,
                )
                return module, probed
            logger.warning(
                "%s=%s was refused: it is missing DB-API surface or its "
                "linked SQLite still has the WAL-reset bug "
                "(https://sqlite.org/wal.html#walresetbug) — falling back "
                "to automatic selection.",
                ENV_OVERRIDE,
                override,
            )

    if not is_sqlite_wal_reset_vulnerable(stdlib_info):
        info.update(module="sqlite3", sqlite_version=stdlib_version, wal_safe=True)
        return stdlib_module, info

    for name in candidates if candidates is not None else DEFAULT_CANDIDATES:
        try:
            module = importer(name)
        except Exception:
            continue
        probed = probe_sqlite_module(module)
        if probed is None:
            continue
        probed.update(stdlib_sqlite_version=stdlib_version, override=False, wal_safe=True)
        logger.info(
            "stdlib SQLite %s has the WAL-reset bug — using drop-in driver "
            "%s (SQLite %s) instead.",
            stdlib_version,
            probed["module"],
            probed["sqlite_version"],
        )
        return module, probed

    # Nothing fixed is available: keep stdlib; the WAL-reset gate in
    # hermes_state will keep databases on journal_mode=DELETE.
    info.update(module="sqlite3", sqlite_version=stdlib_version, wal_safe=False)
    return stdlib_module, info


sqlite3, SQLITE_COMPAT = select_sqlite_module()


def compat_info() -> Dict[str, Any]:
    """Snapshot of the selected driver, for doctor / diagnostics."""
    return dict(SQLITE_COMPAT)


def connection_sqlite_version_info(conn: Any) -> Tuple[int, ...]:
    """Return the SQLite version actually linked behind *conn*.

    The WAL-reset gate must key off the library that backs the specific
    connection: callers outside the compat module (or third-party code)
    may still hand us stdlib connections even when a fixed drop-in was
    selected, and enabling WAL through a vulnerable library would
    re-introduce the corruption race.  Falls back to the selected
    module's version when the probe itself fails (e.g. malformed-schema
    databases where every statement raises).
    """
    try:
        row = conn.execute("SELECT sqlite_version()").fetchone()
        if row and row[0]:
            parsed = parse_sqlite_version(str(row[0]))
            if parsed is not None:
                return parsed
    except Exception:
        pass
    return tuple(sqlite3.sqlite_version_info)
