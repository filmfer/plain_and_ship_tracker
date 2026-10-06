"""Shared test configuration.

Keeps the suite hermetic: tests must never depend on (or write to) an
externally configured database, even when the developer shell exports
``DATABASE_URL``. Pydantic-settings reads OS environment variables regardless
of ``_env_file=None``, so the variable is removed for the duration of each
test. Real-database integration tests use the separate, opt-in
``PBR_TEST_DATABASE_URL`` variable instead (see tests/test_db.py).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may pick up live infrastructure configuration.

    ``DATABASE_URL`` must not leak into tests (pydantic reads OS environment
    regardless of ``_env_file``), and live providers must never activate: the
    suite runs exclusively on mocked responses with the offline demo default.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENSKY_ENABLED", raising=False)
    monkeypatch.delenv("AISHUB_ENABLED", raising=False)
    monkeypatch.delenv("AISHUB_USERNAME", raising=False)
    monkeypatch.delenv("LOCAL_AIS_ENABLED", raising=False)
    monkeypatch.delenv("APP_MODE", raising=False)
