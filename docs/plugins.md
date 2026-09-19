# RackTicker plugins — API v1

A plugin is a folder with a `plugin.json` and one Python file. It can add a
screen, a data source, or both. You never edit RackTicker itself.

## Five minutes to your own screen

From a RackTicker checkout (`python -m pip install -r requirements.txt`):

```sh
python -m app.dev new surf_report          # a working plugin to start from
python -m app.dev check surf_report        # render it: surf_report/preview.png + preview.gif
python -m app.dev preview surf_report      # live in the browser; reloads on every save
python -m app.dev push surf_report --to rackticker.local:8081
```

`push` needs **Plugins → For plugin developers → Allow plugin uploads** switched on
on the device. It installs or updates the plugin, switches it on and adds it to
the playlist; running it again replaces the old version in place.

`check` is made for AI coding agents as much as people: it prints timings and
problems (exceptions, black frames, slow frames) and writes images an agent can
look at. The scaffold includes an `AGENTS.md` with the panel's rules, so Codex,
Claude or Cursor can build a screen without a panel on the desk.

## Folder format

```
surf_report/
  plugin.json    {"id": "surf_report", "name": "Surf report", "entry": "plugin.py",
                  "description": "…", "author": "…", "version": "1.0.0", "homepage": "…"}
  plugin.py      defines plugin = Plugin("surf_report", ...)
  README.md      optional
```

`id` is lowercase letters, digits and underscores (48 max) and must equal
`Plugin.name`. `entry` defaults to `plugin.py`. Other files (images, data) can
sit alongside; read them relative to `Path(__file__).parent`.

## Sharing

Put the folder in a public GitHub repository (on its own, or as one folder of a
repository with several). Anyone can paste the link into **Plugins → Add a
plugin**: `https://github.com/you/repo` or `https://github.com/you/repo/tree/main/surf_report`.
RackTicker downloads that exact commit, checks it, loads it once in a sandbox,
and only then replaces any earlier version. **Update** fetches the newest
commit of the same branch; **Remove** deletes the folder and its settings.

To appear in **Community plugins** for everyone, open a pull request adding an
entry to [`community/index.json`](../community/index.json):

```json
{"id": "surf_report", "name": "Surf report", "author": "you",
 "description": "Wave height, period and water temperature from NOAA buoys.",
 "url": "https://github.com/you/repo/tree/main/surf_report"}
```

## Sandbox and trust

Installed plugins run outside the display's process: one process per plugin, or,
on machines with under 1.5 GB of memory (a Pi 3 has 512 MB), one process shared by
all installed plugins (`RACKTICKER_SANDBOX=shared` or `separate` to choose):
- The display never waits for them. RackTicker asks for a frame and shows the
  newest one it has; a slow plugin only makes its own screen late.
- A crash, a hang (no answer for 6 s) or growing past its memory budget restarts
  the process, with a backoff; in shared mode the plugin responsible is named and
  the others simply come back. Five failures in ten minutes switch a plugin off
  and the Plugins page shows why.
- It runs at lower CPU priority, with a clean environment (no service
  credentials) and its own data folder as `HOME`.
- It sees its own settings and data only, not other plugins' data.

This contains mistakes; it is not a security boundary against malicious code.
A plugin can still reach the network and files the RackTicker service account
can reach (the systemd unit confines that account to its own data directory).
Install plugins from people you trust, the same as any software, and leave
uploads switched off when you are not developing.

Bundled plugins (the `plugins/` folder shipped with RackTicker) run inside the
main process, which lets them feed built-in screens (`provider_for`) and
outputs. pip-installed packages with a `rackticker.plugins` entry point (below)
also still work: switch them on with `--plugin NAME`.

## The Plugin object

`plugin.py` defines a `Plugin` from the public `rackticker` package:

```python
from rackticker import Plugin, Module, new_frame, centered

class Hello(Module):
    name = "hello"

    def render(self, context):
        frame = new_frame()
        centered(frame, context.config["plugins"][self.name]["message"], 12)
        return frame

plugin = Plugin(
    name="hello", label="Hello rack", api_version=1,
    module=Hello, defaults={"message": "HELLO RACK"},
)
```

The plugin id (`plugin.json` `id`), `Plugin.name` and the module's `name` must
match. Built-in names cannot be replaced by a screen plugin. Duplicate
IDs, competing providers for one target, and incompatible API versions are
reported rather than silently overwritten.

`Plugin` can provide any combination of these components:

