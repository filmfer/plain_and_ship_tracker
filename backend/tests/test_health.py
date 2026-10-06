"""Health endpoint and configuration tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import AppMode, Settings
from app.main import create_app


def test_health_defaults_to_demo_mode() -> None:
    """With an empty environment the app must run in demo mode."""
    client = TestClient(create_app(Settings(_env_file=None)))

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app_mode"] == "demo"
    assert body["version"]


def test_health_reflects_configured_mode() -> None:
    """The reported mode reflects the injected settings."""
    settings = Settings(_env_file=None, app_mode=AppMode.production)
    client = TestClient(create_app(settings))

    body = client.get("/api/health").json()

    assert body["app_mode"] == "production"
