"""Plugin platform: folder installs, sandboxed rendering, live enable/disable, removal."""
import asyncio
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import unittest.mock
import zipfile

from app.core import installer as installer_module
from app.core.installer import InstallError, Installer, local_tree_id, parse_github, safe_extract
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


def blob(data):
    import hashlib
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGitHub:
    """GitHub's tree listing and raw files for folders on disk, counting API requests."""
    COMMIT = "c" * 40

    def __init__(self, folders, damage=None, limited=False):
        self.files, self.tree = {}, []
        for prefix, folder in folders.items():
            parent = prefix.rsplit("/", 1)[0] if "/" in prefix else None
            if parent and not any(item["path"] == parent for item in self.tree):
                self.tree.append({"path": parent, "type": "tree", "sha": "d" * 40, "mode": "040000"})
            self.tree.append({"path": prefix, "type": "tree", "sha": local_tree_id(folder), "mode": "040000"})
            for path in sorted(folder.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    name = f"{prefix}/{path.relative_to(folder)}"
                    self.files[name] = path.read_bytes()
                    self.tree.append({"path": name, "type": "blob", "sha": blob(self.files[name]),
                                      "size": len(self.files[name]), "mode": "100644"})
        self.damage, self.limited, self.api_calls, self.fetched = damage, limited, 0, []
        installer_module._trees.clear()

    def get(self, url, params=None, headers=None):
        if url.startswith("https://api.github.com/"):
            self.api_calls += 1
            if self.limited:
                return FakeResponse(403, {}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 900)})
            return FakeResponse(200, {"sha": self.COMMIT, "tree": self.tree, "truncated": False})
        path = url.split(f"/{self.COMMIT}/", 1)[1]
        self.fetched.append(path)
        data = self.files[path]
        return FakeResponse(200, data + b"!" if self.damage and path.endswith(self.damage) else data)


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(self.status)

    async def json(self): return self.body

    @property
    def content(self):
        body = self.body

        class Content:
            async def iter_chunked(self, size):
                yield body
        return Content()


