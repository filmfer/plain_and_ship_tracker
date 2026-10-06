"""Tests for the ship API and ship-aware search."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None)))


def test_list_ships_returns_all_eight() -> None:
    response = _client().get("/api/ships")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 8
    identifiers = {item["entity"]["identifier"] for item in data}
    assert identifiers == {f"DEMO-SHIP-{n:03d}" for n in range(1, 9)}


def test_ship_list_excludes_aircraft() -> None:
    data = _client().get("/api/ships").json()

    assert all(item["entity"]["type"] == "ship" for item in data)
    assert all(item["entity"]["identifier"].startswith("DEMO-SHIP") for item in data)


def test_ships_expose_maritime_identifiers_and_positions() -> None:
    first = _client().get("/api/ships").json()[0]

    entity = first["entity"]
    position = first["position"]
    assert entity["mmsi"] and entity["mmsi"].isdigit()
    assert entity["imo"].startswith("IMO")
    assert entity["callsign"]
    assert position is not None
    assert position["altitude"] is None
    assert position["course"] is not None


def test_get_ship_by_full_id() -> None:
    response = _client().get("/api/ships/ship:DEMO-SHIP-001")

    assert response.status_code == 200
    assert response.json()["entity"]["identifier"] == "DEMO-SHIP-001"


def test_get_ship_by_short_identifier() -> None:
    response = _client().get("/api/ships/DEMO-SHIP-001")

    assert response.status_code == 200
    assert response.json()["entity"]["id"] == "ship:DEMO-SHIP-001"


def test_get_unknown_ship_returns_404() -> None:
    assert _client().get("/api/ships/ship:NOPE").status_code == 404


def test_get_aircraft_id_via_ship_endpoint_returns_404() -> None:
    assert _client().get("/api/ships/aircraft:DEMO001").status_code == 404


def test_search_supports_demo_ship_identifiers() -> None:
    client = _client()
    for query in ["DEMO-SHIP-001", "demo ship 001", "DEMOSHIP001"]:
        response = client.get("/api/search", params={"q": query})
        assert response.status_code == 200
        identifiers = [item["entity"]["identifier"] for item in response.json()]
        assert "DEMO-SHIP-001" in identifiers


def test_search_supports_ship_mmsi_imo_and_name() -> None:
    client = _client()

    by_mmsi = client.get("/api/search", params={"q": "999000001"}).json()
    by_imo = client.get("/api/search", params={"q": "IMO9990001"}).json()
    by_name = client.get("/api/search", params={"q": "Atlantic Trader"}).json()

    assert "DEMO-SHIP-001" in [i["entity"]["identifier"] for i in by_mmsi]
    assert "DEMO-SHIP-001" in [i["entity"]["identifier"] for i in by_imo]
    assert "DEMO-SHIP-001" in [i["entity"]["identifier"] for i in by_name]
