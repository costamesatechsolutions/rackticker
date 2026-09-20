"""Find lettering that is cut off: every text draw on every screen, on live data,
checked against the image it lands in. Letters whose inked rows fall below the
bottom or above the top (lowercase descenders are the usual victims) are listed
with the screen that drew them.

    python -m tools.clip_audit [seconds-per-screen] [screen ...]

Brief cut-offs while text slides in or out are normal; the report counts how many
frames each one lasted so the permanent ones stand out.
"""
import asyncio
from collections import Counter
from dataclasses import replace
import importlib.util
import inspect
import sys
import time
from pathlib import Path

from app.core import fonts

ROOT = Path(__file__).resolve().parents[1]
FOUND = Counter()
SCREEN = ["?"]


def _caller():
    for frame in inspect.stack()[2:8]:
        name = Path(frame.filename).name
        if name not in ("fonts.py", "clip_audit.py", "fx.py", "story.py"):
            return f"{name}:{frame.lineno}"
    return "?"


def _check(image, text, x, y, mask):
    box = mask.getbbox()
    if not box or x + box[2] <= 0 or x + box[0] >= image.width:
        return  # nothing inked, or off to the side (a crawl entering or leaving)
    low, high = y + box[3] - 1, y + box[1]
    if low >= image.height or high < 0:
        if high >= image.height or low < 0:
            return  # entirely outside: not cut, just not shown
        where = "bottom" if low >= image.height else "top"
        FOUND[(SCREEN[0], _caller(), where, str(text)[:24], image.height)] += 1


def patch():
    text_mask, tiny_mask = fonts.text_mask, fonts.tiny_mask

    def draw_text(frame, text, x, y, color=(245, 245, 235), scale=1, smooth=False, mixed=False):
        mask = text_mask(str(text), scale, smooth, mixed)
        _check(frame, text, int(x), int(y), mask)
        frame.paste(color, (int(x), int(y), int(x) + mask.width, int(y) + mask.height), mask)

    def draw_tiny(frame, text, x, y, color=(245, 245, 235)):
        mask = tiny_mask(str(text))
        _check(frame, text, int(x), int(y), mask)
        frame.paste(color, (int(x), int(y), int(x) + mask.width, int(y) + mask.height), mask)
    fonts.draw_text, fonts.draw_tiny = draw_text, draw_tiny
    if "rackticker" in sys.modules:  # plugins import the drawing functions from here
        sys.modules["rackticker"].draw_text, sys.modules["rackticker"].draw_tiny = draw_text, draw_tiny


async def main():
    patch()
    # Imported after patching so every screen picks up the checking versions.
    from app.core.config import validate_config
    from app.core.plugins import PluginRegistry
    from app.core.runtime import Runtime
    from app.outputs.browser import BrowserSink
    from tools.render_demo import PLUGINS, community
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 40
    wanted = sys.argv[2:]
    paths = {**PLUGINS, "traffic": "plugins/traffic/rackticker_traffic.py",
             "url_data": "plugins/url-data/rackticker_url_data.py",
             **community("quakes", "surf", "now_playing", "onboard")}
    registry = PluginRegistry()
    for name, path in paths.items():
        spec = importlib.util.spec_from_file_location(f"audit_{name}", ROOT / path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        registry.register(module.plugin)
    home = {"latitude": 40.758, "longitude": -73.9855}
    config = validate_config({"location": {**home, "name": "New York"},
                              "plugins": {"weather": home}}, registry)
    runtime = Runtime(config, BrowserSink(), registry)
    for _ in range(2):
        for name in runtime.providers:
            try:
                await asyncio.wait_for(runtime.refresh_provider(name), 20)
            except Exception as exc:
                print(f"{name}: {exc}")
    screens = wanted or [name for name in runtime.modules if name not in ("message", "test_pattern")]
    for name in screens:
        module = runtime.modules.get(name)
        if module is None:
            continue
        SCREEN[0] = name
        context = runtime.context()
        for step in range(int(30 * seconds)):
            # A new scene every 20 s, so screens that change station or card per visit show more.
            context = replace(runtime.context(), animation_time=step / 30 % 20, scene=1000 + step // 600)
            try:
                module.render(context)
            except Exception as exc:
                print(f"{name}: render failed: {exc}")
                break
    await runtime.close()
    if not FOUND:
        print("No cut-off lettering found.")
    for (screen, where, edge, text, height), frames in sorted(FOUND.items()):
        print(f"{screen:14} {where:32} {edge:6} {frames:5} frames  h={height:<3} {text!r}")


if __name__ == "__main__":
    asyncio.run(main())
