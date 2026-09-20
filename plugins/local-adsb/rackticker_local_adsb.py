"""Read receiver JSON without taking ownership of its SDR or changing feeders."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import time
from urllib.parse import quote

import aiohttp
from PIL import Image, ImageDraw

from app.core.airlines import airline
from rackticker import Flight, Plugin, Provider, Snapshot, offload

# An aeroplane does not transmit what it is: dump1090 keeps a database of airframes
# beside its web page and its browser joins the two. Joining it here means the type
# is known the moment a plane appears, with no network and no waiting.
DATABASE_DIRS = (Path("/usr/share/skyaware/html/db"), Path("/usr/share/dump1090-fa/html/db"),
                 Path("/usr/local/share/skyaware/html/db"), Path("/usr/share/readsb/html/db"))


N_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # the FAA skips I and O


def _suffix(rem):
    """The one or two trailing letters an N-number can carry."""
    if rem == 0:
        return ""
    rem -= 1
    first, second = divmod(rem, 25)
    return N_ALPHABET[first] + (N_ALPHABET[second - 1] if second else "")


def tail_number(hexid):
    """The US registration an ICAO address belongs to, N1 through N99999.

    The FAA assigns these in one arithmetic sequence, so the tail number on the
    aeroplane can be worked out from the address it transmits."""
    try:
        value = int(str(hexid), 16)
    except (TypeError, ValueError):
        return ""
    if not 0xA00001 <= value <= 0xADF7C7:
        return ""
    offset = value - 0xA00001
    digit1, rest = divmod(offset, 101711)
    out = f"N{digit1 + 1}"
    if rest < 601:
        return out + _suffix(rest)
    rest -= 601
    digit2, rest = divmod(rest, 10111)
    out += str(digit2)
    if rest < 601:
        return out + _suffix(rest)
    rest -= 601
    digit3, rest = divmod(rest, 951)
    out += str(digit3)
    if rest < 601:
        return out + _suffix(rest)
    rest -= 601
    digit4, rest = divmod(rest, 35)
    out += str(digit4)
    # The last place takes nothing, a letter, or one more digit.
    if rest == 0:
        return out
    return out + (N_ALPHABET[rest - 1] if rest <= 24 else str(rest - 25))


class Airframes:
    """The receiver's own aircraft database, read a little at a time.

    It is split into files by the start of the hex code, each holding the rest of
    the code; 8 MB on disk, a few small files in memory."""

    def __init__(self, folders=DATABASE_DIRS):
        self.folders = tuple(folders)
        self.tables = {}

    def _table(self, prefix):
        if prefix not in self.tables:
            table = {}
            for folder in self.folders:
                try:
                    table = json.loads((folder / f"{prefix}.json").read_text())
                    break
                except (OSError, ValueError):
                    continue
            if len(self.tables) > 48:
                self.tables.pop(next(iter(self.tables)))
            self.tables[prefix] = table
        return self.tables[prefix]

    def find(self, identity):
        """{"icao_type", "registration"} for one hex code, or {} if it is not in there."""
        code = str(identity or "").upper()
        if len(code) != 6 or any(c not in "0123456789ABCDEF" for c in code):
            return {}
        for cut in (1, 2, 3, 4):
            entry = self._table(code[:cut]).get(code[cut:])
            if isinstance(entry, dict) and (entry.get("t") or entry.get("r")):
                return {"icao_type": str(entry.get("t") or "").strip().upper(),
                        "registration": str(entry.get("r") or "").strip().upper()}
        return {}


airframes = Airframes()

# Free, key-free community aggregators with the readsb aircraft JSON format.
NETWORK_FEEDS = (("adsb_lol", "https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}"),
                 ("adsb_fi", "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{nm}"))


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Invalid receiver number")
    return value


def optional_number(value, low, high):
    try:
        return round(number(value, low, high))
    except ValueError:
        return None


def distance_bearing(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    delta = math.radians(lon2 - lon1)
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(delta/2)**2
    distance = 3958.7613 * 2 * math.asin(math.sqrt(min(1, max(0, h))))
    bearing = math.degrees(math.atan2(math.sin(delta)*math.cos(b),
                                    math.cos(a)*math.sin(b)-math.sin(a)*math.cos(b)*math.cos(delta))) % 360
    return distance, round(bearing) % 360


def airport_code(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid route airport")
    iata = str(value.get("iata_code") or "").strip().upper()
    icao = str(value.get("icao_code") or "").strip().upper()
    code = iata if len(iata) == 3 and iata.isalnum() else icao
    if not 3 <= len(code) <= 4 or not code.isalnum():
        raise ValueError("Invalid route airport code")
    return code


def apply_route(flight, latitude, longitude, payload):
    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(response, dict):
        raise ValueError("Invalid ADSBDB response")
    route = response.get("flightroute")
    if not isinstance(route, dict):
        return airframe(flight, response)
    origin, destination = route.get("origin"), route.get("destination")
    origin_code, destination_code = airport_code(origin), airport_code(destination)
    destination_lat = number(destination.get("latitude"), -90, 90)
    destination_lon = number(destination.get("longitude"), -180, 180)
    remaining_miles, _ = distance_bearing(latitude, longitude, destination_lat, destination_lon)
    # ADS-B ground speed is knots; distance_bearing returns statute miles.
    eta = None if not flight.speed_kts or flight.speed_kts < 30 else round(
        remaining_miles / 1.150779448 * 60 / flight.speed_kts)
    eta = eta if eta is not None and 0 <= eta <= 24 * 60 else None
    aircraft = response.get("aircraft")
    aircraft_type, registration = flight.aircraft, flight.registration
    if isinstance(aircraft, dict):
        candidate = str(aircraft.get("icao_type") or "").strip().upper()
        if 2 <= len(candidate) <= 4 and candidate.isalnum():
            aircraft_type = candidate
        candidate = str(aircraft.get("registration") or "").strip().upper()
        if 2 <= len(candidate) <= 10 and all(c.isalnum() or c == "-" for c in candidate):
            registration = candidate
    return replace(flight, aircraft=aircraft_type, origin=origin_code,
                   destination=destination_code, registration=registration, eta_minutes=eta)


def airframe(flight, response):
    aircraft = response.get("aircraft")
    if not isinstance(aircraft, dict):
        return flight
    candidate = str(aircraft.get("icao_type") or "").strip().upper()
    if 2 <= len(candidate) <= 4 and candidate.isalnum() and flight.aircraft == "ADS-B":
        flight = replace(flight, aircraft=candidate)
    return flight


def route_cities(payload):
    """("SANTA ANA", "ATLANTA") from an ADSBDB route, for people who do not read airport codes."""
    response = payload.get("response") if isinstance(payload, dict) else None
    route = response.get("flightroute") if isinstance(response, dict) else None
    if not isinstance(route, dict):
        return None
    names = []
    for end in (route.get("origin"), route.get("destination")):
        name = str((end or {}).get("municipality") or "").strip().upper()
        if not 2 <= len(name) <= 40:
            return None
        names.append(name)
    return tuple(names)


def airport_name(end):
    """"San Francisco International Airport" -> "San Francisco Intl"; an airport
    not named after its city keeps its code: "Santa Ana SNA", "New York JFK"."""
    city = " ".join(str(end.get("municipality") or "").split())
    name = " ".join(str(end.get("name") or "").split())
    code = str(end.get("iata_code") or end.get("icao_code") or "").upper()
    if not 2 <= len(city) <= 40:
        return ""
    if name.lower().startswith(city.lower()):
        return f"{city} Intl" if "international" in name.lower() else city
    return f"{city} {code}".strip()


def route_airports(payload):
    response = payload.get("response") if isinstance(payload, dict) else None
    route = response.get("flightroute") if isinstance(response, dict) else None
    if not isinstance(route, dict):
        return None
    names = [airport_name(route.get(key) or {}) for key in ("origin", "destination")]
    return names if all(names) else None


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Receiver JSON exceeds 2 MiB")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Receiver JSON must contain an object")
    return value


def select_aircraft(data, receiver, settings, now, collect=None):
    """Nearest airborne aircraft inside the radius. `collect`, when given, receives
    every candidate (distance, identity, Flight, lat, lon), nearest first."""
    timestamp = number(data.get("now"), 0, now + 5)
    age = max(0, now - timestamp)
    if age > settings["max_age_seconds"]:
        raise ValueError("Receiver data is stale")
    # No public default location. Use the receiver's existing private location.
    lat = number(receiver.get("lat"), -90, 90)
    lon = number(receiver.get("lon"), -180, 180)
    aircraft = data.get("aircraft")
    if not isinstance(aircraft, list):
        raise ValueError("Receiver aircraft must be an array")
    candidates, tracks = [], {}
    tracked = positioned = 0
    for item in aircraft:
        try:
            if not isinstance(item, dict) or item.get("alt_baro") == "ground":
                continue
            identity = str(item.get("hex", "")).strip().upper()
            if not identity or len(identity) > 8:
                continue
            seen = number(item.get("seen", item.get("seen_pos")), 0, 86400)
            if age + seen > settings["max_age_seconds"]:
                continue
            tracked += 1
            seen = number(item.get("seen_pos"), 0, 86400)
            if age + seen > settings["max_age_seconds"]:
                continue
            item_lat = number(item.get("lat"), -90, 90)
            item_lon = number(item.get("lon"), -180, 180)
            distance, bearing = distance_bearing(lat, lon, item_lat, item_lon)
            positioned += 1
            if distance > settings["radius_miles"]:
                continue
            # Network feeds (and newer readsb builds) include type and registration.
            kind = str(item.get("t") or "").strip().upper()
            registration = str(item.get("r") or "").strip().upper()
            if not kind or not registration:   # a receiver reports only what was transmitted
                known = airframes.find(identity)
                kind = kind or known.get("icao_type", "")
                registration = registration or known.get("registration", "") or tail_number(identity)
            # With no callsign, the tail number is what is painted on the aeroplane;
            # its ICAO address is what nobody outside this hobby has ever read.
            callsign = str(item.get("flight") or "").strip().upper()[:8] or registration or identity
            flight = Flight(callsign, kind if 2 <= len(kind) <= 4 and kind.isalnum() else "ADS-B", "---", "---",
                            optional_number(item.get("alt_baro"), -2000, 100000),
                            optional_number(item.get("gs"), 0, 2000), distance, bearing,
                            optional_number(item.get("baro_rate"), -20000, 20000),
                            registration if 2 <= len(registration) <= 10 else "")
            candidates.append((distance, identity, flight, item_lat, item_lon))
            tracks[identity] = optional_number(item.get("track"), 0, 360)
        except (ValueError, TypeError, OverflowError):
            continue  # One malformed report must not hide other valid aircraft.
    candidates.sort(key=lambda row: (row[0], row[1]))
    if collect is not None:
        collect.extend(candidates)
    winner = candidates[0] if candidates else None
    nearby = [aircraft_row(row[2], row[1], tracks.get(row[1]), index == 0)
              for index, row in enumerate(candidates[:16])]
    return timestamp, winner, {"tracked_aircraft": tracked, "positioned_aircraft": positioned,
                               "nearby": nearby, "radius_miles": settings["radius_miles"]}


def aircraft_row(flight, identity, track=None, nearest=False):
    """What the flight screen needs about one aircraft, as plain data."""
    return {"id": identity, "callsign": flight.callsign, "type": "" if flight.aircraft == "ADS-B" else flight.aircraft,
            "distance": round(flight.distance_miles, 2), "bearing": flight.bearing_deg,
            "altitude": flight.altitude_ft, "vertical_rate": flight.vertical_rate, "speed": flight.speed_kts,
            "track": track, "nearest": nearest, "origin": flight.origin if flight.origin != "---" else "",
            "destination": flight.destination if flight.destination != "---" else ""}


def plausible(payload, latitude, longitude):
    """Drop a route the aircraft is not actually flying. Route databases keep a
    callsign's old route for months (a Delta flight over Orange County listed as
    Chicago to New York); a real flight lies near the line between its airports."""
    try:
        route = payload["response"]["flightroute"]
        ends = [(number(route[key].get("latitude"), -90, 90), number(route[key].get("longitude"), -180, 180))
                for key in ("origin", "destination")]
    except (KeyError, TypeError, ValueError, AttributeError):
        return payload
    length = distance_bearing(*ends[0], *ends[1])[0]
    detour = sum(distance_bearing(latitude, longitude, *end)[0] for end in ends)
    if detour <= length * 1.25 + 60:
        return payload
    response = {key: value for key, value in payload["response"].items() if key != "flightroute"}
    return {"response": response}


def journey(payload, latitude, longitude, speed_kts):
    """How far along its route an aircraft is, and rough minutes flown and to go.

    ADS-B carries no departure time; minutes flown assume the current ground
    speed plus about ten minutes of climb, so the screen shows them as '~'."""
    try:
        route = payload["response"]["flightroute"]
        origin = (number(route["origin"].get("latitude"), -90, 90), number(route["origin"].get("longitude"), -180, 180))
        destination = (number(route["destination"].get("latitude"), -90, 90),
                       number(route["destination"].get("longitude"), -180, 180))
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}
    flown = distance_bearing(origin[0], origin[1], latitude, longitude)[0]
    left = distance_bearing(latitude, longitude, destination[0], destination[1])[0]
    direct = distance_bearing(origin[0], origin[1], destination[0], destination[1])[0]
    # A callsign is reused day after day, so the route on file can belong to a
    # different leg than the one being flown. If the aeroplane is nowhere near the
    # line between these two airports, the route is not this flight's: say nothing
    # rather than "374 minutes to go" over a plane that is minutes from landing.
    if flown + left > max(60.0, direct * 1.4 + 60):
        return {}
    result = {"progress": round(flown / max(1.0, flown + left), 3)}
    if speed_kts and speed_kts >= 60:
        mph = speed_kts * 1.150779448
        result["minutes_flown"] = round(flown / mph * 60 + 10)
        # Still climbing, most of the way to go: at its speed now, a transatlantic
        # departure reads as twenty hours. Far out, assume it will cruise.
        cruise = max(speed_kts, 420) if left > 300 else speed_kts
        result["minutes_left"] = round(left / (cruise * 1.150779448) * 60 + (8 if left < 60 else 15))
    return result


def local_end(payload, home):
    """Which end of the route is the local airport ("origin"/"destination"), so the
    screen can say FROM or TO the far city instead of naming home twice."""
    try:
        route = payload["response"]["flightroute"]
        ends = []
        for key in ("origin", "destination"):
            end = route[key]
            ends.append(distance_bearing(home[0], home[1], number(end.get("latitude"), -90, 90),
                                         number(end.get("longitude"), -180, 180))[0])
    except (KeyError, TypeError, ValueError):
        return None
    nearest = min(range(2), key=lambda index: ends[index])
    return ("origin", "destination")[nearest] if ends[nearest] <= LOCAL_AIRPORT_MILES < ends[1 - nearest] else None


# Airline marks are fetched at runtime and never bundled. Google Flights' set is
# transparent and covers most carriers; Kiwi's tiles fill the gaps.
LOGO_URLS = ("https://www.gstatic.com/flights/airline_logos/70px/{code}.png",
             "https://images.kiwi.com/airlines/64/{code}.png")
LOGO_SIZE = 32
# Aircraft beyond the nearest also get routes and logos, up to this many.
DETAILED = 4
LOCAL_AIRPORT_MILES = 40
# Codes a CDN serves with the wrong artwork (Kiwi answers WN with its own logo).
UNUSABLE_LOGOS = {("images.kiwi.com", "WN")}


def shrink_logo(raw, size=LOGO_SIZE):
    """A full-height tail mark. A white or light tile is cut away so the mark sits
    on the black panel instead of a glaring lit square; brand-coloured tiles stay.
    Premultiplied box downscale keeps small marks clean instead of fringed.
    Returns b"" for a CDN's grey placeholder tail."""
    image = Image.open(BytesIO(raw)).convert("RGBA")
    opaque = [pixel for pixel in image.getdata() if pixel[3] > 128]
    if opaque and all(max(pixel[:3]) - min(pixel[:3]) < 12 for pixel in opaque):
        return b""  # Only greys: the generic "unknown airline" artwork.
    if image.size != (64, 64):
        image = image.resize((64, 64), Image.Resampling.LANCZOS)
    seeds = ((3, 3), (60, 3), (3, 60), (60, 60), (32, 1), (1, 32), (62, 32), (32, 62))
    samples = [image.getpixel(seed) for seed in seeds]
    background = max(samples, key=samples.count)
    if sum(background[:3]) / 3 > 185 and background[3] > 200:
        for seed, pixel in zip(seeds, samples):
            if pixel[3] and max(abs(a - b) for a, b in zip(pixel[:3], background[:3])) < 40:
                ImageDraw.floodfill(image, seed, (0, 0, 0, 0), thresh=70)
    image = image.convert("RGBa")
    image.thumbnail((size, size), Image.Resampling.BOX)
    image = image.convert("RGBA")
    tile = Image.new("RGBA", (size, size))
    visible = [(r + g + b) / 3 for r, g, b, a in image.getdata() if a > 128]
    if visible and sum(visible) / len(visible) < 55:
        # Navy marks vanish on unlit LEDs; give them a dim tile to sit on.
        ImageDraw.Draw(tile).rounded_rectangle((0, 0, size - 1, size - 1), radius=4, fill=(52, 56, 66, 255))
    tile.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    output = BytesIO()
    tile.save(output, format="PNG", optimize=True)
    return output.getvalue()


