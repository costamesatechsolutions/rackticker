# Changelog

All notable changes will be recorded here. RackTicker uses Semantic Versioning.

## Unreleased

- **Headlines you can actually read.** The news desk crawled every headline past in 2×
  letters at 30 px/s: ten letters on the panel at a time, two and a half a second, half a
  minute a headline. The desk now shows the whole headline in 5×7 mixed case, two lines
  (about forty letters) at a time, rolling up a line once the eye has had time to take it
  in: a typical headline reads in eight seconds. It is the new `headline` style and what
  `auto` shows, with the Times Square zipper every third visit. The zipper and the TV lower
  third still crawl, but their 2× letters now step two LEDs at a time at twice the speed,
  which looks just as smooth and halves the wait.
- `wrap_text(text, width, scale, mixed)` is in the plugin API: whole-word lines that fit.
- **A feed that is down is not asked every five seconds.** Each failure doubles the wait
  before the next try, up to a minute, bundled and installed plugins alike, and saving new
  settings asks again straight away. On the test rack a price link was being refused by its
  server ("too many requests") every five seconds, all day.
- **No half-second freeze when a plane comes into range.** The ADS-B receiver's files, and
  the aircraft database a new arrival is looked up in, are parsed in the helper process
  instead of on a thread that held up the display; installing a plugin unpacks its
  download off the display's loop too.
- Sportsbook: a team without a logo gets dark letters on a light team colour; Boston's gold
  had white letters that could not be read.

## 1.5.0 - 2026-09-20

- **Smoother, and no more freezes.** The rack's own log showed the display stalling for up
  to two and a half seconds at a time. The Pi 3A+ was short of memory and had pushed the
  display's own pages out to the SD card; every stall was it waiting to read them back. The
  Pi now uses compressed swap in RAM (`rackticker-memory.service`, best effort and only if the
  kernel has zram), the processes hand freed memory back to the system, and each stall the
  panel suffers is recorded in `/api/state` with what it was doing: computing, waiting for
  swap, or collecting garbage.
- **Screens from installed plugins scroll smoothly.** They are drawn a few frames ahead of the
  one on show, so a late reply no longer shows as a hitch in a crawl, and the previous screen
  stays up until a plugin's first picture is ready instead of flashing black between screens.
- **One stuck plugin no longer restarts all of them.** A hung plugin used to be declared on a
  single unanswered request and took every other installed plugin down with it, blanking the
  screen being read. The process is now only stopped after real silence.
- **Screens are not snatched away mid-read.** A screen that says it has nothing to show gets
  four seconds to change its mind (a feed that blinks, a plugin restarting), a plane's card is
  finished before the playlist moves on, one failed refresh is no longer called "cached", and
  a passing plane waits twelve seconds into the current screen before it takes over.
- **The stock tape and the news desk stop stuttering when data arrives.** New quotes swap
  into the crawl without it jumping, and the news desk draws its headline strips a little at
  a time instead of all at once.
- **News says BREAKING** for a story under a minute old: a flashing red chip and its own
  bumper, instead of "0M AGO". Stories under fifteen minutes old glow amber.
- **Flights** are one still card: the airline, the city it left and the city it is going to
  in full (airport codes small beside them), a progress bar with the aircraft on it, and the
  time left. Nothing scrolls or turns over any more.
- The random-dot dissolve is no longer in the automatic rotation of transitions: on an LED
  panel it reads as flicker.

## 1.4.0 - 2026-09-20

- **Flights** now know what the aircraft is. A plane does not transmit its type, so
  the receiver never had one; RackTicker now reads the aircraft database that
  dump1090 keeps beside its own web page, the way its map does. Every aircraft
  overhead resolved instantly on the test rack, with no network call.
- **Sportsbook** keeps the line up once a game is under way: the favourite, the
  spread and the total alternate with the channel. Off with Lines on live games.
- **The stock tape** sits out the weekend; there is a setting for market hours only.
- The README says up front that anyone can write a screen, and links the starter.
  It also no longer claims the control page has no password.

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
