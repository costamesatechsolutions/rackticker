"""Formula 1: a start-light gantry counting down to lights out, the last race's
podium and the championship fight. Key-free Jolpica (Ergast-compatible) API."""
from __future__ import annotations

import asyncio
from datetime import datetime
import math
import random
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from PIL import ImageDraw

from rackticker import Plugin, Provider, Snapshot, Module, new_frame, draw_text
from app.core.fonts import draw_tiny, text_width, tiny_width
from app.core.fx import dim, ease_out
from app.core.renderer import GREEN, MUTED, WHITE, transition
from app.modules.base import missing, stale_marker


BASE = "https://api.jolpi.ca/ergast/f1/current/"
API = BASE + "next/races.json"
F1_RED, GOLD = (225, 6, 0), (255, 214, 70)
PAGE_SECONDS = 8.0
SESSIONS = (("FirstPractice", "FP1"), ("SecondPractice", "FP2"), ("ThirdPractice", "FP3"),
            ("SprintQualifying", "SPRINT QUALI"), ("SprintShootout", "SHOOTOUT"), ("Sprint", "SPRINT"),
            ("Qualifying", "QUALI"))
TEAM_COLORS = {"mercedes": (0, 210, 190), "ferrari": (230, 20, 20), "red_bull": (60, 100, 255),
               "mclaren": (255, 135, 0), "aston_martin": (0, 170, 125), "alpine": (0, 144, 255),
               "williams": (70, 140, 255), "rb": (120, 160, 255), "racing_bulls": (120, 160, 255),
               "sauber": (0, 220, 60), "audi": (210, 210, 215), "haas": (182, 186, 189),
               "cadillac": (230, 200, 120)}


def _stamp(entry):
    return datetime.fromisoformat(f"{entry['date']}T{entry.get('time', '00:00:00Z')}".replace("Z", "+00:00"))


def _short(name):
    return str(name).replace(" Grand Prix", " GP").upper()


def _code(driver):
    return str(driver.get("code") or driver.get("familyName", "???")[:3]).upper()[:3]


def local_clock(moment, zone):
    local = moment.astimezone(zone)
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local:%a} {hour}:{local:%M}{local:%p}".upper()


def normalize(payload, timezone_name):
    try:
        raw = payload["MRData"]["RaceTable"]["Races"][0]
        zone = ZoneInfo(timezone_name)
        start = _stamp(raw)
        race, circuit = _short(raw["raceName"]), str(raw["Circuit"]["circuitName"]).upper()
        locality = str((raw["Circuit"].get("Location") or {}).get("locality") or "").upper()
        sessions = [(label, _stamp(raw[key])) for key, label in SESSIONS
                    if isinstance(raw.get(key), dict) and raw[key].get("date")]
    except (KeyError, IndexError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Invalid F1 schedule response") from exc
    if not 1 <= len(race) <= 64 or not 1 <= len(circuit) <= 64:
        raise ValueError("Invalid F1 race labels")
    local = start.astimezone(zone)
    hour = local.strftime("%I").lstrip("0") or "12"
    return {"race": race, "circuit": circuit, "locality": locality[:16], "start": start,
            "when": f"{local:%a %b} {local.day} {hour}:{local:%M%p}".upper(),
            "round": str(raw.get("round") or "-")[:2],
            "sessions": tuple(sorted(sessions + [("RACE", start)], key=lambda row: row[1]))}


def podium(payload):
    try:
        race = payload["MRData"]["RaceTable"]["Races"][0]
        rows = tuple((_code(row["Driver"]), str(row["Constructor"]["constructorId"])) for row in race["Results"][:3])
    except (KeyError, IndexError, TypeError):
        return None
    return {"race": _short(race["raceName"]), "podium": rows} if len(rows) == 3 else None


def standings(payload):
    try:
        rows = payload["MRData"]["StandingsTable"]["StandingsLists"][0]["DriverStandings"][:3]
        return tuple((int(row["position"]), _code(row["Driver"]),
                      str((row.get("Constructors") or [{}])[0].get("constructorId", "")),
                      round(float(row["points"]))) for row in rows) or None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


class F1Provider(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.cached = None
        self.cache_until = 0.0

    async def _json(self, url):
        async with self.session.get(url) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def fetch(self):
        now = time.monotonic()
        if self.cached is None or now >= self.cache_until:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=5),
                    headers={"User-Agent": "RackTicker/0.2 (+https://github.com/costamesatechsolutions/rackticker)"})
            upcoming, last, table = await asyncio.gather(
                self._json(API), self._json(BASE + "last/results.json"), self._json(BASE + "driverStandings.json"),
                return_exceptions=True)
            if isinstance(upcoming, Exception):
                raise upcoming
            data = normalize(upcoming, self.context.settings["timezone"])
            data["last"] = None if isinstance(last, Exception) else podium(last)
            data["standings"] = None if isinstance(table, Exception) else standings(table)
            self.cached = data
            self.cache_until = now + self.context.settings["refresh_seconds"]
        return Snapshot(self.cached, source="jolpica")

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()


def _fit_tiny(text, width):
    while text and tiny_width(text) > width:
        text = text[:-1].rstrip()
    return text


