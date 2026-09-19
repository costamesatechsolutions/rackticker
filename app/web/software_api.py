"""Software: the installed version, updates from GitHub, and factory reset.

The web app never installs anything itself: it runs unprivileged. It drops a
request ({"commit": sha}) into DATA/update/, and the root-owned updater
(deploy/rackticker-update.py, started by systemd) checks the commit is on the
official main branch, installs it, and rolls back if it does not come up healthy.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import time

import aiohttp
from aiohttp import web

from app import __version__
from app.core.config import starter

REPO = "costamesatechsolutions/rackticker"
INSTALL = Path(__file__).resolve().parents[2]      # /opt/rackticker/current on a Pi
LATEST_SECONDS = 600
_latest = {"at": 0.0, "value": None}
KEYS = {}   # runtime, store and config path keys, set by add_routes


def revision():
    try:
        return (INSTALL / "REVISION").read_text().strip()
    except OSError:
        return ""


def update_dir(request):
    return request.app[KEYS["path"]].resolve().parent / "update"


def updatable(request):
    """Only a Pi installed by install-release.sh can update itself from the page."""
    folder = update_dir(request)
    return bool(revision()) and folder.is_dir() and os.access(folder, os.W_OK)


async def latest(force=False):
    if not force and _latest["value"] and time.monotonic() - _latest["at"] < LATEST_SECONDS:
        return _latest["value"]
    headers = {"User-Agent": f"RackTicker/{__version__} (+https://github.com/{REPO})",
               "Accept": "application/vnd.github+json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), headers=headers) as session:
        # Devices update to the newest published release, not every push to main:
        # a release is a version someone chose to ship. Without releases, main.
        ref, title = "main", ""
        async with session.get(f"https://api.github.com/repos/{REPO}/releases/latest") as response:
            if response.status == 200:
                release = await response.json(content_type=None)
                ref, title = release.get("tag_name") or "main", release.get("name") or release.get("tag_name") or ""
        async with session.get(f"https://api.github.com/repos/{REPO}/commits/{ref}") as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
    commit = payload.get("commit") or {}
    value = {"commit": payload.get("sha", ""), "release": title,
             "message": title or str(commit.get("message") or "").split("\n")[0][:120],
             "date": (commit.get("committer") or {}).get("date")}
    _latest.update(at=time.monotonic(), value=value)
    return value


def read_status(request):
    try:
        status = json.loads((update_dir(request) / "status.json").read_text())
    except (OSError, ValueError):
        return None
    # A request waiting for the updater to pick it up is "queued".
    return status


async def software_get(request):
    installed = revision()
    body = {"version": __version__, "revision": installed, "updatable": updatable(request),
            "status": read_status(request), "latest": None, "update_available": False,
            "queued": (update_dir(request) / "request.json").exists()}
    try:
        body["latest"] = await latest()
        body["update_available"] = bool(installed and body["latest"]["commit"]
                                        and body["latest"]["commit"] != installed)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        body["error"] = f"Could not check GitHub: {exc}"
    return web.json_response(body)


async def software_update(request):
    if not updatable(request):
        raise ValueError("This RackTicker was not installed by the Pi installer, so it updates with git or "
                         "tools/deploy_pi.sh instead")
    body = await request.json()
    # Always ask GitHub again: a cached answer from minutes ago once installed an
    # older release over a newer one.
    newest = await latest(force=True)
    commit = str((body or {}).get("commit") or newest["commit"]).lower()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("Expected a full commit id")
    folder = update_dir(request)
    temporary = folder / "request.tmp"
    temporary.write_text(json.dumps({"commit": commit, "asked_at": int(time.time())}))
    temporary.replace(folder / "request.json")   # the updater starts when this appears
    return web.json_response({"queued": True, "commit": commit})


async def factory_reset(request):
    """Back to a fresh install: default settings and playlist, installed plugins removed.
    The old settings are kept beside the new ones, and Wi-Fi is not touched."""
    from app.web import plugins_api
    runtime, store = request.app[KEYS["runtime"]], request.app[KEYS["store"]]
    manager = request.app[plugins_api.MANAGER]
    path = request.app[KEYS["path"]]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.before-reset-{stamp}"))
    for name in list(manager.installed):
        await plugins_api.disable(runtime, store, name, forget=True)
        plugins_api.installer(runtime, manager).remove(name)
    shutil.rmtree(manager.plugin_data, ignore_errors=True)
    manager.rescan()
    config = store.save(starter(runtime.registry))
    runtime.apply_config(config)
    runtime.home_assistant.reconfigure(config)
    # Under systemd, start afresh so every bundled plugin loads as on first boot.
    if os.environ.get("INVOCATION_ID"):
        asyncio.get_running_loop().call_later(1.5, os._exit, 75)
    return web.json_response({"reset": True, "backup": f"{path.name}.before-reset-{stamp}"})


CONFIG_PATH = web.AppKey("config_path", Path) if hasattr(web, "AppKey") else "config_path"


def add_routes(app, runtime_key, store_key, config_path):
    KEYS.update(runtime=runtime_key, store=store_key, path=CONFIG_PATH)
    app[CONFIG_PATH] = Path(config_path)
    app.add_routes([web.get("/api/software", software_get), web.post("/api/software/update", software_update),
                    web.post("/api/software/reset", factory_reset)])
