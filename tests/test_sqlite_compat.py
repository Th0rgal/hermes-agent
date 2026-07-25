"""hermes_sqlite_compat — WAL-safe SQLite driver selection (issue #69784).

Production runs python-build-standalone whose embedded SQLite (3.50.4)
carries the WAL-reset corruption bug, so hermes_state degrades state
databases to journal_mode=DELETE.  The compat module restores WAL by
selecting a fixed driver when one is available — and must never trust a
candidate (including the HERMES_SQLITE_MODULE override) without verifying
the *linked* library version.  All fixtures here are synthetic modules
wrapping in-memory databases; no production data is touched.
"""

from __future__ import annotations

import sqlite3 as stdlib_sqlite3
import types

import pytest

import hermes_sqlite_compat as compat
import hermes_state
from hermes_state import apply_wal_with_fallback


FIXED = (3, 51, 3)
VULNERABLE = (3, 50, 4)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    """Delegates to a real in-memory DB but lies about sqlite_version()."""

    def __init__(self, real, version_text):
        self._real = real
        self._version_text = version_text

    def execute(self, sql, *args):
        if "sqlite_version()" in sql.lower():
            return _FakeCursor([(self._version_text,)])
        return self._real.execute(sql, *args)

    def close(self):
        self._real.close()


def make_fake_module(version_info, *, name="fake_sqlite", linked_version=None):
    """A sqlite3-compatible module claiming (and linking) *version_info*."""
    linked = linked_version or version_info
    module = types.ModuleType(name)
    module.sqlite_version_info = tuple(version_info)
    module.sqlite_version = ".".join(str(p) for p in version_info)
    module.connect = lambda *a, **kw: _FakeConnection(
        stdlib_sqlite3.connect(":memory:"),
        ".".join(str(p) for p in linked),
    )
    for attr in (
        "Connection",
        "Cursor",
        "Row",
        "Error",
        "DatabaseError",
        "OperationalError",
        "IntegrityError",
    ):
        setattr(module, attr, getattr(stdlib_sqlite3, attr))
    return module


def make_stdlib_stub(version_info):
    return make_fake_module(version_info, name="sqlite3")


def _importer_for(mapping):
    def _import(name):
        try:
            return mapping[name]
        except KeyError:
            raise ImportError(name)

    return _import


class TestProbe:
    def test_accepts_fixed_module(self):
        info = compat.probe_sqlite_module(make_fake_module(FIXED))
        assert info is not None
        assert info["sqlite_version"] == "3.51.3"

    def test_rejects_vulnerable_module(self):
        assert compat.probe_sqlite_module(make_fake_module(VULNERABLE)) is None

    def test_rejects_module_claiming_fixed_but_linking_vulnerable(self):
        """The linked library, not the module metadata, is authoritative."""
        liar = make_fake_module(FIXED, linked_version=VULNERABLE)
        assert compat.probe_sqlite_module(liar) is None

    def test_rejects_module_missing_dbapi_surface(self):
        module = make_fake_module(FIXED)
        del module.Row
        assert compat.probe_sqlite_module(module) is None

    def test_rejects_module_whose_connect_raises(self):
        module = make_fake_module(FIXED)

        def _boom(*a, **kw):
            raise RuntimeError("no database for you")

        module.connect = _boom
        assert compat.probe_sqlite_module(module) is None

    def test_accepts_backport_versions(self):
        for version in ((3, 50, 7), (3, 44, 6)):
            assert compat.probe_sqlite_module(make_fake_module(version)) is not None


