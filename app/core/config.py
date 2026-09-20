"""Strict configuration validation and atomic, human-editable JSON persistence."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from pathlib import Path
from app.core.plugins import PluginRegistry, NAME

MODULES = ("clock", "tixclock", "flight", "sports", "message", "system_status", "test_pattern")
TRANSITIONS = ("cut", "slide_left", "slide_up", "ticker", "wipe", "dissolve", "drop", "auto")

DEFAULT_CONFIG = {
    "version": 1,
    # Switched-on plugins; null means every plugin that ships with RackTicker.
    "enabled_plugins": None,
    # Accept plugin zips pushed over the network (python -m app.dev push).
    "plugin_uploads": False,
    "plugins": {},
    # 30 px/s at 30 fps moves exactly one LED per frame: the smoothest crawl
    # a matrix can show. Other speeds step unevenly by construction.
    "display": {"brightness": 85, "scroll_speed": 30, "scroll_gap": 32,
                "transition": "auto", "transition_seconds": 0.5, "fps": 30,
                # Living-room friendly: dim automatically overnight.
                "night_mode": False, "night_brightness": 20, "night_start": "23:00", "night_end": "07:00"},
    "simulator": {"mode": "led", "zoom": "fit", "animation_speed": 1.0},
    # Home, for weather, nearby flights and traffic. Plugins whose own latitude
    # and longitude are left at 0 use this.
    "location": {"name": "", "latitude": 0.0, "longitude": 0.0},
    # Home Assistant via MQTT discovery (Mosquitto add-on or any broker).
    "home_assistant": {"enabled": False, "host": "", "port": 1883, "username": "", "password": "",
                       "name": "RackTicker", "discovery_prefix": "homeassistant"},
    "modules": {
        "clock": {"enabled": True, "hour_format": "12", "show_seconds": True, "style": "desk"},
        "tixclock": {"enabled": True, "hour_format": "12", "update_interval": 4, "show_label": False},
        "flight": {"enabled": True, "max_distance_miles": 10, "layout": "route"},
        "sports": {"enabled": True},
        "message": {"enabled": True, "title": "RACKTICKER",
                    "text": "WASHER FINISHED • FRONT DOOR OPEN", "scrolling": True},
        "system_status": {"enabled": True, "temperature_unit": "F"},
        "test_pattern": {"enabled": True},
    },
    "playlist": [
        {"id": "clock-1", "module": "clock", "duration": 8, "enabled": True, "mode": "normal"},
        {"id": "tixclock-1", "module": "tixclock", "duration": 20, "enabled": True, "mode": "normal"},
        {"id": "sports-1", "module": "sports", "duration": 10, "enabled": True, "mode": "normal"},
        {"id": "flight-1", "module": "flight", "duration": 10, "enabled": True, "mode": "conditional"},
        {"id": "clock-2", "module": "clock", "duration": 8, "enabled": True, "mode": "normal"},
        {"id": "message-1", "module": "message", "duration": 10, "enabled": True, "mode": "normal"},
        {"id": "status-1", "module": "system_status", "duration": 6, "enabled": True, "mode": "normal"},
    ],
}


class ConfigError(ValueError):
    pass


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ConfigError(f"{label} must be a number from {low} to {high}")


def choice(value, options, label):
    if value not in options:
        raise ConfigError(f"{label} must be one of {', '.join(map(str, options))}")


def boolean(value, label):
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be true or false")


def merge_known(default, raw, path="config"):
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be an object")
    extra = raw.keys() - default.keys()
    if extra:
        raise ConfigError(f"Unknown {path} setting: {sorted(extra)[0]}")
    result = copy.deepcopy(default)
    for key, value in raw.items():
        result[key] = merge_known(default[key], value, f"{path}.{key}") if isinstance(default[key], dict) else copy.deepcopy(value)
    return result


# Settings retired by later versions: dropped on load so older saved configs
# keep validating instead of failing on an unknown key.
RETIRED = {("modules", "flight", "radar_up")}


RETIRED_MODULES = {"iracing"}   # built-in screens that were removed


def retire(raw):
    modules = raw.get("modules") if isinstance(raw, dict) else None
    for name in RETIRED_MODULES:
        if isinstance(modules, dict):
            modules.pop(name, None)
        for key in ("playlist", "interrupts"):
            entries = raw.get(key) if isinstance(raw, dict) else None
            if isinstance(entries, list):
                raw[key] = [entry for entry in entries if not (isinstance(entry, dict) and entry.get("module") == name)]
    for path in RETIRED:
        node = raw
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(path[-1], None)
    return raw


def validate_config(raw, registry=None):
    registry = registry or PluginRegistry()
    if not isinstance(raw, dict):
        raise ConfigError("config must be an object")
    raw = retire(copy.deepcopy(raw))
    if not isinstance(raw.get("modules", {}), dict):
        raise ConfigError("modules must be an object")
    plugin_raw = raw.get("plugins", {})
    if not isinstance(plugin_raw, dict) or len(plugin_raw) > 64:
        raise ConfigError("plugins must be an object with at most 64 entries")
    defaults = copy.deepcopy(DEFAULT_CONFIG)
    for name in plugin_raw.keys() | registry.plugins.keys():
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in MODULES:
            raise ConfigError("Invalid plugin name")
        try:
            defaults["plugins"][name] = registry.settings(name, plugin_raw.get(name, {}))
        except Exception as exc:
            raise ConfigError(f"Plugin {name}: {exc}") from exc
        # Keep a removed plugin's playlist/settings so reinstalling restores it.
        plugin = registry.plugins.get(name)
        if (plugin and plugin.module) or name in raw.get("modules", {}):
            defaults["modules"][name] = {"enabled": True}
    # Plugin settings were migrated and validated by the registry above. Merging
    # the raw copy back would reinstate retired keys and unvalidated values.
    c = merge_known(defaults, {key: value for key, value in raw.items() if key != "plugins"})
    if type(c["version"]) is not int or c["version"] != 1:
        raise ConfigError("Unsupported config version (expected 1)")
    enabled = c["enabled_plugins"]
    if enabled is not None and (not isinstance(enabled, list) or len(enabled) > 64
                                or not all(isinstance(n, str) and NAME.fullmatch(n) for n in enabled)
                                or len(set(enabled)) != len(enabled)):
        raise ConfigError("enabled_plugins must be null or a list of unique plugin ids")
    boolean(c["plugin_uploads"], "plugin uploads")
    where = c["location"]
    number(where["latitude"], -90, 90, "latitude")
    number(where["longitude"], -180, 180, "longitude")
    if not isinstance(where["name"], str) or len(where["name"]) > 80:
        raise ConfigError("Location name must be text up to 80 characters")
    ha = c["home_assistant"]
    boolean(ha["enabled"], "Home Assistant enabled")
    number(ha["port"], 1, 65535, "MQTT port")
    for key, limit in (("host", 253), ("username", 128), ("password", 256), ("name", 40), ("discovery_prefix", 64)):
        if not isinstance(ha[key], str) or len(ha[key]) > limit:
            raise ConfigError(f"Home Assistant {key} must be text up to {limit} characters")
    if ha["enabled"] and not ha["host"].strip():
        raise ConfigError("Home Assistant needs the MQTT broker's address")
    if not ha["name"].strip():
        raise ConfigError("Home Assistant device name cannot be empty")
    d, s, m = c["display"], c["simulator"], c["modules"]
    for key, low, high in (("brightness", 0, 100), ("scroll_speed", 1, 160), ("scroll_gap", 8, 256),
                           ("transition_seconds", .1, 2), ("fps", 5, 30), ("night_brightness", 0, 100)):
        number(d[key], low, high, key)
    boolean(d["night_mode"], "night mode")
    for key in ("night_start", "night_end"):
        if not isinstance(d[key], str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", d[key]):
            raise ConfigError(f"{key} must be a 24-hour HH:MM time")
    choice(d["transition"], TRANSITIONS, "transition")
    choice(s["mode"], ("clean", "led"), "simulator mode")
    choice(s["zoom"], ("4", "6", "8", "fit"), "zoom")
    number(s["animation_speed"], .1, 2, "animation speed")
    for name in c["modules"]:
        boolean(m[name]["enabled"], f"{name}.enabled")
    choice(m["clock"]["hour_format"], ("12", "24"), "hour format")
    boolean(m["clock"]["show_seconds"], "show seconds")
    choice(m["clock"]["style"], ("classic", "desk"), "clock style")
    choice(m["tixclock"]["hour_format"], ("12", "24"), "TIX hour format")
    number(m["tixclock"]["update_interval"], 1, 60, "TIX update interval")
    boolean(m["tixclock"]["show_label"], "TIX label")
    choice(m["system_status"]["temperature_unit"], ("F", "C"), "temperature unit")
    choice(m["flight"]["layout"], ("route", "detail", "minimal"), "flight layout")
    number(m["flight"]["max_distance_miles"], .1, 200, "flight distance")
    boolean(m["message"]["scrolling"], "message scrolling")
    for key, limit in (("title", 32), ("text", 1000)):
        if not isinstance(m["message"][key], str) or not 1 <= len(m["message"][key].strip()) <= limit:
            raise ConfigError(f"Message {key} must contain 1–{limit} characters")
    entries = c["playlist"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        raise ConfigError("Playlist must contain 1–32 entries")
    ids = set()
    template = {"id": "", "module": "clock", "duration": 8, "enabled": True, "mode": "normal"}
    for i, raw_entry in enumerate(entries):
        e = merge_known(template, raw_entry, f"playlist[{i}]")
        if not isinstance(e["id"], str) or not e["id"] or len(e["id"]) > 64 or e["id"] in ids:
            raise ConfigError("Playlist IDs must be nonempty, unique strings up to 64 characters")
        ids.add(e["id"])
        choice(e["module"], c["modules"], "playlist module")
        choice(e["mode"], ("normal", "conditional"), "playlist mode")
        number(e["duration"], 1, 3600, "duration")
        boolean(e["enabled"], "playlist enabled")
        entries[i] = e
    return c


# A first run shows real screens from the plugins that ship, in an order that
# mixes quick glances with longer reads; demo-data screens are left out.
STARTER = (("clock", 8), ("weather", 10), ("finance", 20), ("sportsbook", 24), ("news", 30), ("tixclock", 15),
           ("flight", 12), ("clock", 8), ("town", 20))
# Shipped but off on a fresh install, one switch away on the Plugins page: regional or niche.
OPTIONAL = set()


def starter(registry):
    config = copy.deepcopy(DEFAULT_CONFIG)
    screens = set(MODULES) | {name for name, plugin in registry.plugins.items() if plugin.module}
    if not screens - set(MODULES):
        return config  # No plugins: keep the built-in demo playlist.
    config["playlist"] = [{"id": f"{name}-{index + 1}", "module": name, "duration": seconds, "enabled": True,
                           "mode": "normal"} for index, (name, seconds) in enumerate(STARTER) if name in screens]
    config["enabled_plugins"] = sorted(name for name in registry.plugins if name not in OPTIONAL)
    return config


class ConfigStore:
    def __init__(self, path: Path, registry=None):
        self.path = Path(path)
        self.registry = registry or PluginRegistry()

    def load(self):
        if not self.path.exists():
            return self.save(starter(self.registry))
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            config = validate_config(raw, self.registry)
            # Milestone 1 configs predate TIX Clock. Preserve their existing
            # order/settings, but add the new screen once when upgrading.
            has_tix_entry = any(entry.get("module") == "tixclock" for entry in config["playlist"])
            if ("tixclock" not in raw.get("modules", {}) and not has_tix_entry
                    and len(config["playlist"]) < 32):
                tix_entry = copy.deepcopy(next(entry for entry in DEFAULT_CONFIG["playlist"] if entry["module"] == "tixclock"))
                ids = {entry["id"] for entry in config["playlist"]}
                while tix_entry["id"] in ids:
                    tix_entry["id"] += "-new"
                config["playlist"].insert(1, tix_entry)
                return self.save(config)
            return config
        except (OSError, ValueError, TypeError) as exc:
            raise ConfigError(f"Cannot load {self.path}: {exc}") from exc

    def save(self, raw):
        config = validate_config(raw, self.registry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".rackticker-", suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                json.dump(config, f, indent=2, ensure_ascii=False, allow_nan=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return config
