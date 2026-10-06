"""Tests for query normalization and entity matching."""

from __future__ import annotations

import pytest

from app.models import Entity, EntityType, Source
from app.search import entity_matches, normalize_flight_number, normalize_query


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TP1824", "TP1824"),
        ("tp1824", "TP1824"),
        ("TP 1824", "TP1824"),
        ("TP-1824", "TP1824"),
        ("DEMO-001", "DEMO001"),
        ("demo 001", "DEMO001"),
        ("DEMO-SHIP-001", "DEMOSHIP001"),
    ],
)
def test_normalize_query(raw: str, expected: str) -> None:
    assert normalize_query(raw) == expected


def _aircraft() -> Entity:
    return Entity(
        id="aircraft:DEMO001",
        type=EntityType.aircraft,
        identifier="DEMO001",
        source=Source.mock,
        callsign="DEMO001",
        registration="DEMO001",
        icao24="de0001",
    )


def _ship() -> Entity:
    return Entity(
        id="ship:DEMO-SHIP-001",
        type=EntityType.ship,
        identifier="DEMO-SHIP-001",
        source=Source.mock,
        name="Atlantic Trader",
        mmsi="999000001",
        imo="IMO9990001",
    )


@pytest.mark.parametrize("query", ["DEMO001", "demo001", "DEMO-001", "DEMO 001"])
def test_aircraft_matches_flight_number_variants(query: str) -> None:
    assert entity_matches(_aircraft(), normalize_query(query))


def test_aircraft_does_not_match_ship_identifier() -> None:
    assert not entity_matches(_aircraft(), normalize_query("DEMO-SHIP-001"))


def test_ship_matches_by_mmsi_and_imo() -> None:
    assert entity_matches(_ship(), normalize_query("999000001"))
    assert entity_matches(_ship(), normalize_query("IMO9990001"))
    assert entity_matches(_ship(), normalize_query("atlantic trader"))


def test_no_match_for_unknown() -> None:
    assert not entity_matches(_aircraft(), normalize_query("ZZZ999"))


def test_empty_query_matches_nothing() -> None:
    assert not entity_matches(_aircraft(), "")


# --- Flight-number normalization & field separation (PROMPT 21) -------------


def _tp_aircraft() -> Entity:
    """An aircraft whose identifier spaces must stay strictly separate."""
    return Entity(
        id="aircraft:TP1824",
        type=EntityType.aircraft,
        identifier="TP1824",  # flight number
        source=Source.mock,
        callsign="KLM987",
        registration="G-TP24",
        icao24="4caabc",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TP1824", "TP1824"),
        ("tp1824", "TP1824"),
        ("TP 1824", "TP1824"),
        ("TP-1824", "TP1824"),
        ("tp 1824", "TP1824"),
        ("  tp1824 ", "TP1824"),
        ("TP_1824", "TP1824"),
    ],
)
def test_normalize_flight_number_all_forms(raw: str, expected: str) -> None:
    assert normalize_flight_number(raw) == expected


@pytest.mark.parametrize("query", ["TP1824", "tp1824", "TP 1824", "TP-1824"])
def test_aircraft_matches_flight_number_all_forms(query: str) -> None:
    assert entity_matches(_tp_aircraft(), normalize_query(query))


def test_flight_number_prefix_search_still_works() -> None:
    assert entity_matches(_tp_aircraft(), normalize_query("TP18"))
    assert entity_matches(_tp_aircraft(), normalize_query("tp 18"))


def test_flight_number_stays_separate_from_callsign() -> None:
    entity = _tp_aircraft()  # identifier=TP1824, callsign=KLM987
    assert entity_matches(entity, normalize_query("KLM987"))  # its own field
    # A fragment from the middle of the callsign matches nothing.
    assert not entity_matches(entity, normalize_query("M987"))


def test_flight_number_stays_separate_from_icao24() -> None:
    entity = _tp_aircraft()  # icao24=4caabc
    assert entity_matches(entity, normalize_query("4caabc"))  # full ICAO24: exact
    assert not entity_matches(entity, normalize_query("4CAA"))  # no prefix leak
    assert not entity_matches(entity, normalize_query("CAABC"))  # no substring leak


def test_flight_number_stays_separate_from_registration() -> None:
    entity = _tp_aircraft()  # registration=G-TP24
    assert entity_matches(entity, normalize_query("G-TP24"))  # full: exact
    assert entity_matches(entity, normalize_query("g tp24"))  # form-insensitive
    assert not entity_matches(entity, normalize_query("GTP2"))  # fragment: no
    assert not entity_matches(entity, normalize_query("G-TP"))  # prefix: no


def test_flight_fragment_does_not_match_inside_entity_id() -> None:
    entity = _tp_aircraft()  # id="aircraft:TP1824"
    assert entity_matches(entity, normalize_query("aircraft:tp1824"))  # pasted id
    assert entity_matches(entity, normalize_query("AIRCRAFT:TP18"))  # id prefix
    assert not entity_matches(entity, normalize_query("1824"))  # not inside id
    assert not entity_matches(entity, normalize_query("TP1824K"))  # longer than any field

