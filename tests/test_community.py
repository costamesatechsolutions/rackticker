"""Every community plugin loads, registers and renders with no data."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest

from app.core.config import validate_config
from app.core.manifest import read_manifest
from app.core.models import Message, SystemStatus
from app.core.plugins import PluginRegistry
from app.core.renderer import validate_frame
from app.modules.base import RenderContext

COMMUNITY = Path(__file__).resolve().parents[1] / "community"


class CommunityTests(unittest.TestCase):
    def test_index_lists_real_folders(self):
        index = json.loads((COMMUNITY / "index.json").read_text())
        for entry in index["plugins"]:
            self.assertTrue((COMMUNITY / entry["id"] / "plugin.json").exists(), entry["id"])
            self.assertTrue(entry["url"].endswith(f"/community/{entry['id']}"))

    def test_each_plugin_registers_and_renders_empty(self):
        for folder in sorted(path for path in COMMUNITY.iterdir() if (path / "plugin.json").exists()):
            with self.subTest(folder.name):
                manifest = read_manifest(folder)
                spec = importlib.util.spec_from_file_location(f"community_{manifest.id}", folder / manifest.entry)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                registry = PluginRegistry()
                registry.register(module.plugin)
                config = validate_config({}, registry)
                screen = module.plugin.module()
                context = RenderContext(datetime.now(timezone.utc), 1.0, config, {}, Message("", ""), SystemStatus())
                self.assertFalse(screen.available(context))
                validate_frame(screen.render(context))


def community(name):
    spec = importlib.util.spec_from_file_location(f"community_{name}_test", COMMUNITY / name / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeparturesTests(unittest.TestCase):
    def test_long_names_shorten_the_way_boards_do(self):
        departures = community("departures")
        self.assertEqual(departures._fits("MILANO CENTRALE", 70, False), "MILANO C.LE")
        self.assertEqual(departures._fits("Genève-Aéroport", 40, True), "Genève")
        self.assertEqual(departures._fits("Eger", 40, True), "Eger")

    def test_paired_platforms_fit_the_column(self):
        departures = community("departures")
        self.assertEqual(departures._track_label("43/44"), "43")
        self.assertEqual(departures._track_label("7/8"), "7/8")
        self.assertEqual(departures._track_label("12"), "12")

    def test_station_announces_delays_platforms_and_cancellations(self):
        from zoneinfo import ZoneInfo
        departures = community("departures")
        when = datetime(2026, 9, 18, 17, 5, tzinfo=ZoneInfo("Europe/Rome"))
        row = {"time": when, "delay": 15, "kind": "FR", "number": "9612", "destination": "Milano Centrale",
               "track": "24", "moved": False, "cancelled": False}
        heading, said = departures.notices([row, {**row, "delay": 2}, {**row, "delay": 0, "moved": True},
                                            {**row, "cancelled": True}], "trenitalia")
        self.assertEqual(heading, "AVVISO")
        self.assertEqual(said, ["FR 9612 per MILANO CENTRALE delle 17:05: ritardo 15 minuti",
                                "FR 9612 per MILANO CENTRALE delle 17:05 parte dal binario 24",
                                "FR 9612 per MILANO CENTRALE delle 17:05 è cancellato"])


class AmtrakTests(unittest.TestCase):
    """Amtrak's own feed, as the board reads it."""

    def run_for(self, **extra):
        return {"trainNum": "580", "routeName": "Pacific Surfliner", "destName": "San Diego Santa Fe Depot",
                "destCode": "SAN", "trainState": "Active",
                "stations": [{"code": "ANA", "schDep": "2026-09-19T15:49:00-07:00",
                              "dep": "2026-09-19T16:01:00-07:00", "platform": "2", "depCmnt": ""}],
                **extra}

    def row(self, run, code="ANA"):
        from zoneinfo import ZoneInfo
        return community("departures")._amtrak_row(run, code, ZoneInfo("America/Los_Angeles"))

    def test_a_departure_carries_its_route_destination_and_delay(self):
        row = self.row(self.run_for())
        self.assertEqual(row["kind"], "SURF")            # the timetable's name for the route
        self.assertEqual(row["number"], "580")
        self.assertEqual(row["destination"], "San Diego")  # not "San Diego Santa Fe Depot"
        self.assertEqual(row["delay"], 12)
        self.assertEqual(row["track"], "2")
        self.assertEqual(row["time"].strftime("%H:%M"), "15:49")

    def test_a_train_that_ends_here_is_an_arrival_and_not_shown(self):
        self.assertIsNone(self.row(self.run_for(destCode="ANA")))

    def test_a_stop_with_no_departure_time_is_not_a_departure(self):
        run = self.run_for()
        run["stations"][0] = {"code": "ANA", "schArr": "2026-09-19T15:48:00-07:00"}
        self.assertIsNone(self.row(run))

    def test_a_train_that_does_not_call_here_is_skipped(self):
        self.assertIsNone(self.row(self.run_for(), code="LAX"))

    def test_a_cancelled_stop_is_marked(self):
        run = self.run_for()
        run["stations"][0]["depCmnt"] = "Cancelled"
        self.assertTrue(self.row(run)["cancelled"])

    def test_an_unlisted_route_still_gets_a_badge(self):
        row = self.row(self.run_for(routeName="Borealis Extra"))
        self.assertTrue(row["kind"])
        self.assertLessEqual(len(row["kind"]), 5)