class InstallerTests(unittest.IsolatedAsyncioTestCase):
    def test_github_links(self):
        self.assertEqual(parse_github("https://github.com/a/b"), ("a", "b", None, ""))
        self.assertEqual(parse_github("https://github.com/a/b/tree/main/community/x"), ("a", "b", "main", "community/x"))
        with self.assertRaises(InstallError):
            parse_github("https://example.com/a/b")

    async def test_installs_one_folder_of_a_shared_repository_with_one_api_request(self):
        github = FakeGitHub({"plugins/counter": FIXTURE, "plugins/other": FIXTURE})
        with tempfile.TemporaryDirectory() as directory:
            installer = Installer(Path(directory) / "plugins")
            manifest = await installer.from_github("https://github.com/o/r/tree/main/plugins/counter", github)
            self.assertEqual(manifest.id, "counter")
            self.assertEqual(github.api_calls, 1)
            self.assertNotIn("plugins/other/plugin.json", github.fetched)  # only this plugin's files
            source = manifest.source()
            self.assertEqual(source["tree"], local_tree_id(FIXTURE))
            self.assertEqual(local_tree_id(manifest.path), source["tree"])  # untouched on disk
            await installer.from_github("https://github.com/o/r/tree/main/plugins/counter", github)
            self.assertEqual(github.api_calls, 1)  # the listing is kept and shared

    async def test_a_damaged_download_is_refused(self):
        github = FakeGitHub({"plugins/counter": FIXTURE}, damage="plugin.json")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(InstallError):
            await Installer(Path(directory) / "plugins").from_github("https://github.com/o/r/tree/main/plugins/counter", github)

    async def test_githubs_hourly_limit_is_said_plainly(self):
        github = FakeGitHub({"plugins/counter": FIXTURE}, limited=True)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(InstallError) as caught:
            await Installer(Path(directory) / "plugins").from_github("https://github.com/o/r/tree/main/plugins/counter", github)
        self.assertIn("60 times an hour", str(caught.exception))
        asked = github.api_calls
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(InstallError):
            await Installer(Path(directory) / "plugins").from_github("https://github.com/o/r/tree/main/plugins/counter", github)
        self.assertEqual(github.api_calls, asked)  # not asked again until GitHub's reset
        installer_module._limited.update(until=0.0, message="")

    async def test_a_repository_of_several_plugins_needs_a_folder(self):
        github = FakeGitHub({"plugins/counter": FIXTURE, "plugins/other": FIXTURE})
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(InstallError) as caught:
            await Installer(Path(directory) / "plugins").from_github("https://github.com/o/r/tree/main/plugins", github)
        self.assertIn("several plugins", str(caught.exception))

    async def test_an_update_check_asks_github_once_per_repository(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            def installed(name, tree=None):
                folder = Path(directory) / name
                shutil.copytree(FIXTURE, folder, ignore=shutil.ignore_patterns("__pycache__"))
                source = {"kind": "github", "owner": "o", "repo": "r", "ref": "main", "folder": f"plugins/{name}",
                          "url": f"https://github.com/o/r/tree/main/plugins/{name}"}
                if tree:
                    source["tree"] = tree
                (folder / ".source.json").write_text(json.dumps(source))
                return SimpleNamespace(path=folder, source=lambda: json.loads((folder / ".source.json").read_text()))

            current = local_tree_id(FIXTURE)
            manager = SimpleNamespace(installed={
                "same": installed("same", current),     # fingerprint recorded, unchanged
                "older": installed("older"),            # installed before fingerprints, unchanged on disk
                "changed": installed("changed", current),
            })
            listing = {"sha": "c" * 40, "entries": {
                "plugins/same": {"type": "tree", "sha": current}, "plugins/older": {"type": "tree", "sha": current},
                "plugins/changed": {"type": "tree", "sha": "e" * 40}}}
            asked = []

            async def tree(session, owner, repo, ref, fresh=False):
                asked.append((owner, repo, ref))
                return listing

            plugins_api._updates.update(at=0.0, data=None)
            with unittest.mock.patch.object(plugins_api, "repo_tree", tree):
                result = await plugins_api.known_updates(manager, fresh=True)
            self.assertEqual(asked, [("o", "r", "main")])
            self.assertEqual({name: row["update"] for name, row in result.items()},
                             {"same": False, "older": False, "changed": True})
            self.assertEqual(manager.installed["older"].source()["tree"], current)  # remembered from now on
            plugins_api._updates.update(at=0.0, data=None)

    def test_a_plugin_says_where_it_is_listed(self):
        from app.core.manifest import read_manifest
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "counter"
            shutil.copytree(FIXTURE, folder, ignore=shutil.ignore_patterns("__pycache__"))
            raw = json.loads((folder / "plugin.json").read_text())
            for given, listed in (("space", "space"), ("nonsense", None), (None, None)):
                (folder / "plugin.json").write_text(json.dumps({**raw, "category": given}))
                self.assertEqual(read_manifest(folder).summary()["category"], listed)
            self.assertIsNone(read_manifest(folder).preview())
            (folder / "preview.gif").write_bytes(b"GIF89a")
            self.assertEqual(read_manifest(folder).preview().name, "preview.gif")

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

    async def test_the_store_shows_previews_and_groups(self):
        from aiohttp.test_utils import TestClient, TestServer
        index = {"plugins": [{"id": "surf", "name": "Surf", "category": "around",
                              "url": "https://github.com/o/r/tree/main/plugins/surf"}]}

        async def fake_index():
            return index

        async with TestClient(TestServer(self.app)) as client:
            with unittest.mock.patch.object(plugins_api, "community_index", fake_index):
                body = await (await client.get("/api/plugins/community")).json()
            self.assertEqual(body["plugins"][0]["preview"],
                             "https://raw.githubusercontent.com/o/r/main/plugins/surf/preview.gif")
            self.assertIn("around", body["categories"])
            shipped = await client.get("/api/plugins/weather/preview")  # a bundled plugin's own animation
            self.assertEqual(shipped.status, 200)
            self.assertEqual(shipped.headers["Content-Type"], "image/gif")
            self.assertEqual((await client.get("/api/plugins/nothing/preview")).status, 404)

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


class QuietProcess:
    """Just enough of a subprocess for the watchdog to look at."""
    returncode = None
    pid = 0


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    """A hang restarts every plugin in the shared process, so it takes real silence to declare one."""

    def setUp(self):
        from app.core import sandbox
        self.sandbox = sandbox
        self.process = sandbox.PluginProcess(tempfile.gettempdir())
        self.killed = []
        self.process.process = QuietProcess()
        self.process.kill = lambda host, reason: self.killed.append((host.name, reason))
        self.host = type("Host", (), {"name": "slow", "pending_at": time.monotonic() - 60})()
        self.process.hosts = {"slow": self.host}
        for name, value in (("HANG_SECONDS", .3), ("HESITATE_SECONDS", .4)):
            patcher = unittest.mock.patch.object(sandbox, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("resident_mb",):
            patch = unittest.mock.patch.object(sandbox, name, lambda pid: None)
            patch.start()
            self.addCleanup(patch.stop)

    async def test_a_process_that_is_answering_others_is_not_hung(self):
        self.process.heard = time.monotonic()
        self.assertFalse(await self.process.inspect(self.process.process))
        self.assertEqual(self.killed, [])
        self.assertIsNone(self.host.pending_at, "the lost request was not asked for again")

    async def test_a_silent_process_with_a_request_open_is_stopped(self):
        self.process.heard = time.monotonic() - 60
        self.assertTrue(await self.process.inspect(self.process.process))
        self.assertEqual(self.killed, [("slow", "stopped responding")])

    async def test_replies_that_arrive_while_the_watchdog_hesitates_save_the_process(self):
        self.process.heard = time.monotonic() - 60

        async def late_reply():
            await asyncio.sleep(.2)         # the display was stalled; the reader catches up
            self.process.heard = time.monotonic()
        asyncio.ensure_future(late_reply())
        self.assertFalse(await self.process.inspect(self.process.process))
        self.assertEqual(self.killed, [])


class SmoothCrawlTests(unittest.IsolatedAsyncioTestCase):
    """An installed plugin's crawl reaches the panel one pixel per frame, with no black frame between screens."""

    async def asyncSetUp(self):
        import os
        os.environ["RACKTICKER_SANDBOX"] = "shared"
        self.addCleanup(os.environ.pop, "RACKTICKER_SANDBOX", None)
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        await Installer(root / "plugins").from_folder(Path(__file__).parent / "fixtures" / "crawler")
        self.app = create_app(root / "config.json")
        self.runtime, self.store = self.app[RUNTIME], self.app[STORE]
        self.manager = self.app[plugins_api.MANAGER]
        await self.runtime.start()
        await plugins_api.enable(self.runtime, self.store, self.manager, "crawler")
        self.host = self.runtime.sandboxes["crawler"]
        deadline = time.monotonic() + 20
        while self.host.state != "running" and time.monotonic() < deadline:
            await asyncio.sleep(.05)
        self.runtime.config["display"]["transition"] = "cut"

    async def asyncTearDown(self):
        await self.runtime.close()
        self.directory.cleanup()

    async def watch(self, seconds):
        """Every distinct picture the panel is sent for a while: (pixel x of the crawler or None, is black)."""
        seen, last, end = [], -1, time.monotonic() + seconds
        while time.monotonic() < end:
            if self.runtime.frame_count != last:
                last = self.runtime.frame_count
                frame = self.runtime.frame
                lit = [x for x in range(128) if frame.getpixel((x, 5)) != (0, 0, 0)]
                seen.append((lit[0] if lit else None, frame.getbbox() is None))
            await asyncio.sleep(.002)
        return seen

    async def test_the_crawl_advances_one_pixel_a_frame(self):
        self.runtime.preview("crawler")
        await asyncio.sleep(.5)
        seen = [x for x, _ in await self.watch(4) if x is not None]
        steps = [(b - a) % 128 for a, b in zip(seen, seen[1:])]
        self.assertGreater(len(steps), 80)
        even = sum(step == 1 for step in steps) / len(steps)
        self.assertGreater(even, .95, f"the crawl hitched: {sorted(set(steps))}")

    async def test_arriving_at_an_installed_screen_never_flashes_black(self):
        self.runtime.preview("clock")
        await asyncio.sleep(.3)
        self.runtime.preview("crawler")
        frames = await self.watch(1.2)
        self.assertTrue(frames)
        self.assertFalse(any(black for _, black in frames), "a blank frame was shown between screens")


@unittest.skipUnless(hasattr(__import__("os"), "killpg"), "process groups are POSIX")
class ProcessTreeTests(unittest.IsolatedAsyncioTestCase):
    async def test_killing_a_plugin_process_takes_what_it_started_with_it(self):
        import os
        from app.core import sandbox
        # Stands in for a plugin that shelled out to ffmpeg: the grandchild must not outlive it.
        process = await asyncio.create_subprocess_exec(
            "sh", "-c", "sleep 300 & echo $!; wait", stdout=asyncio.subprocess.PIPE, **sandbox.GROUP)
        grandchild = int(await process.stdout.readline())
        sandbox.kill_tree(process)
        await process.wait()
        for _ in range(50):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(.02)
        self.fail("the process the plugin started was left running")


class SandboxHoldTests(unittest.TestCase):
    """An installed plugin's hold() means what it does in-process: finish the item that
    was showing when the dwell ran out, not the item that was showing on the first frame."""

    def test_hold_is_asked_only_once_the_display_wants_to_move_on(self):
        from PIL import Image
        from app.core.story import Storyboard
        from app.sandbox_child import Host
        from rackticker import Module, Plugin

        class Cards(Module):
            name = "cards"

            def __init__(self):
                self.board = Storyboard()

            def _card(self, context):
                build = lambda _visit: [(index, 5.0) for index in range(4)]
                self.board.sync(context.animation_time, build, context.scene)
                return self.board.current(context.animation_time, build)

            def hold(self, context):
                return bool(self._card(context)) and self.board.hold()

            def render(self, context):
                self._card(context)
                return Image.new("RGB", (128, 32))

        sent = []
        channel = unittest.mock.Mock(send=lambda header, payload=b"": sent.append(header))
        host = Host(Plugin("cards", "Cards", module=Cards), channel)
        for t in range(0, 12):      # the first two cards and into the third: the dwell hasn't run out
            host.render({"op": "render", "t": float(t), "scene": 1})
        self.assertFalse(any(header.get("asked") for header in sent))
        host.render({"op": "render", "t": 12.0, "scene": 1, "hold": True})
        self.assertTrue(sent[-1]["asked"] and sent[-1]["hold"], "card 3 is on screen and must finish")
        host.render({"op": "render", "t": 14.9, "scene": 1})
        self.assertTrue(sent[-1]["hold"])
        host.render({"op": "render", "t": 15.1, "scene": 1})
        self.assertFalse(sent[-1]["hold"], "card 3 is done; the playlist may move on")

    def test_the_display_waits_for_an_answer_for_this_visit(self):
        from app.core.sandbox import SandboxHost
        host = SandboxHost.__new__(SandboxHost)
        host.state, host.hold_scene, host.hold_answer = "running", None, (3, False)
        self.assertTrue(host.ask_hold(4))           # no answer for this visit yet
        self.assertEqual(host.hold_scene, 4)
        host.hold_answer = (4, False)
        self.assertFalse(host.ask_hold(4))
        host.state = "restarting"
        self.assertFalse(host.ask_hold(4))
