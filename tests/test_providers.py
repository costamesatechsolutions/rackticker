import unittest
from datetime import datetime, timezone
from app.providers.mock_flights import MockFlightProvider, normalize_flight
from app.providers.mock_sports import MockSportsProvider, normalize_game


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_mock_scenario_is_normalized(self):
        for provider in (MockFlightProvider(), MockSportsProvider()):
            for scenario in provider.scenarios:
                provider.set_scenario(scenario)
                snapshot = await provider.fetch()
                self.assertEqual(snapshot.source,"mock")
                self.assertFalse(snapshot.stale)
                self.assertIsNotNone(snapshot.updated_at.tzinfo)
                if scenario != "none": self.assertIsNotNone(snapshot.data)
            with self.assertRaises(ValueError): provider.set_scenario("invalid")

    def test_flight_converts_strings_and_bounds_labels(self):
        f = normalize_flight({"callsign":" ual1187 ","altitude_ft":"7425","distance_miles":"4.1","bearing_deg":360})
        self.assertEqual(f.callsign,"UAL1187")
        self.assertEqual(f.altitude_ft,7425)
        self.assertEqual(f.bearing_deg,0)
        self.assertEqual(f.origin,"---")
        with self.assertRaises(ValueError): normalize_flight({"distance_miles":"nan"})

    def test_generic_sports_normalization_and_invalid_score(self):
        g = normalize_game({"home":{"abbreviation":"sea", "score":"24", "color":"bad"},
                            "away":{"abbreviation":"sf","score":17},"status":"live"})
        self.assertEqual(g.home.abbreviation,"SEA")
        self.assertEqual(g.home.score,24)
        self.assertEqual(g.home.color,"#e8f0eb")
        with self.assertRaises(ValueError): normalize_game({"home":{"score":-1},"away":{}})
