# RackTicker — notes for AI coding agents (Codex, Claude, Cursor…)

RackTicker drives a 128×32 RGB LED panel (two 64×32 HUB75 panels) from a Raspberry Pi,
with a local web page for control. Python 3.11+, asyncio, aiohttp, Pillow. No build step.

## Most work is a plugin, not a core change
- A plugin is a folder: `plugin.json` + one Python file defining `plugin = Plugin(...)`.
- Start one with `python -m app.dev new my_plugin`, then follow `my_plugin/AGENTS.md`.
- Check it with `python -m app.dev check path/to/plugin` and **look at the preview.png it writes**.
- Bundled plugins live in `plugins/` (run in-process). Plugins users install live in
  `DATA/plugins/` and run sandboxed (`app/sandbox_child.py`).
- Public API: import from `rackticker` only. Reference: `docs/plugins.md`.

## Map
- `app/core/runtime.py` render loop, scheduler glue, plugin lifecycle.
- `app/core/scheduler.py` playlist, interrupts, holds.  `app/core/config.py` strict config schema.
- `app/core/fonts.py` 5×7 font (+ lowercase with descenders via `mixed=True`), 3×5 tiny font.
- `app/core/fx.py` effects (Lettering, Particles, sprites).  `app/core/story.py` Storyboard.
- `app/core/plugin_manager.py`, `manifest.py`, `installer.py`, `sandbox.py` the plugin platform.
- `app/modules/` built-in screens.  `app/web/` API + control page (`static/`, no framework).
- `app/integrations/` Home Assistant over MQTT.  `deploy/` systemd + HUB75 daemon (C++).

## Rules that keep the panel smooth and readable
- Renders are synchronous on the display loop: no I/O, well under 10 ms on a laptop.
- Parse large payloads with `offload()`; providers run on their own thread.
- Legible from 5–10 ft: 2× smooth text for the headline, 5×7 for details, tiny font only for labels.
- Saturated colours only; no pink/magenta/pastel. Nothing cut off mid-word: fit or page.
- Time for animation comes from `context.animation_time`.

- `python -m tools.clip_audit 40 my_screen` renders on live data and lists any letters cut off
  at the top or bottom of what they're drawn into (lowercase g, p, y hang 2 rows per scale).

## Tests and deploy
- `python -m unittest discover -s tests -t .`
- `tools/deploy_pi.sh` deploys the committed HEAD to a Pi (`RACKTICKER_PI_HOST=user@host`).
