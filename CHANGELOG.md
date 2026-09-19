# Changelog

All notable changes will be recorded here. RackTicker uses Semantic Versioning.

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
