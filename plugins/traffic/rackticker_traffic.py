"""Freeway traffic board for California: what the freeways near you are doing.

- Incidents from the California Highway Patrol's public media feed: a freeway
  shield, what happened, where and how long ago. Only the incident type,
  freeway, direction and cross street are shown; free-text dispatch details are
  never parsed or displayed.
- The overhead message signs near you, drawn like the real amber signs, from
  Caltrans' public sign feed.
- Drive times from Caltrans' travel-time segments, coloured by how slow they
  are running against the quickest seen.
Everything is found from your location: no dispatch centre or freeway list to
type in.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import math
import re
import time
from zoneinfo import ZoneInfo

import aiohttp
from PIL import Image, ImageDraw

from rackticker import Plugin, Provider, Snapshot, Module, new_frame, offload
from app.core.fonts import draw_text, draw_tiny, text_width, tiny_width
from app.core.fx import ease_out
from app.core.story import Storyboard
from app.modules.base import missing, stale_marker

FEED = "https://media.chp.ca.gov/sa_xml/sa.xml"
PACIFIC = ZoneInfo("America/Los_Angeles")
RED, AMBER, WHITE, DULL = (255, 70, 50), (255, 176, 0), (236, 240, 236), (120, 110, 90)
BLUE, GREEN, ORANGE = (40, 90, 220), (40, 150, 80), (255, 120, 30)
ALL_CLEAR = (90, 235, 120)
GREEN_TEXT = (90, 235, 120)
# The body fills the panel below the header: no bottom crawl repeating the cards.
BODY_TOP, BODY_HEIGHT = 9, 23
MAX_CARDS = 6

# (kind shown on the panel, detail, severity rank, colour) by CHP log type.
KINDS = (
    (re.compile(r"sig\s*alert", re.I), "SIGALERT", "", 0, RED),
    (re.compile(r"closure", re.I), "CLOSURE", "", 1, RED),
    (re.compile(r"^1180\b|fatal", re.I), "MAJOR CRASH", "", 2, RED),
    (re.compile(r"^1179\b"), "CRASH", "INJURIES", 2, RED),
    (re.compile(r"^1181\b"), "CRASH", "MINOR INJ", 3, ORANGE),
    (re.compile(r"^1182\b"), "CRASH", "NO INJURY", 3, ORANGE),
    (re.compile(r"^1183\b|collision", re.I), "CRASH", "", 3, ORANGE),
    (re.compile(r"^2000[12]\b|hit and run", re.I), "HIT & RUN", "", 4, ORANGE),
    (re.compile(r"fire", re.I), "CAR FIRE", "", 2, RED),
    (re.compile(r"animal", re.I), "ANIMAL", "", 5, AMBER),
    (re.compile(r"^1125\b|hazard", re.I), "HAZARD", "", 5, AMBER),
    (re.compile(r"^1126\b|disabled|stalled", re.I), "STALLED CAR", "", 6, AMBER),
    (re.compile(r"traffic break", re.I), "TRAFFIC BREAK", "", 4, AMBER),
)
IGNORED = re.compile(r"assist|caltrans|maintenance|wanted|escort|detail|request", re.I)
LOG = re.compile(
    r'<LogTime>"(?P<time>.*?)"</LogTime>\s*<LogType>"(?P<type>.*?)"</LogType>\s*'
    r'<Location>"(?P<location>.*?)"</Location>.*?<Area>"(?P<area>.*?)"</Area>.*?'
    r'<LATLON>"(?P<latlon>.*?)"</LATLON>', re.S)
ROUTE = re.compile(r"^\s*(?P<system>I|SR|US)[- ]?(?P<number>\d{1,3})\s*(?P<dir>[NSEW])?\b\s*(?:/\s*(?P<cross>.*))?$", re.I)
DIRECTIONS = {"N": "NB", "S": "SB", "E": "EB", "W": "WB"}


def classify(log_type):
    code = log_type.strip()
    if IGNORED.search(code):
        return None
    for pattern, kind, detail, rank, color in KINDS:
        if pattern.search(code):
            return kind, detail, rank, color
    return None


def parse_route(location):
    match = ROUTE.match(location or "")
    if not match:
        return None
    cross = re.sub(r"\s+", " ", (match["cross"] or "").strip()).upper()
    cross = re.sub(r"\b(ONR|OFR)\b", lambda m: "ON-RAMP" if m[1] == "ONR" else "OFF-RAMP", cross)
    return match["system"].upper(), match["number"], DIRECTIONS.get((match["dir"] or "").upper(), ""), cross


def _latlon(value):
    try:
        lat, lon = value.split(":")
        return int(lat) / 1_000_000, -int(lon) / 1_000_000
    except (ValueError, AttributeError):
        return None


def miles(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def parse_feed(xml, centers, home=None, radius_miles=15):
    incidents, seen = [], {}
    for block in xml.split('<Dispatch ID = "')[1:]:
        if centers is not None and block[:block.find('"')] not in centers:
            continue
        for log in LOG.finditer(block):
            kind = classify(log["type"])
            route = parse_route(log["location"])
            if not kind or not route:
                continue
            spot = _latlon(log["latlon"])
            if home and spot and miles(home, spot) > radius_miles:
                continue
            try:
                at = datetime.strptime(" ".join(log["time"].split()), "%b %d %Y %I:%M%p").replace(tzinfo=PACIFIC)
            except ValueError:
                continue
            system, number, direction, cross = route
            incident = {"kind": kind[0], "detail": kind[1], "rank": kind[2], "color": kind[3], "system": system,
                        "route": number, "direction": direction, "cross": cross, "at": at,
                        "patrol": "FSP" in log["area"].upper()}
            # Freeway Service Patrol re-logs CHP incidents; keep one per spot and kind.
            key = (system, number, direction, cross, kind[0])
            previous = seen.get(key)
            if previous is None:
                seen[key] = incident
                incidents.append(incident)
            elif previous["patrol"] and not incident["patrol"]:
                incidents[incidents.index(previous)] = seen[key] = incident
    return sorted(incidents, key=lambda row: (row["rank"], -row["at"].timestamp()))


def recent(incidents, now, hours):
    """CHP keeps cleared logs in the feed for a long time; only show what is plausibly still active."""
    return [row for row in incidents if (now - row["at"]).total_seconds() <= hours * 3600]


def age_label(at, now):
    minutes = max(0, int((now - at).total_seconds() // 60))
    if minutes < 1:
        return "NOW"
    return f"{minutes}M" if minutes < 60 else f"{minutes // 60}H{minutes % 60:02d}" if minutes < 600 else f"{minutes // 60}H"


def shield(image, system, number, x, y):
    """20×17 route marker: interstate red/blue shield, green state spade, white US shield."""
    draw = ImageDraw.Draw(image)
    if system == "I":
        draw.rounded_rectangle((x, y, x + 19, y + 16), radius=4, fill=BLUE)
        draw.rectangle((x + 1, y, x + 18, y + 3), fill=RED)
        draw.point([(x, y), (x + 19, y)], fill=(0, 0, 0))
        ink = WHITE
    elif system == "US":
        draw.rounded_rectangle((x, y, x + 19, y + 16), radius=3, fill=WHITE)
        ink = (0, 0, 0)
    else:
        draw.rounded_rectangle((x, y, x + 19, y + 16), radius=6, fill=GREEN)
        ink = WHITE
    top = y + 6 if system == "I" else y + 5
    if text_width(number) <= 18:
        draw_text(image, number, x + 10 - text_width(number) // 2, top, ink)
    else:
        draw_tiny(image, number, x + 10 - tiny_width(number) // 2, top + 1, ink)


def freeways(value):
    """"I-405, SR-73" -> [("I", "405"), ("SR", "73")] for the all-clear shields."""
    result = []
    for part in value.split(","):
        match = re.fullmatch(r"\s*(I|SR|US)[- ]?(\d{1,3})\s*", part, re.I)
        if match:
            result.append((match[1].upper(), match[2]))
    return result


def fit_text(text, width):
    while text and text_width(text) > width:
        text = text[:-1].rstrip()
    return text


def fit_tiny(text, width):
    while text and tiny_width(text) > width:
        text = text[:-1].rstrip()
    return text



CALTRANS = "https://cwwp2.dot.ca.gov/data/d{district}/{kind}/{kind}StatusD{padded}.json"
# Caltrans district offices, to pick the districts around a location.
DISTRICTS = {1: (40.80, -124.16), 2: (40.59, -122.39), 3: (38.58, -121.49), 4: (37.80, -122.27),
             5: (35.28, -120.66), 6: (36.74, -119.79), 7: (34.05, -118.25), 8: (34.10, -117.29),
             9: (37.36, -118.39), 10: (37.95, -121.29), 11: (32.72, -117.16), 12: (33.68, -117.83)}
TEMPLATE = re.compile(r"\bRTE\s*\d+|MINUTES TO|\bMIN\b", re.I)
SIGN_NUMBER = re.compile(r"CMS\s*(\d+)", re.I)
SIGN_AMBER = (255, 160, 0)


def districts_near(home, miles=70):
    ranked = sorted(DISTRICTS, key=lambda district: miles_between(home, DISTRICTS[district]))
    near = [district for district in ranked if miles_between(home, DISTRICTS[district]) <= miles]
    return (near or ranked[:1])[:2]


def miles_between(a, b):
    return miles(a, b)


def _route(text):
    match = re.fullmatch(r"\s*(I|SR|US)[- ]?(\d{1,3})\s*", str(text or ""), re.I)
    return (match[1].upper(), match[2]) if match else None


def parse_signs(payloads, home, radius_miles):
    """(active sign messages, freeways with signs nearby, sign positions by number)."""
    signs, routes, positions = {}, {}, {}
    for payload in payloads:
        for item in (payload or {}).get("data") or []:
            sign = item.get("cms") or {}
            where = sign.get("location") or {}
            try:
                spot = (float(where["latitude"]), float(where["longitude"]))
            except (KeyError, TypeError, ValueError):
                continue
            number = SIGN_NUMBER.search(str(where.get("locationName") or ""))
            if number:
                positions[number[1]] = spot
            distance = miles(home, spot)
            if distance > radius_miles:
                continue
            route = _route(where.get("route"))
            if route and (route not in routes or distance < routes[route]):
                routes[route] = distance
            message = sign.get("message") or {}
            if sign.get("inService") != "True" or message.get("display") == "Blank":
                continue
            phases = []
            for phase in ("phase1", "phase2"):
                block = message.get(phase) or {}
                lines = [" ".join(str(block.get(f"{phase}Line{n}") or "").split()).upper() for n in (1, 2, 3)]
                if any(lines):
                    phases.append(lines)
            text = " ".join(" ".join(lines) for lines in phases)
            if not phases or TEMPLATE.search(text):
                continue  # travel-time templates are shown on the drive-times card instead
            if text not in signs or distance < signs[text]["miles"]:
                signs[text] = {"route": route, "direction": str(where.get("direction") or "")[:1].upper(),
                               "place": str(where.get("nearbyPlace") or "").upper(), "phases": phases,
                               "miles": round(distance, 1)}
    ordered = sorted(signs.values(), key=lambda sign: sign["miles"])
    return ordered[:4], [route for route, _ in sorted(routes.items(), key=lambda item: item[1])], positions


def parse_travel_times(payloads, positions, home, radius_miles):
    """Drive times for segments that start at a sign near home."""
    times = []
    for payload in payloads:
        for item in (payload or {}).get("data") or []:
            segment = item.get("tt") or {}
            where = segment.get("location") or {}
            begin, end = where.get("begin") or {}, where.get("end") or {}
            number = SIGN_NUMBER.search(str(begin.get("beginFreeFormDescription") or ""))
            spot = positions.get(number[1]) if number else None
            if spot is None or miles(home, spot) > radius_miles:
                continue
            try:
                seconds = int((segment.get("traveltime") or {}).get("calculatedTraveltime"))
            except (TypeError, ValueError):
                continue
            route = _route(begin.get("beginRoute"))
            to = destination(end.get("endLocationName"))
            if not route or seconds <= 0 or not to or to == f"RTE {route[1]}":
                continue  # the feed lists a few segments "to" their own freeway: no sign says that
            place = re.sub(r"\d+$", "", " ".join(str(begin.get("beginLocationName") or "").upper().split())).strip()
            times.append({"route": route, "direction": str(where.get("travelFlowDirection") or "")[:1].upper(),
                          "to": to, "seconds": seconds, "sign": number[1], "at": place,
                          "miles": round(miles(home, spot), 1),
                          "key": f"{begin.get('beginRoute')}|{begin.get('beginLocationName')}|{end.get('endLocationName')}"})
    return times


def destination(name):
    """How the sign writes it: "74 FWY" -> "RTE 74", "JWA" -> "AIRPORT"."""
    name = " ".join(str(name or "").upper().split())
    number = re.fullmatch(r"(\d{1,3})\s*(FWY|HWY)?", name)
    if number:
        return f"RTE {number[1]}"
    return {"JWA": "AIRPORT", "LAX": "AIRPORT"}.get(name, name)[:12]


class TrafficProvider(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.cached = None
        self.cache_until = 0.0
        self.quickest = {}   # travel-time segment -> quickest seconds seen, the "no traffic" baseline

    async def _get(self, url, text=False):
        async with self.session.get(url) as response:
            response.raise_for_status()
            return await (response.text(errors="replace") if text else response.json(content_type=None))

    async def fetch(self):
        now = time.monotonic()
        settings = self.context.settings
        if self.cached is None or now >= self.cache_until:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=5),
                    headers={"User-Agent": "RackTicker/0.2 (+https://github.com/costamesatechsolutions/rackticker)"})
            home = (settings["latitude"], settings["longitude"]) if settings["latitude"] or settings["longitude"] else None
            chosen = settings["dispatch_centers"].strip().lower()
            centers = None if chosen in ("", "auto") and home else \
                {code.strip().upper() for code in settings["dispatch_centers"].split(",") if code.strip() and code.strip().lower() != "auto"} or {"OCCC"}
            xml = await self._get(FEED, text=True)
            incidents = await offload(parse_feed, xml, centers, home, settings["radius_miles"])
            signs, routes, drive = [], [], []
            if home:
                districts = districts_near(home)
                urls = [(kind, CALTRANS.format(district=district, kind=kind, padded=f"{district:02d}"))
                        for district in districts for kind in ("cms", "tt")]
                results = await asyncio.gather(*(self._get(url) for _, url in urls), return_exceptions=True)
                cms = [result for (kind, _), result in zip(urls, results) if kind == "cms" and not isinstance(result, Exception)]
                tt = [result for (kind, _), result in zip(urls, results) if kind == "tt" and not isinstance(result, Exception)]
                signs, routes, positions = parse_signs(cms, home, settings["radius_miles"])
                drive = parse_travel_times(tt, positions, home, settings["radius_miles"])
                for segment in drive:
                    best = self.quickest.get(segment["key"])
                    self.quickest[segment["key"]] = min(best or segment["seconds"], segment["seconds"])
                    segment["normal"] = self.quickest[segment["key"]]
            self.cached = {"incidents": recent(incidents, datetime.now(PACIFIC), settings["max_age_hours"]),
                           "signs": signs, "freeways": routes[:4], "drive": drive}
            self.cache_until = now + settings["refresh_seconds"]
        return Snapshot(self.cached, source="chp")

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()


def board(snapshot):
    """The provider's board; older snapshots were a bare incident list."""
    data = snapshot.data if snapshot else None
    if isinstance(data, list):
        return {"incidents": data, "signs": [], "freeways": [], "drive": []}
    return data or {"incidents": [], "signs": [], "freeways": [], "drive": []}


