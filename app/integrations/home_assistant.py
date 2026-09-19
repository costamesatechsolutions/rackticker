"""Home Assistant over MQTT discovery: RackTicker appears as a device by itself.

Entities: a light (on/off and brightness), a "Screen" picker, a "Show <screen>"
switch per screen (switches pass through Matter and Alexa, so "Alexa, turn on
Sportsbook" works), a Next button, a Message box and a Now-showing sensor.
Needs an MQTT broker, such as Home Assistant's Mosquitto add-on.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re

from app.integrations.mqtt import MQTTClient, MQTTError

log = logging.getLogger("home_assistant")
PLAYLIST = "Playlist"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "rackticker"


class HomeAssistant:
    def __init__(self, runtime, store):
        self.runtime, self.store = runtime, store
        self.task = None
        self.settings = None
        self.status = {"state": "off", "error": None}
        self.client = None

    def reconfigure(self, config):
        settings = copy.deepcopy(config.get("home_assistant") or {})
        if settings == self.settings:
            return
        self.settings = settings
        if self.task:
            self.task.cancel()
            self.task = None
        if settings.get("enabled") and settings.get("host"):
            self.task = asyncio.ensure_future(self._run(settings))
        else:
            self.status = {"state": "off", "error": None}

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _run(self, settings):
        delay = 5
        while True:
            try:
                await self._session(settings)
                delay = 5
            except asyncio.CancelledError:
                raise
            except (OSError, MQTTError, asyncio.TimeoutError) as exc:
                self.status = {"state": "error", "error": str(exc) or type(exc).__name__}
                log.warning("Home Assistant connection: %s", self.status["error"])
            finally:
                if self.client:
                    await self.client.close()
                    self.client = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)

    def _screens(self):
        """(module, label) for screens that can be shown right now."""
        labels = {item["name"]: item["label"] for item in self.runtime.catalog()["modules"] if item["available"]}
        in_playlist = [entry["module"] for entry in self.runtime.config["playlist"] if entry["enabled"]]
        wanted = [name for name in dict.fromkeys(in_playlist) if name in labels
                  and self.runtime.config["modules"].get(name, {}).get("enabled")]
        return [(name, labels[name]) for name in wanted]

    async def _session(self, settings):
        name = settings.get("name") or "RackTicker"
        node = slug(name)
        base = f"rackticker/{node}"
        prefix = settings.get("discovery_prefix") or "homeassistant"
        self.client = client = MQTTClient(settings["host"], int(settings.get("port") or 1883), f"rackticker-{node}",
                                          settings.get("username") or "", settings.get("password") or "",
                                          will=(f"{base}/status", "offline"))
        await client.connect()
        self.status = {"state": "connected", "error": None}
        device = {"identifiers": [f"rackticker_{node}"], "name": name, "manufacturer": "RackTicker",
                  "model": "128×32 LED ticker"}
        common = {"availability_topic": f"{base}/status", "device": device}
        screens = self._screens()

        async def announce(kind, key, config):
            await client.publish(f"{prefix}/{kind}/{node}/{key}/config",
                                 json.dumps({**common, "unique_id": f"{node}_{key}", **config}), retain=True)

        await announce("light", "display", {"name": "Display", "schema": "json", "brightness": True,
                                             "brightness_scale": 100, "command_topic": f"{base}/light/set",
                                             "state_topic": f"{base}/light/state", "icon": "mdi:led-strip-variant"})
        await announce("select", "screen", {"name": "Screen", "options": [PLAYLIST] + [label for _, label in screens],
                                            "command_topic": f"{base}/screen/set", "state_topic": f"{base}/screen/state",
                                            "icon": "mdi:television-guide"})
        await announce("button", "next", {"name": "Next screen", "command_topic": f"{base}/next/press",
                                          "icon": "mdi:skip-next"})
        await announce("text", "message", {"name": "Message", "command_topic": f"{base}/message/set", "max": 255,
                                           "icon": "mdi:message-text"})
        await announce("sensor", "now", {"name": "Now showing", "state_topic": f"{base}/screen/state",
                                         "icon": "mdi:monitor"})
        for module, label in screens:
            await announce("switch", f"show_{module}", {"name": f"Show {label}", "icon": "mdi:play-box",
                                                        "command_topic": f"{base}/show/{module}/set",
                                                        "state_topic": f"{base}/show/{module}/state"})
        await client.subscribe(f"{base}/+/set", f"{base}/+/press", f"{base}/show/+/set")
        await client.publish(f"{base}/status", "online", retain=True)
        published = {}
        labels = dict(screens)

        async def state():
            current = self.runtime.scheduler.current
            module = current.module if current else None
            showing = labels.get(module, PLAYLIST) if current and current.kind == "preview" else PLAYLIST
            now = labels.get(module) or (module or "Idle").replace("_", " ").title()
            values = {f"{base}/screen/state": now if showing != PLAYLIST else PLAYLIST,
                      f"{base}/light/state": json.dumps({"state": "ON" if self.runtime.power else "OFF",
                                                         "brightness": self.runtime.config["display"]["brightness"]})}
            for name in labels:
                values[f"{base}/show/{name}/state"] = "ON" if current and current.kind == "preview" and \
                    current.module == name else "OFF"
            for topic, value in values.items():
                if published.get(topic) != value:
                    await client.publish(topic, value, retain=True)
                    published[topic] = value

        async def ticker():
            while True:
                await state()
                await asyncio.sleep(1)

        pulse = asyncio.create_task(ticker())
        try:
            while True:
                message = await client.messages.get()
                if message is None:
                    raise MQTTError("Connection to the MQTT broker was lost")
                topic, payload = message
                try:
                    self._command(topic[len(base) + 1:], payload.decode("utf-8", "replace").strip(), screens)
                except (ValueError, KeyError) as exc:
                    log.info("Ignored Home Assistant command %s: %s", topic, exc)
                await state()
        finally:
            pulse.cancel()

    def _command(self, path, value, screens):
        runtime = self.runtime
        by_label = {label: module for module, label in screens}
        if path == "light/set":
            data = json.loads(value)
            if "brightness" in data:
                raw = copy.deepcopy(runtime.config)
                raw["display"]["brightness"] = max(1, min(100, int(data["brightness"])))
                runtime.apply_config(self.store.save(raw))
            runtime.set_power(data.get("state", "ON") == "ON")
        elif path == "screen/set":
            if value == PLAYLIST:
                runtime.scheduler.resume(runtime.eligible())
            else:
                runtime.preview(by_label[value])
        elif path == "next/press":
            runtime.scheduler.next(runtime.eligible())
        elif path == "message/set":
            if value:
                asyncio.ensure_future(runtime.scenario("message", message={"title": "MESSAGE", "body": value[:255],
                                                                            "scrolling": True}, interrupt=True))
        elif path.startswith("show/") and path.endswith("/set"):
            module = path[5:-4]
            if module not in dict(screens):
                raise ValueError("unknown screen")
            if value == "ON":
                runtime.preview(module)
            else:
                runtime.scheduler.resume(runtime.eligible())
        runtime.dirty = True
