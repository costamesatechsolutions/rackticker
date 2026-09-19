# Building a RackTicker plugin (instructions for AI coding agents)

You are editing one RackTicker plugin: this folder. Do not edit RackTicker itself.

## Loop
1. Edit `plugin.py` (and `plugin.json` for name/description/version).
2. Run `python -m app.dev check .` from a RackTicker checkout (or `python -m app.dev check path/to/this/folder`).
3. **Look at `preview.png`** (and `preview.gif`) before claiming anything works. Fix what you see.
4. Repeat. `python -m app.dev push . --to HOST:PORT` installs it on a real device.

## The panel
- 128 × 32 RGB LEDs, viewed from 5–10 feet. Every pixel counts; empty space is good.
- `draw_text(frame, text, x, y, color, scale=1, smooth=False, mixed=False)`: 5×7 font. `scale=2, smooth=True` for the one thing people should read from across the room. `mixed=True` keeps lowercase (names, sentences); lowercase descenders take 2 extra rows (9 rows total).
- `draw_tiny` (3×5) is for small labels only, never for the important value.
- Colours: saturated LED colours from `rackticker` (`WHITE, AMBER, GREEN, RED, BLUE, MUTED`). No pink, magenta or pastels: they wash out. Dim colours below ~40 per channel are invisible.
- One idea per screen. If it does not fit, page it (`Storyboard`) instead of shrinking it.
- Text that is cut off reads as broken: measure with `text_width` and fit or page.

## Rules
- `render()` must be fast (well under 10 ms on a laptop; a Raspberry Pi 3 is ~6× slower) and do **no I/O**.
- Network only in `Provider.fetch()`, with `aiohttp` and a timeout under 5 s. Parse big payloads with `offload(function, data)`.
- Motion uses `context.animation_time` (seconds since the screen appeared), never `time.time()`. Return `1 / context.config["display"]["fps"]` from `refresh_interval` when animated.
- `available()` returns False when there is nothing worth showing; the playlist then skips the screen.
- Settings: flat `defaults` (text, numbers, booleans), optional `choices`, `help`, and `ui` hints
  (`{"type": "slider", "min": 1, "max": 60}`, `{"type": "tags"}`, `{"type": "location"}` on `latitude`, `{"advanced": True}`).
  Read them with `context.config["plugins"][name]` (screen) or `self.context.settings` (provider).
- Settings with `latitude`/`longitude` left at 0 get the device's home location automatically.
- No API keys in code or defaults. If a service needs one, add a setting and say so in `help`.
- Import only from `rackticker` (the public API), the standard library, `aiohttp` and `PIL`.

Full reference: `docs/plugins.md` in the RackTicker repository.
