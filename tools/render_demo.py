"""Render docs/demo.gif: the real runtime and bundled plugins on live public data,
drawn with an LED-dot look for the README.

Run from the repository root:  python -m tools.render_demo [seconds-per-screen]

Flights and traffic are left out so a demo never reveals where it was made.
"""
import asyncio
import importlib.util
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from app.core.config import validate_config
from app.core.plugins import PluginRegistry
from app.core.runtime import Runtime
from app.outputs.browser import BrowserSink

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = {"finance": "plugins/finance/rackticker_finance.py", "free_sports": "plugins/free-sports/rackticker_free_sports.py",
           "sportsbook": "plugins/sportsbook/rackticker_sportsbook.py",
           "markets": "plugins/prediction-markets/rackticker_markets.py", "news": "plugins/news/rackticker_news.py",
           "weather": "plugins/weather/rackticker_weather.py", "ticker_wall": "plugins/ticker-wall/rackticker_ticker_wall.py",
           "arcade": "plugins/arcade/rackticker_arcade.py", "town": "plugins/pixel-town/rackticker_town.py",
           "f1": "plugins/f1-schedule/rackticker_f1.py", "departures": "community/departures/plugin.py",
           "tanks": "community/tanks/plugin.py"}
SCREENS = ("clock", "finance", "sportsbook", "departures", "markets", "news", "weather", "tanks", "ticker_wall",
           "town", "arcade", "f1")
SCALE, FPS = 3, 10


def led_mask():
    """A dark gap around each LED so the enlargement reads as a matrix, not pixel art."""
    cell = Image.new("L", (SCALE, SCALE), 0)
    ImageDraw.Draw(cell).rectangle((0, 0, SCALE - 2, SCALE - 2), fill=255)
    mask = Image.new("L", (128 * SCALE, 32 * SCALE))
    for y in range(0, 32 * SCALE, SCALE):
        for x in range(0, 128 * SCALE, SCALE):
            mask.paste(cell, (x, y))
    return Image.merge("RGB", (mask, mask, mask))


async def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 6
    registry = PluginRegistry()
    for name, path in PLUGINS.items():
        spec = importlib.util.spec_from_file_location(f"demo_{name}", ROOT / path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        registry.register(module.plugin)
    config = validate_config({
        "plugins": {"weather": {"latitude": 40.758, "longitude": -73.9855}, "arcade": {"mode": "quest"},
                    "ticker_wall": {"style": "taqueria"}, "tanks": {"iss": False}},
        "modules": {name: {"enabled": True} for name in SCREENS if name in ("clock",)},
        "display": {"transition": "slide_left", "brightness": 100},
        "playlist": [{"id": name, "module": name, "duration": seconds, "enabled": True, "mode": "normal"}
                     for name in SCREENS],
    }, registry)
    runtime = Runtime(config, BrowserSink(), registry)
    # A few rounds of refreshes so team logos (fetched a handful per poll) are in.
    for _ in range(5):
        for name in runtime.providers:
            await runtime.refresh_provider(name)
    runtime.scheduler.tick(0, runtime.eligible())
    mask, frames = led_mask(), []
    now, dt = time.monotonic(), 1 / 30
    visited = set()
    step = 0
    while True:
        now += dt
        await runtime.step(dt, now)
        current = runtime.scheduler.current.module
        if current == SCREENS[0] and visited >= set(SCREENS):
            break
        visited.add(current)
        # A README loop shows each screen briefly; skip the hold that finishes a headline.
        if runtime.scheduler.current.elapsed >= seconds:
            runtime.scheduler.next(runtime.eligible())
        if step % (30 // FPS) == 0:
            big = runtime.frame.resize((128 * SCALE, 32 * SCALE), Image.Resampling.NEAREST)
            frames.append(ImageChops.multiply(big, mask))
        step += 1
        if step > 30 * seconds * len(SCREENS) * 4:
            break
    await runtime.close()
    output = ROOT / "docs/demo.gif"
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=1000 // FPS, loop=0, optimize=True)
    print(f"{output} · {len(frames)} frames · {output.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    asyncio.run(main())
