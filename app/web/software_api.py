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


def reset_choices(request):
    """The resets this install can actually carry out, for the page to offer."""
    folder = request.app[KEYS["path"]].resolve().parent / "reset"
    rooted = folder.is_dir() and os.access(folder, os.W_OK)
    return [{"scope": scope, "what": what} for scope, what in RESET_SCOPES.items()
            if rooted or scope == "settings"]


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
            "timezone": current_timezone(), "resets": reset_choices(request),
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


def current_timezone():
    try:
        return Path("/etc/timezone").read_text().strip() or time.strftime("%Z")
    except OSError:
        return datetime.now().astimezone().tzname() or ""


async def timezone_post(request):
    """Ask the root helper to set the clock's time zone; the display restarts after."""
    from zoneinfo import available_timezones
    body = await request.json()
    wanted = str((body or {}).get("timezone") or "").strip()
    if wanted not in available_timezones():
        raise ValueError("Unknown time zone")
    folder = update_dir(request).parent
    if not os.access(folder, os.W_OK):
        raise ValueError("This RackTicker cannot set its own time zone (it is not a Pi install)")
    temporary = folder / "timezone.tmp"
    temporary.write_text(wanted)
    temporary.replace(folder / "timezone")
    return web.json_response({"timezone": wanted, "applying": True})


async def timezones_get(request):
    from zoneinfo import available_timezones
    return web.json_response({"current": current_timezone(), "zones": sorted(available_timezones())})


RESET_SCOPES = {
    "settings": "Settings, playlist and installed plugins. Wi-Fi and the password stay.",
    "network": "Forget Wi-Fi and open the setup network, so it can join somewhere else.",
    "everything": "Settings, plugins, the password and Wi-Fi: the Pi as it arrived.",
    "ship": "Everything, plus this device's identity and logs. It powers off, ready to pass on.",
}


def reset_dir(request):
    return request.app[KEYS["path"]].resolve().parent / "reset"


def ask_root_to_reset(request, scope):
    """The web app is unprivileged: leave the request for the root-owned helper."""
    folder = reset_dir(request)
    if not (folder.is_dir() and os.access(folder, os.W_OK)):
        raise ValueError("This RackTicker cannot reset its own Wi-Fi (it is not a Pi install)")
    temporary = folder / "request.tmp"
    temporary.write_text(json.dumps({"scope": scope, "asked_at": int(time.time())}))
    temporary.replace(folder / "request.json")   # the helper starts when this appears


async def factory_reset(request):
    """Back to a fresh install. How far back depends on the scope that was asked for.

    `settings` is this process's own job. Anything touching Wi-Fi, the device's
    identity or the system log needs root, so it goes to deploy/rackticker-reset.py."""
    from app.web import plugins_api
    body = await request.json() if request.can_read_body else {}
    scope = str((body or {}).get("scope") or "settings").lower()
    if scope not in RESET_SCOPES:
        raise ValueError("Unknown reset")
    if scope != "settings":
        ask_root_to_reset(request, scope)
        if scope == "network":
            return web.json_response({"reset": True, "scope": scope,
                                      "note": "Forgetting Wi-Fi; the setup network opens in a moment."})
        # The helper clears the settings too, and restarts or powers off when it is done.
        return web.json_response({"reset": True, "scope": scope, "note": RESET_SCOPES[scope]})
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
    return web.json_response({"reset": True, "scope": "settings",
                              "backup": f"{path.name}.before-reset-{stamp}"})


CONFIG_PATH = web.AppKey("config_path", Path) if hasattr(web, "AppKey") else "config_path"


def add_routes(app, runtime_key, store_key, config_path):
    KEYS.update(runtime=runtime_key, store=store_key, path=CONFIG_PATH)
    app[CONFIG_PATH] = Path(config_path)
    app.add_routes([web.get("/api/software", software_get), web.post("/api/software/update", software_update),
                    web.post("/api/software/reset", factory_reset),
                    web.get("/api/timezones", timezones_get), web.post("/api/timezone", timezone_post)])
