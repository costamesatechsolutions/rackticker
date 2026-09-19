"""Plugin developer tools.

    python -m app.dev new my_plugin            scaffold a plugin folder
    python -m app.dev check ./my_plugin        render it headless: contact sheet, GIF, timings, problems
    python -m app.dev preview ./my_plugin      live preview in the browser, reloading on every save
    python -m app.dev push ./my_plugin --to rackticker.local:8081
                                               install or update it on a device (uploads must be allowed)

`check` is built for AI coding agents as much as people: it prints a short
report and writes images an agent can look at, so a plugin can be iterated
without a panel on the desk.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples" / "starter"
NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


# --- new -----------------------------------------------------------------------

def new(args):
    name = args.name
    if not NAME.fullmatch(name):
        sys.exit("Plugin ids are lowercase letters, digits and underscores, starting with a letter (e.g. surf_report)")
    target = Path(args.dir) / name
    if target.exists():
        sys.exit(f"{target} already exists")
    shutil.copytree(TEMPLATE, target, ignore=shutil.ignore_patterns("__pycache__"))
    label = name.replace("_", " ").title()
    for path in target.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".json", ".md"):
            text = path.read_text().replace("starter_plugin", name).replace("Starter plugin", label)
            path.write_text(text)
    print(f"Created {target}/\n")
    print(f"  python -m app.dev check {target}     render it and look at {target}/preview.png")
    print(f"  python -m app.dev preview {target}   live in the browser, reloads on save")
    print(f"\nAI agents: {target}/AGENTS.md has the rules.")


# --- check -----------------------------------------------------------------------

def load(folder):
    import importlib.util
    from app.core.manifest import read_manifest
    manifest = read_manifest(folder)
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location(manifest.module_name, folder / manifest.entry)
    module = importlib.util.module_from_spec(spec)
    sys.modules[manifest.module_name] = module
    spec.loader.exec_module(module)
    plugin = getattr(module, "plugin", None)
    return manifest, plugin


def check(args):
    folder = Path(args.path).resolve()
    problems, notes = [], []
    try:  # watch every text draw for letters cut off at the top or bottom
        from tools.clip_audit import FOUND as clipped, SCREEN, patch
        patch()
    except ImportError:
        clipped, SCREEN = None, None
    try:
        manifest, plugin = load(folder)
    except Exception as exc:
        sys.exit(f"FAIL  could not load: {type(exc).__name__}: {exc}")
    from app.core.config import validate_config
    from app.core.models import Message, Snapshot, SystemStatus
    from app.core.plugins import PluginRegistry
    from app.core.renderer import validate_frame
    from app.modules.base import RenderContext
    from app.plugin_api import Plugin, PluginContext
    if not isinstance(plugin, Plugin):
        sys.exit(f"FAIL  {manifest.entry} must define plugin = Plugin(...)")
    registry = PluginRegistry()
    try:
        registry.register(plugin)
    except ValueError as exc:
        sys.exit(f"FAIL  plugin rejected: {exc}")
    if plugin.name != manifest.id:
        problems.append(f"Plugin name {plugin.name!r} differs from plugin.json id {manifest.id!r}")
    config = validate_config({"plugins": {plugin.name: json.loads(args.settings) if args.settings else {}}}, registry)
    settings = config["plugins"][plugin.name]
    events = []
    snapshots = {}

    async def fetch():
        if not plugin.provider:
            return
        provider = plugin.provider(PluginContext(plugin.name, lambda: settings, lambda *a: events.append(a) or True))
        for attempt in range(args.fetches):
            started = time.perf_counter()
            try:
                snapshot = await asyncio.wait_for(provider.fetch(), 6)
            except Exception as exc:
                problems.append(f"fetch {attempt + 1} failed: {type(exc).__name__}: {exc}")
                break
            spent = time.perf_counter() - started
            notes.append(f"fetch {attempt + 1}: {spent * 1000:.0f} ms")
            if spent > 5:
                problems.append("fetch took over 5 s; RackTicker gives providers 6 s")
            if not isinstance(snapshot, Snapshot):
                problems.append("fetch() must return a Snapshot")
                break
            snapshots[plugin.provider_for or plugin.name] = snapshot
        await provider.close()

    asyncio.run(fetch())
    if not plugin.module:
        notes.append("no screen (module) to render")
        report(manifest, problems, notes)
        return
    module = plugin.module()
    fps = 30
    frames, times, holds, slow = [], [], 0, 0
    now = __import__("datetime").datetime.now().astimezone()
    for index in range(int(args.seconds * fps)):
        t = index / fps
        context = RenderContext(now, t, config, snapshots, Message("", ""), SystemStatus(), 1)
        try:
            if index == 0 and not module.available(context):
                notes.append("available() is False with this data: the playlist would skip the screen")
            holds += bool(module.hold(context))
            started = time.perf_counter()
            frame = validate_frame(module.render(context))
            spent = time.perf_counter() - started
        except Exception as exc:
            problems.append(f"render at t={t:.2f}s raised {type(exc).__name__}: {exc}")
            break
        times.append(spent)
        slow += spent > .025
        frames.append(frame)
        interval = module.refresh_interval(context)
        if index == 0 and interval == float("inf"):
            notes.append("static screen (refresh_interval is inf): redrawn only when data changes")
    if frames:
        ordered = sorted(times)
        notes.append(f"render: median {ordered[len(ordered) // 2] * 1000:.1f} ms, worst {ordered[-1] * 1000:.1f} ms "
                     f"(the Pi 3 is ~6× slower than a laptop)")
        if slow:
            problems.append(f"{slow} frames took over 25 ms here; on a Pi they will stutter")
        if not any(frame.getbbox() for frame in frames):
            problems.append("every frame is completely black")
        if holds == len(frames):
            notes.append("hold() stayed True the whole run: the playlist waits (up to its limit) for your story")
        out = Path(args.out or folder)
        sheet(frames, out / "preview.png")
        gif(frames, out / "preview.gif")
        notes.append(f"wrote {out / 'preview.png'} (contact sheet) and {out / 'preview.gif'} (animation)")
    if events:
        notes.append(f"asked to take over the display {len(events)} time(s)")
    # Cut off for a second or more: not a slide-in, a layout that doesn't fit.
    for (_, where, edge, text, height), count in sorted((clipped or {}).items()):
        if count >= fps:
            problems.append(f"{text!r} is cut off at the {edge} ({where}, drawn into an image {height} px tall, "
                            f"{count} frames): move it or leave room for letters that hang below the line")
    report(manifest, problems, notes)


def report(manifest, problems, notes):
    print(f"{manifest.name} ({manifest.id})")
    for note in notes:
        print(f"  ·  {note}")
    for problem in problems:
        print(f"  ✗  {problem}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    if problems:
        sys.exit(1)


def enlarge(frame, scale=4):
    """The frame as LEDs: round dots with dark gaps, like the real panel."""
    from PIL import Image, ImageDraw
    mask = Image.new("L", (scale, scale))
    ImageDraw.Draw(mask).ellipse((0, 0, scale - 1, scale - 1), fill=255)
    tile = Image.new("L", (128 * scale, 32 * scale))
    for y in range(32):
        for x in range(128):
            tile.paste(mask, (x * scale, y * scale))
    big = frame.resize((128 * scale, 32 * scale), Image.Resampling.NEAREST)
    return Image.composite(big, Image.new("RGB", big.size, (14, 14, 12)), tile)


def sheet(frames, path, count=8):
    from PIL import Image, ImageDraw
    indices = [round(i * (len(frames) - 1) / max(1, count - 1)) for i in range(min(count, len(frames)))]
    scale, gap = 4, 18
    image = Image.new("RGB", (2 * (128 * scale + 8) + 8, ((len(indices) + 1) // 2) * (32 * scale + gap) + 4), (36, 34, 31))
    draw = ImageDraw.Draw(image)
    for index, frame_index in enumerate(indices):
        frame = frames[frame_index]
        x, y = 8 + (index % 2) * (128 * scale + 8), (index // 2) * (32 * scale + gap)
        position = frame_index / 30
        draw.text((x, y + 3), f"t = {position:.1f} s", fill=(200, 196, 188))
        image.paste(enlarge(frame, scale), (x, y + gap - 4))
    image.save(path)


def gif(frames, path):
    small = [frame.resize((256, 64)) for frame in frames[::3]]
    small[0].save(path, save_all=True, append_images=small[1:], duration=100, loop=0)


# --- preview ---------------------------------------------------------------------

def preview(args):
    folder = Path(args.path).resolve()
    from aiohttp import web
    from app.core.installer import Installer
    from app.web import plugins_api
    from app.web.server import RUNTIME, STORE, create_app
    work = Path(tempfile.mkdtemp(prefix="rackticker-preview-"))
    installer = Installer(work / "plugins", reserved={"clock"})
    manifest = asyncio.run(installer.from_folder(folder))
    config = work / "config.json"
    config.write_text(json.dumps({"enabled_plugins": [manifest.id], "playlist": [
        {"id": manifest.id, "module": manifest.id, "duration": 3600, "enabled": True}]}))
    app = create_app(config, bundled_by_default=False)

    def stamp():
        return max((path.stat().st_mtime for path in folder.rglob("*") if path.is_file()
                    and "__pycache__" not in path.parts and not path.name.startswith("preview.")), default=0)

    async def watch(app):
        runtime, store, manager = app[RUNTIME], app[STORE], app[plugins_api.MANAGER]
        seen = stamp()
        while True:
            await asyncio.sleep(1)
            current = stamp()
            if current == seen:
                continue
            seen = current
            try:
                await plugins_api.install(runtime, store, manager, lambda _session: installer.from_folder(folder))
                runtime.preview(manifest.id)
                print(time.strftime("%H:%M:%S"), "reloaded")
            except Exception as exc:
                print(time.strftime("%H:%M:%S"), "reload failed:", exc)

    async def begin(app):
        task = asyncio.create_task(watch(app))
        yield
        task.cancel()

    app.cleanup_ctx.append(begin)
    print(f"Previewing {manifest.name} at http://127.0.0.1:{args.port} — edits reload automatically. Ctrl+C to stop.")
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None, print=None)


# --- push ------------------------------------------------------------------------

def push(args):
    import urllib.error
    import urllib.request
    folder = Path(args.path).resolve()
    from app.core.manifest import read_manifest
    manifest = read_manifest(folder)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if path.is_file() and not ({"__pycache__", ".git"} & set(relative.parts)) and \
                    not path.name.startswith(("preview.", ".")):
                archive.write(path, f"{manifest.id}/{relative}")
    host = args.to if ":" in args.to else f"{args.to}:8080"
    url = f"http://{host}/api/plugins/upload"
    body = json.dumps({"zip": base64.b64encode(data.getvalue()).decode(), "note": manifest.version}).encode()
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-RackTicker": "1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            json.load(response)
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read() or b"{}").get("error") or exc.reason
        sys.exit(f"Upload refused: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach {host}: {exc.reason}")
    print(f"Installed {manifest.name} {manifest.version} on {host}. It is on and in the playlist.")


def main():
    parser = argparse.ArgumentParser(prog="python -m app.dev", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("new", help="scaffold a plugin folder")
    command.add_argument("name")
    command.add_argument("--dir", default=".")
    command.set_defaults(run=new)
    command = commands.add_parser("check", help="render a plugin headless and report problems")
    command.add_argument("path")
    command.add_argument("--seconds", type=float, default=12)
    command.add_argument("--fetches", type=int, default=1, help="provider fetches before rendering")
    command.add_argument("--settings", help="JSON settings to try, e.g. '{\"city\": \"Rome\"}'")
    command.add_argument("--out", help="where to write preview.png and preview.gif (default: the plugin folder)")
    command.set_defaults(run=check)
    command = commands.add_parser("preview", help="live preview in a browser, reloading on save")
    command.add_argument("path")
    command.add_argument("--port", type=int, default=8090)
    command.set_defaults(run=preview)
    command = commands.add_parser("push", help="install or update the plugin on a device")
    command.add_argument("path")
    command.add_argument("--to", required=True, help="device address, e.g. rackticker.local:8081")
    command.set_defaults(run=push)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
