"""Runs installed plugins away from the display: `python -m app.sandbox_child [FOLDER]`.

The display never waits on plugin code. A plugin that crashes, hangs, leaks
memory or blocks on I/O takes down only this process; RackTicker restarts it
with a backoff and keeps showing everything else.

One process can host one plugin or, on small machines, several. Protocol:
RackTicker writes JSON lines to stdin, each naming its plugin:
  {"op": "load", "folder": "/…/plugins/departures"}
  {"op": "unload", "plugin": "departures"}
  {"op": "configure", "plugin": "departures", "settings": {...}, "display": {...}}
  {"op": "render", "plugin": "departures", "t": 1.25, "now": 1726600000.0, "scene": 7}
  {"op": "validate", "plugin": "departures", "id": 3, "settings": {...}}
This process answers on stdout with a 4-byte big-endian length, a JSON header
and, for frames, 128×32×3 bytes of RGB; every header carries "plugin":
  {"op": "describe", ...}   once at start (settings, label, capabilities)
  {"op": "frame", "available": true, "hold": false, "interval": 0.033, "bytes": 12288}
  {"op": "status", "available": true}   after each data refresh
  {"op": "event", "duration": 10}       the plugin asked to take over the display
Anything the plugin prints goes to stderr, which RackTicker logs.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time
import traceback

POLL_SECONDS, FETCH_TIMEOUT = 5, 6
DEFAULT_DISPLAY = {"fps": 30, "scroll_speed": 30, "scroll_gap": 32, "brightness": 85}


class Channel:
    def __init__(self):
        # The real stdout carries the protocol; print() from plugin code must
        # not corrupt it, so file descriptor 1 is pointed at stderr.
        self.out = os.fdopen(os.dup(1), "wb", buffering=0)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        self.lock = threading.Lock()

    def send(self, header, payload=b""):
        header = dict(header, bytes=len(payload))
        data = json.dumps(header, separators=(",", ":"), default=str).encode()
        with self.lock:
            self.out.write(struct.pack(">I", len(data)) + data + payload)


def load(folder):
    from app.core.manifest import read_manifest
    from app.plugin_api import Plugin
    manifest = read_manifest(folder)
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))  # for the plugin's own helper modules
    # Several plugins can share this process and each may call its file plugin.py:
    # import every entry under its own name.
    spec = importlib.util.spec_from_file_location(f"rackticker_installed_{manifest.id}", folder / manifest.entry)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    plugin = getattr(module, "plugin", None)
    if not isinstance(plugin, Plugin):
        raise TypeError(f"{manifest.entry} must define `plugin = Plugin(...)`")
    if plugin.name != manifest.id:
        raise ValueError(f"Plugin name {plugin.name!r} must match plugin.json id {manifest.id!r}")
    if plugin.output or plugin.provider_for:
        raise ValueError("Installed plugins provide screens and data; outputs and provider_for need a bundled plugin")
    if not plugin.module:
        raise ValueError("An installed plugin needs a screen (module)")
    return manifest, plugin


def describe(plugin):
    return {"op": "describe", "id": plugin.name, "label": plugin.label, "defaults": plugin.defaults,
            "choices": {key: list(options) for key, options in plugin.choices.items()}, "help": plugin.help,
            "ui": plugin.ui, "provider": bool(plugin.provider),
            "event_priority": getattr(plugin.module, "event_priority", 10)}


class Host:
    def __init__(self, plugin, channel):
        from app.core.plugins import PluginRegistry
        from app.plugin_api import PluginContext
        self.plugin, self.channel = plugin, channel
        self.registry = PluginRegistry()
        self.registry.register(plugin)
        self.settings = dict(plugin.defaults)
        self.display = dict(DEFAULT_DISPLAY)
        self.snapshots = {}
        context = PluginContext(plugin.name, lambda: self.settings, self.emit)
        self.module = plugin.module()
        self.provider = plugin.provider(context) if plugin.provider else None

    def send(self, header, payload=b""):
        self.channel.send({**header, "plugin": self.plugin.name}, payload)

    def emit(self, module=None, duration=10):
        self.send({"op": "event", "duration": float(duration)})
        return True

    def context(self, message):
        from app.core.models import Message, SystemStatus
        from app.modules.base import RenderContext
        now = datetime.fromtimestamp(float(message.get("now") or time.time())).astimezone()
        config = {"plugins": {self.plugin.name: self.settings}, "display": self.display,
                  "modules": {self.plugin.name: {"enabled": True}}}
        return RenderContext(now, float(message.get("t", 0)), config, self.snapshots, Message("", ""),
                             SystemStatus(), int(message.get("scene", 0)))

    def render(self, message):
        from app.core.renderer import validate_frame
        context = self.context(message)
        started = time.perf_counter()
        try:
            available = bool(self.module.available(context))
            hold = bool(self.module.hold(context))
            frame = validate_frame(self.module.render(context))
            interval = float(self.module.refresh_interval(context))
        except Exception as exc:
            traceback.print_exc()
            self.send({"op": "frame", "error": f"{type(exc).__name__}: {exc}"[:300], "scene": context.scene})
            return
        self.send({"op": "frame", "available": available, "hold": hold,
                   "interval": interval if interval == interval else 1.0,
                   "ms": round((time.perf_counter() - started) * 1000, 1),
                   "scene": context.scene, "t": context.animation_time}, frame.tobytes())

    def configure(self, message):
        settings = message.get("settings")
        if isinstance(settings, dict):
            self.settings = self.registry.settings(self.plugin.name, settings)
        if isinstance(message.get("display"), dict):
            self.display = {**DEFAULT_DISPLAY, **message["display"]}

    def validate(self, message):
        try:
            self.registry.settings(self.plugin.name, message.get("settings") or {})
            error = None
        except Exception as exc:
            error = str(exc)
        self.send({"op": "validated", "id": message.get("id"), "error": error})

    async def poll(self):
        from app.core.models import Snapshot, utcnow
        name = self.plugin.name
        while True:
            previous = self.snapshots.get(name)
            try:
                result = await asyncio.wait_for(self.provider.fetch(), FETCH_TIMEOUT)
                if not isinstance(result, Snapshot):
                    raise TypeError("Provider must return a Snapshot")
                if (utcnow() - result.updated_at).total_seconds() > 30:
                    result = replace(result, stale=True)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                print(f"provider error: {message}", file=sys.stderr)
                result = (replace(previous, stale=True, error=message) if previous
                          else Snapshot(None, stale=True, error=message))
            self.snapshots[name] = result
            try:
                available = bool(self.module.available(self.context({})))
            except Exception:
                available = False
            self.send({"op": "status", "available": available, "error": result.error})
            await asyncio.sleep(POLL_SECONDS)


def reader(loop, queue):
    for line in sys.stdin.buffer:
        loop.call_soon_threadsafe(queue.put_nowait, line)
    loop.call_soon_threadsafe(queue.put_nowait, None)


async def serve(folders):
    channel = Channel()
    hosts, pollers = {}, {}

    async def open_plugin(folder):
        try:
            _, plugin = load(Path(folder))
            if plugin.name in hosts:
                await close_plugin(plugin.name)
            host = Host(plugin, channel)
        except Exception as exc:
            traceback.print_exc()
            channel.send({"op": "fatal", "folder": str(folder), "error": f"{type(exc).__name__}: {exc}"[:500]})
            return
        hosts[plugin.name] = host
        host.send(describe(plugin))
        if host.provider:
            pollers[plugin.name] = asyncio.create_task(host.poll())

    async def close_plugin(name):
        host = hosts.pop(name, None)
        poller = pollers.pop(name, None)
        if poller:
            poller.cancel()
        if host and host.provider:
            try:
                await asyncio.wait_for(host.provider.close(), 2)
            except Exception:
                pass

    for folder in folders:
        await open_plugin(folder)
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    threading.Thread(target=reader, args=(loop, queue), daemon=True).start()
    try:
        while True:
            line = await queue.get()
            if line is None:
                break
            batch = [line]
            while not queue.empty():  # take everything waiting, then answer only the newest renders
                batch.append(queue.get_nowait())
            renders = {}
            for item in batch:
                if item is None:
                    return 0
                try:
                    message = json.loads(item)
                except ValueError:
                    continue
                op, name = message.get("op"), message.get("plugin")
                if op == "load":
                    await open_plugin(message.get("folder"))
                elif op == "unload":
                    await close_plugin(name)
                elif op == "render":
                    renders[name] = message
                elif name in hosts:
                    _handle(hosts[name], message)
            for name, message in renders.items():
                if name in hosts:
                    hosts[name].render(message)
    finally:
        for name in list(hosts):
            await close_plugin(name)
    return 0


def _handle(host, message):
    op = message.get("op")
    if op == "configure":
        try:
            host.configure(message)
        except Exception as exc:
            print(f"{host.plugin.name}: settings rejected: {exc}", file=sys.stderr)
    elif op == "validate":
        host.validate(message)


def main():
    folders = [Path(arg).resolve() for arg in sys.argv[1:] if not arg.startswith("--")]
    if "--describe" in sys.argv:
        folder = folders[0]
        channel = Channel()
        try:
            _, plugin = load(folder)
            channel.send(describe(plugin))
            return 0
        except Exception as exc:
            traceback.print_exc()
            channel.send({"op": "fatal", "error": f"{type(exc).__name__}: {exc}"[:500]})
            return 2
    if hasattr(os, "nice"):
        try:
            os.nice(5)  # The main display process always wins the CPU.
        except OSError:
            pass
    return asyncio.run(serve(folders))


if __name__ == "__main__":
    sys.exit(main())