class LocalADSB(Provider):
    def __init__(self, context):
        self.context = context
        self.alerted = {}
        self.last_alert = float("-inf")
        self.routes = {}
        self.lookups = {}   # (hex, callsign) -> lookup running in the background
        self.session = None
        self.network_cache = None
        self.cities = None
        self.logos = {}
        self.logo_session = None

    def _session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4),
                headers={"User-Agent": "RackTicker/1.0 (+https://github.com/costamesatechsolutions/rackticker)"})
        return self.session

    async def _json(self, url):
        async with self._session().get(url) as response:
            if response.status == 404:
                return {}
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _adsbdb(self, path):
        payload = await self._json(f"https://api.adsbdb.com/v0/{path}")
        response = payload.get("response") if isinstance(payload, dict) else None
        return response if isinstance(response, dict) else {}

    async def _adsb_lol(self, identity):
        """Type and registration from adsb.lol's aircraft database, which knows many
        airline airframes ADSBDB does not (United, Delta)."""
        payload = await self._json(f"https://api.adsb.lol/v2/hex/{quote(identity)}")
        rows = payload.get("ac") if isinstance(payload, dict) else None
        row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        kind, registration = str(row.get("t") or "").upper(), str(row.get("r") or "").upper()
        return {"icao_type": kind, "registration": registration} if kind or registration else {}

    async def _lookup(self, key, settings):
        """Route and airframe for one aircraft, asked of every source at once."""
        identity, callsign = key
        airline_flight = callsign != identity and callsign.isalnum() and airline(callsign) is not None
        asks = [self._adsbdb(f"aircraft/{quote(identity)}"), self._adsb_lol(identity)]
        if airline_flight:  # private and military flights publish no route; the airframe is the story
            asks.append(self._adsbdb(f"callsign/{quote(callsign)}"))
        results = await asyncio.gather(*asks, return_exceptions=True)
        answered = [r for r in results if not isinstance(r, Exception)]
        facts = {}
        for result in answered:
            if isinstance(result.get("aircraft"), dict):
                facts["aircraft"] = result["aircraft"]
            if isinstance(result.get("flightroute"), dict):
                facts["flightroute"] = result["flightroute"]
        spare = results[1] if not isinstance(results[1], Exception) else {}
        aircraft = facts.get("aircraft") or {}
        if spare and not str(aircraft.get("icao_type") or "").strip():
            aircraft = {**aircraft, **{k: v for k, v in spare.items() if v}}
            facts["aircraft"] = aircraft
        if not str(aircraft.get("icao_type") or "").strip():
            local = airframes.find(identity)
            if local:
                facts["aircraft"] = {**aircraft, **local}
        payload = {"response": facts} if facts else None
        # A good answer keeps for hours; a miss or a timeout is asked again in two minutes.
        complete = "aircraft" in facts and ("flightroute" in facts or not airline_flight)
        life = settings["route_cache_seconds"] if complete else 120
        self.routes[key] = (time.monotonic() + life, payload)
        if len(self.routes) > 512:
            self.routes.pop(next(iter(self.routes)))

    def route(self, identity, callsign, settings):
        """What is known about one aircraft ({"aircraft", "flightroute"}), right away.
        Lookups run in the background: the screen never waits on the network, and
        a new plane gains its route and type a poll or two after it appears."""
        if not settings["route_lookup"]:
            return None
        key = (identity, callsign)
        cached = self.routes.get(key)
        if (not cached or cached[0] <= time.monotonic()) and key not in self.lookups:
            task = asyncio.create_task(self._lookup(key, settings))
            self.lookups[key] = task
            task.add_done_callback(lambda _, key=key: self.lookups.pop(key, None))
        return cached[1] if cached else None

    async def enrich(self, candidates, rows, settings, home):
        """Routes for the nearest few aircraft; returns the nearest as a Flight."""
        self.cities = None
        if not candidates:
            return None
        detailed = candidates[:DETAILED]
        payloads = [self.route(row[1], row[2].callsign, settings) for row in detailed]
        winner = None
        for index, ((_, identity, flight, latitude, longitude), payload) in enumerate(zip(detailed, payloads)):
            payload = plausible(payload, latitude, longitude)
            if payload is not None:
                try:
                    flight = apply_route(flight, latitude, longitude, payload)
                    cities = route_cities(payload)
                except (ValueError, TypeError, OverflowError):
                    cities = None
                row = rows[index] if index < len(rows) else None
                if row is not None and row["id"] == identity:
                    row.update(aircraft_row(flight, identity, row["track"], row["nearest"]))
                    if cities:
                        row["cities"] = list(cities)
                    airports = route_airports(payload)
                    if airports:
                        row["airports"] = airports
                    if home:
                        row["local"] = local_end(payload, home)
                    row.update(journey(payload, latitude, longitude, flight.speed_kts))
                if index == 0:
                    self.cities = cities
            if index == 0:
                winner = flight
        return winner

    async def close(self):
        for task in list(self.lookups.values()):
            task.cancel()
        for session in (self.session, self.logo_session):
            if session is not None and not session.closed:
                await session.close()

    async def airline_logo(self, callsign):
        """A small airline mark fetched at runtime (never bundled) and cached per airline.
        At most one download per poll keeps the provider inside its deadline."""
        carrier = airline(callsign)
        if not carrier:
            return b""
        code = carrier[0]
        if code in self.logos:
            return self.logos[code]
        if self.logo_session is None or self.logo_session.closed:
            self.logo_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2),
                headers={"User-Agent": "RackTicker/0.2 (+https://github.com/costamesatechsolutions/rackticker)"})
        packed = b""
        for template in LOGO_URLS:
            url = template.format(code=code)
            if (url.split("/")[2], code) in UNUSABLE_LOGOS:
                continue
            try:
                raw = await self._download(url)
                packed = await offload(shrink_logo, raw) if raw else b""
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
                return b""  # Try again on a later poll.
            if packed:
                break
        self.logos[code] = packed
        return packed

    async def _download(self, url):
        async with self.logo_session.get(url) as response:
            response.raise_for_status()
            # Unknown airlines redirect to the CDN's generic artwork; that is not their mark.
            if response.url.path.endswith("/airlines.png"):
                return b""
            raw = await response.read()
        if len(raw) > 200_000:
            raise ValueError("Logo is too large")
        return raw

    async def _network(self, settings, local_error):
        """Community ADS-B aggregators around the configured location, used only
        while the local receiver is unplugged, stopped or stale."""
        latitude, longitude = settings["latitude"], settings["longitude"]
        if not settings["network_fallback"] or (latitude == 0 and longitude == 0):
            raise local_error
        tick = time.monotonic()
        if self.network_cache and tick < self.network_cache[0]:
            return self.network_cache[1]
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4),
                headers={"User-Agent": "RackTicker/0.2 (+https://github.com/costamesatechsolutions/rackticker)"})
        nautical = max(1, min(250, math.ceil(settings["radius_miles"] / 1.150779448)))
        for name, template in NETWORK_FEEDS:
            try:
                async with self.session.get(template.format(lat=f"{latitude:.4f}", lon=f"{longitude:.4f}",
                                                            nm=nautical)) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                aircraft = payload.get("ac") if isinstance(payload, dict) else None
                if not isinstance(aircraft, list):
                    aircraft = payload.get("aircraft") if isinstance(payload, dict) else None
                if not isinstance(aircraft, list):
                    raise ValueError("Unexpected ADS-B network response")
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                continue
            result = ({"now": time.time(), "aircraft": aircraft}, {"lat": latitude, "lon": longitude}, name)
            self.network_cache = (tick + 8, result)
            return result
        raise ConnectionError("Local receiver unavailable and no ADS-B network feed answered")

    async def fetch(self):
        settings = self.context.settings
        source, feed = "local_adsb", None
        candidates = []
        try:
            data, receiver = await asyncio.gather(
                asyncio.to_thread(read_json, settings["aircraft_path"]),
                asyncio.to_thread(read_json, settings["receiver_path"]))
            timestamp, winner, metadata = select_aircraft(data, receiver, settings, time.time(), candidates)
        except (OSError, ValueError) as exc:
            candidates.clear()
            data, receiver, feed = await self._network(settings, exc)
            timestamp, winner, metadata = select_aircraft(data, receiver, settings, time.time(), candidates)
            source = "adsb_network"
        if feed:
            metadata["feed"] = feed
        home = (receiver.get("lat"), receiver.get("lon")) if winner else None
        flight = await self.enrich(candidates, metadata["nearby"], settings, home)
        if flight and self.cities:
            metadata["cities"] = self.cities
        if flight:
            # At most one new download per poll keeps the provider inside its deadline.
            logos, fetched = {}, False
            for row in metadata["nearby"][:DETAILED]:
                carrier = airline(row["callsign"])
                if not carrier or carrier[0] in logos:
                    continue
                if carrier[0] not in self.logos and fetched:
                    continue
                fetched = fetched or carrier[0] not in self.logos
                logo = await self.airline_logo(row["callsign"])
                if logo:
                    logos[carrier[0]] = logo
            if logos:
                metadata["logos"] = logos
        tick = time.monotonic()
        self.alerted = {k: v for k, v in self.alerted.items() if tick - v < 900}
        if winner and winner[0] <= settings["interrupt_radius_miles"] and settings["interrupts"]:
            _, identity, *_ = winner
            if identity not in self.alerted and tick - self.last_alert >= settings["cooldown_seconds"]:
                if self.context.emit_event("flight", 10):
                    self.alerted[identity] = tick
                    self.last_alert = tick
                    if len(self.alerted) > 512:
                        self.alerted.pop(next(iter(self.alerted)))
        return Snapshot(flight,
                        updated_at=datetime.fromtimestamp(timestamp, timezone.utc), source=source,
                        metadata=metadata)


