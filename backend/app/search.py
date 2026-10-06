"""Query normalization and entity matching for the search endpoint.

Flight numbers arrive in many shapes (``TP1824``, ``tp1824``, ``TP 1824``,
``TP-1824``); :func:`normalize_flight_number` maps every shape to one
canonical form - uppercase, every non-alphanumeric separator removed - so
queries and stored fields compare identically.

Field separation (project rule #18): the search never blurs identifier
spaces. Each field group has its own match rule:

* **flight number** (``identifier``) and **callsign`` - *prefix* match on the
  canonical form, so progressive typing works while a flight-number fragment
  can never hit another field's substring;
* **registration**, **icao24**, **mmsi**, **imo** - *exact* matches only:
  they are complete identifiers, never searchable fragments;
* **name** - *substring* (free text such as "Atlantic Trader");
* **id** - *prefix* (pasted ``aircraft:...`` / ``ship:...`` ids).
"""

from __future__ import annotations

from app.models import Entity


def normalize_flight_number(value: str) -> str:
    """Return the canonical flight-number form.

    ``TP1824`` / ``tp1824`` / ``TP 1824`` / ``TP-1824`` all become
    ``TP1824``: uppercase and drop every non-alphanumeric character.
    """
    return "".join(char for char in value.upper() if char.isalnum())


def normalize_query(value: str) -> str:
    """Uppercase and remove non-alphanumeric characters (general query form)."""
    return normalize_flight_number(value)


def entity_matches(entity: Entity, normalized_query: str) -> bool:
    """Return ``True`` if ``normalized_query`` matches ``entity``.

    ``normalized_query`` must already be normalized with
    :func:`normalize_query`. Each field group is evaluated under its own rule
    (see the module docstring) so flight numbers stay separate from callsign,
    ICAO24 and registration.
    """
    if not normalized_query:
        return False
    # Flight number + callsign: prefix on the canonical, separator-free form.
    for field in (entity.identifier, entity.callsign):
        if field and normalize_flight_number(field).startswith(normalized_query):
            return True
    # Registration / ICAO24 / MMSI / IMO: exact identifier matches only.
    for field in (entity.registration, entity.icao24, entity.mmsi, entity.imo):
        if field and normalize_query(field) == normalized_query:
            return True
    # Free text name: substring. Pasted entity id: prefix.
    if entity.name and normalized_query in normalize_query(entity.name):
        return True
    return bool(entity.id) and normalize_query(entity.id).startswith(normalized_query)

