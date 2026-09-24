"""Nonblocking application orchestration; rendering is independent of the web UI."""
from __future__ import annotations

import asyncio
from collections import deque
import gc
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import logging
import sys
import threading
import time
import zipfile
import json
from pathlib import Path
import tempfile

try:
    import resource
except ImportError:         # Windows: no page-fault counts, everything else works
    resource = None

from app.core.plugins import PluginRegistry, BUILTINS
from app.plugin_api import PluginContext
from app.providers.base import Provider, retry_seconds
from app.outputs.base import FrameSink
from app.core import memory, offload
from app.core.fonts import centered
from app.core.models import Snapshot, Message, PriorityEvent, SystemStatus, utcnow
from app.core.playlist import from_config
from app.core.renderer import (new_frame, transition, transition_seconds, validate_frame,
                               AMBER, AUTO_TRANSITIONS, MUTED)
from app.core.sandbox import PluginProcess, SandboxHost, SandboxModule, shared_by_default
from app.core.scheduler import Scheduler
from app.modules.base import Module, RenderContext
from app.modules.clock import ClockModule
from app.modules.tixclock import TixClockModule
from app.modules.flights import FlightModule
from app.modules.sports import SportsModule
from app.modules.message import MessageModule
from app.modules.system_status import SystemStatusModule
from app.modules.test_pattern import TestPatternModule
from app.providers.mock_flights import MockFlightProvider
from app.providers.mock_sports import MockSportsProvider

log = logging.getLogger("renderer")
# Animation time is counted in integer units so hours of 1/30 s additions never
# drift; 1200 per second divides every common frame rate exactly.
ANIMATION_UNITS = 1200
# A frame taking longer than this to draw, or a gap this long between frames,
# is visible as a hitch in a 1 px/frame crawl; both are recorded for /api/state.
SLOW_RENDER_SECONDS = .025
STALL_SECONDS = .1
# Which screens are on offer is worth knowing a few times a second, not thirty.
ELIGIBLE_SECONDS = .25
TRIM_SECONDS = 45           # how often freed memory is handed back to the system
# A screen from an installed plugin is drawn by another process. When the playlist arrives
# at it, the previous screen stays up until its first picture is here (normally a frame or
# two), so the panel never shows a black frame between screens.
SCENE_GATE_SECONDS = .6
# One failed refresh (a slow reply from a Pi's Wi-Fi) is not news; data is called stale, and
# screens say so, only after this many in a row.
FAILURES_BEFORE_STALE = 3
# A provider's deadline. Feeds on a Pi's Wi-Fi, with the CPU shared by the panel's refresh and
# the ADS-B decoder, can take longer than the two seconds they take on a laptop.
PROVIDER_SECONDS = 9


NETWORK_STATE = Path("/run/rackticker-network/network.json")


def read_network():
    try:
        return json.loads(NETWORK_STATE.read_text())
    except (OSError, ValueError):
        return {}


UPDATE_STATUS = Path("/var/lib/rackticker/update/status.json")
THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")
NOTICE_SECONDS = 24 * 3600      # a problem is worth saying for a day, not forever


def read_temperature(fallback=25.6):
    try:
        return int(THERMAL.read_text().strip()) / 1000
    except (OSError, ValueError):
        return fallback


def software_notice(now=None):
    """A few words for the panel when the last update or repair went wrong.

    Only the panel is looked at by people who will never open the web page, so a
    failed update has to be visible there or it is invisible."""
    try:
        status = json.loads(UPDATE_STATUS.read_text())
    except (OSError, ValueError):
        return ""
    now = now if now is not None else time.time()
    if not status.get("error") or now - status.get("at", 0) > NOTICE_SECONDS:
        return ""
    return "UPDATE FAILED"


def local_address():
    """(hostname.local, LAN IPv4 or "") without sending anything: a UDP socket
    'connected' to a public address reveals the outgoing interface's address."""
    import socket
    host = f"{socket.gethostname().split('.')[0]}.local"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("1.1.1.1", 53))
            address = probe.getsockname()[0]
    except OSError:
        address = ""
    return host, "" if address.startswith("127.") else address


