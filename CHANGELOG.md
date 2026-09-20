# Changelog

All notable changes will be recorded here. RackTicker uses Semantic Versioning.

## 1.3.0 - 2026-09-19

- **Pixel Town**: people no longer walk through the taco truck — they pass behind
  it — and hungry ones form a real queue at the window instead of standing inside
  one another, wait their turn and leave with the taco in hand. The street they
  walk down is new too: a pavement, shadows underfoot, lit shopfronts with their
  own awnings, a cook in the serving window, steam off the griddle, vans in the
  traffic, birds by day and a cat at 3 AM.
- The control page's port is now stated plainly: 8080 on a computer, 8081 on a Pi.

## 1.2.0 - 2026-09-19

- Reset goes as far back as you need, from Settings: settings and plugins, Wi-Fi
  only (for when RackTicker moves without you), everything, or **Ready to pass on**
  — which also clears the system log, the shell history and the device's own
  identity, then powers off.
- A device now makes its own machine id, ssh host keys and name on first boot after
  that reset, named after its Pi (`rackticker-3c4d`). Cards cloned from one image
  no longer share an identity, an ssh host key or a `.local` name.
- A welcome step on first open: pick where the rack is and RackTicker sets its
  location and its clock in one go.
- **Restart the display** in Settings, for the times a restart is all it needs.
- The Status screen now reports this device instead of placeholders: whether it is
  really online, its own temperature, and a failed update, which used to be visible
  only on the control page.
- Photographs of a built unit in the README.

## 1.1.0 - 2026-09-19

- Set the time zone from Settings, no SSH: clocks, schedules and news timestamps
  follow it anywhere in the world.

## 1.0.11 - 2026-09-19

- Update now always re-checks GitHub first, and a device refuses to install a
  version older than the one it runs. A cached answer could install an older release.

## 1.0.10 - 2026-09-19

- The parsing helper is recycled, so it no longer grows to a third of the memory in use.

## 1.0.9 - 2026-09-19

- An install is no longer mistaken for a crash: rolling back needs repeated restarts
  outside an install, and the display gets long enough to stop.

## 1.0.8 - 2026-09-19

- Memory and priority limits per service: the display, an update or a plugin can no
  longer take the machine (or ssh) down with them.

## 1.0.7 - 2026-09-19

- Installing no longer risks running a 512 MB Pi out of memory: the display pauses
  during the work, the panel companion compiles gently, and the Pi gets swap.
- Flights: the aircraft type shows beside the destination when both will not fit.

## 1.0.6 - 2026-09-19

- Flights: one still card with the airline, the big route, both cities in full and
  the time left. Nothing cycles.

## 1.0.5 - 2026-09-19

- Night brightness: near-black backgrounds and unlit TIX cells stay dark instead of glowing.
- Departures: announcements crawl one LED a frame under a steady heading.

## 1.0.4 - 2026-09-19

- The unplug-three-times password reset counts real power-ups only.

## 1.0.3 - 2026-09-19

- Forgot the password? Unplug RackTicker as soon as its panel lights up, three
  times in a row, and it is cleared. No computer needed.
- Dark colours no longer flicker in scan lines at low night brightness.

## 1.0.2 - 2026-09-19

- Optional control-page password (Settings → Software); stored only as a salted hash.
- Repeated crashes switch RackTicker back to the previous version by itself.
- The system log is capped so years of running do not wear out the SD card.

## 1.0.1 - 2026-09-19

- Updates from the web page with automatic rollback, daily self-healing, factory
  reset, Wi-Fi setup mode, a boot splash with the control page's address, and a
  one-line installer. Devices update to the newest published release.
- Hockey and football like the broadcast: shots, power plays, empty nets, goal
  scorers; a field strip with the first-down line, timeouts and big-play banners.
- News skips stories older than 12 hours (adjustable) and leads with the newest.
- Fixed: a blank panel after installing the network keeper, plugin installs
  from GitHub reading partial downloads.

## 1.0.0 - 2026-09

The first public release.

- Plugin platform: plugins are folders; install from a GitHub link or the
  community list, update and remove from the page; installed plugins run
  sandboxed in their own process. `python -m app.dev new/check/preview/push`
  and AGENTS.md guides for building screens with AI coding agents.
- New control page: Now / Screens / Plugins / Settings, team pickers, place
  search, one home location, light and dark.
- Home Assistant over MQTT discovery: light, screen picker, Show switches,
  Next and Message, ready for Alexa and Google through Home Assistant.
- Flights redesigned as an airport board with real airline marks, airport
  names, aircraft names and route progress; stale routes rejected.
- Traffic finds everything from your location: CHP incidents, Caltrans
  message signs and drive times drawn the way the signs read.
- Sportsbook: TV-style bases, count and outs; down and distance; big-play
  banners, and take-overs when your teams score or make the play.
- Lowercase with true descenders and accents for names and headlines.
- New: Data from a link, and community plugins Departures (with station
  announcements), Tanks, Quakes, Surf and Now playing (album art, lyrics and a
  car-stereo analyser).
- The printable 2U rack face, revision 10: four parts, eight screws.
- Smoothness: providers on their own thread, parsing off the render loop,
  steady panel refresh and colour-order tuning via matrix.conf.
