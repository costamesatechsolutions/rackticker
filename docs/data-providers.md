# Data providers

RackTicker separates screen modules from data providers. A module decides how
information looks; a provider supplies the information. This keeps the core
small and lets installations add local or hosted data sources as plugins.

## Aircraft

The bundled `local_adsb` plugin reads `aircraft.json` and `receiver.json` from
a local dump1090-fa installation. It does not require a cloud account or send
receiver data anywhere. The flight screen appears only when a tracked aircraft
has useful data, so disconnecting the SDR does not leave a dead page in the
playlist.

## Sports

The built-in sports provider is demo data for testing layouts and a new panel.
The bundled `free_sports` plugin replaces it with key-free NHL, NFL, MLB and NBA
schedule and live-score data. It defaults to Anaheim, the Kings and the Padres;
live and nearby games rotate every eight seconds, followed by upcoming games for
favorite teams. Configure `leagues`, `favorite_teams`, `refresh_seconds`,
`cycle_seconds`, and `timezone` under `plugins.free_sports`. WNBA, college
football and men's college basketball can also be enabled. `show_logos` fetches
only the current matchup's provider-hosted marks into a bounded memory cache;
the project does not redistribute team artwork.

## News

The bundled `news` plugin reads standard RSS or Atom over HTTPS and displays a
Times Square-style headline ribbon. It defaults to a technology feed and stores
only a bounded in-memory headline list. Configure `feed_url`, `source_label`,
`refresh_seconds`, and `cycle_seconds` under `plugins.news`.

NHL data comes from the league's public web API. The other enabled leagues use
ESPN's key-free scoreboard web feed. ESPN does not publish a compatibility
contract for that feed, so the plugin treats it as a best-effort source and
keeps the other leagues working when one request fails.

## Formula 1

The community `f1` plugin shows the next race, circuit, round and local start time
using the open-source [Jolpica F1 API](https://github.com/jolpica/jolpica-f1).
It caches the schedule for 15 minutes by default. Configure `timezone` and
`refresh_seconds` under `plugins.f1`.

## Prediction markets

The community `markets` plugin rotates active public Kalshi and Polymarket
questions by recent activity. It displays the current implied YES probability
and does not contain authentication, wallet, order, or trading code. The feeds
are cached and partial failures are isolated, so one venue can remain on screen
when the other is unavailable. Configure `refresh_seconds` and `cycle_seconds`
under `plugins.markets`.

## Ticker scenes

The community `ticker_wall` plugin is a data-free presentation module with arena,
Times Square and taqueria scenes. Each scene accepts pipe-separated custom text,
and `auto` mode rotates all three. It deliberately animates small borders and
text rather than panel brightness, avoiding full-screen flashes.

The community `arcade` plugin adds original autonomous runner, platform and cap
shuffle scenes designed for the 128×32 display. It uses no ROMs, game assets,
network data or input device. `auto` rotates the scenes; each can also be held
individually.

## Finance

The bundled `finance` plugin shows stocks, funds, commodities and cryptocurrency
with price, daily percentage move and an intraday sparkline. Its default `auto`
mode discovers two liquid gainers, two liquid losers and one heavily traded
name, then adds the S&P 500, Bitcoin and crude oil. `custom` mode uses the
comma-separated `symbols` list. Quotes come from a key-free chart web feed and
are best-effort because that feed has no published compatibility contract.

## Weather

The bundled `weather` plugin uses the key-free Open-Meteo forecast API. It shows
a pixel-art condition graphic, temperature, feels-like temperature, wind,
sunrise and sunset. Defaults target Santa Ana and Fahrenheit; latitude,
longitude, units and refresh cadence are configurable.

Additional leagues need provider-specific plugins:

- [TheSportsDB](https://www.thesportsdb.com/documentation) offers a public v1
  API for basic development. Its live-score v2 endpoints require a premium API
  key, currently described on its official API page.
- [Sportradar](https://developer.sportradar.com/getting-started/docs/your-account)
  offers trial access after account registration and uses per-product API keys
  and quotas.

A provider plugin belongs under `plugins/<provider>/` and registers through the
public API in `app/plugin_api.py`. Keep credentials out of repository config:
read them from environment variables or a root-readable file on the device.