| Component | Factory | Contract |
| --- | --- | --- |
| Screen | `module()` | A `Module` returning a new 128×32 RGB Pillow image |
| Data | `provider(plugin_context)` | A `Provider` whose async `fetch()` returns `Snapshot(data)` |
| Display | `output(plugin_context)` | A `FrameSink` consuming the canonical framebuffer |

### The display toolkit

Everything the built-in screens use is exported from `rackticker`, so a community
screen can look as good as the ones that ship:

| Tool | What it gives you |
| --- | --- |
| `Lettering(text, effect, palette, mode, seed)` | Animated words: `assemble` (pixels fly in), `drop`, `slot` reels, `typewriter`, `wave`, `scroll_stop`, `chomp`, `split`, `sparkle`, `fireworks`. Colour modes `solid`, `alternate`, `chase`, `rainbow`, `fire`, `gradient`. `.duration` tells you how long it runs. |
| `Storyboard` | Plays variable-length items back to back (headlines, cards, phrases), resumes where the last visit stopped, and never swaps data under an item on screen. |
| `Module.hold(context)` | Return `True` while mid-story so the playlist waits for the current item to finish (bounded by the scheduler). `Storyboard.hold()` implements it for you. |
| `draw_text(..., scale=2, smooth=True)` | Rounded Scale2x headline lettering; `draw_tiny` is a 3×5 font for dense tables. |
| `draw_text(..., mixed=True)` | Keeps lowercase, drawn like station dot-matrix boards with true descenders (a mixed line is 9 rows, not 7). Use it for names and sentences; keep codes, scores and prices in capitals. |
| `loop_strip`, `crawl_once_x` | Endless or one-pass crawls. Pre-render a strip once per data change; each frame is then just a paste. |
| `sprite(ascii_art, palette)`, `stamp` | Pixel-art sprites from strings, cached. |
| `Particles`, `bulb_border`, `chase_bar`, `triangle`, `hsv`, `mix`, `dim`, easing | Casino lights, confetti, market arrows and motion curves. |

Animated screens return `1 / context.config["display"]["fps"]` from
`refresh_interval`; static screens keep the default (redraw only when data or
settings change). Time comes from `context.animation_time`, which advances in
whole frames, so a crawl moving 30 px/s at 30 fps steps exactly one LED per frame.
Keep per-frame work small: the Raspberry Pi 3A+ runs every screen at 7–30% of one
core, and most of that budget belongs to pixels that actually change.

When a later version renames or retires a setting, supply
`migrate_settings(settings) -> settings` so saved configurations upgrade instead
of failing validation.

Factories must do no I/O or resource acquisition. Put network work in async
provider methods and acquire output resources lazily. Exported API v1 names
should be imported from `rackticker`; importing `app.core.*` couples a plugin to
internal implementation details. Normalized `Flight`, `Game`, `Team`, `Race`,
`Message`, `SystemStatus` and `PriorityEvent` models are also exported from
`rackticker`; generic screens can use their own data classes inside `Snapshot`
without changing the core.

### Settings

`defaults` is a flat mapping of up to 32 fields. Text, finite numbers and booleans
automatically become controls in the web UI. `choices={"mode": ("auto", "night")}`
turns a text field into a dropdown, and `help={"mode": "…"}` adds a one-line hint
under it. A `validate_settings(settings)`
callback can enforce ranges or lengths; raise `ValueError` with a useful message.
Type checks and unknown-field rejection apply before the callback. Invalid
updates leave the previous configuration untouched.

`ui` hints give a setting a better control in Settings:

