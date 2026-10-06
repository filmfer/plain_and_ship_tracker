from app.providers.base import AircraftProvider, ShipProvider
from app.providers.safe import SafeAircraftProvider, SafeShipProvider


class FailingProvider(AircraftProvider, ShipProvider):
    def get_entities(self):
        raise RuntimeError("boom")
    def get_positions(self):
        raise RuntimeError("boom")

def test_safe_aircraft_provider_swallows_exceptions():
    provider = SafeAircraftProvider(FailingProvider())
    assert provider.get_entities() == []
    assert provider.get_positions() == []

def test_safe_ship_provider_swallows_exceptions():
    provider = SafeShipProvider(FailingProvider())
    assert provider.get_entities() == []
    assert provider.get_positions() == []

    assert provider.get_entities() == []
    assert provider.get_positions() == []
