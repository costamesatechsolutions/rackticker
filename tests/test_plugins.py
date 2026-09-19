import asyncio
import copy
from importlib import util
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from rackticker import Plugin, Module, Provider, FrameSink, Snapshot, new_frame
from app.core.config import ConfigError, ConfigStore, validate_config
from app.core.plugins import PluginRegistry, discover_plugins
from app.core.runtime import Runtime
from app.core.source import source_zip
from app.outputs.browser import BrowserSink


class Example(Module):
    name = "example"

    def render(self, context):
        frame = new_frame()
        frame.putpixel((0, 0), (255, 100, 0))
        return frame


def registry_for(*plugins):
    registry = PluginRegistry()
    for plugin in plugins:
        registry.register(plugin)
    return registry


class PluginTests(unittest.TestCase):
    def test_only_explicitly_enabled_entry_points_execute(self):
        good, inactive, broken = Mock(), Mock(), Mock()
        good.name, inactive.name, broken.name = "example", "untrusted", "broken"
        good.load.return_value = Plugin("example", "Example", module=Example)
        broken.load.side_effect = ImportError("missing dependency")
        with patch("app.core.plugins.metadata.entry_points", return_value=[good, inactive, broken]):
            registry = discover_plugins(["example", "broken", "missing"])
        inactive.load.assert_not_called()
        self.assertIn("example", registry.plugins)
        self.assertEqual(registry.status["broken"]["state"], "failed")
        self.assertEqual(registry.status["missing"]["state"], "missing")

    def test_duplicate_discovery_names_are_rejected_before_import(self):
        first, second = Mock(), Mock()
        first.name = second.name = "example"
        with patch("app.core.plugins.metadata.entry_points", return_value=[first, second]):
            registry = discover_plugins(["example"])
        first.load.assert_not_called()
        second.load.assert_not_called()
        self.assertEqual(registry.status["example"]["state"], "failed")

    def test_contract_version_collisions_and_settings_are_validated(self):
        for plugin in (Plugin("clock", "Collision", module=Example),
                       Plugin("example", "Wrong API", api_version=2, module=Example),
                       Plugin("bad-name", "Invalid ID", module=Example),
                       Plugin("example", "Bad settings", module=Example, defaults={"nested": {}})):
            with self.assertRaises(ValueError): registry_for(plugin)
        registry = registry_for(Plugin("example", "Example", module=Example,
                                      defaults={"message": "hello", "rate": 5, "flash": False}))
        for settings in ({"rate": True}, {"rate": float("nan")}, {"message": 12}, {"unknown": 1}):
            with self.assertRaises(ConfigError):
                validate_config({"plugins": {"example": settings}}, registry)

    def test_removed_plugin_keeps_settings_and_playlist_across_restart(self):
        registry = registry_for(Plugin("example", "Example", module=Example, defaults={"message": "hello"}))
        with tempfile.TemporaryDirectory() as folder:
            store = ConfigStore(Path(folder) / "config.json", registry)
            config = store.load()
            config["plugins"]["example"]["message"] = "my text"
            config["playlist"].append({"id": "plugin-1", "module": "example"})
            store.save(config)
            without = ConfigStore(store.path).load()
            self.assertEqual(without["plugins"]["example"]["message"], "my text")
            self.assertEqual(without["playlist"][-1]["module"], "example")
            runtime = Runtime(without, BrowserSink())
            self.assertNotIn("plugin-1", runtime.eligible())
            self.assertTrue(runtime.eligible())
            restored = ConfigStore(store.path, registry).load()
            self.assertEqual(restored["plugins"]["example"]["message"], "my text")

    def test_user_removed_tix_entry_stays_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConfigStore(Path(folder) / "config.json")
            config = store.load()
            config["playlist"] = [e for e in config["playlist"] if e["module"] != "tixclock"]
            store.save(config)
            self.assertFalse(any(e["module"] == "tixclock" for e in store.load()["playlist"]))

    def test_source_offer_contains_rebuildable_core_and_no_private_config(self):
        with ZipFile(BytesIO(source_zip())) as archive:
            names = archive.namelist()
            for name in ("app/main.py", "rackticker/__init__.py", "LICENSE", "pyproject.toml"):
                self.assertIn("rackticker-source/" + name, names)
            self.assertIn("rackticker-source/plugins/local-adsb/rackticker_local_adsb.py", names)
            self.assertIn("rackticker-source/plugins/free-sports/rackticker_free_sports.py", names)
            self.assertIn("rackticker-source/plugins/f1-schedule/rackticker_f1.py", names)
            self.assertIn("rackticker-source/plugins/prediction-markets/rackticker_markets.py", names)
            self.assertIn("rackticker-source/plugins/finance/rackticker_finance.py", names)
            self.assertIn("rackticker-source/plugins/weather/rackticker_weather.py", names)
            self.assertIn("rackticker-source/plugins/news/rackticker_news.py", names)
            self.assertIn("rackticker-source/plugins/arcade/rackticker_arcade.py", names)
            self.assertIn("rackticker-source/plugins/ticker-wall/rackticker_ticker_wall.py", names)
            self.assertFalse(any("exports/" in n or n.endswith("config/config.json") or ".venv/" in n for n in names))
            self.assertIn(b"GNU AFFERO", archive.read("rackticker-source/LICENSE"))


class PluginRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sample_plugin_renders_and_current_settings_apply(self):
        path = Path(__file__).resolve().parents[1] / "examples/hello-plugin/rackticker_hello.py"
        spec = util.spec_from_file_location("hello_test", path)
        example = util.module_from_spec(spec)
        spec.loader.exec_module(example)
        registry = registry_for(example.plugin)
        config = validate_config({"display": {"transition": "cut"}}, registry)
        runtime = Runtime(config, BrowserSink(), registry)
        try:
            runtime.preview("hello")
            await runtime.step(.1, time.monotonic())
            changed = copy.deepcopy(config)
            changed["plugins"]["hello"]["message"] = "NEW MESSAGE"
            runtime.apply_config(validate_config(changed, registry))
            runtime.preview("hello")
            for _ in range(3):  # A new scene rebuilds the sign from current settings.
                await runtime.step(.5, time.monotonic())
            self.assertEqual(runtime.frame.size, (128, 32))
            self.assertEqual(runtime.frame.mode, "RGB")
            phrases = [lettering.text for lettering, _ in runtime.modules["hello"].board.items]
            self.assertEqual(phrases, ["NEW MESSAGE"])
        finally:
            await runtime.close()

    async def test_provider_replaces_mock_observes_settings_and_closes(self):
        class LiveProvider(Provider):
            def __init__(self, context): self.context, self.closed = context, False
            async def fetch(self): return Snapshot(self.context.settings["value"], source="example")
            async def close(self): self.closed = True
        registry = registry_for(Plugin("example", "Live data", provider=LiveProvider,
                                       provider_for="sports", defaults={"value": "live"}))
        config = validate_config({}, registry)
        runtime = Runtime(config, BrowserSink(), registry)
        await runtime.refresh_provider("sports")
        self.assertEqual(runtime.snapshots["sports"].data, "live")
        self.assertIsNone(runtime.state()["scenarios"]["sports"])
        with self.assertRaisesRegex(ValueError, "live data"):
            await runtime.scenario("sports", "goal")
        updated = copy.deepcopy(config)
        updated["plugins"]["example"]["value"] = "updated"
        runtime.apply_config(updated)
        await runtime.refresh_provider("sports")
        self.assertEqual(runtime.snapshots["sports"].data, "updated")
        await runtime.close()
        self.assertTrue(runtime.providers["sports"].closed)

    async def test_invalid_factory_does_not_replace_live_source_with_mock(self):
        registry = registry_for(Plugin("example", "Broken live provider", provider=lambda _: object(), provider_for="flight"))
        runtime = Runtime(validate_config({}, registry), BrowserSink(), registry)
        self.assertNotIn("flight", runtime.providers)
        self.assertTrue(runtime.snapshots["flight"].stale)
        self.assertEqual(registry.status["example"]["state"], "failed")
        await runtime.step(.1, time.monotonic())
        self.assertEqual(runtime.scheduler.current.module, "clock")
        await runtime.close()

    async def test_output_plugin_receives_same_frame_and_shutdown_isolated(self):
        class Capture(FrameSink):
            def __init__(self, context): self.frame, self.closed = None, False
            async def display(self, frame): self.frame = frame
            async def close(self): self.closed = True
        registry = registry_for(Plugin("capture", "Capture", output=Capture))
        runtime = Runtime(validate_config({"display": {"transition": "cut"}}, registry), BrowserSink(), registry)
        output = runtime.outputs[-1]
        runtime.preview("tixclock")
        await runtime.step(.1, time.monotonic())
        self.assertIs(output.frame, runtime.frame)
        self.assertEqual(output.brightness, 85)
        await runtime.close()
        self.assertTrue(output.closed)
