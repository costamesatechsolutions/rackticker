"""Discover installed packages without importing them; load explicit opt-ins only."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from importlib import metadata
import copy
import math
import re

from app.plugin_api import Plugin

PLUGIN_GROUP = "rackticker.plugins"
NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
BUILTINS = {"clock": "Clock", "tixclock": "TIX Clock", "flight": "Flight",
            "sports": "Sports", "message": "Message", "system_status": "Status",
            "test_pattern": "Colour test"}


def check_settings(settings):
    if not isinstance(settings, dict) or len(settings) > 32:
        raise ValueError("Plugin settings must be an object with at most 32 fields")
    for key, value in settings.items():
        if not isinstance(key, str) or not NAME.fullmatch(key):
            raise ValueError("Plugin setting names must use lowercase letters, numbers and underscores")
        if type(value) not in (str, bool, int, float):
            raise ValueError(f"Plugin setting {key} must be text, boolean or a number")
        if isinstance(value, str) and len(value) > 1000:
            raise ValueError(f"Plugin setting {key} is too long")
        if type(value) in (int, float) and not math.isfinite(value):
            raise ValueError(f"Plugin setting {key} must be finite")


@dataclass
class PluginRegistry:
    plugins: dict[str, Plugin] = field(default_factory=dict)
    status: dict[str, dict] = field(default_factory=dict)
    # Installed plugins that run in a sandbox process: name -> Manifest.
    sandboxed: dict = field(default_factory=dict)

    def unregister(self, name):
        self.plugins.pop(name, None)
        self.sandboxed.pop(name, None)

    def register(self, plugin):
        if not isinstance(plugin, Plugin) or type(plugin.api_version) is not int or plugin.api_version != 1:
            raise ValueError("Plugin must declare supported API version 1")
        if not NAME.fullmatch(plugin.name) or plugin.name in BUILTINS or plugin.name in self.plugins:
            raise ValueError("Plugin name is invalid or already registered")
        if not isinstance(plugin.label, str) or not 1 <= len(plugin.label.strip()) <= 60:
            raise ValueError("Plugin label must contain 1–60 characters")
        factories = (plugin.module, plugin.provider, plugin.output)
        if not any(factories) or any(f is not None and not callable(f) for f in factories):
            raise ValueError("Plugin needs a module, provider or output factory")
        if plugin.provider_for and (not plugin.provider or plugin.provider_for not in BUILTINS):
            raise ValueError("provider_for requires a provider and a built-in target")
        target = plugin.provider_for or plugin.name
        if plugin.provider and any(p.provider and (p.provider_for or p.name) == target for p in self.plugins.values()):
            raise ValueError(f"Multiple plugin providers for {target}")
        check_settings(plugin.defaults)
        for key, options in plugin.choices.items():
            if (key not in plugin.defaults or not all(isinstance(option, str) for option in options)
                    or plugin.defaults[key] not in options):
                raise ValueError(f"choices for {key} must be text options that include the default")
        if not all(key in plugin.defaults and isinstance(text, str) for key, text in plugin.help.items()):
            raise ValueError("help entries must describe existing settings")
        if not all(key in plugin.defaults and isinstance(hint, dict) for key, hint in plugin.ui.items()):
            raise ValueError("ui hints must describe existing settings")
        if plugin.validate_settings:
            plugin.validate_settings(copy.deepcopy(plugin.defaults))
        self.plugins[plugin.name] = plugin

    def settings(self, name, raw):
        check_settings(raw)
        plugin = self.plugins.get(name)
        if not plugin:
            return copy.deepcopy(raw)  # Retain settings if a plugin is unavailable.
        if plugin.migrate_settings:
            raw = plugin.migrate_settings(copy.deepcopy(raw))
            check_settings(raw)
        if raw.keys() - plugin.defaults.keys():
            raise ValueError(f"Unknown settings for plugin {name}")
        result = copy.deepcopy(plugin.defaults)
        for key, value in raw.items():
            default = plugin.defaults[key]
            numeric = type(default) in (int, float) and type(value) in (int, float)
            if not numeric and type(value) is not type(default):
                raise ValueError(f"Wrong type for {name}.{key}")
            if key in plugin.choices and value not in plugin.choices[key]:
                raise ValueError(f"{name}.{key} must be one of {', '.join(plugin.choices[key])}")
            result[key] = value
        if plugin.validate_settings:
            plugin.validate_settings(copy.deepcopy(result))
        return result

    def catalog(self):
        return [{"name": name, "label": plugin.label,
                 "module": bool(plugin.module), "provider_for": plugin.provider_for,
                 "provider": bool(plugin.provider), "output": bool(plugin.output),
                 "defaults": plugin.defaults, "choices": {key: list(options) for key, options in plugin.choices.items()},
                 "help": plugin.help, "ui": plugin.ui} for name, plugin in self.plugins.items()]


def discover_plugins(enabled=()):
    registry = PluginRegistry()
    entries = list(metadata.entry_points(group=PLUGIN_GROUP))
    counts = Counter(ep.name for ep in entries)
    selected = set(enabled)
    for ep in entries:
        registry.status[ep.name] = {"state": "inactive", "error": None}
        if ep.name not in selected:
            continue
        try:
            if counts[ep.name] != 1:
                raise ValueError("Duplicate installed entry-point name")
            plugin = ep.load()  # Installed and explicitly enabled local code only.
            if not isinstance(plugin, Plugin) or plugin.name != ep.name:
                raise ValueError("Entry-point name must match Plugin.name")
            registry.register(plugin)
            registry.status[ep.name]["state"] = "loaded"
        except Exception as exc:
            registry.status[ep.name] = {"state": "failed", "error": str(exc)}
    for name in selected - counts.keys():
        registry.status[name] = {"state": "missing", "error": "Install this plugin in the RackTicker environment"}
    return registry