| Hint | Control |
| --- | --- |
| `{"type": "slider", "min": 3, "max": 30, "step": 1, "unit": "s"}` | Slider with a readout |
| `{"type": "tags"}` | A comma-separated list edited as removable chips |
| `{"type": "multi", "options": ["NFL", "NBA"]}` | Pick several; stored comma separated |
| `{"type": "teams", "leagues": "leagues"}` | Team search by name (ESPN's list), stored as abbreviations |
| `{"type": "location"}` on `latitude` | City or ZIP search that fills `latitude` and `longitude` |
| `{"type": "secret"}` | A password box; the value is never sent back to the page |
| `{"advanced": True}` | Tucked under an Advanced fold (paths, poll timing) |
| `{"label": "Seconds per game"}` | Any hint can rename the field |

A plugin whose `latitude` and `longitude` are both 0 receives the device's home
location (Settings → Home) in `PluginContext.settings`, so most people never
enter coordinates twice.

Screen settings are in `context.config["plugins"][name]`. Provider and output
factories receive `PluginContext`; its `.settings` property returns current
settings, so read it when performing work rather than caching a copy at startup.
Mark tokens and API keys `{"type": "secret"}`: the page shows dots instead of the
value and `/api/config` never returns it. Do not put secrets in plugin defaults or
examples. A plugin that must keep something it learns (a rotated refresh token) can
write it under `Path.home()`, which for an installed plugin is its own data folder.

### Providers and events

By default a plugin's snapshots appear at `context.snapshots[plugin.name]`.
Set `provider_for="flight"` or `"sports"` to supply a built-in
screen with its existing normalized data model. For example, a local ADS-B
plugin can read `dump1090-fa` JSON and return a `Snapshot[Flight]`; the decoder
continues to own the SDR, and existing feeder services continue independently.

The current runtime polls providers every five seconds with a six-second timeout.
It retains the previous snapshot on error, marks it stale, and marks timestamps
older than 30 seconds stale. Preserve the source timestamp; do not label an old
receiver file as fresh merely because it was read now. Implement async `close()`
to release client sessions. Run blocking SDK calls in a worker thread and apply
their own timeouts: cancellation cannot forcibly stop arbitrary blocking code.

`plugin_context.emit_event(module_name, duration_seconds)` requests a priority
interrupt; the module's `event_priority` determines scheduling. Deduplicate events
and implement cooldowns in the provider. A provider calling this during `fetch()`
requests the event before its newly returned snapshot is stored; rendering begins
on a later runtime step. Use `Module.available(context)` for conditional playlist
entries. An interrupt resumes the existing playlist afterwards.

Parse big payloads with `offload`. A long `json.loads`, XML parse or image
decode holds Python's GIL, so even in a thread it freezes the display; on a
Raspberry Pi a 1 MB feed stalled the crawl for 300 ms. Download on the event loop,
then hand the bytes to a module-level function:

```python
from rackticker import offload

def parse(raw):                       # runs in a helper process
    return normalize(json.loads(raw))

rows = await offload(parse, await response.read())
```

The function and its arguments must be picklable (module-level functions in an
installed plugin are). When the helper cannot run it, for example for a plugin
loaded from a file path in tests, `offload` runs it in a thread instead, so your
code is the same either way.

### Outputs

Outputs receive the same 128×32 RGB image as the browser and PNG exporter, with
brightness supplied separately. They must not mutate the image. `display()` must
return promptly: use a bounded queue holding only the latest frame when hardware
needs a worker. Implement `close()` and accept repeated cleanup where practical.
Hardware libraries belong inside their plugin; importing RackTicker on a Mac
must not require GPIO or HUB75 drivers. Alternative mounts and output transports
are possible; arbitrary framebuffer resolutions are outside API v1.

## Failure behavior

Import and factory errors are shown on the Plugins page. A render exception
skips the screen for 30 seconds; provider failures keep the last data and mark
it stale. A bundled or pip plugin runs inside the main process, so an infinite
loop there would still stall the display; installed (folder) plugins are
isolated as described above. If a selected live provider fails to start, its
built-in mock is removed instead of masquerading as the real source.

Switching a plugin off keeps its settings and playlist entries (its screens are
skipped); switching it back on restores them. Switching a plugin on adds its
screen to the playlist once; nothing adds itself without being asked.

## pip packages

A plugin can also be a Python package installed with pip into RackTicker's
environment. Declare the entry point in its `pyproject.toml`:

```toml
[project.entry-points."rackticker.plugins"]
hello = "rackticker_hello:plugin"
```

and start RackTicker with `--plugin hello` (repeatable). Discovery reads package
metadata without importing code; only selected packages are loaded.
`examples/hello-plugin` is both a pip package and an installable folder.

## Licensing and source

RackTicker and the example use `AGPL-3.0-only`, matching InkSlab's stated AGPL v3
choice. Commercial use is allowed. Distributed derivative versions carry the
license's corresponding-source obligations; modified versions used over a network
must offer their corresponding source to those users. See
[AGPL section 13](https://www.gnu.org/licenses/agpl-3.0.html#section13).

For closely integrated Python plugins, use AGPL-3.0 or a compatible open-source
license; permissive code can often be incorporated, but attribution still matters.
Do not assume every unrelated service communicating over HTTP must adopt this
license. License compatibility depends on how works are combined; GNU discusses
the distinction in its [plugin FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLPlugins).

The web footer offers the running core's source and license. The source ZIP uses
an explicit file allowlist and excludes private configuration, backups and
installed third-party plugins. Plugin authors/distributors must provide matching
plugin source separately. When shipping an assembled device or modified release,
include matching build/install instructions, dependency notices and the source
for the covered components. Keep historical license notices for previously
distributed versions; relicensing a new release does not revoke old permissions.
