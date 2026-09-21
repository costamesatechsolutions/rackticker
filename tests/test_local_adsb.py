import asyncio
import copy
from importlib import util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from rackticker import PluginContext
from app.core.config import validate_config
from app.core.plugins import PluginRegistry
from app.core.runtime import Runtime
from app.outputs.browser import BrowserSink

spec = util.spec_from_file_location("adsb_test", Path(__file__).resolve().parents[1] / "plugins/local-adsb/rackticker_local_adsb.py")
adsb = util.module_from_spec(spec)
spec.loader.exec_module(adsb)


class TailNumberTests(unittest.TestCase):
    """A US registration is arithmetic on the address the aeroplane transmits.

    Checked against 99 live registrations from a public feed: all 99 matched."""

    def test_the_ends_of_the_range(self):
        self.assertEqual(adsb.tail_number("A00001"), "N1")
        self.assertEqual(adsb.tail_number("ADF7C7"), "N99999")

    def test_known_aircraft(self):
        self.assertEqual(adsb.tail_number("A8D46A"), "N66808")
        self.assertEqual(adsb.tail_number("AC5E6E"), "N8963Q")

    def test_addresses_outside_the_united_states_have_no_n_number(self):
        self.assertEqual(adsb.tail_number("4B1919"), "")     # Swiss
        self.assertEqual(adsb.tail_number("400001"), "")     # British

    def test_nonsense_is_not_a_registration(self):
        for bad in ("", None, "zzz", "GGGGGG", 12.5):
            self.assertEqual(adsb.tail_number(bad), "")

    def test_every_address_in_the_range_makes_a_plausible_tail(self):
        import random
        for _ in range(500):
            value = random.randrange(0xA00001, 0xADF7C7)
            tail = adsb.tail_number(f"{value:X}")
            self.assertRegex(tail, r"^N[1-9]\d{0,4}[A-HJ-NP-Z]{0,2}$")


class RouteSanityTests(unittest.TestCase):
    """A callsign is flown again every day, so the route on file may be yesterday's."""

    LAX, SFO = (33.94, -118.40), (37.62, -122.38)
    SPS, SBN = (33.99, -98.49), (41.71, -86.32)     # Wichita Falls to South Bend
    OVER_ORANGE_COUNTY = (33.70, -117.89)

    def journey(self, origin, destination, where, speed=420):
        payload = {"response": {"flightroute": {
            "origin": {"latitude": origin[0], "longitude": origin[1]},
            "destination": {"latitude": destination[0], "longitude": destination[1]}}}}
        return adsb.journey(payload, where[0], where[1], speed)

    def test_a_flight_on_its_route_keeps_its_timings(self):
        found = self.journey(self.LAX, self.SFO, self.OVER_ORANGE_COUNTY)
        self.assertIn("minutes_left", found)
        self.assertGreater(found["minutes_left"], 0)

    def test_a_route_the_aircraft_is_nowhere_near_is_not_this_flight(self):
        """It read as 374 minutes to go over a plane that was minutes from landing."""
        self.assertEqual(self.journey(self.SPS, self.SBN, self.OVER_ORANGE_COUNTY), {})

    def test_a_short_hop_is_not_thrown_out_for_being_short(self):
        near = (33.80, -118.10)
        self.assertIn("progress", self.journey(self.LAX, (32.73, -117.19), near))

    def test_a_parked_aircraft_has_no_minutes_but_keeps_its_route(self):
        found = self.journey(self.LAX, self.SFO, self.LAX, speed=0)
        self.assertIn("progress", found)
        self.assertNotIn("minutes_left", found)


