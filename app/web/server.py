"""Local control API and a binary framebuffer WebSocket; no frontend build step."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit
import logging

from aiohttp import web
from app.core.config import ConfigError, ConfigStore
from app.core.runtime import Runtime
from app.core.plugin_manager import PluginManager
from app.integrations.home_assistant import HomeAssistant
from app.web import access, lookup_api, plugins_api, software_api
from app.core.source import source_zip
from app.outputs.browser import BrowserSink
from app.outputs.hub75 import Hub75Sink

# aiohttp added typed AppKey after Debian Bookworm's 3.8 release. String keys
# keep the same behavior on the Pi while newer environments retain type hints.
if hasattr(web, "AppKey"):
    RUNTIME = web.AppKey("runtime", Runtime)
    STORE = web.AppKey("config_store", ConfigStore)
    SOCKETS = web.AppKey("sockets", set)
else:  # pragma: no cover - exercised on the Bookworm deployment
    RUNTIME, STORE, SOCKETS = "runtime", "config_store", "sockets"
STATIC = Path(__file__).parent / "static"
log = logging.getLogger("web")


@web.middleware
async def local_api(request, handler):
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.netloc != request.host or parsed.scheme != request.scheme:
            return web.json_response({"error": "Cross-origin access is not allowed"}, status=403)
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if request.headers.get("X-RackTicker") != "1":
            return web.json_response({"error": "X-RackTicker: 1 header required"}, status=403)
        if request.content_type != "application/json":
            return web.json_response({"error": "Send application/json"}, status=415)
    try:
        response = await handler(request)
    except (ConfigError, ValueError, TypeError, KeyError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError:
        log.exception("Configuration or file operation failed")
        return web.json_response({"error": "File operation failed; see the server log"}, status=500)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    return response


async def object_body(request):
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object")
    return body


async def index(request):
    return web.FileResponse(STATIC / "index.html")


SECRET = "\u2022" * 8  # what the page shows instead of a stored password


def secret_fields(registry):
    """(plugin, setting) pairs a plugin marked {"type": "secret"}: tokens and keys."""
    return [(name, key) for name, plugin in registry.plugins.items()
            for key, hint in (plugin.ui or {}).items() if isinstance(hint, dict) and hint.get("type") == "secret"]


def public(config, registry=None):
    """The configuration as the page sees it: stored passwords and tokens are never sent back."""
    shown = copy.deepcopy(config)
    if shown["home_assistant"]["password"]:
        shown["home_assistant"]["password"] = SECRET
    for name, key in secret_fields(registry) if registry else ():
        settings = shown["plugins"].get(name) or {}
        if settings.get(key):
            settings[key] = SECRET
    return shown


async def config_get(request):
    runtime = request.app[RUNTIME]
    return web.json_response(public(runtime.config, runtime.registry))


async def config_put(request):
    runtime = request.app[RUNTIME]
    body = await object_body(request)
    ha = body.get("home_assistant")
    if isinstance(ha, dict) and ha.get("password") == SECRET:
        ha["password"] = runtime.config["home_assistant"]["password"]
    for name, key in secret_fields(runtime.registry):  # unchanged secrets come back masked
        settings = (body.get("plugins") or {}).get(name)
        if isinstance(settings, dict) and settings.get(key) == SECRET:
            settings[key] = runtime.config["plugins"].get(name, {}).get(key, "")
    config = request.app[STORE].save(body)
    runtime.apply_config(config)
    runtime.home_assistant.reconfigure(config)
    return web.json_response(public(config, runtime.registry))


async def state_get(request):
    return web.json_response(request.app[RUNTIME].state())


async def catalog_get(request):
    return web.json_response(request.app[RUNTIME].catalog())


async def source_get(request):
    return web.Response(body=await asyncio.to_thread(source_zip), content_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="rackticker-source.zip"'})


async def control(request):
    body = await object_body(request)
    runtime = request.app[RUNTIME]
    action = body.get("action")
    if action == "preview":
        runtime.preview(body.get("module"))
    elif action == "next":
        runtime.auto_demo = False
        runtime.scheduler.next(runtime.eligible())
    elif action == "pause":
        runtime.auto_demo = False
        runtime.scheduler.paused = True
    elif action == "resume":
        runtime.auto_demo = False
        runtime.scheduler.resume(runtime.eligible())
    elif action == "demo":
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Demo enabled must be boolean")
        runtime.auto_demo = enabled
        runtime.scheduler.paused = False
        if enabled:
            runtime.demo_due = 0
        else:
            runtime.scheduler.resume(runtime.eligible())
    elif action == "power":
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Power enabled must be boolean")
        runtime.set_power(enabled)
    elif action == "brightness":
        value = body.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 100:
            raise ValueError("Brightness must be 1–100")
        raw = copy.deepcopy(runtime.config)
        raw["display"]["brightness"] = round(value)
        runtime.apply_config(request.app[STORE].save(raw))
        runtime.set_power(True)
    elif action == "fault":
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Fault enabled must be boolean")
        runtime.provider_fault = enabled
        async with runtime.provider_lock:
            await asyncio.gather(*(runtime.refresh_provider(name) for name in runtime.providers))
    else:
        raise ValueError("Unknown control action")
    return web.json_response(runtime.state())


async def scenario(request):
    body = await object_body(request)
    interrupt = body.get("interrupt", False)
    if not isinstance(interrupt, bool):
        raise ValueError("Interrupt must be boolean")
    runtime = request.app[RUNTIME]
    if interrupt and runtime.auto_demo:
        runtime.auto_demo = False
        runtime.scheduler.resume(runtime.eligible())
    accepted = await request.app[RUNTIME].scenario(body.get("module"), body.get("scenario"),
                                                 interrupt, body.get("message"))
    return web.json_response({"interrupt_accepted": accepted, "state": request.app[RUNTIME].state()})


async def frame_png(request):
    return web.Response(body=request.app[RUNTIME].png(), content_type="image/png",
                        headers={"Content-Disposition": 'attachment; filename="rackticker-128x32.png"'})


async def sequence_zip(request):
    # Snapshot/encode is small (30 × 128×32); zip contains original pixels and timestamps.
    return web.Response(body=request.app[RUNTIME].sequence_zip(), content_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="rackticker-frames.zip"'})


async def websocket(request):
    sink = request.app[RUNTIME].sink
    if len(sink.listeners) >= 12:
        raise web.HTTPServiceUnavailable(text="Maximum 12 emulator clients")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
    await ws.prepare(request)
    request.app[SOCKETS].add(ws)
    event = sink.subscribe()

    async def send():
        last_frame = last_state = -1
        try:
            while not ws.closed and not sink.closed:
                await event.wait()
                event.clear()
                if last_state != sink.state_revision:
                    last_state = sink.state_revision
                    await asyncio.wait_for(ws.send_json({"type": "state", **sink.state}), 2)
                if last_frame != sink.sequence:
                    last_frame = sink.sequence
                    await asyncio.wait_for(ws.send_bytes(sink.pixels), 2)
        except (ConnectionError, asyncio.TimeoutError, RuntimeError):
            await ws.close()

    sender = asyncio.create_task(send())
    try:
        async for _ in ws:
            pass
    finally:
        sender.cancel()
        with suppress(asyncio.CancelledError):
            await sender
        sink.unsubscribe(event)
        request.app[SOCKETS].discard(ws)
    return ws


def create_app(config_path, enabled_plugins=(), registry=None, output="browser", bundled_by_default=False):
    config_path = Path(config_path)
    manager = PluginManager(config_path.resolve().parent, cli=enabled_plugins, bundled_by_default=bundled_by_default)
    if registry is None:
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        except (OSError, ValueError):
            raw = {}  # ConfigStore.load reports the problem properly below.
        registry = manager.load(manager.enabled(raw))
    store = ConfigStore(config_path, registry)
    runtime = Runtime(store.load(), BrowserSink(), registry, manager)
    if output == "hub75":
        matrix = Hub75Sink()
        matrix.set_brightness(runtime.config["display"]["brightness"])
        runtime.outputs.append(matrix)
    app = web.Application(middlewares=[access.guard, local_api], client_max_size=32 * 1024)
    app[RUNTIME], app[STORE], app[SOCKETS] = runtime, store, set()
    app[plugins_api.MANAGER] = manager
    app.add_routes([
        web.get("/", index), web.get("/api/config", config_get), web.put("/api/config", config_put),
        web.get("/api/state", state_get), web.post("/api/control", control), web.post("/api/scenario", scenario),
        web.get("/api/catalog", catalog_get),
        web.get("/api/source.zip", source_get),
        web.get("/api/frame.png", frame_png), web.get("/api/sequence.zip", sequence_zip), web.get("/ws", websocket),
        web.static("/static", STATIC),
    ])
    plugins_api.add_routes(app, RUNTIME, STORE)
    lookup_api.add_routes(app)
    software_api.add_routes(app, RUNTIME, STORE, config_path)
    access.setup(app, config_path)

    runtime.home_assistant = HomeAssistant(runtime, store)

    async def lifecycle(app):
        await runtime.start()
        runtime.home_assistant.reconfigure(runtime.config)
        yield
        await runtime.home_assistant.close()
        await runtime.close()

    async def shutdown(app):
        await asyncio.gather(*(ws.close(code=1001, message=b"Server shutdown") for ws in list(app[SOCKETS])))

    app.cleanup_ctx.append(lifecycle)
    app.on_shutdown.append(shutdown)
    return app
