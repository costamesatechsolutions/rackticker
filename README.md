# RackTicker

**Rack enhancement that fits in 2U.**

**By Pine Heights Ventures LLC dba [Costa Mesa Tech Solutions](https://github.com/costamesatechsolutions)**

![RackTicker cycling through its screens on live data](docs/demo.gif)

Live LED signage for your server rack. RackTicker turns two 64×32 RGB LED panels and a Raspberry Pi into a 128×32 ticker
that lives in a 19-inch rack: stock tape, odds boards, news, flights overhead,
freeway traffic, weather, arcade games and whatever you build next. Every screen is
a plugin, every bundled data source is free and needs no key, and nothing needs a
cloud account. Run it on your computer first: the browser emulator shows the exact
pixels the panels will.

## Screens

| Screen | What it shows |
| --- | --- |
| Stock tape | The day's movers and your watchlist with company names, sparklines and each company's latest headline, under a flipping index header |
| Sportsbook | A Vegas odds board: spreads, totals and moneylines, big live scores with the bases, count and outs or the down and distance, banners for big plays, fireworks when your team scores |
| Prediction markets | Polymarket and Kalshi: each question whole and still, then its odds slide up |
| News desk | Network channels as a TV lower third or a Times Square zipper, in mixed-case headlines |
| Flights | The planes overhead like an airport board: airline mark, flight number and route, with the destination, aircraft, altitude and time to go turning over beneath; a departures-style list when several are about; a fly-by when one is overhead. Local ADS-B receiver or a free network feed |
| Traffic | California freeways near you, found from your location: CHP incidents, the real overhead message signs, and travel-time signs as they read over the road |
| Weather | Animated sky, hourly chart and four-day forecast |
| Pixel Town | A living city on real time: sunrise, lit windows at night, your weather, real flights overhead |
| Arcade | Pixel Quest, Tetris, Snake, Breakout, Invaders and Pong, played live by AIs |
| Shop sign | Your own words on a storefront sign: letters that assemble, drop and spin, in taqueria, Vegas, arena and Times Square styles |
| Data from a link | Any value from a JSON address as a big card, no code |
| Clocks, F1 | Desk clock (with a 4:20 surprise), TIX Clock, the next Grand Prix |

**Community plugins** install from the Plugins page with one click:

| Plugin | What it shows |
| --- | --- |
| [Departures](community/departures) | Live station boards from Budapest-Keleti, Roma Termini, Milan, Florence, Venice, Naples and Zürich, each in its country's style, with trains pulling into the platform |
| [Tanks](community/tanks) | California's reservoirs, and the space station's urine and water tanks, as sloshing water |
| [Quakes](community/quakes) | Earthquakes around you from the USGS on a 24-hour seismograph |
| [Surf](community/surf) | Wave height, swell, water temperature and the next tide at your break |
| [Now playing](community/now_playing) | The song you're listening to like an old car stereo: album art, scrolling title, track and time, spectrum analyser. Spotify or Home Assistant |

Screens finish the headline, card or lap they are showing before the playlist moves
on, skip themselves when they have nothing to show, and move on a frame-locked clock
so crawls step exactly one LED per frame.

## What you need

| Part | Notes |
| --- | --- |
| **Raspberry Pi 3A+** | The reference build. A Pi Zero 2 W should work (untested); the original Zero W is too slow |
| **2 × Waveshare 64×32 P2.5 RGB LED matrix panels** | HUB75, chained side by side: 320 × 80 mm of screen |
| **WatangTech RGB Matrix Adapter Board** | Sits on the Pi's header and drives the panels ("regular" pin mapping) |
| **5 V power supply with a barrel plug** | 4 A or more; 8 A leaves headroom at full brightness. Match the adapter's jack |
| **microSD card** | 16 GB or more, Raspberry Pi OS Lite |
| **3D printed 2U rack face** | Four parts, eight M3 × 10 screws, and yes, it'll fit: [BEZEL_V10.md](hardware/enclosure/BEZEL_V10.md) |
| **USB ADS-B receiver** *(optional)* | An RTL-SDR stick and antenna for planes overhead; without one, Flights uses a free network feed |

The panel frame is always exactly 128×32 RGB. Typography, clipping and motion are
designed at that size, so what the emulator shows is what the panels show.

## Try it on your computer

Python 3.10 or newer:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m app
```

Open **http://localhost:8080/**. The first run starts a playlist of real screens;
set your city under **Settings → Home** for weather, flights and traffic.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m app
```

`python -m app --help` lists options (`--port`, `--config`, `--host 0.0.0.0` to
open it to your LAN, `--output hub75` on a Pi).

## Install on a Pi

1. With [Raspberry Pi Imager](https://www.raspberrypi.com/software/), flash **Raspberry Pi
   OS Lite (64-bit)**. Under Edit Settings, set a hostname (for example `rackticker`),
   a user and password, your Wi-Fi, and enable SSH.
2. Boot it, SSH in once, and run:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/costamesatechsolutions/rackticker/main/tools/install.sh | sudo bash
   ```

   It builds the panel driver, reboots, and installs the newest release by itself
   (about ten minutes on a Pi 3A+). When the panels light up they show the address
   of the control page, for example `rackticker.local:8081`.

That is the last time you need SSH:

- **Updates:** Settings → Software shows when a new version is out; press Update.
  The new version is installed beside the running one, and if it does not come up
  healthy within a minute RackTicker switches back to the old one by itself.
- **Self-healing:** every day, and at boot, RackTicker checks its installed files
  against fingerprints taken at install and repairs anything damaged, from the
  previous version or a fresh download.
- **Wi-Fi setup mode:** with no Wi-Fi saved, or after five minutes without the
  saved one (new router, new password, moved house), the panel shows
  **Wi-Fi setup**: join the `RackTicker-Setup` network with your phone, pick your
  Wi-Fi on the page that opens, and it reconnects. If the old network comes back,
  it rejoins by itself. A working connection is never touched.
- **Factory reset:** Settings → Software. Your old settings are kept as a backup.

Rendering runs under an unprivileged service account; a small root-owned C++
companion alone owns the GPIO and receives frames over a local socket, and the
updater and network keeper are separate root services the web page can only ask.

**Developing from a computer:** `RACKTICKER_PI_HOST=pi@rackticker.local ./tools/deploy_pi.sh`
installs your committed working copy the same way, with the same health check and rollback.

## Panel tuning

The companion accepts every standard `rpi-rgb-led-matrix` `--led-*` flag from
`/etc/rackticker/matrix.conf` (installed once, never overwritten by updates);
restart it with `sudo systemctl restart rackticker-matrix` after editing.

- **Flicker:** the default `--led-limit-refresh=120` holds one steady refresh rate.
  Unlimited, the reference build swung from 208 Hz down to 93 Hz whenever the Pi was
  busy decoding ADS-B, which shows as bursts of flicker. Measure your panel with
  `--led-show-refresh` and keep the limit below its lowest rate.
- **Colours:** press **Settings → Developer tools → Colour test** and check each block matches its label. If
  GREEN shows blue (and amber looks pink), your panels are wired RBG: add
  `--led-rgb-sequence=RBG`.
- **Dim colours:** `--led-pwm-lsb-nanoseconds=160` (the default here) keeps very low
  brightness from drifting pink.
- **Other hardware:** for example an Adafruit bonnet:
  `--led-gpio-mapping=adafruit-hat --led-rows=32 --led-cols=64 --led-chain=2`.

## The control page

- **Now**: the live panel, what is playing, Pause and Next, a tile per screen to
  put it up now, panel on/off, brightness and a quick message.
- **Screens**: the playlist. Switch screens on and off, set their seconds, reorder,
  and open any screen's settings: team pickers by name, league chips, city search.
- **Plugins**: switch plugins on and off, install from a GitHub link or the
  community list, update, restart, remove.
- **Settings**: home location, brightness and night dimming, transitions, Home
  Assistant, and developer tools.

The page has no login: keep it on a trusted network (it binds to `127.0.0.1` unless
you pass `--host`). Everything it does is also a small JSON API under `/api/`.

## Home Assistant and voice

Settings → Home Assistant connects RackTicker to an MQTT broker (the Mosquitto
add-on works). It appears in Home Assistant as a device by itself: a light for on/off
and brightness, a Screen picker, a **Show …** switch for every screen, Next and a
Message box. Expose those switches to Alexa or Google through Home Assistant (or its
Matter bridge) and say "turn on Show Sportsbook".

## Make a screen

A plugin is a folder with a `plugin.json` and a Python file. You never edit
RackTicker itself.

```sh
python -m app.dev new surf_report           # a working starting point
python -m app.dev check surf_report         # renders preview.png / preview.gif and reports problems
python -m app.dev preview surf_report       # live in the browser, reloads on save
python -m app.dev push surf_report --to rackticker.local:8081
```

Share it as a folder in a public GitHub repository: anyone installs it by pasting
the link into **Plugins → Add a plugin** and updates it with one click. Installed
plugins run in their own sandboxed process, so a crash or hang restarts only that
plugin and the display never waits for it. The scaffold ships an `AGENTS.md` with
the panel's rules, so an AI coding agent (Codex, Claude, Cursor) can build and check
a screen on its own. To list yours for everyone, add it to
[community/index.json](community/index.json). Full guide: [docs/plugins.md](docs/plugins.md).

## How it stays smooth

Animation time is counted in integer units, so a 30 px/s crawl at 30 fps moves one
LED every frame for weeks. Data providers run on their own thread and hand feed
parsing to a helper process, so a 1 MB market feed never freezes a frame; installed
plugins run in their own processes entirely. Frames slower than 25 ms and loop
stalls are recorded under `perf` in `/api/state`. Details:
[docs/architecture.md](docs/architecture.md).

## Develop

```sh
python -m unittest discover -s tests -t .
python -m tools.render_demo          # regenerate docs/demo.gif from live data
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## License

AGPL-3.0-only. Copyright (c) 2026 RackTicker contributors. The original fonts and
assets are covered by the same license; dependencies keep their own. The control
page links to the license and a source archive of the running core. See
[LICENSE](LICENSE) and the [plugin licensing notes](docs/plugins.md#licensing-and-source).