class AirframeDatabaseTests(unittest.TestCase):
    """The receiver's own database: a plane does not transmit what it is."""

    def setUp(self):
        import json, tempfile
        from pathlib import Path
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "A.json").write_text(json.dumps({"children": ["AC"], "6EE47": {"t": "B738"}}))
        (self.folder / "AC.json").write_text(json.dumps({"5E6E": {"t": "B38M", "desc": "L2J"}}))
        (self.folder / "4B.json").write_text(json.dumps({"1919": {"r": "HB-JND", "t": "B77W"}}))
        self.db = adsb.Airframes((self.folder,))

    def test_a_type_is_found_however_deep_its_file_is(self):
        self.assertEqual(self.db.find("a6ee47")["icao_type"], "B738")     # one-character prefix
        self.assertEqual(self.db.find("AC5E6E")["icao_type"], "B38M")     # two
        self.assertEqual(self.db.find("4b1919"), {"icao_type": "B77W", "registration": "HB-JND"})

    def test_an_aircraft_that_is_not_in_there_is_not_guessed_at(self):
        self.assertEqual(self.db.find("abcdef"), {})

    def test_nonsense_never_reaches_the_disk(self):
        for bad in ("", None, "zz", "ac5e6", "ac5e6eff", "../../etc"):
            self.assertEqual(self.db.find(bad), {})

    def test_a_missing_database_is_not_an_error(self):
        from pathlib import Path
        self.assertEqual(adsb.Airframes((Path("/nowhere/db"),)).find("ac5e6e"), {})

    def test_it_keeps_only_a_few_files_in_memory(self):
        for n in range(200):
            self.db.find(f"{n:06X}")
        self.assertLessEqual(len(self.db.tables), 49)


class ADSBTests(unittest.TestCase):
    def setUp(self):
        self.settings = copy.deepcopy(adsb.plugin.defaults)
        self.receiver = {"lat": 0.0, "lon": 0.0}
        self.report = {"hex": "abcdef", "flight": " TEST123 ", "lat": .01, "lon": 0,
                       "seen_pos": 1, "alt_baro": 1234, "gs": 130.5, "baro_rate": -500}

    def select(self, reports, age=0):
        return adsb.select_aircraft({"now": 1000-age, "aircraft": reports}, self.receiver, self.settings, 1000)

    def test_nearest_fresh_airborne_report_and_unknown_metadata(self):
        far = {**self.report, "hex": "123456", "lat": .02}
        ground = {**self.report, "alt_baro": "ground", "lat": .001}
        _, winner, metadata = self.select([far, ground, self.report])
        self.assertEqual(winner[1], "ABCDEF")
        self.assertEqual((metadata["tracked_aircraft"], metadata["positioned_aircraft"]), (2, 2))
        self.assertEqual([row["nearest"] for row in metadata["nearby"]], [True, False])
        self.assertLess(metadata["nearby"][0]["distance"], metadata["nearby"][1]["distance"])
        flight = winner[2]
        self.assertEqual(flight.callsign, "TEST123")
        self.assertEqual(flight.origin, "---")
        self.assertEqual(flight.aircraft, "ADS-B")
        self.assertAlmostEqual(flight.distance_miles, .690934, places=5)
        self.assertEqual(flight.bearing_deg, 0)

    def test_stale_receiver_and_combined_position_age(self):
        with self.assertRaisesRegex(ValueError, "stale"): self.select([self.report], age=16)
        self.assertIsNone(self.select([{**self.report, "seen_pos": 10}], age=6)[1])

    def test_an_aircraft_with_no_callsign_and_no_us_registration_keeps_its_address(self):
        report = {**self.report, "hex": "4b1919", "flight": ""}
        self.assertEqual(self.select([report])[1][2].callsign, "4B1919")

    def test_bad_reports_do_not_hide_good_report(self):
        bad = [None, {}, {**self.report, "lat": float("nan")}, {**self.report, "seen_pos": True},
               {**self.report, "lon": 190}, {**self.report, "lat": 50}]
        self.assertEqual(self.select(bad + [self.report])[1][1], "ABCDEF")

    def test_missing_telemetry_stays_unknown_and_missing_location_is_error(self):
        report = {**self.report, "flight": "", "alt_baro": None, "gs": None, "baro_rate": None}
        flight = self.select([report])[1][2]
        # No callsign: a US aeroplane is named by the tail number painted on it,
        # worked out from its address, rather than by the address itself.
        self.assertEqual(flight.callsign, "N86QU")
        self.assertIsNone(flight.altitude_ft)
        self.assertIsNone(flight.speed_kts)
        self.assertIsNone(flight.vertical_rate)
        self.receiver = {}
        with self.assertRaises(ValueError): self.select([report])

    def test_tracks_fresh_aircraft_without_position(self):
        report = {"hex": "abc123", "flight": "NOFIX", "seen": 1}
        _, winner, metadata = self.select([report])
        self.assertIsNone(winner)
        self.assertEqual((metadata["tracked_aircraft"], metadata["positioned_aircraft"]), (1, 0))
        self.assertEqual(metadata["nearby"], [])

    def test_route_enrichment_adds_airports_type_registration_and_eta(self):
        flight = self.select([self.report])[1][2]
        payload = {"response": {
            "aircraft": {"icao_type": "B738", "registration": "N123AB"},
            "flightroute": {
                "origin": {"iata_code": "LAX", "icao_code": "KLAX"},
                "destination": {"iata_code": "SFO", "icao_code": "KSFO",
                                "latitude": 0.5, "longitude": 0.0},
            },
        }}
        enriched = adsb.apply_route(flight, .01, 0, payload)
        self.assertEqual((enriched.origin, enriched.destination), ("LAX", "SFO"))
        self.assertEqual((enriched.aircraft, enriched.registration), ("B738", "N123AB"))
        self.assertIsInstance(enriched.eta_minutes, int)

    def test_bad_or_missing_route_is_nonfatal(self):
        flight = self.select([self.report])[1][2]
        self.assertIs(adsb.apply_route(flight, 0, 0, {"response": {}}), flight)
        with self.assertRaises(ValueError):
            adsb.apply_route(flight, 0, 0, {"response": {"flightroute": {"origin": {}, "destination": {}}}})


class ADSBIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_file_cache_failure_recovery_and_alert_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            settings = {**adsb.plugin.defaults, "aircraft_path": str(path / "aircraft.json"),
                        "receiver_path": str(path / "receiver.json")}
            (path / "receiver.json").write_text(json.dumps({"lat": 0, "lon": 0}))
            report = {"hex": "abcdef", "flight": "REAL123", "lat": .01, "lon": 0, "seen_pos": 0}
            (path / "aircraft.json").write_text(json.dumps({"now": time.time(), "aircraft": [report]}))
            registry = PluginRegistry()
            registry.register(adsb.plugin)
            config = validate_config({"plugins": {"local_adsb": settings}, "display": {"transition": "cut"}}, registry)
            runtime = Runtime(config, BrowserSink(), registry)
            try:
                await runtime.refresh_provider("flight")
                self.assertEqual(runtime.snapshots["flight"].source, "local_adsb")
                self.assertEqual(runtime.scheduler.current.module, "flight")
                await runtime.step(.1, time.monotonic())
                self.assertEqual(runtime.frame.size, (128, 32))
                events = len(runtime.events)
                await runtime.refresh_provider("flight")
                self.assertEqual(len(runtime.events), events)
                (path / "aircraft.json").write_text("{bad json")
                await runtime.refresh_provider("flight")
                self.assertFalse(runtime.snapshots["flight"].stale)      # one bad read is forgiven...
                for _ in range(2):
                    await runtime.refresh_provider("flight")
                self.assertTrue(runtime.snapshots["flight"].stale)       # ...three in a row are not
                self.assertEqual(runtime.snapshots["flight"].data.callsign, "REAL123")
                self.assertFalse(runtime.modules["flight"].available(runtime.context()))
                (path / "aircraft.json").write_text(json.dumps({"now": time.time(), "aircraft": []}))
                await runtime.refresh_provider("flight")
                self.assertFalse(runtime.snapshots["flight"].stale)
                self.assertIsNone(runtime.snapshots["flight"].data)
            finally:
                await runtime.close()

    async def test_rejected_alert_is_retried_and_cooldown_limits_new_aircraft(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "receiver.json").write_text('{"lat":0,"lon":0}')
            settings = {**adsb.plugin.defaults, "receiver_path": str(path / "receiver.json"),
                        "aircraft_path": str(path / "aircraft.json")}
            emit = Mock(side_effect=[False, True])
            provider = adsb.LocalADSB(PluginContext("local_adsb", lambda: settings, emit))
            def report(identity):
                (path / "aircraft.json").write_text(json.dumps({"now":time.time(),"aircraft":[
                    {"hex":identity,"lat":.01,"lon":0,"seen_pos":0}]}))
            report("abcdef")
            await provider.fetch()
            await provider.fetch()
            await provider.fetch()
            report("123456")
            await provider.fetch()
            self.assertEqual(emit.call_count, 2)

    async def test_display_radius_does_not_expand_interrupt_radius(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "receiver.json").write_text('{"lat":0,"lon":0}')
            settings = {**adsb.plugin.defaults, "receiver_path": str(path / "receiver.json"),
                        "aircraft_path": str(path / "aircraft.json")}
            (path / "aircraft.json").write_text(json.dumps({"now": time.time(), "aircraft": [
                {"hex": "abcdef", "lat": .1, "lon": 0, "seen_pos": 0}]}))
            emit = Mock()
            provider = adsb.LocalADSB(PluginContext("local_adsb", lambda: settings, emit))
            snapshot = await provider.fetch()
            self.assertIsNotNone(snapshot.data)
            self.assertGreater(snapshot.data.distance_miles, settings["interrupt_radius_miles"])
            emit.assert_not_called()


