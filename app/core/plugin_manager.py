"""Which plugins exist, which are switched on, and loading them.

Three kinds, one list in Settings:
  bundled    folders in RackTicker's own plugins/ directory, run in-process
  installed  folders in DATA/plugins/ (GitHub, zip, dev push), run sandboxed
  packages   pip-installed entry points (the original API v1 route), in-process

Switched-on plugins are the configuration's `enabled_plugins` list; when it is
absent every bundled plugin is on. Nothing here imports plugin code until a
plugin is switched on.
"""
from __future__ import annotations

import importlib
from importlib import metadata
import json
from pathlib import Path
import struct
import subprocess
import sys

from app.core.manifest import DESCRIPTION, scan
from app.core.plugins import BUILTINS, PLUGIN_GROUP, PluginRegistry
from app.core.sandbox import SandboxModule, plugin_env
from app.plugin_api import Plugin

BUNDLED = Path(__file__).resolve().parents[2] / "plugins"


class PluginManager:
    def __init__(self, data_dir, bundled_dir=BUNDLED, cli=(), bundled_by_default=True):
        self.bundled_by_default = bundled_by_default
        self.data_dir = Path(data_dir)
        self.installed_dir = self.data_dir / "plugins"
        self.plugin_data = self.data_dir / "plugin-data"
        self.bundled_dir = Path(bundled_dir)
        self.cli = list(cli)
        self.rescan()

    def rescan(self):
        self.bundled, bundled_errors = scan(self.bundled_dir, bundled=True)
        installed, installed_errors = scan(self.installed_dir)
        self.errors = {**bundled_errors, **installed_errors}
        self.installed = {}
        for name, manifest in installed.items():
            if name in self.bundled or name in BUILTINS:
                self.errors[manifest.path.name] = f"{name}: the id is already used by a built-in plugin"
            else:
                self.installed[name] = manifest
        self.packages = {}
        for entry in metadata.entry_points(group=PLUGIN_GROUP):
            if entry.name not in self.bundled and entry.name not in self.installed:
                self.packages.setdefault(entry.name, []).append(entry)

    def known(self):
        return {**{name: "package" for name in self.packages}, **{name: "installed" for name in self.installed},
                **{name: "bundled" for name in self.bundled}}

    def default_enabled(self):
        base = list(self.bundled) if self.bundled_by_default else []
        return base + [name for name in self.cli if name not in base]

    def enabled(self, config_raw):
        chosen = config_raw.get("enabled_plugins") if isinstance(config_raw, dict) else None
        return list(chosen) if isinstance(chosen, list) else self.default_enabled()

    def load(self, enabled):
        registry = PluginRegistry()
        for name in dict.fromkeys(enabled):
            self.register(registry, name)
        return registry

    def register(self, registry, name):
        """Load one plugin into the registry; failures are recorded, not raised."""
        registry.status[name] = {"state": "loaded", "error": None}
        try:
            if name in self.bundled:
                manifest = self.bundled[name]
                # Import by module name from its folder, so offload() workers can
                # import the same module to parse feeds off the render loop.
                folder = str(manifest.path)
                if folder not in sys.path:
                    sys.path.insert(1, folder)
                plugin = getattr(importlib.import_module(manifest.module_name), "plugin", None)
                if not isinstance(plugin, Plugin) or plugin.name != name:
                    raise ValueError(f"{manifest.entry} must define plugin = Plugin({name!r}, ...)")
                registry.register(plugin)
            elif name in self.installed:
                manifest = self.installed[name]
                description = manifest.described() or describe_now(manifest.path)
                plugin = Plugin(name, str(description.get("label") or manifest.name)[:60] or name,
                                module=SandboxModule, defaults=description.get("defaults") or {},
                                choices={key: tuple(value) for key, value in (description.get("choices") or {}).items()},
                                help=description.get("help") or {}, ui=description.get("ui") or {})
                registry.register(plugin)
                registry.sandboxed[name] = manifest
            elif name in self.packages:
                entries = self.packages[name]
                if len(entries) != 1:
                    raise ValueError("Duplicate installed entry-point name")
                plugin = entries[0].load()  # Installed and explicitly enabled local code only.
                if not isinstance(plugin, Plugin) or plugin.name != name:
                    raise ValueError("Entry-point name must match Plugin.name")
                registry.register(plugin)
            else:
                registry.status[name] = {"state": "missing", "error": "Not installed"}
        except Exception as exc:
            registry.unregister(name)
            registry.status[name] = {"state": "failed", "error": str(exc) or type(exc).__name__}
        return registry.status[name]

    def describe_manifest(self, name):
        manifest = self.bundled.get(name) or self.installed.get(name)
        if manifest:
            return {**manifest.summary(), "kind": "bundled" if manifest.bundled else "installed"}
        if name in self.packages:
            return {"id": name, "name": name, "kind": "package", "bundled": False, "source": {}}
        return {"id": name, "name": name, "kind": "missing", "bundled": False, "source": {}}


def describe_now(folder, timeout=25):
    """Synchronous describe for startup, cached next to the plugin."""
    result = subprocess.run([sys.executable, "-m", "app.sandbox_child", str(folder), "--describe"],
                            capture_output=True, timeout=timeout, cwd=str(folder), env=plugin_env(folder))
    out = result.stdout
    if len(out) < 4:
        lines = [line for line in result.stderr.decode("utf-8", "replace").splitlines() if line.strip()]
        raise ValueError(lines[-1][:300] if lines else "The plugin did not start")
    header = json.loads(out[4:4 + struct.unpack(">I", out[:4])[0]])
    if header.get("op") == "fatal":
        raise ValueError(header.get("error") or "The plugin failed to load")
    try:
        (Path(folder) / DESCRIPTION).write_text(json.dumps(header, indent=2))
    except OSError:
        pass
    return header
