from datetime import datetime, timezone
import unittest

from PIL import Image

from app.core.config import validate_config
from app.core.models import Flight, Message, Snapshot, SystemStatus
from app.modules.base import RenderContext
from app.modules.flights import FlightModule, flight_number


def row(callsign, distance=3.0, **extra):
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

    def flight_card(self, r, t=3.0):
        flight = Flight(r["callsign"], r.get("type", "ADS-B"), "---", "---", r.get("altitude", 5000), 200, 3.0, 90, -500)
        snap = Snapshot(flight, source="local_adsb", metadata={"nearby": [r]})
        return self.module.render(self.context(snap, t))

    def test_the_card_is_one_still_screen_nothing_scrolls_or_turns_over(self):
        r = row("UAL1432", type="B738", origin="LAX", destination="DEN", cities=["LOS ANGELES", "DENVER"],
                progress=.4, minutes_left=95, altitude=33000, speed=470)
        settled = self.flight_card(r, 1.0).tobytes()
        for t in (2.0, 4.6, 7.5, 9.9):
            self.assertEqual(self.flight_card(r, t).tobytes(), settled, f"the card changed at {t}s")

    def test_the_cities_are_named_in_full_and_the_bar_shows_how_far_along_it_is(self):
        base = dict(type="B738", origin="LAX", destination="DEN", minutes_left=95)
        named = self.flight_card(row("UAL1", cities=["LOS ANGELES", "DENVER"], progress=.4, **base)).tobytes()
        codes = self.flight_card(row("UAL1", progress=.4, **base)).tobytes()
        early = self.flight_card(row("UAL1", cities=["LOS ANGELES", "DENVER"], progress=.1, **base)).tobytes()
        late = self.flight_card(row("UAL1", cities=["LOS ANGELES", "DENVER"], progress=.9, **base)).tobytes()
        self.assertNotEqual(named, codes, "the cities were not shown")
        self.assertNotEqual(early, late, "the bar did not move with the flight")

    def test_a_long_city_name_is_shortened_at_a_hyphen_never_cut_mid_word(self):
        from app.modules.flights import INFO_X, FlightModule
        card = Image.new("RGB", (128, 32))
        FlightModule._line(card, "Minneapolis-St Paul", "MSP", 9, (255, 255, 255))
        # "Minneapolis" plus the code fits the 93 px; the whole name could not
        self.assertLessEqual(card.getbbox()[2], 128)
        with_all = Image.new("RGB", (128, 32))
        FlightModule._line(with_all, "Minneapolis", "MSP", 9, (255, 255, 255))
        self.assertEqual(card.tobytes(), with_all.tobytes())

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


class UnroutedCardTests(unittest.TestCase):
    """A flight with no route on file still fits its card in whole lines."""

    def test_height_and_speed_are_dropped_whole_never_cut_in_half(self):
        module = FlightModule()
        config = validate_config({})
        for altitude in (900, 16325, 41000):
            row = {"id": "SWA2492", "callsign": "SWA2492", "distance": 6.0, "bearing": 200, "altitude": altitude,
                   "type": "B38M", "speed": 470, "vertical_rate": 900}
            flight = Flight("SWA2492", "B38M", "---", "---", altitude, 470, 6.0, 200, 900)
            snap = Snapshot(flight, source="local_adsb", metadata={"nearby": [row]})
            context = RenderContext(datetime.now(timezone.utc), 3.0, config, {"flight": snap}, Message("X", "X"),
                                    SystemStatus())
            card = module.render(context)
            # the last column of the panel holds the end of a line only if a line ran off the edge
            self.assertIsNone(card.crop((127, 9, 128, 26)).getbbox(), f"a line ran off the panel at {altitude} ft")
