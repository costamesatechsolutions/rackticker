"""An optional password for the control page and its API.

Off until someone sets one in Settings. Then the browser asks once (HTTP Basic,
any user name) and remembers it. Only a salted PBKDF2 hash is stored, in
DATA/access.json beside the configuration, and it is never sent to the page. A
forgotten password is cleared by deleting that file (or a factory reset of the SD
card). Home Assistant talks MQTT, so it is not affected.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

from aiohttp import web

ITERATIONS = 120_000
FILE = web.AppKey("access_file", Path) if hasattr(web, "AppKey") else "access_file"
ACCEPTED = web.AppKey("access_accepted", set) if hasattr(web, "AppKey") else "access_accepted"


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


def stored(app):
    try:
        return json.loads(app[FILE].read_text())
    except (OSError, ValueError):
        return None


def set_password(app, password):
    path = app[FILE]
    app[ACCEPTED].clear()
    if not password:
        path.unlink(missing_ok=True)
        return
    if len(password) < 4 or len(password) > 128:
        raise ValueError("Use a password of 4 to 128 characters")
    salt = os.urandom(16)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"salt": salt.hex(), "hash": _hash(password, salt)}))
    temporary.chmod(0o600)
    temporary.replace(path)


def allowed(app, header):
    record = stored(app)
    if not record:
        return True
    if not header.startswith("Basic "):
        return False
    if header in app[ACCEPTED]:          # hashing is slow on a Pi: remember a good header
        return True
    try:
        _, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    good = hmac.compare_digest(_hash(password, bytes.fromhex(record["salt"])), record["hash"])
    if good:
        app[ACCEPTED].add(header)
    return good


@web.middleware
async def guard(request, handler):
    if not allowed(request.app, request.headers.get("Authorization", "")):
        return web.Response(status=401, text="RackTicker: password required",
                            headers={"WWW-Authenticate": 'Basic realm="RackTicker", charset="UTF-8"'})
    return await handler(request)


async def access_post(request):
    body = await request.json()
    set_password(request.app, str((body or {}).get("password") or ""))
    return web.json_response({"password_set": bool(stored(request.app))})


async def access_get(request):
    return web.json_response({"password_set": bool(stored(request.app))})


def setup(app, config_path):
    app[FILE] = Path(config_path).resolve().parent / "access.json"
    app[ACCEPTED] = set()
    app.add_routes([web.get("/api/access", access_get), web.post("/api/access", access_post)])
