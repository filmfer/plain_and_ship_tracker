"""Versioned SQL migration runner for the historical store.

Plain, dependency-free approach: ordered ``NNNN_name.sql`` files are applied
once each, in order, each inside its own transaction, and recorded in a
``schema_migrations`` bookkeeping table. Re-running is always a no-op.

CLI::

    cd backend && python -m app.migrate up      # apply pending migrations
    cd backend && python -m app.migrate status  # show applied / pending

The runner never touches PostgreSQL unless invoked; the application itself
does not auto-migrate (keeps startup side-effect free and deployments explicit).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import psycopg

from app.config import get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
FILENAME_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    name       text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    """One migration file on disk."""

    version: str
    name: str
    sql: str

    @property
    def label(self) -> str:
        return f"{self.version}_{self.name}"


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Load all ``NNNN_name.sql`` files, ordered by version.

    Raises ``ValueError`` on malformed filenames or duplicate versions so a
    broken migration set fails loudly before any SQL runs.
    """
    migrations: list[Migration] = []
    seen: dict = {}
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME_RE.match(path.name)
        if match is None:
            raise ValueError(f"malformed migration filename: {path.name!r}")
        version = match["version"]
        if version in seen:
            raise ValueError(
                f"duplicate migration version {version}: {seen[version]!r} and {path.name!r}"
            )
        seen[version] = path.name
        migrations.append(Migration(version, match["name"], path.read_text(encoding="utf-8")))
    migrations.sort(key=lambda migration: migration.version)
    return migrations


def pending_migrations(
    available: Sequence[Migration], applied: set
) -> list[Migration]:
    """Return ``available`` migrations whose version is not in ``applied``."""
    return [migration for migration in available if migration.version not in applied]


def applied_versions(conn) -> set:
    """Read the recorded migration versions from ``schema_migrations``."""
    conn.execute(CREATE_MIGRATIONS_TABLE)
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return {row[0] for row in rows}


def run_migrations(conn, directory: Path = MIGRATIONS_DIR) -> list:
    """Apply pending migrations, each in its own transaction.

    ``conn`` is a psycopg3 connection in **autocommit** mode. Returns the
    labels of the migrations applied by this call (empty when up to date).
    """
    already_applied = applied_versions(conn)
    applied_now = []
    for migration in pending_migrations(discover_migrations(directory), already_applied):
        with conn.transaction():
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (migration.version, migration.name),
            )
        applied_now.append(migration.label)
        logger.info("applied migration %s", migration.label)
    return applied_now


def migration_status(conn, directory: Path = MIGRATIONS_DIR) -> tuple:
    """Return ``(applied_labels, pending_labels)`` without changing anything."""
    already_applied = applied_versions(conn)
    available = discover_migrations(directory)
    applied = [m.label for m in available if m.version in already_applied]
    pending = [m.label for m in pending_migrations(available, already_applied)]
    return applied, pending


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point (``python -m app.migrate``)."""
    parser = argparse.ArgumentParser(
        prog="python -m app.migrate",
        description="Apply or inspect SQL migrations for the historical store.",
    )
    parser.add_argument(
        "command", nargs="?", default="up", choices=("up", "status"),
        help="up: apply pending migrations (default); status: list applied/pending",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_url.strip():
        print(
            "DATABASE_URL is not set - no database to migrate (demo mode). "
            "Set DATABASE_URL in .env first.",
            file=sys.stderr,
        )
        return 1

    try:
        with psycopg.connect(
            settings.database_url,
            connect_timeout=settings.db_connect_timeout_s,
            autocommit=True,
        ) as conn:
            if args.command == "status":
                applied, pending = migration_status(conn)
                for label in applied:
                    print(f"applied  {label}")
                for label in pending:
                    print(f"pending  {label}")
            else:
                applied_now = run_migrations(conn)
                if applied_now:
                    for label in applied_now:
                        print(f"applied  {label}")
                else:
                    print("already up to date")
    except Exception as exc:  # noqa: BLE001 - CLI reports any failure plainly
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
