"""Tests for the versioned SQL migration runner (offline - no database)."""

from __future__ import annotations

import pytest

from app.migrate import (
    Migration,
    discover_migrations,
    migration_status,
    pending_migrations,
    run_migrations,
)

M1 = Migration("0001", "alpha", "CREATE TABLE alpha ();")
M2 = Migration("0002", "beta", "CREATE TABLE beta ();")


class FakeCursor:
    def __init__(self, rows=()):
        self._rows = list(rows)
        self.rowcount = len(self._rows)

    def fetchall(self):
        return self._rows


class FakeConn:
    """Minimal autocommit psycopg3 connection stand-in."""

    def __init__(self, applied_versions=()):
        self.statements = []
        self.transactions = 0
        self._applied = set(applied_versions)

    def execute(self, sql, params=None):
        self.statements.append((sql.strip(), params))
        if sql.strip().startswith("SELECT version FROM schema_migrations"):
            return FakeCursor([(version,) for version in sorted(self._applied)])
        if sql.strip().startswith("INSERT INTO schema_migrations"):
            self._applied.add(params[0])
        return FakeCursor()

    def transaction(self):
        conn = self

        class _Transaction:
            def __enter__(self):
                conn.transactions += 1
                return self

            def __exit__(self, *exc):
                return False

        return _Transaction()


def _write(tmp_path, name, sql="-- migration"):
    (tmp_path / name).write_text(sql, encoding="utf-8")


def test_discover_orders_by_version(tmp_path):
    _write(tmp_path, "0010_tenth.sql")
    _write(tmp_path, "0002_second.sql")
    _write(tmp_path, "0001_first.sql")
    labels = [migration.label for migration in discover_migrations(tmp_path)]
    assert labels == ["0001_first", "0002_second", "0010_tenth"]


def test_discover_rejects_malformed_filename(tmp_path):
    _write(tmp_path, "bad-name.sql")
    with pytest.raises(ValueError, match="malformed"):
        discover_migrations(tmp_path)


def test_discover_rejects_duplicate_versions(tmp_path):
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0001_b.sql")
    with pytest.raises(ValueError, match="duplicate"):
        discover_migrations(tmp_path)


def test_pending_filters_applied_and_keeps_order():
    pending = pending_migrations([M1, M2], applied={"0001"})
    assert [migration.label for migration in pending] == ["0002_beta"]


def test_run_migrations_applies_each_once(tmp_path):
    _write(tmp_path, "0001_alpha.sql", "CREATE TABLE alpha ();")
    _write(tmp_path, "0002_beta.sql", "CREATE TABLE beta ();")
    conn = FakeConn()

    assert run_migrations(conn, tmp_path) == ["0001_alpha", "0002_beta"]
    assert run_migrations(conn, tmp_path) == []

    # One transaction per applied migration, plus a bookkeeping insert each.
    assert conn.transactions == 2
    inserts = [
        params
        for sql, params in conn.statements
        if sql.startswith("INSERT INTO schema_migrations")
    ]
    assert ("0001", "alpha") in inserts
    assert ("0002", "beta") in inserts
    # Migration SQL itself was executed before its bookkeeping insert.
    executed_sql = [sql for sql, _ in conn.statements]
    assert executed_sql.index("CREATE TABLE alpha ();") < next(
        i for i, sql in enumerate(executed_sql) if sql.startswith("INSERT INTO schema_migrations")
    )


def test_run_migrations_skips_recorded_versions(tmp_path):
    _write(tmp_path, "0001_alpha.sql", "CREATE TABLE alpha ();")
    conn = FakeConn(applied_versions={"0001"})
    assert run_migrations(conn, tmp_path) == []
    assert not any(sql.startswith("CREATE TABLE alpha") for sql, _ in conn.statements)


def test_migration_status_partitions_applied_and_pending(tmp_path):
    _write(tmp_path, "0001_alpha.sql")
    _write(tmp_path, "0002_beta.sql")
    conn = FakeConn(applied_versions={"0001"})
    applied, pending = migration_status(conn, tmp_path)
    assert applied == ["0001_alpha"]
    assert pending == ["0002_beta"]
