# Future integration boundary

RackTicker owns its clock, configuration, playlist, rendering, and outputs.
Home Assistant is an optional source of normalized information and events.
Losing an integration must never prevent standalone operation.

## Private data sources

Some data can only be used privately (a service licensed for personal projects,
or your own accounts). Keep such a source in its own private service and read it
from a private, installed plugin: the plugin lives in your own repository, is
installed with `python -m app.dev push`, and nothing about it ships with RackTicker.

## Providers

`FlightProvider` and `SportsProvider` expose async `fetch()`
and return `Snapshot[Flight | Game]` as appropriate. A snapshot contains
the normalized data, original update timestamp, source name, stale flag, and an
optional error. The runtime polls separately from rendering, bounds request
time, retains the last successful data on error, and adds visible stale labels.

The `local_adsb` plugin can optionally enrich the nearest aircraft through
ADSBDB. It queries the combined aircraft/callsign endpoint, caches successful
responses for six hours, and retains normal local receiver output when the
service is unavailable or does not know a callsign. Origin and destination are
shown as IATA codes when available. The displayed ETA is a rough estimate from
remaining great-circle distance and current ADS-B ground speed; it is not a
scheduled or airline-provided arrival time. Set `route_lookup` to `false` for a
fully local-only installation.

For a real integration, put vendor schema conversion beside its provider. Keep
credentials outside committed configuration (environment variables or a local
secret file). Preserve upstream data timestamps instead of stamping old cached
responses as newly live. Apply provider-specific age thresholds and rate limits
when those API contracts are known. Add persistent caches only when useful.

## Events

The current event contract is `PriorityEvent(module, duration, priority, reason)`.
The scheduler suspends its exact cursor and resumes it after the interrupt.
Modules declare their default priority; conditional eligibility is independent.

Before enabling real automatic events, add an adapter-level stable event ID,
timestamp/TTL, and a bounded deduplication/cooldown policy. Examples: one overhead
event per aircraft passage, or one event per game/score revision. Current manual
events are intentional developer actions, and equal/lower priority interruptions
are coalesced while another event is active.

## REST / generic JSON

A later authenticated `/api/events` route can normalize a bounded payload into
`Message` plus `PriorityEvent`, or update an existing provider snapshot. Validate
event timestamps, input size, module IDs, and duration. Use a local bearer token
or an authenticated reverse proxy before accepting external automation traffic.
The current `/api/scenario` route is for mock development and is not that public
contract. It does not call any third-party service.

Example future message payload:

```json
{
  "event_id": "garage-open-20260912T152400",
  "module": "message",
  "title": "GARAGE",
  "text": "DOOR OPEN",
  "duration_seconds": 10
}
```

## MQTT / Home Assistant

An optional async MQTT adapter could subscribe to `rackticker/events` and publish
device availability. Both REST and MQTT should feed the same normalization/event
adapter. HA discovery and controls can then sit on top of that small contract.
Reconnect with backoff, make stale/unknown status explicit, and expire retained
alert messages before scheduling them. Do not put an MQTT client or HA dependency
inside visual modules or make broker/HA availability a startup prerequisite.

No MQTT client, HA discovery, authentication, or generic external event endpoint
is implemented in Milestone 1.