class Runtime:
    def __init__(self, config, sink, registry=None, manager=None):
        self.config = config
        self.registry = registry or PluginRegistry()
        self.sink = sink
        self.outputs = [sink]
        self.modules = {module.name: module for module in (
            ClockModule(), TixClockModule(), FlightModule(), SportsModule(), MessageModule(), SystemStatusModule(),
            TestPatternModule())}
        self.providers = {"flight": MockFlightProvider(), "sports": MockSportsProvider()}
        self.snapshots = {}
        self.provider_fault = False
        self.system_scenario = "normal"
        self.system = SystemStatus()
        self.message = self.config_message()
        self.scheduler = Scheduler(from_config(config))
        self.failed_until = {}
        self.failures = {}          # consecutive failed refreshes, per provider
        self.retry_at = {}          # provider -> monotonic time a failing provider is next asked
        self.frame = new_frame()
        self.frame_count = 0
        self.history = deque(maxlen=30)
        self.events = deque(maxlen=30)
        self.slow = deque(maxlen=40)
        self.refreshing = set()
        # Providers run on their own event loop in a thread. TLS handshakes and
        # socket reads then stay off the render loop (OpenSSL releases the GIL),
        # which measured as the largest source of dropped frames on a Pi.
        self.main_loop = None
        self.io_loop = None
        self.io_thread = None
        self.animation_clock = 0.0
        self.scene_started = 0.0
        self.scene_token = None
        self.scene_transition = "cut"
        self.clock_debt = 0.0
        self.animation_units = 0
        self.scene_units = 0
        self.previous = self.frame
        self.target = self.frame
        self.dirty = True
        self.render_due = 0.0
        self.auto_demo = False
        self.demo_index = 0
        self.demo_due = 0
        self.tasks = []
        self.provider_lock = asyncio.Lock()
        self.started_at = time.monotonic()
        self.splash_until = 0.0     # the boot splash: where to find the control page
        self.port = None
        self.network = {}           # what the network keeper reports (Wi-Fi setup mode)
        self.network_checked = 0.0
        self.splash_address = None
        self.last_state_at = 0.0
        self.last_history_at = 0.0
        self.wall_second = None
        self.wall_now = None
        self.power = True
        self.set_brightness(self.effective_brightness())
        self.manager = manager
        self.home_assistant = None
        self.plugin_process = None  # shared sandbox on small machines
        self.sandboxes = {}
        self._eligible = None
        self._eligible_at = 0.0
        self._eligible_config = None
        self.trimmed = time.monotonic()
        self.scene_gate = None      # when to stop waiting for the new screen's first picture
        self.gc_seconds = 0.0       # time spent in garbage collection since the last note
        self.gc_started = 0.0
        self.cpu_mark = time.process_time()
        self.fault_mark = self.page_faults()
        self.install_plugins()

    def install_plugins(self):
        for name in list(self.registry.plugins):
            self.install_plugin(name)

    def install_plugin(self, name):
        self._eligible = None
        plugin = self.registry.plugins[name]
        if name in self.registry.sandboxed:
            manifest = self.registry.sandboxed[name]
            root = self.manager.plugin_data if self.manager else Path(tempfile.gettempdir()) / "rackticker"
            if shared_by_default():
                if self.plugin_process is None:
                    self.plugin_process = PluginProcess(root / "shared", "plugins")
                host = SandboxHost(manifest, root / name, self, self.plugin_process)
            else:
                host = SandboxHost(manifest, root / name, self)
            priority = (manifest.described() or {}).get("event_priority", 10)
            self.sandboxes[name] = host
            self.modules[name] = SandboxModule(host, priority if isinstance(priority, int) else 10)
            self.registry.status[name] = {"state": "loaded", "error": None}
            return
        # Plugin events are automatic, so they wait until the current screen has been readable.
        context = PluginContext(name, lambda n=name: self.plugin_settings(n), self.plugin_event)
        try:
            # Factories must be side-effect free; acquire resources in async methods.
            module = plugin.module() if plugin.module else None
            provider = plugin.provider(context) if plugin.provider else None
            output = plugin.output(context) if plugin.output else None
            if plugin.module and (not isinstance(module, Module) or module.name != name):
                raise TypeError("Module must subclass Module and use the plugin's name")
            if plugin.provider and not isinstance(provider, Provider):
                raise TypeError("Provider factory must return a Provider")
            if plugin.output and not isinstance(output, FrameSink):
                raise TypeError("Output factory must return a FrameSink")
            if output:
                output.set_brightness(self.effective_brightness())
            if module:
                self.modules[name] = module
            if provider:
                self.providers[plugin.provider_for or name] = provider
            if output:
                self.outputs.append(output)
            self.registry.status[name] = {"state": "loaded", "error": None}
        except Exception as exc:
            self.registry.status[name] = {"state": "failed", "error": str(exc)}
            self.record("plugin", f"{name} failed to start: {exc}", "error")
            if plugin.provider_for:
                # An explicitly selected live source must not silently become mock data.
                self.providers.pop(plugin.provider_for, None)
                self.snapshots[plugin.provider_for] = Snapshot(None, stale=True, source=name, error=str(exc))

    async def add_plugin(self, name):
        """Switch on a plugin the registry just loaded, while running."""
        self.install_plugin(name)
        if name in self.sandboxes:
            await self.sandboxes[name].start()
            return
        plugin = self.registry.plugins.get(name)
        target = plugin.provider_for or name if plugin else name
        if plugin and plugin.provider and target in self.providers:
            await self.refresh_provider(target)

    async def remove_plugin(self, name):
        """Switch a plugin off while running: its screen, data and process go."""
        host = self.sandboxes.pop(name, None)
        if host:
            await host.stop()
        plugin = self.registry.plugins.get(name)
        module = self.modules.get(name)
        if module is not None and name not in BUILTINS:
            self.modules.pop(name, None)
        if plugin and plugin.provider:
            target = plugin.provider_for or name
            provider = self.providers.pop(target, None)
            self.snapshots.pop(target, None)
            if provider is not None:
                try:
                    closing = provider.close()
                    await asyncio.wait_for(self.on_provider_loop(closing) if self.io_loop else closing, 2)
                except Exception:
                    log.exception("Provider cleanup failed")
        current = self.scheduler.current
        if current and current.module == name:
            self.scheduler.next(self.eligible(fresh=True))
        self._eligible = None
        self.dirty = True

    def catalog(self):
        labels = {**BUILTINS, **{n: p.label for n, p in self.registry.plugins.items()}}
        def demo(name):
            # Built-in screens still on made-up sample data (no plugin supplies them).
            provider = self.providers.get(name)
            return name == "system_status" or bool(provider is not None and hasattr(provider, "set_scenario"))
        return {"api_version": 1, "modules": [
                    {"name": n, "label": labels.get(n, n), "available": n in self.modules, "demo": demo(n)}
                    for n in self.config["modules"]],
                "plugins": self.registry.catalog(), "status": self.registry.status}

    def config_message(self):
        m = self.config["modules"]["message"]
        return Message(m["title"], m["text"], m["scrolling"])

    def set_brightness(self, value):
        for sink in self.outputs:
            sink.set_brightness(value)

    def plugin_settings(self, name):
        """A plugin's settings, with the home location filled in where it left
        latitude and longitude at 0."""
        settings = self.config["plugins"][name]
        home = self.config.get("location") or {}
        if (settings.get("latitude") == 0 and settings.get("longitude") == 0
                and (home.get("latitude") or home.get("longitude"))):
            return {**settings, "latitude": home["latitude"], "longitude": home["longitude"]}
        return settings

    def set_power(self, on):
        """Off blanks the panel (brightness 0); rendering and data carry on."""
        self.power = bool(on)
        self.set_brightness(self.effective_brightness())
        self.record("display", "switched on" if on else "switched off")

    def effective_brightness(self, now=None):
        if not getattr(self, "power", True):
            return 0
        display = self.config["display"]
        if not display.get("night_mode"):
            return display["brightness"]
        now = now or datetime.now()
        minutes = now.hour * 60 + now.minute
        start, end = ((int(value[:2]) * 60 + int(value[3:])) for value in (display["night_start"], display["night_end"]))
        night = start <= minutes < end if start < end else (minutes >= start or minutes < end)
        return display["night_brightness"] if night else display["brightness"]

    def apply_brightness_schedule(self):
        value = self.effective_brightness()
        if any(sink.brightness != value for sink in self.outputs):
            self.set_brightness(value)
            night = value != self.config["display"]["brightness"]
            self.record("display", f"brightness {value}% ({'night' if night else 'day'} schedule)")

    def record(self, source, message, level="info"):
        self.events.appendleft({"time": datetime.now().strftime("%H:%M:%S"),
                                "source": source, "message": message, "level": level})
        getattr(logging.getLogger(source), level)(message)

    def context(self):
        # Local timezone conversion can be costly (especially on macOS). Screens
        # need at most second resolution. Recheck the actual epoch second so DST,
        # timezone changes, and NTP corrections still take effect while running.
        second = int(time.time())
        if second != self.wall_second:
            self.wall_now = datetime.now(timezone.utc).astimezone()
            self.wall_second = second
            self.apply_brightness_schedule()
        return RenderContext(self.wall_now, (self.animation_units - self.scene_units) / ANIMATION_UNITS,
                             self.config, self.snapshots, self.message, self.system, self.scene_token or 0)

    def eligible(self, fresh=False):
        now = time.monotonic()
        if (not fresh and self._eligible is not None and self._eligible_config is self.config
                and now - self._eligible_at < ELIGIBLE_SECONDS):
            return self._eligible
        context = self.context()
        result = set()
        for e in self.scheduler.entries:
            if (e.module not in self.modules or not e.enabled
                    or not self.config["modules"][e.module]["enabled"]
                    or now < self.failed_until.get(e.module, 0)):
                continue
            try:
                # A screen with nothing to show (no game on, no plane nearby) waits
                # its turn; "conditional" entries from older configs mean the same.
                if self.modules[e.module].available(context):
                    result.add(e.id)
            except Exception as exc:
                self.fail_module(e.module, exc)
        self._eligible, self._eligible_at, self._eligible_config = result, now, self.config
        return result

    def fail_module(self, module, error):
        self._eligible = None
        self.failed_until[module] = time.monotonic() + 30
        self.record(module, f"module failed; skipping for 30s: {error}", "error")

    def plugin_event(self, module, duration=10):
        """Interrupt requested by a plugin. Plugin events are automatic, so they wait
        until the current screen has been readable; providers call this from the
        provider thread, so the scheduler change is handed to the render loop."""
        try:
            on_main = asyncio.get_running_loop() is self.main_loop or self.main_loop is None
        except RuntimeError:
            on_main = self.main_loop is None
        if on_main:
            return self.interrupt(module, duration, defer=True)

        async def request():
            return self.interrupt(module, duration, defer=True)
        return asyncio.run_coroutine_threadsafe(request(), self.main_loop).result(timeout=2)

    def _provider_loop(self):
        if self.io_loop is None:
            self.io_loop = asyncio.new_event_loop()
            self.io_thread = threading.Thread(target=self.io_loop.run_forever, name="providers", daemon=True)
            self.io_thread.start()
        return self.io_loop

    async def on_provider_loop(self, coroutine):
        if self.main_loop is None:
            self.main_loop = asyncio.get_running_loop()
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coroutine, self._provider_loop()))

    async def refresh_provider(self, name):
        previous = self.snapshots.get(name)
        self.refreshing.add(name)
        try:
            if self.provider_fault:
                raise ConnectionError("simulated API outage")
            # Public schedule feeds can take more than two seconds on a Pi over
            # Wi-Fi. Fetching is already isolated from the render loop, so a
            # slightly wider deadline improves reliability without affecting
            # matrix frame timing.
            result = await asyncio.wait_for(self.on_provider_loop(self.providers[name].fetch()), timeout=PROVIDER_SECONDS)
            if not isinstance(result, Snapshot):
                raise TypeError("Provider must return a Snapshot")
            if (utcnow() - result.updated_at).total_seconds() > 30:
                result = replace(result, stale=True)
            if previous and previous.error:
                self.record(name, "provider recovered")
            self.failures[name] = 0
            self.retry_at.pop(name, None)
        except Exception as exc:
            # Timeouts stringify to "", which logged as a blank error on the Pi.
            message = str(exc) or type(exc).__name__
            self.failures[name] = self.failures.get(name, 0) + 1
            self.retry_at[name] = time.monotonic() + retry_seconds(self.failures[name])
            if previous and self.failures[name] < FAILURES_BEFORE_STALE and not self.provider_fault:
                result = replace(previous, error=message)
            else:
                result = replace(previous, stale=True, error=message) if previous else Snapshot(None, stale=True, error=message)
            if not previous or previous.error != message:
                self.record(name, f"provider error: {message}", "warning")
        finally:
            self.refreshing.discard(name)
        self.snapshots[name] = result
        self._eligible = None
        if previous is None or (previous.data, previous.stale, previous.error) != (result.data, result.stale, result.error):
            self.dirty = True

    async def poll_providers(self):
        while True:
            now = time.monotonic()
            async with self.provider_lock:
                await asyncio.gather(*(self.refresh_provider(name) for name in self.providers
                                       if now >= self.retry_at.get(name, 0) - .5))
            if self.system_scenario == "normal":
                self.read_system()
            await asyncio.sleep(5)

    def read_system(self):
        """What the Status screen reports, from this device rather than from hope."""
        was = self.system
        network = read_network()
        # The keeper's word while it is fresh; a stale file is from a keeper that
        # stopped, and stale news is worse than no news.
        if network.get("at", 0) < time.time() - 120:
            network = {}
        if network.get("mode") in ("setup", "offline"):
            internet = False
        elif network.get("mode") == "online" or "online" in network:
            internet = bool(network.get("online", True))
        else:
            live = [s for s in self.snapshots.values() if s.source != "mock"]
            internet = not live or any(not s.error for s in live)
        assistant = self.home_assistant.status.get("state") == "connected" if self.home_assistant else True
        self.system = SystemStatus(internet, assistant, read_temperature(was.rack_temp_c), software_notice())
        if self.system != was:
            self.dirty = True

    def apply_config(self, config):
        self.config = config
        self.retry_at.clear()       # new settings may be the fix: ask failing providers again now
        for host in self.sandboxes.values():
            host.configure()
        self.message = self.config_message()
        self.set_brightness(self.effective_brightness())
        # Build eligibility against the new playlist, not stale entry IDs.
        entries = from_config(config)
        self.scheduler.entries = entries
        self.scheduler.replace(entries, self.eligible(fresh=True))
        self.dirty = True
        self.record("config", "configuration saved and applied")

    def preview(self, module):
        if module not in self.modules:
            raise ValueError("Unknown module")
        if not self.config["modules"][module]["enabled"]:
            raise ValueError(f"{module} is disabled; enable it in settings first")
        self.auto_demo = False
        self.scheduler.preview(module)
        self.dirty = True

    def interrupt(self, module, duration=10, defer=False):
        if module not in self.modules or not self.config["modules"][module]["enabled"]:
            return False
        if type(duration) not in (int, float) or not 1 <= duration <= 3600:
            raise ValueError("Interrupt duration must be 1–3600 seconds")
        priority = self.modules[module].event_priority
        reason = "plugin event" if defer else "developer scenario"
        accepted = self.scheduler.interrupt(PriorityEvent(module, duration, priority, reason), defer=defer)
        self.record("playlist", f"{'interrupt' if accepted else 'coalesced event'} -> {module}")
        self.dirty = True
        return accepted

    async def scenario(self, module, scenario=None, interrupt=False, message=None):
        if module in self.providers:
            if not callable(getattr(self.providers[module], "set_scenario", None)):
                raise ValueError("This provider uses live data; mock scenarios are unavailable")
            async with self.provider_lock:
                self.providers[module].set_scenario(scenario)
                await self.refresh_provider(module)
            if module == "flight" and self.snapshots[module].data:
                f = self.snapshots[module].data
                self.record("flight", f"new aircraft {f.callsign} distance={f.distance_miles}mi")
        elif module == "system_status":
            if scenario not in ("normal", "ha_offline", "internet_offline", "rack_hot"):
                raise ValueError("Unknown system scenario")
            self.system_scenario = scenario
            self.system = SystemStatus(scenario != "internet_offline", scenario != "ha_offline",
                                       42 if scenario == "rack_hot" else 25.6)
        elif module == "message":
            if not isinstance(message, dict):
                raise ValueError("A message object is required")
            title, body, scrolling = message.get("title", "ALERT"), message.get("body", ""), message.get("scrolling", True)
            if not isinstance(title, str) or not 1 <= len(title.strip()) <= 32:
                raise ValueError("Message title must contain 1–32 characters")
            if not isinstance(body, str) or not 1 <= len(body.strip()) <= 1000:
                raise ValueError("Message body must contain 1–1000 characters")
            if not isinstance(scrolling, bool):
                raise ValueError("Message scrolling must be boolean")
            self.message = Message(title, body, scrolling)
        else:
            raise ValueError("Unknown scenario module")
        self._eligible = None
        self.dirty = True
        self.record(module, f"mock scenario -> {scenario or 'custom message'}")
        accepted = False
        if interrupt:
            if module != "flight" or self.modules["flight"].available(self.context()):
                accepted = self.interrupt(module)
        return accepted

    async def demo_step(self):
        steps = [("clock", None), ("tixclock", None), ("sports", "live"), ("flight", "united"),
                 ("message", None), ("sports", "goal"),
                 ("flight", "southwest"), ("system_status", "rack_hot"), ("sports", "final")]
        for _ in steps:
            module, scenario = steps[self.demo_index % len(steps)]
            self.demo_index += 1
            if self.config["modules"][module]["enabled"]:
                if scenario:
                    if module not in self.providers or hasattr(self.providers[module], "set_scenario"):
                        await self.scenario(module, scenario)
                self.scheduler.preview(module)
                self.dirty = True
                break
        self.demo_due = time.monotonic() + 7

    def state(self):
        return {"scheduler": self.scheduler.status(), "auto_demo": self.auto_demo,
                "brightness": self.sink.brightness, "frames": self.frame_count,
                "clients": len(self.sink.listeners), "uptime": int(time.monotonic() - self.started_at),
                "provider_fault": self.provider_fault,
                "power": self.power, "home_assistant": self.home_assistant.status if self.home_assistant else None,
                "plugins": {**self.registry.status, **{name: {**self.registry.status.get(name, {}), **host.status()}
                                                       for name, host in self.sandboxes.items()}},
                "scenarios": {**{k: getattr(p, "scenario", None) for k, p in self.providers.items()}, "system_status": self.system_scenario},
                "providers": {name: {"source": s.source, "stale": s.stale, "error": s.error,
                                     "has_data": s.data is not None,
                                     "updated_at": s.updated_at.isoformat()} for name, s in self.snapshots.items()},
                "perf": {"slow": list(self.slow)},
                "events": list(self.events), "failed_modules": [k for k,v in self.failed_until.items() if v > time.monotonic()]}

    @staticmethod
    def page_faults():
        """Major page faults so far: memory that had to be read back from the SD card."""
        return resource.getrusage(resource.RUSAGE_SELF).ru_majflt if resource else 0

    def watch_gc(self, phase, info):
        if phase == "start":
            self.gc_started = time.perf_counter()
        else:
            self.gc_seconds += time.perf_counter() - self.gc_started

    def note_slow(self, kind, module, seconds):
        """Record a slow frame with what it was doing: computing (cpu), waiting for memory to
        come back from swap (faults), collecting garbage (gc). A stall with none of these
        was the process not being scheduled at all."""
        cpu, faults = time.process_time(), self.page_faults()
        entry = {"time": datetime.now().strftime("%H:%M:%S"), "kind": kind, "module": module,
                 "ms": round(seconds * 1000), "providers": sorted(self.refreshing),
                 "cpu_ms": round((cpu - self.cpu_mark) * 1000), "faults": faults - self.fault_mark,
                 "gc_ms": round(self.gc_seconds * 1000)}
        self.gc_seconds = 0.0
        self.slow.appendleft(entry)
        if seconds >= .25:
            log.warning("%s %s took %dms (cpu %dms, faults %d, gc %dms; refreshing: %s)", kind, module,
                        entry["ms"], entry["cpu_ms"], entry["faults"], entry["gc_ms"],
                        ", ".join(entry["providers"]) or "-")

    def hold(self, cursor):
        module = self.modules.get(cursor.module)
        try:
            return bool(module and module.hold(self.context()))
        except Exception as exc:
            self.fail_module(cursor.module, exc)
            return False

    def transition_kind(self):
        kind = self.config["display"]["transition"]
        return self.scene_transition if kind == "auto" else kind

    def transition_duration(self):
        return transition_seconds(self.transition_kind(), self.config["display"]["transition_seconds"])

    async def step(self, dt, now):
        self.animation_units += round(dt * self.config["simulator"]["animation_speed"] * ANIMATION_UNITS)
        self.animation_clock = self.animation_units / ANIMATION_UNITS
        eligible = self.eligible()
        self.scheduler.tick(dt, eligible, self.hold)
        token = self.scheduler.revision
        context = self.context()
        if token != self.scene_token:
            self.previous = self.frame.copy()
            self.scene_started = self.animation_clock
            self.scene_units = self.animation_units
            self.scene_token = token
            self.scene_transition = AUTO_TRANSITIONS[token % len(AUTO_TRANSITIONS)]
            self.scene_gate = now + SCENE_GATE_SECONDS
            self.dirty = True
            context = self.context()
        current = self.scheduler.current
        if self.dirty or now >= self.render_due:
            if current:
                try:
                    module = self.modules[current.module]
                    started = time.perf_counter()
                    self.target = validate_frame(module.render(context))
                    spent = time.perf_counter() - started
                    if spent > SLOW_RENDER_SECONDS:
                        self.note_slow("render", current.module, spent)
                    self.render_due = now + module.refresh_interval(context)
                except Exception as exc:
                    self.fail_module(current.module, exc)
                    self.scheduler.next(self.eligible(fresh=True))
                    self.dirty = True
                    self.scene_gate = None
                    return
            else:
                self.target = new_frame()
                centered(self.target, "RACKTICKER", 5, AMBER)
                centered(self.target, "PLAYLIST EMPTY", 19, MUTED)
                self.render_due = float("inf")
            self.dirty = False
        waiting = False
        if self.scene_gate is not None:
            module = self.modules.get(current.module) if current else None
            try:
                waiting = bool(module and now < self.scene_gate and not module.ready(context))
            except Exception:
                waiting = False
            if waiting:
                # The new screen has nothing to show yet: keep the old one up, and start
                # the transition when there is a picture to bring in.
                self.scene_started = self.animation_clock
                self.render_due = min(self.render_due, now + 1 / self.config["display"]["fps"])
            else:
                self.scene_gate = None
        progress = (self.animation_clock - self.scene_started) / self.transition_duration()
        candidate = self.previous if waiting else transition(self.previous, self.target, progress, self.transition_kind())
        if now - self.network_checked >= 5:
            self.network_checked = now
            self.network = read_network()
        if self.network.get("mode") == "setup":
            candidate = self.setup_screen(now)
        elif now < self.splash_until:
            candidate = self.splash(now)
        # Compare only on rendered/transition candidates; static frames reuse their image.
        changed = candidate is not self.frame and candidate.tobytes() != self.frame.tobytes()
        self.frame = candidate
        if changed:
            self.frame_count += 1
            for sink in tuple(self.outputs):
                try:
                    await sink.display(self.frame)
                except Exception as exc:
                    self.record("output", f"{type(sink).__name__} failed: {exc}", "error")
                    self.outputs.remove(sink)
                    try:
                        await asyncio.wait_for(sink.close(), 2)
                    except Exception:
                        log.exception("Failed output cleanup")
        if now - self.last_history_at >= .1:
            self.history.append((round(now - self.started_at, 3), self.frame.tobytes()))
            self.last_history_at = now
        if now - self.last_state_at >= .5:
            self.sink.set_state(self.state())
            self.last_state_at = now

    def frame_step(self, elapsed, period):
        """Advance time in whole frame periods. Sleep jitter then never turns a
        1 px/frame crawl into 0- and 2-pixel hops; leftover time carries over."""
        if elapsed > .25:
            self.clock_debt = 0.0
            return elapsed
        self.clock_debt += elapsed
        frames = round(self.clock_debt / period)
        dt = frames * period
        self.clock_debt -= dt
        return dt

    def show_splash(self, seconds=15.0):
        """On the panel at boot: RACKTICKER and the address of its control page."""
        self.splash_until = time.monotonic() + seconds

    def splash(self, now):
        if self.splash_address is None or (now % 2 < .05 and not self.splash_address[1]):
            self.splash_address = local_address()
        host, address = self.splash_address
        frame = new_frame()
        centered(frame, "RACKTICKER", 2, AMBER)
        from app.core.fonts import draw_tiny, tiny_width
        port = "" if self.port in (80, None) else f":{self.port}"
        notice = read_network().get("notice")
        lines = ((15, notice), (23, f"{host}{port}".upper())) if notice else \
            ((15, f"{host}{port}".upper()), (23, f"{address}{port}" if address else "CONNECTING..."))
        for y, line in lines:
            draw_tiny(frame, line, (128 - tiny_width(line)) // 2, y, MUTED if y == 23 else (230, 232, 230))
        return frame

    def setup_screen(self, now):
        """Wi-Fi setup mode, from the network keeper: how to give RackTicker a network."""
        from app.core.fonts import draw_tiny, tiny_width
        frame = new_frame()
        centered(frame, "WI-FI SETUP", 1, AMBER)
        steps = ("ON YOUR PHONE, JOIN", self.network.get("hotspot", "RackTicker-Setup").upper(),
                 f"THEN OPEN {self.network.get('portal', 'http://10.42.0.1').replace('http://', '')}")
        blink = int(now) % 2 == 0
        for y, line, color in ((11, steps[0], MUTED), (17, steps[1], (240, 242, 240) if blink else AMBER),
                               (25, steps[2], MUTED)):
            draw_tiny(frame, line, (128 - tiny_width(line)) // 2, y, color)
        return frame

    async def run(self):
        last = time.monotonic()
        deadline = last
        while True:
            now = time.monotonic()
            period = 1 / self.config["display"]["fps"]
            # Lateness against the intended wake, not the gap: static screens nap
            # for 100 ms on purpose, and that is not a stall.
            if now - deadline > STALL_SECONDS:
                current = self.scheduler.current
                self.note_slow("stall", current.module if current else "idle", now - deadline)
            if self.auto_demo and now >= self.demo_due:
                await self.demo_step()
            await self.step(self.frame_step(min(now - last, 5), period), now)
            last = now
            # Static modules do no drawing; 10Hz handles controls and dwell deadlines.
            active = (self.animation_clock - self.scene_started < self.transition_duration()
                      or self.render_due - now < .1 or self.scene_gate is not None)
            if not active:
                deadline = time.monotonic() + .1
            else:
                # Absolute deadlines keep a steady cadence regardless of render cost.
                deadline += period
                if deadline < time.monotonic() - period:
                    deadline = time.monotonic() + period
            if now - self.trimmed >= TRIM_SECONDS:
                self.trimmed = now
                memory.trim()
            self.cpu_mark, self.fault_mark = time.process_time(), self.page_faults()
            await asyncio.sleep(max(0.0, deadline - time.monotonic()))

    async def start(self):
        await asyncio.gather(*(self.refresh_provider(name) for name in self.providers))
        self.scheduler.tick(0, self.eligible())
        # Long-lived startup objects (fonts, caches, plugin tables) never need
        # rescanning; freezing them keeps later collections short on a Pi.
        gc.collect()
        gc.freeze()
        # Collections then happen a tenth as often, and a thread that wants the interpreter
        # (the render loop, when a provider is busy) gets it within a millisecond, not five.
        gc.set_threshold(7000, 20, 20)
        sys.setswitchinterval(.001)
        gc.callbacks.append(self.watch_gc)
        for name, host in list(self.sandboxes.items()):
            try:
                await host.start()
            except OSError as exc:
                self.registry.status[name] = {"state": "failed", "error": str(exc)}
        self.record("app", "RackTicker ready • 128x32 RGB")
        self.tasks = [asyncio.create_task(self.run(), name="renderer"),
                      asyncio.create_task(self.poll_providers(), name="providers")]

    async def close(self):
        if self.watch_gc in gc.callbacks:
            gc.callbacks.remove(self.watch_gc)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.gather(*(host.stop() for host in self.sandboxes.values()), return_exceptions=True)
        for provider in self.providers.values():
            try:
                # Sessions belong to the loop that opened them.
                await asyncio.wait_for(self.on_provider_loop(provider.close()) if self.io_loop else provider.close(), 2)
            except Exception:
                log.exception("Provider cleanup failed")
        if self.io_loop is not None:
            self.io_loop.call_soon_threadsafe(self.io_loop.stop)
            self.io_thread.join(timeout=2)
            self.io_loop.close()
            self.io_loop = None
        offload.shutdown()
        for sink in self.outputs:
            try:
                await asyncio.wait_for(sink.clear(), 2)
            except Exception:
                log.exception("Output clear failed")
            finally:
                try:
                    await asyncio.wait_for(sink.close(), 2)
                except Exception:
                    log.exception("Output cleanup failed")

    def png(self):
        out = BytesIO()
        self.frame.save(out, format="PNG")
        return out.getvalue()

    def sequence_zip(self):
        from PIL import Image
        frames = list(self.history)
        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            manifest = {"width": 128, "height": 32, "mode": "RGB", "frames": []}
            for index, (timestamp, pixels) in enumerate(frames):
                name = f"frame-{index:03d}.png"
                png = BytesIO()
                Image.frombytes("RGB", (128,32), pixels).save(png, format="PNG")
                archive.writestr(name, png.getvalue())
                manifest["frames"].append({"file": name, "monotonic_seconds": timestamp})
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        return out.getvalue()
