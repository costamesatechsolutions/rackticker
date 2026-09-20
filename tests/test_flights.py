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

    def test_busy_sky_gives_each_near_aircraft_its_own_full_card(self):
        flight = Flight("UAL1", "B738", "SFO", "SNA", 3000, 200, 3.0, 90, -500)
        rows = [row("UAL1", 3.0, origin="SFO", destination="SNA", local="destination",
                    cities=["SAN FRANCISCO", "SANTA ANA"]), row("DAL2", 6.0), row("N1", 12.0)]
        plan = self.module._plan(rows, 10)
        self.assertEqual([kind for kind, *_ in plan], ["identity", "identity"])   # the one 12 miles out is too far
        snap = Snapshot(flight, source="local_adsb", metadata={"nearby": rows})
        for t in (0, 2, 6, 12, 20):
            self.assertIsNotNone(self.module.render(self.context(snap, t)).getbbox())

    def test_an_aircraft_with_no_route_on_file_says_which_airport_it_is_at(self):
        """A callsign's route is often another day's; where it is and how it moves is not."""
        landing = row("AAL941", 3.0, type="A321", altitude=1900, speed=140, vertical_rate=-700,
                      phase="arriving", airport="SNA", airport_city="Santa Ana")
        flight = Flight("AAL941", "A321", "---", "---", 1900, 140, 3.0, 45, -700)
        snap = Snapshot(flight, source="local_adsb", metadata={"nearby": [landing]})
        with_leg = self.module.render(self.context(snap, 1.0)).tobytes()
        bare = dict(landing, phase="", airport="", airport_city="")
        without = self.module.render(self.context(Snapshot(flight, source="local_adsb", metadata={"nearby": [bare]}), 1.0))
        self.assertNotEqual(with_leg, without.tobytes(), "the landing airport was not shown")

    def test_the_bottom_line_turns_over_through_altitude_speed_and_distance(self):
        rows = [row("UAL1", 3.4, type="B738", altitude=3500, speed=210, vertical_rate=-800, bearing=45,
                    minutes_flown=95)]
        first = self.module._more(rows[0], 1)[0]
        second = self.module._more(rows[0], 2)[0]
        self.assertIn("3,500FT", first)
        self.assertIn("210KT", first)
        self.assertIn("3.4MI NE", second)
        self.assertIn("IN AIR 1H35M", second)

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
