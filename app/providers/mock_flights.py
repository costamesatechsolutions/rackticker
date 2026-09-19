from app.core.models import Flight, Snapshot
from app.providers.base import FlightProvider, finite_number, label


def normalize_flight(raw):
    return Flight(
        callsign=label(raw.get("callsign"), 8), aircraft=label(raw.get("aircraft"), 4),
        origin=label(raw.get("origin"), 3), destination=label(raw.get("destination"), 3),
        altitude_ft=int(finite_number(raw.get("altitude_ft", 0), -2000, 100000)),
        speed_kts=int(finite_number(raw.get("speed_kts", 0), 0, 2500)),
        distance_miles=finite_number(raw.get("distance_miles", 0), 0, 20000),
        bearing_deg=int(finite_number(raw.get("bearing_deg", 0), 0, 360)) % 360,
        vertical_rate=int(finite_number(raw.get("vertical_rate", 0), -20000, 20000)),
    )


class MockFlightProvider(FlightProvider):
    scenarios = ("none", "united", "southwest", "high")

    def __init__(self):
        self.scenario = "none"

    def set_scenario(self, scenario):
        if scenario not in self.scenarios:
            raise ValueError("Unknown flight scenario")
        self.scenario = scenario

    async def fetch(self):
        if self.scenario == "none":
            return Snapshot(None)
        raw = {"callsign": "UAL1187", "aircraft": "B738", "origin": "LAX", "destination": "DEN",
               "altitude_ft": 7425, "speed_kts": 412, "distance_miles": 4.1, "bearing_deg": 45, "vertical_rate": 1200}
        if self.scenario == "southwest":
            raw.update(callsign="SWA204", origin="OAK", destination="BUR", altitude_ft=2200,
                       speed_kts=185, distance_miles=1.2, vertical_rate=-650)
        elif self.scenario == "high":
            raw.update(callsign="DAL418", aircraft="A359", origin="SEA", destination="ATL",
                       altitude_ft=38000, speed_kts=486, distance_miles=22.8, vertical_rate=0)
        return Snapshot(normalize_flight(raw))
