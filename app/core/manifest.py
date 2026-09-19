"""Plugin folders: find them and read their plugin.json without running any code.

A plugin is a folder:

    my-plugin/
      plugin.json   {"id": "my_plugin", "name": "My plugin", "entry": "plugin.py", ...}
      plugin.py     defines `plugin = Plugin(...)`

Bundled plugins ship inside RackTicker and run in the main process. Installed
plugins (from GitHub, a zip or `python -m app.dev push`) live in the data
directory and run in their own sandbox process.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
ENTRY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\.py$")
MANIFEST = "plugin.json"
# Written by the installer next to an installed plugin's code.
SOURCE, DESCRIPTION = ".source.json", ".describe.json"


@dataclass
class Manifest:
    id: str
    name: str
    path: Path
    entry: str = "plugin.py"
    description: str = ""
    author: str = ""
    version: str = ""
    homepage: str = ""
    bundled: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def module_name(self):
        return self.entry[:-3]

    def source(self):
        return _read(self.path / SOURCE) or {}

    def described(self):
        """Settings and capabilities the sandbox reported when it was installed."""
        return _read(self.path / DESCRIPTION)

    def summary(self):
        return {"id": self.id, "name": self.name, "description": self.description, "author": self.author,
                "version": self.version, "homepage": self.homepage, "bundled": self.bundled,
                "source": self.source()}


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_manifest(folder, bundled=False):
    """Manifest for a plugin folder; raises ValueError with a fixable message."""
    folder = Path(folder)
    raw = _read(folder / MANIFEST)
    if raw is None:
        raise ValueError(f"{folder.name}: missing or unreadable {MANIFEST}")
    plugin_id = raw.get("id")
    if not isinstance(plugin_id, str) or not NAME.fullmatch(plugin_id):
        raise ValueError(f"{folder.name}: id must be lowercase letters, digits and underscores, starting with a letter")
    entry = raw.get("entry", "plugin.py")
    if not isinstance(entry, str) or not ENTRY.fullmatch(entry) or not (folder / entry).is_file():
        raise ValueError(f"{plugin_id}: entry must name a Python file in the plugin folder")
    text = {key: str(raw.get(key) or "")[:limit] for key, limit in
            (("name", 60), ("description", 300), ("author", 80), ("version", 32), ("homepage", 300))}
    return Manifest(plugin_id, text["name"] or plugin_id, folder, entry, text["description"], text["author"],
                    text["version"], text["homepage"], bundled,
                    {key: value for key, value in raw.items() if key not in {"id", "entry", *text}})


def scan(directory, bundled=False):
    """({id: Manifest}, {folder name: error}) for every plugin folder in a directory."""
    found, errors = {}, {}
    directory = Path(directory)
    if not directory.is_dir():
        return found, errors
    for folder in sorted(path for path in directory.iterdir() if path.is_dir() and not path.name.startswith(".")):
        if not (folder / MANIFEST).exists():
            continue
        try:
            manifest = read_manifest(folder, bundled)
        except ValueError as exc:
            errors[folder.name] = str(exc)
            continue
        if manifest.id in found:
            errors[folder.name] = f"{manifest.id}: another folder already uses this id"
            continue
        found[manifest.id] = manifest
    return found, errors