class F1Module(Module):
    name = "f1"

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    @staticmethod
    def _pages(data):
        return (["next"] + (["podium"] if data.get("last") else [])
                + (["standings"] if data.get("standings") else []))

    def hold(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data) and context.animation_time < len(self._pages(snap.data)) * PAGE_SECONDS

    def render(self, context):
        snap = context.snapshots.get(self.name)
        if not snap or not snap.data:
            return missing("F1")
        data, t = snap.data, context.animation_time
        pages = self._pages(data)
        page = math.floor(t / PAGE_SECONDS)
        local = t - page * PAGE_SECONDS
        draw_page = lambda index, at: getattr(self, f"_{pages[index % len(pages)]}")(data, context, at)
        frame = draw_page(page, local)
        if page and len(pages) > 1 and local < .45:
            frame = transition(draw_page(page - 1, PAGE_SECONDS), frame, local / .45, "wipe")
        return stale_marker(frame, snap)

    @staticmethod
    def _header(frame, title, right):
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 0, 12, 6), fill=F1_RED)
        draw_tiny(frame, "F1", 3, 1, WHITE)
        draw_tiny(frame, right, 128 - tiny_width(right), 1, MUTED)
        draw_tiny(frame, _fit_tiny(title, 128 - tiny_width(right) - 20), 16, 1, WHITE)

    def _next(self, data, context, local):
        frame = new_frame()
        draw = ImageDraw.Draw(frame)
        self._header(frame, data["race"], f"ROUND {data['round']}")
        seconds = (data["start"] - context.now).total_seconds()
        racing = -7200 < seconds <= 0
        cycle = context.animation_time % 6
        lit = 0 if racing or cycle >= 4.2 else min(5, math.floor(cycle / .55))
        for index in range(5):
            x = 2 + index * 11
            draw.rectangle((x - 1, 9, x + 8, 25), fill=(20, 20, 24))
            for y in (11, 18):
                on = index < lit
                draw.ellipse((x, y, x + 6, y + 6), fill=(255, 30, 20) if on else (54, 8, 8))
                if on:
                    draw.point((x + 2, y + 2), fill=(255, 200, 180))
        if racing:
            label, value, color = "LIGHTS OUT", "RACE ON", GREEN if math.floor(context.animation_time * 2) % 2 else WHITE
        elif seconds > 0:
            days, rest = divmod(int(seconds), 86400)
            hours, rest = divmod(rest, 3600)
            minutes, secs = divmod(rest, 60)
            label = "LIGHTS OUT IN"
            value = f"{days}D {hours:02d}H {minutes:02d}M" if days else f"{hours:02d}:{minutes:02d}:{secs:02d}"
            color = WHITE
        else:
            label, value, color = "CHEQUERED", "FINISHED", MUTED
        draw_tiny(frame, label, 62, 11, GOLD)
        draw_text(frame, value, 62, 18, color)
        upcoming = next(((name, moment) for name, moment in data["sessions"] if moment > context.now), None)
        zone = context.now.tzinfo
        line = f"{upcoming[0]} {local_clock(upcoming[1], zone)}" if upcoming else data["circuit"]
        if data.get("locality"):
            line = f"{line}  {data['locality']}"
        draw_tiny(frame, _fit_tiny(line, 128), 0, 27, MUTED)
        return frame

    def _podium(self, data, context, local):
        frame = new_frame()
        draw = ImageDraw.Draw(frame)
        last = data["last"]
        self._header(frame, last["race"], "RESULT")
        for order, (index, x, top) in enumerate(((2, 85, 24), (1, 5, 20), (0, 45, 16))):
            code, team = last["podium"][index]
            color = TEAM_COLORS.get(team, (170, 170, 180))
            rise = ease_out((local - order * .25) / .6)
            block_top = 31 - round((31 - top) * rise)
            draw.rectangle((x, block_top, x + 37, 31), fill=dim(color, .5))
            draw.rectangle((x, block_top, x + 37, block_top), fill=color)
            place = str(index + 1)
            if 31 - block_top >= 7:
                draw_tiny(frame, place, x + 19 - tiny_width(place) // 2, block_top + 2, WHITE)
            if rise > .98:
                draw_text(frame, code, x + 19 - text_width(code) // 2, top - 8, GOLD if index == 0 else WHITE)
        if local > 1.4:
            sparks = random.Random(math.floor(context.animation_time * 8))
            for _ in range(4):
                draw.point((sparks.randrange(44, 84), sparks.randrange(7, 15)), fill=GOLD)
        return frame

    def _standings(self, data, context, local):
        frame = new_frame()
        draw = ImageDraw.Draw(frame)
        self._header(frame, "DRIVERS", "STANDINGS")
        rows = data["standings"]
        leader = max(1, rows[0][3])
        for index, (position, code, team, points) in enumerate(rows[:3]):
            y = 9 + index * 8
            color = TEAM_COLORS.get(team, (170, 170, 180))
            draw_tiny(frame, str(position), 0, y + 1, MUTED)
            draw.rectangle((5, y, 6, y + 6), fill=color)
            draw_text(frame, code, 9, y, WHITE)
            label = str(points)
            draw_text(frame, label, 128 - text_width(label), y, GOLD if index == 0 else WHITE)
            limit = 128 - text_width(label) - 4
            length = round((limit - 30) * points / leader * ease_out((local - index * .15) / .8))
            if length > 0:
                draw.rectangle((30, y + 2, 30 + length, y + 4), fill=color)
        return frame


def validate(settings):
    timezone_name = settings.get("timezone")
    if not isinstance(timezone_name, str):
        raise ValueError("timezone must be an IANA timezone name")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be an IANA timezone name") from exc
    refresh = settings.get("refresh_seconds")
    if isinstance(refresh, bool) or not isinstance(refresh, (int, float)) or not 300 <= refresh <= 3600:
        raise ValueError("refresh_seconds must be 300–3600")


plugin = Plugin("f1", "Formula 1", module=F1Module, provider=F1Provider,
                defaults={"timezone": "America/Los_Angeles", "refresh_seconds": 900},
                validate_settings=validate,
    ui={"timezone": {"advanced": True, "label": "Time zone"}, "refresh_seconds": {"advanced": True}})
