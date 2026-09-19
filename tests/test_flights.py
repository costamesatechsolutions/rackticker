from datetime import datetime, timezone
import unittest

from app.core.config import validate_config
from app.core.models import Flight, Message, Snapshot, SystemStatus
from app.modules.base import RenderContext
from app.modules.flights import FlightModule, flight_number


def row(callsign, distance, **extra):
    return {"id": callsign, "callsign": callsign, "distance": distance, "bearing": 90, "altitude": 5000, **extra}


class FlightScreenTests(unittest.TestCase):
    def setUp(self):
        self.module = FlightModule()
        self.config = validate_config({})

    def context(self, snap, t=0.0):
        return RenderContext(datetime.now(timezone.utc), t, self.config, {"flight": snap}, Message("X", "X"), SystemStatus())

    def test_airline_callsigns_read_as_flight_numbers(self):
        self.assertEqual(flight_number("UAL0432"), ("UNITED 432", "UA432"))
        self.assertEqual(flight_number("N31444"), ("N31444", "N31444"))

    def test_busy_sky_lists_aircraft_then_spotlights_the_near_ones(self):
        flight = Flight("UAL1", "B738", "SFO", "SNA", 3000, 200, 3.0, 90, -500)
        rows = [row("UAL1", 3.0, origin="SFO", destination="SNA", local="destination",
                    cities=["SAN FRANCISCO", "SANTA ANA"]), row("DAL2", 6.0), row("N1", 12.0)]
        plan = self.module._plan(rows, 10)
        self.assertEqual([kind for kind, *_ in plan], ["board", "identity", "identity"])
        snap = Snapshot(flight, source="local_adsb", metadata={"nearby": rows})
        for t in (0, 2, 6, 12, 20):
            self.assertIsNotNone(self.module.render(self.context(snap, t)).getbbox())

    def test_nearly_overhead_aircraft_flies_across_first(self):
        plan = self.module._plan([row("UAL1", 1.0)], 10)
        self.assertEqual([kind for kind, *_ in plan], ["flyby", "identity"])

    def test_only_a_near_aircraft_makes_the_screen_available(self):
        far = Snapshot(Flight("UAL1", "B738", "---", "---", 30000, 400, 25.0, 0, 0), source="local_adsb",
                       metadata={"tracked_aircraft": 40})
        self.assertFalse(self.module.available(self.context(far)))

    def test_a_plane_leaving_mid_visit_is_finished_not_replaced_by_empty_sky(self):
        flight = Flight("UAL1", "B738", "SFO", "SNA", 3000, 200, 3.0, 90, -500)
        snap = Snapshot(flight, source="local_adsb", metadata={"nearby": [row("UAL1", 3.0)]})
        before = self.module.render(self.context(snap, 3))
        gone = Snapshot(None, source="local_adsb", metadata={})
        after = self.module.render(self.context(gone, 3))
        self.assertEqual(before.tobytes(), after.tobytes())