class BartTests(unittest.TestCase):
    """BART counts in minutes from now; the board works in clock times."""

    PAYLOAD = {"root": {"station": [{"abbr": "EMBR", "etd": [
        {"destination": "Antioch", "estimate": [
            {"minutes": "Leaving", "platform": "2", "color": "YELLOW", "hexcolor": "#ffff33", "delay": "274"},
            {"minutes": "17", "platform": "2", "color": "YELLOW", "hexcolor": "#ffff33", "delay": "0"}]},
        {"destination": "Millbrae", "estimate": [
            {"minutes": "8", "platform": "1", "color": "RED", "hexcolor": "#ff0000", "delay": "0",
             "cancelflag": "1"},
            {"minutes": "", "platform": "1", "color": "RED", "hexcolor": "#ff0000", "delay": "0"}]}]}]}}

    def bart_rows(self):
        import asyncio
        from datetime import datetime
        from zoneinfo import ZoneInfo
        departures = community("departures")
        zone = ZoneInfo("America/Los_Angeles")
        when = datetime(2026, 9, 19, 19, 0, tzinfo=zone)

        class Reply:
            status = 200
            def raise_for_status(self): pass
            async def json(self, content_type=None): return BartTests.PAYLOAD
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False

        class Session:
            def get(self, *args, **kwargs): return Reply()

        return asyncio.run(departures.bart(Session(), "EMBR", when, zone))

    def test_minutes_from_now_become_departure_times(self):
        rows = self.bart_rows()
        times = [row["time"].strftime("%H:%M") for row in rows]
        self.assertEqual(times[:3], ["19:00", "19:17", "19:08"])   # "Leaving" is now

    def test_each_line_keeps_its_own_colour_and_platform(self):
        rows = self.bart_rows()
        self.assertEqual(rows[0]["kind"], "YEL")
        self.assertEqual(rows[0]["color"], "ffff33")
        self.assertEqual(rows[0]["track"], "2")
        self.assertEqual(rows[0]["delay"], 5)        # BART counts delay in seconds
        self.assertTrue(rows[2]["cancelled"])

    def test_a_train_with_no_estimate_is_left_off(self):
        self.assertEqual(len(self.bart_rows()), 3)   # four estimates, one without minutes

    def test_a_pale_line_colour_gets_dark_letters(self):
        """Nothing reads white on BART's yellow."""
        from PIL import Image
        departures = community("departures")
        frame = Image.new("RGB", (128, 9))
        departures._badge(frame, "YEL", 0, 1, "bart", "ffff33")
        ink = {frame.load()[x, y] for x in range(2, 20) for y in range(1, 8)}
        self.assertIn((255, 255, 51), ink)                  # the line's real colour, undimmed
        self.assertTrue(any(sum(c) < 120 for c in ink))     # and dark letters on it