class TestSelection:
    def test_fixed_stdlib_is_preferred(self):
        stdlib = make_stdlib_stub(FIXED)
        module, info = compat.select_sqlite_module(
            env={}, stdlib_module=stdlib, importer=_importer_for({})
        )
        assert module is stdlib
        assert info["module"] == "sqlite3"
        assert info["wal_safe"] is True

    def test_vulnerable_stdlib_falls_to_fixed_dropin(self):
        stdlib = make_stdlib_stub(VULNERABLE)
        dropin = make_fake_module(FIXED, name="pysqlite3.dbapi2")
        module, info = compat.select_sqlite_module(
            env={},
            stdlib_module=stdlib,
            importer=_importer_for({"pysqlite3.dbapi2": dropin}),
        )
        assert module is dropin
        assert info["wal_safe"] is True
        assert info["stdlib_sqlite_version"] == "3.50.4"

    def test_vulnerable_dropin_is_refused(self):
        """pysqlite3-binary currently bundles 3.51.1 — must not be trusted."""
        stdlib = make_stdlib_stub(VULNERABLE)
        dropin = make_fake_module((3, 51, 1), name="pysqlite3.dbapi2")
        module, info = compat.select_sqlite_module(
            env={},
            stdlib_module=stdlib,
            importer=_importer_for({"pysqlite3.dbapi2": dropin}),
        )
        assert module is stdlib
        assert info["module"] == "sqlite3"
        assert info["wal_safe"] is False

    def test_broken_candidate_is_skipped(self):
        stdlib = make_stdlib_stub(VULNERABLE)

        def _import(name):
            raise ImportError(name)

        module, info = compat.select_sqlite_module(
            env={}, stdlib_module=stdlib, importer=_import
        )
        assert module is stdlib
        assert info["wal_safe"] is False

    def test_env_override_selects_fixed_module(self):
        stdlib = make_stdlib_stub(VULNERABLE)
        override = make_fake_module(FIXED, name="my_backport")
        module, info = compat.select_sqlite_module(
            env={compat.ENV_OVERRIDE: "my_backport"},
            stdlib_module=stdlib,
            importer=_importer_for({"my_backport": override}),
        )
        assert module is override
        assert info["override"] is True
        assert info["wal_safe"] is True

    def test_env_override_refused_when_vulnerable(self, caplog):
        """An operator override never bypasses the version gate."""
        stdlib = make_stdlib_stub(VULNERABLE)
        override = make_fake_module(VULNERABLE, name="my_backport")
        with caplog.at_level("WARNING", logger="hermes_sqlite_compat"):
            module, info = compat.select_sqlite_module(
                env={compat.ENV_OVERRIDE: "my_backport"},
                stdlib_module=stdlib,
                importer=_importer_for({"my_backport": override}),
            )
        assert module is stdlib
        assert info["wal_safe"] is False
        assert any("refused" in r.getMessage() for r in caplog.records)

    def test_env_override_sqlite3_forces_stdlib(self):
        stdlib = make_stdlib_stub(FIXED)
        dropin = make_fake_module(FIXED, name="pysqlite3.dbapi2")
        module, info = compat.select_sqlite_module(
            env={compat.ENV_OVERRIDE: "sqlite3"},
            stdlib_module=stdlib,
            importer=_importer_for({"pysqlite3.dbapi2": dropin}),
        )
        assert module is stdlib
        assert info["override"] is True

    def test_process_selection_is_wired(self):
        """The module-level selection is what hermes_state actually uses."""
        assert hermes_state.sqlite3 is compat.sqlite3
        info = compat.compat_info()
        assert info["module"]
        assert info["sqlite_version"]
        assert isinstance(info["wal_safe"], bool)


class TestConnectionVersionProbe:
    def test_reports_linked_version_of_real_connection(self):
        conn = stdlib_sqlite3.connect(":memory:")
        try:
            assert (
                compat.connection_sqlite_version_info(conn)
                == stdlib_sqlite3.sqlite_version_info
            )
        finally:
            conn.close()

    def test_falls_back_to_selected_module_when_probe_fails(self):
        class _Broken:
            def execute(self, *a, **kw):
                raise RuntimeError("malformed schema")

        assert compat.connection_sqlite_version_info(_Broken()) == tuple(
            compat.sqlite3.sqlite_version_info
        )

    def test_parse_sqlite_version(self):
        assert compat.parse_sqlite_version("3.51.3") == (3, 51, 3)
        assert compat.parse_sqlite_version("3.50") == (3, 50, 0)
        assert compat.parse_sqlite_version("garbage") is None


class TestPerConnectionWalGate:
    """apply_wal_with_fallback must key off the connection's linked library."""

    @pytest.fixture(autouse=True)
    def _reset_warnings(self):
        hermes_state._wal_reset_bug_warned_paths.clear()
        yield
        hermes_state._wal_reset_bug_warned_paths.clear()

    def test_vulnerable_connection_gets_delete(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(
            hermes_state,
            "connection_sqlite_version_info",
            lambda conn: VULNERABLE,
        )
        conn = stdlib_sqlite3.connect(str(tmp_path / "vuln.db"))
        try:
            with caplog.at_level("WARNING", logger="hermes_state"):
                mode = apply_wal_with_fallback(conn, db_label="vuln.db")
            assert mode == "delete"
        finally:
            conn.close()
        message = "\n".join(r.getMessage() for r in caplog.records)
        # Warning names the connection's linked version and the remediation.
        assert "3.50.4" in message
        assert "auto-detected" in message

    def test_fixed_connection_gets_wal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            hermes_state,
            "connection_sqlite_version_info",
            lambda conn: FIXED,
        )
        conn = stdlib_sqlite3.connect(str(tmp_path / "fixed.db"))
        try:
            mode = apply_wal_with_fallback(conn, db_label="fixed.db")
            assert mode == "wal"
            assert (
                conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            )
        finally:
            conn.close()

    def test_sessiondb_records_journal_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            hermes_state,
            "connection_sqlite_version_info",
            lambda conn: FIXED,
        )
        db = hermes_state.SessionDB(db_path=tmp_path / "state.db")
        try:
            assert db.journal_mode == "wal"
        finally:
            db.close()
