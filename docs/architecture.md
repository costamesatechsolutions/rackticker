# Architecture

How RackTicker turns data into a 128×32 frame and keeps it smooth.

## Files

```text
app/
  core/
    config.py          strict defaults, validation, atomic JSON persistence
    fonts.py           original MIT 5×7 bitmap glyphs at 1× / 2× / 3×
    models.py          normalized immutable provider snapshots and event models
    playlist.py        playlist entry model; repeated modules have distinct IDs
    scheduler.py       deterministic dwell, conditions, interrupt/resume
    renderer.py        canonical frame, clipping, scrolling, pixel transitions
    runtime.py         provider polling, rendering, failure isolation, exports
  modules/
    base.py            render context, availability, cadence, event priority
    clock.py           large clock, local date, 12/24h, optional seconds
    tixclock.py        counted-square clock with deterministic reshuffling
    flights.py         route, detail, and distance layouts
    sports.py          generic teams, high-contrast scores, game state
    message.py         static alert or reusable nonblocking ticker
    system_status.py   concise optional mock connectivity/temperature
  providers/
    base.py            FlightProvider, SportsProvider
    mock_flights.py    normalized mock ADS-B-style scenarios
    mock_sports.py     pregame/live/final/goal examples
  outputs/
    base.py            FrameSink contract
    browser.py         bounded latest-frame WebSocket fan-out
    hub75.py           Unix-datagram sink for the privileged matrix companion
  web/
    server.py          aiohttp routes, lifecycle, WebSocket transport
    static/            index.html, styles.css, app.js; no build step
  main.py              CLI entry point
config/
  config.example.json  complete example, checked by tests
  config.json          generated user config, gitignored
docs/
  integrations.md     future adapters and event boundary
  frames/             native example PNGs, including flight variants
  frames.png          nearest-neighbor contact sheet
tests/                unit and HTTP/WebSocket integration tests
tools/
  render_examples.py  regenerate the example gallery
  benchmark.py        local render-loop CPU/cadence measurement
requirements.txt      two direct runtime dependencies: Pillow, aiohttp
pyproject.toml        optional packaging and rackticker CLI
LICENSE               AGPL-3.0-only project license
```

```text
provider I/O ──> normalized Snapshot ──> Module.render(context)
                                              │
playlist / priority scheduler ─────────────────┘
                                              ▼
                              Pillow RGB frame, 128×32
                                              │
                              integer pixel transition
                                              ▼
                                  canonical framebuffer
                                     │              │
                              BrowserSink             Hub75Sink
                                     │              │
                               RGB888 / ws     SetImage(frame)
                                     ▼              ▼
                              browser canvas    physical panels
```

Modules only receive normalized state and render Pillow images. They cannot
call APIs or produce HTML. The runtime validates frame dimensions and RGB mode.
Every sink receives the same image object, and sinks must not mutate it.
Pillow and aiohttp usage follows their [image documentation](https://pillow.readthedocs.io/en/stable/reference/Image.html)
and [server documentation](https://docs.aiohttp.org/en/stable/web_quickstart.html).

## Scheduling and failure handling

- A playlist entry plays only while `Module.available(context)` is true, so a
  screen with nothing to show (no game on, no plane nearby) waits its turn.
  Disabled entries/modules are skipped. An empty eligible playlist shows a small
  idle screen and remains controllable.
- Modules declare `event_priority`. Message=30, flight=20, sports=10. Higher
  priority events can interrupt an interrupt; equal/lower events are coalesced
  while one is active. The suspended stack is bounded to eight. No stale backlog.
- Interrupt completion resumes exact elapsed dwell. Explicit Next discards
  suspended state. Saving a changed playlist retains a valid current entry when
  possible and clears suspended interrupts. Preview dwell is indefinite.
- Provider polling runs in a separate asyncio task every five seconds, with a
  six-second timeout. Provider errors retain the latest data and mark it stale;
  the screen explicitly displays **STALE / CACHED**. Snapshots older than 30
  seconds are also stale. Caches are in memory only and reset on restart.
- A module render/availability exception logs once and skips it for 30 seconds.
  Other screens, including the local clock, keep running. A failed sink is
  removed from active output until restart.
- Simulated internet/HA status is illustrative data. It does not control the
  real computer's network, and it does not imply an actual HA connection.
  **Simulate provider outage** independently exercises provider degradation.

## Rendering and performance

All coordinates land on integer pixels. Original 5×7 glyphs scale to 10×14 and
15×21 with no antialiasing. Strings use a bounded mask cache. Sports/status
render only when data or settings change; the clock checks once per second.
Moving screens are capped at the configured 5–30 fps (default 25). Scrolling
uses elapsed time, a configurable speed and gap, and an explicit clipping box.
Debug animation speed changes motion, not timekeeping or playlist dwell.

The TIX Clock follows the original field proportions: 1×3, 3×3, 2×3 and 3×3
for HH:MM. Each field lights the number of squares represented by its digit; a
local seeded permutation changes the occupied squares on the original clock's
default four-second cadence. That keeps the display playful while making every
output sink deterministic.

Transitions are cut, slides, wipe, dissolve and drop; all transition frames are
native 128×32 RGB, including exports taken mid-transition.

Smoothness is engineered, not hoped for. Animation time is counted in integer
units, so a 30 px/s crawl at 30 fps moves exactly one LED every frame for weeks
on end. Providers hand JSON/XML parsing and image decoding to
`rackticker.offload()`, which runs it in a helper process, so a 1 MB market feed
never freezes a frame. Frames slower than 25 ms and loop stalls are recorded
with the screen and the providers refreshing at the time, visible under `perf`
in `/api/state`.

WebSocket binary messages are exactly **12,288 bytes**, row-major RGB888 without
a header. JSON text messages carry runtime metadata. Unchanged pixels are not
rebroadcast. Each client has a single latest-frame notification; slow clients
drop intermediate frames rather than building queues. Sends time out after two
seconds; at most 12 clients are allowed. No browser is required for the renderer
to run. The short export history is bounded to about 360 KiB of RGB data.

```sh
python -m tools.benchmark --seconds 10
```

This reports CPU consumption and output-frame counts for static and scrolling
screens on your current machine. Pi performance and electrical refresh must
still be measured on the target hardware; workstation results are not Pi claims.

## Plugins

Bundled plugins load in-process from `plugins/`; installed plugins run in
`app/sandbox_child.py` processes managed by `app/core/sandbox.py`. See
[plugins.md](plugins.md) for the contract and the sandbox.
