# Changelog

All notable changes will be recorded here. RackTicker uses Semantic Versioning.

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