def validate(settings):
    for key in ("aircraft_path", "receiver_path"):
        if (not isinstance(settings[key], str)
                or not (Path(settings[key]).is_absolute() or PurePosixPath(settings[key]).is_absolute())):
            raise ValueError(f"{key} must be an absolute local path")
    number(settings["radius_miles"], .1, 200)
    number(settings["interrupt_radius_miles"], .1, 200)
    if settings["interrupt_radius_miles"] > settings["radius_miles"]:
        raise ValueError("interrupt_radius_miles cannot exceed radius_miles")
    number(settings["max_age_seconds"], 1, 30)
    number(settings["cooldown_seconds"], 10, 3600)
    number(settings["route_cache_seconds"], 900, 86400)
    if type(settings["interrupts"]) is not bool:
        raise ValueError("interrupts must be boolean")
    if type(settings["route_lookup"]) is not bool:
        raise ValueError("route_lookup must be boolean")
    if type(settings["network_fallback"]) is not bool:
        raise ValueError("network_fallback must be boolean")
    number(settings["latitude"], -90, 90)
    number(settings["longitude"], -180, 180)


plugin = Plugin("local_adsb", "ADS-B flights", provider=LocalADSB, provider_for="flight",
                defaults={"aircraft_path": "/run/dump1090-fa/aircraft.json",
                          "receiver_path": "/run/dump1090-fa/receiver.json",
                          "radius_miles": 35, "interrupt_radius_miles": 2.5, "max_age_seconds": 15,
                          "interrupts": True, "cooldown_seconds": 600,
                          "route_lookup": False, "route_cache_seconds": 21600,
                          # Location for the network fallback; 0,0 disables it.
                          "network_fallback": True, "latitude": 0.0, "longitude": 0.0},
                validate_settings=validate,
                help={"network_fallback": "Use free community ADS-B feeds while your antenna is offline",
                      "latitude": "Fallback location; 0 and 0 disables the network feed",
                      "interrupt_radius_miles": "Aircraft this close take over the display"},
    ui={"aircraft_path": {"advanced": True}, "receiver_path": {"advanced": True}, "max_age_seconds": {"advanced": True}, "route_cache_seconds": {"advanced": True}, "cooldown_seconds": {"advanced": True, "label": "Seconds between take-overs"}, "latitude": {"type": "location", "label": "Location for the network feed"}, "longitude": {"advanced": True}, "radius_miles": {"type": "slider", "min": 1, "max": 50, "unit": "mi", "label": "Watch radius"}, "interrupt_radius_miles": {"type": "slider", "min": 0.5, "max": 10, "step": 0.5, "unit": "mi", "label": "Take over within"}, "route_lookup": {"label": "Look up routes and aircraft"}})
