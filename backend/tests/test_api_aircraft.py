"""Tests for the aircraft and search API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None)))


def test_list_aircraft_returns_all_eight() -> None:
    response = _client().get("/api/aircraft")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 8
    identifiers = {item["entity"]["identifier"] for item in data}
    assert identifiers == {f"DEMO{n:03d}" for n in range(1, 9)}


def test_aircraft_list_excludes_ships() -> None:
    data = _client().get("/api/aircraft").json()

    assert all(item["entity"]["type"] == "aircraft" for item in data)
    assert all("SHIP" not in item["entity"]["identifier"] for item in data)


def test_aircraft_include_positions() -> None:
    data = _client().get("/api/aircraft").json()

    first = data[0]
    assert first["position"] is not None
    assert -90.0 <= first["position"]["latitude"] <= 90.0
    assert first["position"]["source"] == "mock"


def test_get_aircraft_by_full_id() -> None:
    response = _client().get("/api/aircraft/aircraft:DEMO001")

    assert response.status_code == 200
    assert response.json()["entity"]["identifier"] == "DEMO001"


def test_get_aircraft_by_short_identifier() -> None:
    response = _client().get("/api/aircraft/DEMO001")

    assert response.status_code == 200
    assert response.json()["entity"]["id"] == "aircraft:DEMO001"


def test_get_unknown_aircraft_returns_404() -> None:
    assert _client().get("/api/aircraft/aircraft:NOPE").status_code == 404


def test_get_ship_id_via_aircraft_endpoint_returns_404() -> None:
    assert _client().get("/api/aircraft/ship:DEMO-SHIP-001").status_code == 404


def test_search_supports_demo_flight_number_variants() -> None:
    client = _client()
    for query in ["DEMO001", "demo001", "DEMO-001", "DEMO 001"]:
        response = client.get("/api/search", params={"q": query})
        assert response.status_code == 200
        identifiers = [item["entity"]["identifier"] for item in response.json()]
        assert "DEMO001" in identifiers


def test_search_adds_position_data() -> None:
    data = _client().get("/api/search", params={"q": "DEMO001"}).json()

    assert data[0]["position"]["source"] == "mock"


def test_search_unknown_returns_empty_list() -> None:
    response = _client().get("/api/search", params={"q": "ZZZ999"})

    assert response.status_code == 200
    assert response.json() == []


def test_search_requires_query_parameter() -> None:
    assert _client().get("/api/search").status_code == 422