class TailNumberTests(unittest.TestCase):
    """A US registration is arithmetic on the address the aeroplane transmits.

    Checked against 99 live registrations from a public feed: all 99 matched."""

    def test_the_ends_of_the_range(self):
        self.assertEqual(adsb.tail_number("A00001"), "N1")
        self.assertEqual(adsb.tail_number("ADF7C7"), "N99999")

    def test_known_aircraft(self):
        self.assertEqual(adsb.tail_number("A8D46A"), "N66808")
        self.assertEqual(adsb.tail_number("AC5E6E"), "N8963Q")

    def test_addresses_outside_the_united_states_have_no_n_number(self):
        self.assertEqual(adsb.tail_number("4B1919"), "")     # Swiss
        self.assertEqual(adsb.tail_number("400001"), "")     # British

    def test_nonsense_is_not_a_registration(self):
        for bad in ("", None, "zzz", "GGGGGG", 12.5):
            self.assertEqual(adsb.tail_number(bad), "")

    def test_every_address_in_the_range_makes_a_plausible_tail(self):
        import random
        for _ in range(500):
            value = random.randrange(0xA00001, 0xADF7C7)
            tail = adsb.tail_number(f"{value:X}")
            self.assertRegex(tail, r"^N[1-9]\d{0,4}[A-HJ-NP-Z]{0,2}$")


class RouteSanityTests(unittest.TestCase):
    def test_a_route_the_aircraft_is_not_flying_is_dropped(self):
        payload = {"response": {"aircraft": {"icao_type": "A321"}, "flightroute": {
            "origin": {"iata_code": "ORD", "latitude": 41.98, "longitude": -87.90},
            "destination": {"iata_code": "LGA", "latitude": 40.78, "longitude": -73.87}}}}
        over_orange_county = adsb.plausible(payload, 33.7, -117.9)
        self.assertNotIn("flightroute", over_orange_county["response"])
        self.assertIn("aircraft", over_orange_county["response"])
        self.assertIs(adsb.plausible(payload, 41.5, -80.0), payload)

    def test_local_end_names_the_home_airport(self):
        payload = {"response": {"flightroute": {
            "origin": {"latitude": 33.68, "longitude": -117.87},
            "destination": {"latitude": 37.62, "longitude": -122.38}}}}
        self.assertEqual(adsb.local_end(payload, (33.7, -117.9)), "origin")
        self.assertIsNone(adsb.local_end(payload, (35.0, -119.5)))
