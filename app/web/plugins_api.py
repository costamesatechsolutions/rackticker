"""Plugin management API: list, switch on/off, install from GitHub, upload, update, remove.

Everything here takes effect immediately, without restarting RackTicker.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
from pathlib import Path
import time

import aiohttp
from aiohttp import web

from app.core.config import DEFAULT_CONFIG, MODULES
from app.core.installer import InstallError, Installer

log = logging.getLogger("plugins")
MANAGER = web.AppKey("plugin_manager", object) if hasattr(web, "AppKey") else "plugin_manager"
COMMUNITY_URL = "https://raw.githubusercontent.com/costamesatechsolutions/rackticker/main/community/index.json"
COMMUNITY_LOCAL = Path(__file__).resolve().parents[2] / "community" / "index.json"
MAX_UPLOAD = 28 * 1024 * 1024  # a 20 MB zip, base64 encoded
_community = {"at": 0.0, "data": None}


def add_routes(app, runtime_key, store_key):
    lock = asyncio.Lock()

    def parts(request):
        return request.app[runtime_key], request.app[store_key], request.app[MANAGER]

    async def listing(request):
        runtime, _, manager = parts(request)
        return web.json_response(describe_all(runtime, manager))

    async def action(request):
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object")
        name, verb = body.get("id"), body.get("action")
        runtime, store, manager = parts(request)
        if name not in manager.known():
            raise ValueError("Unknown plugin")
        async with lock:
            if verb == "enable":
                await enable(runtime, store, manager, name)
            elif verb == "disable":
                await disable(runtime, store, name)
            elif verb == "restart":
                host = runtime.sandboxes.get(name)
                if not host:
                    raise ValueError("Only installed plugins run in their own process")
                await host.restart()
            elif verb == "remove":
                if name not in manager.installed:
                    raise ValueError("Built-in plugins can be switched off but not removed")
                await disable(runtime, store, name, forget=True)
                installer(runtime, manager).remove(name)
                manager.rescan()
            elif verb == "update":
                source = manager.installed[name].source() if name in manager.installed else {}
                if source.get("kind") != "github":
                    raise ValueError("Only plugins installed from GitHub can update themselves")
                await install(runtime, store, manager, lambda session: installer(runtime, manager).from_github(
                    source["url"], session))
            else:
                raise ValueError("Unknown plugin action")
        return web.json_response(describe_all(runtime, manager))

    async def install_github(request):
        body = await request.json()
        url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(url, str):
            raise ValueError("Send {\"url\": \"https://github.com/...\"}")
        runtime, store, manager = parts(request)
        async with lock:
            manifest = await install(runtime, store, manager,
                                     lambda session: installer(runtime, manager).from_github(url, session))
        return web.json_response({"installed": manifest.id, **describe_all(runtime, manager)})

    async def upload(request):
        """Developer push: {"zip": base64}. Off unless Settings allows uploads."""
        runtime, store, manager = parts(request)
        if not runtime.config.get("plugin_uploads"):
            raise web.HTTPForbidden(text=json.dumps({"error": "Turn on 'Allow plugin uploads' in Settings first"}),
                                    content_type="application/json")
        raw = bytearray()
        async for chunk in request.content.iter_chunked(65536):
            raw += chunk
            if len(raw) > MAX_UPLOAD:
                raise ValueError("Upload is larger than 20 MB")
        body = json.loads(raw)
        try:
            data = base64.b64decode(body["zip"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Send {\"zip\": \"<base64 zip>\"}") from exc
        source = {"kind": "upload", "from": request.remote or "", "note": str(body.get("note") or "")[:120]}
        async with lock:
            manifest = await install(runtime, store, manager,
                                     lambda session: installer(runtime, manager).from_zip(data, source))
        return web.json_response({"installed": manifest.id, **describe_all(runtime, manager)})

    async def updates(request):
        """For each GitHub-installed plugin: is a newer commit available?"""
        runtime, _, manager = parts(request)
        result = {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            for name, manifest in manager.installed.items():
                source = manifest.source()
                if source.get("kind") != "github":
                    continue
                try:
                    latest = await Installer.latest_commit(session, source["owner"], source["repo"], source["ref"])
                    result[name] = {"current": source.get("commit"), "latest": latest,
                                    "update": latest != source.get("commit")}
                except (aiohttp.ClientError, asyncio.TimeoutError, InstallError, KeyError) as exc:
                    result[name] = {"error": str(exc) or type(exc).__name__}
        return web.json_response(result)

    async def community(request):
        return web.json_response(await community_index())

    app.add_routes([web.get("/api/plugins", listing), web.post("/api/plugins", action),
                    web.post("/api/plugins/install", install_github), web.post("/api/plugins/upload", upload),
                    web.get("/api/plugins/updates", updates), web.get("/api/plugins/community", community)])


def installer(runtime, manager):
    reserved = set(MODULES) | set(manager.bundled) | set(manager.packages)
    return Installer(manager.installed_dir, reserved)


def enabled_list(runtime, manager):
    chosen = runtime.config.get("enabled_plugins")
    return list(chosen) if isinstance(chosen, list) else manager.default_enabled()


async def enable(runtime, store, manager, name):
    status = manager.register(runtime.registry, name)
    if status["state"] != "loaded":
        raise ValueError(f"{name} could not be loaded: {status['error']}")
    raw = copy.deepcopy(runtime.config)
    raw["enabled_plugins"] = list(dict.fromkeys(enabled_list(runtime, manager) + [name]))
    plugin = runtime.registry.plugins[name]
    # Switching a screen on is asking to see it: add it to the playlist once.
    if plugin.module and not any(entry["module"] == name for entry in raw["playlist"]) and len(raw["playlist"]) < 32:
        ids = {entry["id"] for entry in raw["playlist"]}
        entry_id = name
        while entry_id in ids:
            entry_id += "-2"
        raw["playlist"].append({"id": entry_id, "module": name, "duration": 12, "enabled": True, "mode": "normal"})
    runtime.apply_config(store.save(raw))
    await runtime.add_plugin(name)


async def disable(runtime, store, name, forget=False):
    await runtime.remove_plugin(name)
    runtime.registry.unregister(name)
    runtime.registry.status.pop(name, None)
    raw = copy.deepcopy(runtime.config)
    manager_default = raw.get("enabled_plugins")
    enabled = manager_default if isinstance(manager_default, list) else None
    if enabled is None:
        enabled = [plugin for plugin in runtime.registry.plugins] + [name]
    raw["enabled_plugins"] = [plugin for plugin in enabled if plugin != name]
    if forget:  # Removing a plugin removes its settings and playlist entries too.
        raw["plugins"].pop(name, None)
        raw["modules"].pop(name, None)
        raw["playlist"] = [entry for entry in raw["playlist"] if entry["module"] != name] or \
            copy.deepcopy(DEFAULT_CONFIG["playlist"][:1])
    runtime.apply_config(store.save(raw))


async def install(runtime, store, manager, fetch):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        try:
            manifest = await fetch(session)
        except aiohttp.ClientError as exc:
            raise ValueError(f"Download failed: {exc}") from exc
    manager.rescan()
    name = manifest.id
    if name in runtime.registry.plugins:  # An update: reload with the new code and settings.
        await runtime.remove_plugin(name)
        runtime.registry.unregister(name)
        status = manager.register(runtime.registry, name)
        if status["state"] != "loaded":
            raise ValueError(f"{name} updated but failed to load: {status['error']}")
        runtime.apply_config(store.save(copy.deepcopy(runtime.config)))
        await runtime.add_plugin(name)
    else:
        await enable(runtime, store, manager, name)
    runtime.record("plugins", f"installed {name} {manifest.version}".strip())
    return manifest


def describe_all(runtime, manager):
    enabled = set(enabled_list(runtime, manager))
    state = runtime.state()["plugins"]
    items = []
    for name, kind in sorted(manager.known().items(), key=lambda item: (item[1] != "installed", item[0])):
        summary = manager.describe_manifest(name)
        plugin = runtime.registry.plugins.get(name)
        items.append({**summary, "kind": kind, "enabled": name in enabled,
                      "label": plugin.label if plugin else summary.get("name"),
                      "screen": bool(plugin and plugin.module), "status": state.get(name)})
    return {"plugins": items, "errors": manager.errors, "uploads": bool(runtime.config.get("plugin_uploads"))}


async def community_index():
    """The community list from GitHub (cached 10 minutes), or the copy shipped with this version."""
    if _community["data"] is not None and time.monotonic() - _community["at"] < 600:
        return _community["data"]
    data = None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async with session.get(COMMUNITY_URL) as response:
                response.raise_for_status()
                data = json.loads(await response.text())
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        try:
            data = json.loads(COMMUNITY_LOCAL.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"plugins": []}
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        data = {"plugins": []}
    _community.update(at=time.monotonic(), data=data)
    return data
