"""Plugin platform: folder installs, sandboxed rendering, live enable/disable, removal."""
import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

from app.core.installer import InstallError, Installer, parse_github, safe_extract
from app.core.plugin_manager import PluginManager
from app.web.server import RUNTIME, STORE, create_app
from app.web import plugins_api

FIXTURE = Path(__file__).parent / "fixtures" / "counter"


def zipped(folder, prefix="counter-main/"):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for path in folder.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, prefix + str(path.relative_to(folder)))
    return out.getvalue()


class InstallerTests(unittest.IsolatedAsyncioTestCase):
    def test_github_links(self):
        self.assertEqual(parse_github("https://github.com/a/b"), ("a", "b", None, ""))
        self.assertEqual(parse_github("https://github.com/a/b/tree/main/community/x"), ("a", "b", "main", "community/x"))
        with self.assertRaises(InstallError):
            parse_github("https://example.com/a/b")

    def test_archives_cannot_escape(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("../evil.py", "x")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(InstallError):
            safe_extract(out.getvalue(), directory)

    async def test_zip_install_describes_and_refuses_reserved_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Installer(Path(directory) / "plugins", reserved={"clock"})
            manifest = await installer.from_zip(zipped(FIXTURE), {"kind": "test"})
            self.assertEqual(manifest.id, "counter")
            self.assertEqual(manifest.described()["defaults"], {"label": "COUNT", "crash": False})
            self.assertEqual(manifest.source()["kind"], "test")
            with self.assertRaises(InstallError):
                await Installer(Path(directory) / "p2", reserved={"counter"}).from_zip(zipped(FIXTURE), {})


class SandboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        await Installer(root / "plugins").from_folder(FIXTURE)
        self.path = root / "config.json"
        self.app = create_app(self.path)
        self.runtime, self.store = self.app[RUNTIME], self.app[STORE]
        self.manager = self.app[plugins_api.MANAGER]
        await self.runtime.start()

    async def asyncTearDown(self):
        await self.runtime.close()
        self.directory.cleanup()

    async def until(self, check, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if check():
                return True
            await asyncio.sleep(.05)
        return False

    async def test_enable_render_crash_restart_disable_remove(self):
        runtime = self.runtime
        self.assertIn("counter", self.manager.installed)
        self.assertNotIn("counter", runtime.modules)  # installed, but not switched on yet
        await plugins_api.enable(runtime, self.store, self.manager, "counter")
        self.assertEqual(json.loads(self.path.read_text())["enabled_plugins"], ["counter"])
        self.assertTrue(any(entry["module"] == "counter" for entry in runtime.config["playlist"]))
        host = runtime.sandboxes["counter"]
        self.assertTrue(await self.until(lambda: host.state == "running" and host.available))
        runtime.preview("counter")
        module = runtime.modules["counter"]
        # The running renderer asks the process for frames; one arrives for this scene.
        self.assertTrue(await self.until(lambda: host.frame is not None
                                         and host.frame_scene == runtime.context().scene))
        self.assertIsNotNone(module.render(runtime.context()).getbbox())
        # Settings reach the process without a restart.
        raw = json.loads(json.dumps(runtime.config))
        raw["plugins"]["counter"]["crash"] = True
        runtime.apply_config(self.store.save(raw))
        self.assertTrue(await self.until(lambda: host.state == "restarting", 12))
        self.assertIn("code 3", host.error)
        raw["plugins"]["counter"]["crash"] = False
        runtime.apply_config(self.store.save(raw))
        self.assertTrue(await self.until(lambda: host.state == "running", 15))
        await plugins_api.disable(runtime, self.store, "counter")
        self.assertNotIn("counter", runtime.modules)
        self.assertIsNone(host.process.process)
        await plugins_api.enable(runtime, self.store, self.manager, "counter")
        await plugins_api.disable(runtime, self.store, "counter", forget=True)
        plugins_api.installer(runtime, self.manager).remove("counter")
        self.manager.rescan()
        self.assertNotIn("counter", self.manager.installed)
        self.assertNotIn("counter", runtime.config["plugins"])


class SharedSandboxTests(SandboxTests):
    """The same life cycle with installed plugins sharing one process (small boards)."""

    async def asyncSetUp(self):
        import os
        os.environ["RACKTICKER_SANDBOX"] = "shared"
        self.addCleanup(os.environ.pop, "RACKTICKER_SANDBOX", None)
        await super().asyncSetUp()

    async def test_two_plugins_share_one_process(self):
        import shutil
        second = Path(self.directory.name) / "second"
        shutil.copytree(FIXTURE, second)
        manifest = json.loads((second / "plugin.json").read_text())
        manifest["id"] = "counter_two"
        (second / "plugin.json").write_text(json.dumps(manifest))
        code = (second / "plugin.py").read_text().replace('"counter"', '"counter_two"')
        (second / "plugin.py").write_text(code)
        await Installer(Path(self.directory.name) / "plugins").from_folder(second)
        self.manager.rescan()
        await plugins_api.enable(self.runtime, self.store, self.manager, "counter")
        await plugins_api.enable(self.runtime, self.store, self.manager, "counter_two")
        first, other = self.runtime.sandboxes["counter"], self.runtime.sandboxes["counter_two"]
        self.assertIs(first.process, other.process)
        self.assertTrue(await self.until(lambda: first.state == "running" and other.state == "running"))
        self.assertTrue(await self.until(lambda: first.available and other.available))


class SecretSettingTests(unittest.TestCase):
    def test_secret_plugin_settings_never_reach_the_page(self):
        from app.core.plugins import PluginRegistry
        from app.plugin_api import Plugin
        from app.core.config import validate_config
        from app.web.server import SECRET, public, secret_fields
        registry = PluginRegistry()
        from app.plugin_api import Module
        registry.register(Plugin("keyed", "Keyed", module=type("Keyed", (Module,), {"name": "keyed"}), defaults={"token": "", "city": ""}, ui={"token": {"type": "secret"}}))
        config = validate_config({"plugins": {"keyed": {"token": "abc123", "city": "Roma"}}}, registry)
        shown = public(config, registry)
        self.assertEqual(shown["plugins"]["keyed"]["token"], SECRET)
        self.assertEqual(shown["plugins"]["keyed"]["city"], "Roma")
        self.assertEqual(config["plugins"]["keyed"]["token"], "abc123")
        self.assertEqual(secret_fields(registry), [("keyed", "token")])


class RetiredScreenTests(unittest.TestCase):
    def test_old_configs_drop_the_removed_iracing_screen(self):
        from app.core.config import validate_config
        config = validate_config({"modules": {"iracing": {"enabled": True}},
                                  "playlist": [{"id": "iracing-1", "module": "iracing", "duration": 8},
                                               {"id": "clock-1", "module": "clock", "duration": 8}]})
        self.assertNotIn("iracing", config["modules"])
        self.assertEqual([entry["module"] for entry in config["playlist"]], ["clock"])


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_limited_waits_for_the_whole_body(self):
        from app.core.installer import read_limited

        class Slow:  # a body that arrives in pieces, as it does over Wi-Fi
            class content:
                @staticmethod
                async def iter_chunked(size):
                    for piece in (b"PK", b"\x03\x04", b"rest"):
                        yield piece
        self.assertEqual(await read_limited(Slow, 100), b"PK\x03\x04rest")
        self.assertEqual(await read_limited(Slow, 3), b"PK\x03")
