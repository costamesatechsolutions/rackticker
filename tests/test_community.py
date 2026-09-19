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


class NowPlayingTests(unittest.TestCase):
    def test_synced_lyrics_follow_the_song(self):
        now_playing = community("now_playing")
        lines = now_playing.synced_lines("[00:37.66]Waiting in a car\n[00:42.39]Waiting for a ride\n[01:10.00]")
        self.assertEqual(lines[0], (37.66, "Waiting in a car"))
        self.assertIsNone(now_playing.current_line(lines, 20))
        self.assertEqual(now_playing.current_line(lines, 40)[0], "Waiting in a car")
        self.assertEqual(now_playing.current_line(lines, 45)[0], "Waiting for a ride")
        self.assertIsNone(now_playing.current_line(lines, 55))   # instrumental: the analyser's turn