class MetrolinkTests(unittest.TestCase):
    """Metrolink answers for every station at once, in milliseconds since the epoch."""

    ROWS = [
        {"PlatformName": "ARTIC", "TrainDesignation": "M1860", "RouteCode": "IEOC LINE",
         "TrainDestination": "San Bernardino - Downtown", "TrainMovementTime": "/Date(1789876140000)/",
         "CalcTrainMovementTime": "/Date(1789876440000)/", "CalculatedStatus": "ON TIME",
         "FormattedTrackDesignation": "Track 1"},
        {"PlatformName": "ARTIC", "TrainDesignation": "A591S", "RouteCode": "PAC SURF",
         "TrainDestination": "LA Union Station", "TrainMovementTime": "/Date(1789877580000)/",
         "CalcTrainMovementTime": "/Date(0)/", "CalculatedStatus": "CANCELLED",
         "FormattedTrackDesignation": "Track 2"},
        {"PlatformName": "FULLERTON", "TrainDesignation": "M1754", "RouteCode": "91/PV Line",
         "TrainDestination": "South Perris", "TrainMovementTime": "/Date(1789876140000)/",
         "CalcTrainMovementTime": "/Date(1789876140000)/", "CalculatedStatus": "ON TIME",
         "FormattedTrackDesignation": "Track 3"},
    ]

    def board(self, platform):
        import asyncio
        from datetime import datetime
        from zoneinfo import ZoneInfo
        departures = community("departures")
        departures._metrolink_cache.update(at=0.0, rows=None)
        zone = ZoneInfo("America/Los_Angeles")

        class Reply:
            def raise_for_status(self): pass
            async def json(self, content_type=None): return MetrolinkTests.ROWS
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False

        class Session:
            def get(self, *args, **kwargs): return Reply()

        return asyncio.run(departures.metrolink(Session(), platform, datetime.now(zone), zone))

    def test_only_the_trains_calling_at_this_platform(self):
        self.assertEqual([row["number"] for row in self.board("FULLERTON")], ["M1754"])

    def test_the_line_keeps_its_own_name_and_the_track_loses_the_word(self):
        row = self.board("ARTIC")[0]
        self.assertEqual(row["kind"], "IEOC")
        self.assertEqual(row["track"], "1")
        self.assertEqual(row["delay"], 5)
        self.assertEqual(row["destination"], "San Bernardino")   # not "- Downtown" as well

    def test_a_placeholder_time_is_not_a_train_twenty_seven_years_late(self):
        cancelled = self.board("ARTIC")[1]
        self.assertEqual(cancelled["delay"], 0)
        self.assertTrue(cancelled["cancelled"])

    def test_a_train_finishing_its_run_here_is_not_a_departure(self):
        self.assertEqual([row["number"] for row in self.board("LAUS")], [])


class SurfTideTests(unittest.TestCase):
    """The sea sits where the tide puts it: 0 at dead low, 1 at dead high."""

    def level(self, high, turn, then, now="2026-09-19 18:00"):
        surf = community("surf")
        return surf.Report._level({"high": high, "time": turn, "then": then},
                                  datetime.strptime(now, "%Y-%m-%d %H:%M"))

    def test_the_water_is_nearly_in_an_hour_before_high(self):
        self.assertGreater(self.level(True, "2026-09-19 19:00", "2026-09-20 01:12"), .8)

    def test_the_water_is_nearly_out_an_hour_before_low(self):
        self.assertLess(self.level(False, "2026-09-19 19:00", "2026-09-20 01:12"), .2)

    def test_just_past_low_the_water_is_still_down(self):
        self.assertLess(self.level(True, "2026-09-20 00:00", "2026-09-20 06:12"), .1)

    def test_one_turn_of_the_tide_is_not_enough_to_say(self):
        surf = community("surf")
        self.assertIsNone(surf.Report._level({"high": True, "time": "2026-09-19 19:00", "then": None}))
        self.assertIsNone(surf.Report._level(None))

    def test_nonsense_times_do_not_take_the_screen_down(self):
        surf = community("surf")
        self.assertIsNone(surf.Report._level({"high": True, "time": "later", "then": "much later"}))
        # A second turn before the first would divide by a swing of nothing.
        self.assertIsNone(self.level(True, "2026-09-19 19:00", "2026-09-19 19:00"))


class NowPlayingTests(unittest.TestCase):
    def test_synced_lyrics_follow_the_song(self):
        now_playing = community("now_playing")
        lines = now_playing.synced_lines("[00:37.66]Waiting in a car\n[00:42.39]Waiting for a ride\n[01:10.00]")
        self.assertEqual(lines[0], (37.66, "Waiting in a car"))
        self.assertIsNone(now_playing.current_line(lines, 20))
        self.assertEqual(now_playing.current_line(lines, 40)[0], "Waiting in a car")
        self.assertEqual(now_playing.current_line(lines, 45)[0], "Waiting for a ride")
        self.assertIsNone(now_playing.current_line(lines, 55))   # instrumental: the analyser's turn
