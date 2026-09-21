"""Installed plugins run in their own process (see app/sandbox_child.py).

The display asks the plugin's process for a frame and shows the newest one
it has, one frame late at most; it never waits. The process is restarted with
a backoff if it crashes, hangs or grows past its memory budget, and switched
off (with the reason shown in Settings) if it keeps failing.
"""
from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import os
from pathlib import Path
import struct
import sys
import time

from PIL import Image

from app.core.fonts import centered
from app.core.renderer import MUTED, new_frame
from app.modules.base import Module

log = logging.getLogger("sandbox")
APP_ROOT = Path(__file__).resolve().parents[2]
FRAME_BYTES = 128 * 32 * 3
HANG_SECONDS = 10.0         # nothing heard from the process for this long, with a request open, means it is stuck
HESITATE_SECONDS = 1.0      # before stopping a silent process, wait this long for replies the display has yet to read
LOOKAHEAD = 4               # frames drawn ahead of the one on show, so a late reply never shows as a hitch
QUEUE_LIMIT = 12            # frames kept per plugin; the oldest give way
SLOW_FRAME = .2             # a screen that redraws less often than this is static: no need to draw ahead
MEMORY_MB = 160             # resident memory budget per plugin process (a Pi 3 has 512 MB in all)
CRASH_WINDOW, CRASH_LIMIT = 600, 5
BACKOFF = (1, 2, 5, 10, 30, 60)