class TrafficModule(Module):
    name = "traffic"

    def __init__(self):
        self.board = Storyboard()

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _deck(self, context):
        snap = context.snapshots.get("traffic")
        if not snap or snap.data is None:
            return [], {}
        data = board(snap)
        deck = [("incident", incident) for incident in data["incidents"][:MAX_CARDS]]
        if not deck and not snap.error:
            deck.append(("clear", None))
        deck += [("sign", sign) for sign in data["signs"][:3]]
        # One card per nearby travel-time sign, the nearest first, drawn as the sign reads.
        by_sign = {}
        for row in sorted(data["drive"], key=lambda row: row.get("miles", 0)):
            by_sign.setdefault(row.get("sign") or row["key"], []).append(row)
        deck += [("drive", rows[:2]) for rows in list(by_sign.values())[:2]]
        return deck, data

    def available(self, context):
        deck, _ = self._deck(context)
        return any(kind != "clear" for kind, _ in deck)

    def _card(self, context):
        deck, data = self._deck(context)
        seconds = context.config["plugins"][self.name]["card_seconds"]
        build = lambda _visit: [(card, seconds + (2 if card[0] in ("sign", "drive") else 0)) for card in deck]
        self.board.sync(context.animation_time, build, context.scene)
        return self.board.current(context.animation_time, build), data

    def hold(self, context):
        card, _ = self._card(context)
        return bool(card) and self.board.hold()

    def render(self, context):
        card, data = self._card(context)
        if not card:
            return missing("TRAFFIC")
        (kind, item), local, _ = card
        t = context.animation_time
        frame = new_frame()
        if kind == "clear":
            return self._clear(context, data)
        if kind == "sign":
            return self._sign(frame, item, local)
        if kind == "drive":
            return self._drive(frame, item, local)
        draw = ImageDraw.Draw(frame)
        draw_tiny(frame, "TRAFFIC", 0, 1, AMBER)
        incidents = data["incidents"]
        count = f"{len(incidents)} INCIDENTS" if len(incidents) != 1 else "1 INCIDENT"
        draw_tiny(frame, count, 128 - tiny_width(count), 1, DULL)
        for px in range(0, 128, 2):
            draw.point((px, 7), fill=(70, 50, 12))
        body = Image.new("RGB", (128, BODY_HEIGHT))
        self._body(body, item, context.now, t)
        roll = round((1 - ease_out(local / .35)) * BODY_HEIGHT) if local < .35 else 0
        if roll < BODY_HEIGHT:
            frame.paste(body.crop((0, 0, 128, BODY_HEIGHT - roll)), (0, BODY_TOP + roll))
        return stale_marker(frame, context.snapshots.get("traffic"))

    @staticmethod
    def _sign(frame, sign, local):
        """The overhead message sign itself: three amber lines, phases alternating."""
        route = sign.get("route")
        label = " ".join(part for part in (f"{route[0]}-{route[1]}" if route else "", sign["direction"],
                                           sign["place"]) if part)
        draw_tiny(frame, fit_tiny(label, 128), 64 - tiny_width(fit_tiny(label, 128)) // 2, 0, (120, 80, 0))
        phases = sign["phases"]
        lines = phases[int(local // 3) % len(phases)]
        for index, line in enumerate(lines):
            if not line:
                continue
            text = line if text_width(line) <= 128 else fit_tiny(line, 128)
            if text_width(line) <= 128:
                draw_text(frame, text, 64 - text_width(text) // 2, 8 + index * 8, SIGN_AMBER)
            else:
                draw_tiny(frame, text, 64 - tiny_width(text) // 2, 9 + index * 8, SIGN_AMBER)
        return frame

    @staticmethod
    def _drive(frame, rows, local):
        """A travel-time sign as it reads over the freeway: MINUTES TO:, then each
        route and its minutes, coloured by how slow they run against the quickest seen."""
        system, number = rows[0]["route"]
        heading = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}.get(rows[0]["direction"], "")
        where = f"{'I-' if system == 'I' else 'SR-'}{number} {heading}"
        at = f"AT {rows[0]['at']}" if rows[0].get("at") else ""
        draw_tiny(frame, where, 0, 1, AMBER)
        if at and tiny_width(where) + 4 + tiny_width(at) <= 128:
            draw_tiny(frame, at, 128 - tiny_width(at), 1, DULL)
        draw_text(frame, "MINUTES TO:", 0, 8, AMBER)
        for index, row in enumerate(rows[:2]):
            if local < .2 + index * .2:
                continue
            y = 16 + index * 8
            minutes = str(max(1, round(row["seconds"] / 60)))
            ratio = row["seconds"] / max(1, row.get("normal") or row["seconds"])
            color = AMBER if ratio < 1.25 else (255, 110, 20) if ratio < 1.6 else RED
            draw_text(frame, fit_text(row["to"], 128 - text_width(minutes) - 6), 0, y, AMBER)
            draw_text(frame, minutes, 128 - text_width(minutes), y, color)
        return frame

    @staticmethod
    def _clear(context, data):
        """A working feed with nothing active is good news, not missing data."""
        frame = new_frame()
        draw_tiny(frame, "TRAFFIC", 0, 1, AMBER)
        for px in range(0, 128, 2):
            ImageDraw.Draw(frame).point((px, 7), fill=(70, 50, 12))
        chosen = context.config["plugins"]["traffic"]["freeways"]
        watched = (freeways(chosen) if chosen.strip() else [tuple(route) for route in data.get("freeways") or []])[:4]
        for index, (system, number) in enumerate(watched):
            shield(frame, system, number, index * 24, 12)
        right = max(len(watched) * 24, 60)
        centre = right + (128 - right) // 2
        draw_text(frame, "ALL", centre - text_width("ALL") // 2, 11, ALL_CLEAR)
        draw_text(frame, "CLEAR", centre - text_width("CLEAR") // 2, 21, ALL_CLEAR)
        return frame

    @staticmethod
    def _body(body, incident, now, t):
        shield(body, incident["system"], incident["route"], 0, 3)
        age = age_label(incident["at"], now)
        urgent = incident["rank"] <= 2
        color = incident["color"] if not urgent or math.floor(t * 2) % 2 == 0 else WHITE
        kind = incident["kind"] if text_width(incident["kind"]) <= 100 - tiny_width(age) else incident["kind"][:9]
        draw_text(body, kind, 24, 2, color)
        draw_tiny(body, age, 128 - tiny_width(age), 3, DULL)
        # Detail sits beside the headline only when it fits whole; a half word reads as noise.
        detail_x = 24 + text_width(kind) + 4
        if incident["detail"] and detail_x + tiny_width(incident["detail"]) + 4 <= 128 - tiny_width(age):
            draw_tiny(body, incident["detail"], detail_x, 3, DULL)
        # Where it is, in full-size letters when they fit: this is what you read from the couch.
        where = " ".join(part for part in (incident["direction"], "@", incident["cross"]) if part)
        if text_width(where) <= 104:
            draw_text(body, where, 24, 13, WHITE)
        else:
            draw_tiny(body, fit_tiny(where, 104), 24, 14, WHITE)


def validate(settings):
    watched = settings.get("freeways")
    listed = [part for part in str(watched).split(",") if part.strip()]
    if not isinstance(watched, str) or len(listed) > 4 or len(freeways(watched)) != len(listed):
        raise ValueError("freeways: leave empty to detect them, or list up to 4 like I-405, SR-55, US-101")
    centers = settings.get("dispatch_centers")
    if not isinstance(centers, str) or not re.fullmatch(r"\s*(auto|[A-Za-z]{4}(\s*,\s*[A-Za-z]{4})*)\s*", centers, re.I):
        raise ValueError("dispatch_centers is auto, or CHP center codes like OCCC")
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180), ("radius_miles", 1, 100),
                           ("refresh_seconds", 60, 900), ("card_seconds", 3, 20), ("max_age_hours", 1, 24)):
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{key} must be {low}–{high}")


def migrate(settings):
    # The first release listed four Orange County freeways by default; detected now.
    if settings.get("freeways") == "I-405, SR-55, I-5, SR-73":
        settings["freeways"] = ""
    return settings


plugin = Plugin(
    "traffic", "Freeway traffic", module=TrafficModule, provider=TrafficProvider,
    defaults={"dispatch_centers": "auto", "latitude": 0.0, "longitude": 0.0, "radius_miles": 15,
              "refresh_seconds": 120, "card_seconds": 5, "max_age_hours": 3, "freeways": ""},
    validate_settings=validate, migrate_settings=migrate,
    help={"dispatch_centers": "auto uses every CHP center and keeps what is near you",
          "freeways": "Leave empty to use the freeways near you, or list up to four, e.g. I-405, SR-73",
          "max_age_hours": "Hide incidents logged longer ago than this; CHP keeps cleared logs in the feed",
          "latitude": "Leave at 0 to use the home location from Settings",
          "radius_miles": "How far from home counts as near"},
    ui={"latitude": {"type": "location", "label": "Location"}, "longitude": {"advanced": True},
        "refresh_seconds": {"advanced": True},
        "card_seconds": {"type": "slider", "min": 3, "max": 20, "unit": "s", "label": "Seconds per card"},
        "radius_miles": {"type": "slider", "min": 1, "max": 60, "unit": "mi", "label": "Radius"},
        "max_age_hours": {"advanced": True}, "freeways": {"type": "tags", "advanced": True},
        "dispatch_centers": {"advanced": True, "label": "CHP dispatch centers"}})