def plugin_env(data_dir):
    """A plain environment: no secrets or credentials inherited from the service."""
    keep = ("PATH", "TZ", "LANG", "LC_ALL", "SYSTEMROOT")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    # One malloc arena per thread is how a busy process on a small board ends up twice its
    # real size; two is plenty for a plugin process.
    env.update({"HOME": str(data_dir), "PYTHONPATH": str(APP_ROOT), "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "MALLOC_ARENA_MAX": "2"})
    return env


async def describe(folder, timeout=25):
    """Load a plugin in a throwaway process and return what it declares."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.sandbox_child", str(folder), "--describe",
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(folder), env=plugin_env(folder))
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise ValueError("The plugin took too long to load")
    if len(out) < 4:
        raise ValueError(_last_line(err) or "The plugin did not start")
    header = json.loads(out[4:4 + struct.unpack(">I", out[:4])[0]])
    if header.get("op") == "fatal":
        raise ValueError(header.get("error") or "The plugin failed to load")
    return header


def _last_line(data):
    lines = [line for line in data.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def total_memory_mb():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def shared_by_default():
    """Small boards (a Pi 3 has 512 MB) run installed plugins in one shared process;
    each extra process would cost about 30 MB. RACKTICKER_SANDBOX overrides."""
    choice = os.environ.get("RACKTICKER_SANDBOX", "").lower()
    if choice in ("shared", "separate"):
        return choice == "shared"
    memory = total_memory_mb()
    return memory is not None and memory < 1500


class PluginProcess:
    """One sandbox process hosting one plugin, or several on small machines.

    The display never talks to plugin code directly: it sends requests down this
    process's stdin and reads replies, each tagged with the plugin they are for."""

    def __init__(self, data_dir, label="plugins"):
        self.data_dir = Path(data_dir)
        self.label = label
        self.hosts = {}
        self.process = None
        self.tasks = []
        self.starting = None
        self.log_budget = (0.0, 0)
        self.culprit = None
        self.heard = time.monotonic()      # the last time the process said anything at all

    async def ensure(self):
        if self.process is not None and self.process.returncode is None:
            return
        if self.starting is None:
            self.starting = asyncio.ensure_future(self._spawn())
        try:
            await self.starting
        finally:
            self.starting = None

    async def _spawn(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.sandbox_child",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(self.data_dir), env=plugin_env(self.data_dir))
        self.process = process
        self.heard = time.monotonic()
        self.tasks = [asyncio.create_task(self._read(process)), asyncio.create_task(self._errors(process)),
                      asyncio.create_task(self._watch(process))]

    async def attach(self, host):
        self.hosts[host.name] = host
        await self.ensure()
        self.send({"op": "load", "folder": str(host.manifest.path)})

    async def detach(self, host):
        if self.hosts.get(host.name) is host:
            del self.hosts[host.name]
            self.send({"op": "unload", "plugin": host.name})
        if not self.hosts:
            await self.stop()

    async def stop(self):
        process, self.process = self.process, None
        for task in self.tasks:
            task.cancel()
        self.tasks = []
        if process and process.returncode is None:
            try:
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 2)
            except (asyncio.TimeoutError, OSError, ConnectionError):
                process.kill()
                await process.wait()

    def send(self, message):
        process = self.process
        if process is None or process.stdin is None or process.stdin.is_closing():
            return False
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            return True
        except (ConnectionError, RuntimeError):
            return False

    def kill(self, culprit, reason):
        """Stop the process because of one plugin; the others simply come back."""
        culprit.error = reason
        self.culprit = culprit.name
        if self.process is not None and self.process.returncode is None:
            self.process.kill()

    async def _read(self, process):
        reason = "exited"
        self.culprit = None
        try:
            while True:
                size = struct.unpack(">I", await process.stdout.readexactly(4))[0]
                if size > 65536:
                    reason = "sent a malformed message"
                    break
                header = json.loads(await process.stdout.readexactly(size))
                length = int(header.get("bytes") or 0)
                if not 0 <= length <= FRAME_BYTES:
                    reason = "sent a malformed message"
                    break
                payload = await process.stdout.readexactly(length) if length else b""
                self.heard = time.monotonic()
                host = self.hosts.get(header.get("plugin"))
                if host is None and header.get("folder"):
                    host = next((item for item in self.hosts.values()
                                 if str(item.manifest.path) == header["folder"]), None)
                if host is not None:
                    host._message(header, payload)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except (ValueError, struct.error):
            reason = "sent a malformed message"
        except asyncio.CancelledError:
            raise
        if process.returncode is None:
            process.kill()
        code = await process.wait()
        if process is not self.process:
            return  # stopped on purpose
        self.process = None
        if reason == "exited" and code:
            reason = f"exited with code {code}"
        for host in list(self.hosts.values()):
            if self.culprit is None or host.name == self.culprit:
                host._exited(host.error if self.culprit == host.name else reason)
            else:
                host._bounced()

    async def _errors(self, process):
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            window, count = self.log_budget
            now = time.monotonic()
            if now - window > 60:
                window, count = now, 0
            if count < 30 * max(1, len(self.hosts)):  # a chatty plugin cannot flood the service log
                log.info("[%s] %s", self.label, text[:500])
            self.log_budget = (window, count + 1)

    async def _watch(self, process):
        while process.returncode is None:
            await asyncio.sleep(2)
            if await self.inspect(process):
                return

    async def inspect(self, process):
        """One look at the process: True if it was stopped because it is stuck or too big."""
        for host in list(self.hosts.values()):
            waiting = host.pending_at is not None and time.monotonic() - host.pending_at > HANG_SECONDS
            if not waiting:
                continue
            if time.monotonic() - self.heard < HANG_SECONDS:
                # The process is answering others: this one lost a request (or the display
                # was busy), which is not a hang. Ask again rather than restart everything.
                host.pending_at = None
                continue
            # Silent for a long time. If the display itself was stalled the replies may
            # simply be waiting to be read: give the reader a moment before deciding.
            await asyncio.sleep(HESITATE_SECONDS)
            if time.monotonic() - self.heard >= HANG_SECONDS and host.pending_at is not None:
                self.kill(host, "stopped responding")
                return True
        rss = resident_mb(process.pid)
        budget = MEMORY_MB + 40 * max(0, len(self.hosts) - 1)
        if rss and rss > budget and self.hosts:
            # Blame the plugin that loaded last: it is the likeliest newcomer to leak.
            self.kill(list(self.hosts.values())[-1], f"used {rss:.0f} MB of memory (limit {budget})")
            return True
        return False


class SandboxHost:
    """One installed plugin as the display sees it: its latest frame and state."""

    def __init__(self, manifest, data_dir, runtime, process=None):
        self.manifest = manifest
        self.name = manifest.id
        self.data_dir = Path(data_dir)
        self.runtime = runtime
        self.process = process or PluginProcess(data_dir, manifest.id)
        self.shared = process is not None
        self.state, self.error = "stopped", None
        self.crashes = deque()
        self.frame = None
        self.frame_bytes = b""
        self.frame_scene = None
        self.frame_t = None
        self.queue = deque()            # (t, scene, pixels) drawn ahead, oldest first
        self.asked = (None, None)       # (scene, t) of the last frame requested
        self.received = (None, b"")     # (scene, pixels) of the last frame that arrived
        self.available = False
        self.hold = False
        self.interval = 0.0             # until the plugin says how often it changes, assume every frame
        self.pending_at = None
        self.render_ms = deque(maxlen=60)
        self.stopping = False

    # --- lifecycle -------------------------------------------------------------

    async def start(self):
        self.stopping = False
        self.state, self.pending_at = "starting", None
        self.queue.clear()
        self.asked = (None, None)
        self.received = (None, b"")
        await self.process.attach(self)
        self.configure()

    async def stop(self):
        self.stopping = True
        await self.process.detach(self)
        self.state = "stopped" if self.state != "failed" else "failed"

    async def restart(self):
        await self.stop()
        self.crashes.clear()
        self.state, self.error = "stopped", None
        await self.start()

    def _exited(self, reason):
        if self.stopping:
            return
        now = time.monotonic()
        self.crashes.append(now)
        while self.crashes and now - self.crashes[0] > CRASH_WINDOW:
            self.crashes.popleft()
        self.error = reason
        self.available = False
        self.pending_at = None
        if len(self.crashes) >= CRASH_LIMIT:
            self.state = "failed"
            self.runtime.record(self.name, f"plugin switched off after repeated failures: {reason}", "error")
            asyncio.ensure_future(self.process.detach(self))
            return
        delay = BACKOFF[min(len(self.crashes) - 1, len(BACKOFF) - 1)]
        self.state = "restarting"
        self.runtime.record(self.name, f"plugin stopped ({reason}); restarting in {delay}s", "warning")
        asyncio.get_running_loop().call_later(delay, lambda: asyncio.ensure_future(self._revive()))

    def _bounced(self):
        """The shared process went down because of another plugin: come straight back."""
        if self.stopping:
            return
        self.available, self.pending_at, self.state = False, None, "restarting"
        asyncio.get_running_loop().call_later(1, lambda: asyncio.ensure_future(self._revive()))

    async def _revive(self):
        if not self.stopping and self.state == "restarting":
            try:
                await self.start()
            except OSError as exc:
                self._exited(str(exc))

    # --- traffic -----------------------------------------------------------------

    def send(self, message):
        return self.process.send({**message, "plugin": self.name})

    def configure(self):
        config = self.runtime.config
        settings = self.runtime.plugin_settings(self.name) if self.name in config["plugins"] else {}
        self.send({"op": "configure", "settings": settings, "display": config["display"]})

    def request(self, context):
        """Ask for the frames the display will need next; never waits for them.

        A crawl moves a pixel a frame, so a reply that comes one frame late shows as
        a hitch. Animated screens are therefore drawn a few frames ahead and the
        display shows the one whose moment has come; static ones are asked for once."""
        if self.state != "running":
            return
        config = context.config
        period = config["simulator"]["animation_speed"] / config["display"]["fps"]
        now = context.animation_time
        scene, asked = self.asked
        ahead = LOOKAHEAD if self.interval <= SLOW_FRAME else 1
        if scene != context.scene or asked is None or asked < now - period * 8:
            first = now
            self.queue = deque(item for item in self.queue if item[1] == context.scene)
        else:
            first = asked + period
        last = now + ahead * period
        if self.interval > SLOW_FRAME:
            # A screen that rarely changes: one fresh frame each time it is due.
            if scene == context.scene and asked is not None and asked >= now:
                return
            first = last = now
        wall = context.now.timestamp()
        moment, sent = first, False
        while moment <= last + 1e-9:
            if self.send({"op": "render", "t": moment, "scene": context.scene,
                          "now": wall + (moment - now) / max(config["simulator"]["animation_speed"], 1e-6)}):
                self.pending_at = self.pending_at or time.monotonic()
                self.asked = (context.scene, moment)
                sent = True
            moment += period
        return sent

    def frame_for(self, context):
        """The picture for this moment: the newest drawn frame that is not from the future."""
        config = context.config
        period = config["simulator"]["animation_speed"] / config["display"]["fps"]
        limit = context.animation_time + period * .999
        chosen = None
        while self.queue and self.queue[0][1] != context.scene:
            self.queue.popleft()                    # a screen we have left
        while self.queue and self.queue[0][0] <= limit:
            chosen = self.queue.popleft()
        if chosen is not None:
            t, scene, pixels = chosen
            self.frame_t, self.frame_scene = t, scene
            if pixels != self.frame_bytes:
                self.frame_bytes = pixels
                self.frame = Image.frombytes("RGB", (128, 32), pixels)
        return self.frame if self.frame_scene == context.scene else None

    def has_frame(self, scene):
        return self.frame_scene == scene or any(item[1] == scene for item in self.queue)

    def _message(self, header, payload):
        op = header.get("op")
        if op == "describe":
            self.state, self.error = "running", None
            self.configure()
        elif op == "fatal":
            self.error = header.get("error") or "failed to load"
            self._exited(self.error)
        elif op == "frame":
            self.pending_at = None
            if header.get("error"):
                self.runtime.fail_module(self.name, header["error"])
                return
            if len(payload) != FRAME_BYTES:
                return
            self.available = bool(header.get("available"))
            self.hold = bool(header.get("hold"))
            self.interval = max(1 / 60, min(3600.0, float(header.get("interval") or 1)))
            self.render_ms.append(float(header.get("ms") or 0))
            self.queue.append((float(header.get("t") or 0.0), header.get("scene"), payload))
            while len(self.queue) > QUEUE_LIMIT:
                self.queue.popleft()
            # Only a changed picture marks the screen dirty; otherwise a static plugin
            # would be asked for frame after identical frame forever.
            changed = self.received != (header.get("scene"), payload)
            self.received = (header.get("scene"), payload)
            current = self.runtime.scheduler.current
            if changed and current and current.module == self.name:
                self.runtime.dirty = True
        elif op == "status":
            self.available = bool(header.get("available"))
        elif op == "event":
            duration = header.get("duration")
            duration = float(duration) if isinstance(duration, (int, float)) else 10.0
            self.runtime.plugin_event(self.name, max(1.0, min(duration, 120.0)))

    def status(self):
        timings = sorted(self.render_ms)
        process = self.process.process
        return {"state": self.state, "error": self.error, "sandboxed": True, "shared": self.shared,
                "render_ms": round(timings[len(timings) // 2], 1) if timings else None,
                "memory_mb": resident_mb(process.pid) if process else None}


def resident_mb(pid):
    try:
        pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return round(pages * os.sysconf("SC_PAGE_SIZE") / 1_048_576, 1)


class SandboxModule(Module):
    """Stands in for an installed plugin's screen inside the main process."""

    def __init__(self, host, event_priority=10):
        self.host = host
        self.name = host.name
        self.event_priority = event_priority

    def available(self, context):
        return self.host.state == "running" and self.host.available

    def ready(self, context):
        return self.host.has_frame(context.scene)

    def refresh_interval(self, context):
        self.host.request(context)
        return max(1 / context.config["display"]["fps"], min(self.host.interval, 1.0))

    def hold(self, context):
        return self.host.hold

    def render(self, context):
        self.host.request(context)
        frame = self.host.frame_for(context)
        if frame is None:
            # Nothing drawn for this visit yet: a quiet placeholder, never an old scene.
            frame = new_frame()
            if self.host.state != "running":
                centered(frame, self.host.manifest.name.upper()[:21], 8, MUTED)
                centered(frame, "STARTING" if self.host.state == "starting" else "UNAVAILABLE", 18, MUTED)
        return frame
