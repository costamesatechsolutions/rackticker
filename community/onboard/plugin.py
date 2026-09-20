"""Onboard: the strip map inside the carriage, for a train that is really running.

The display above the doors of a train: the line drawn as a row of stops, the
train sliding along it, the stop it is coming to next, and how far is left. The
train is a real one — Amtrak's live feed through the key-free Amtraker API — so
the dot moves because a train moved.

Pick a train by number, or name a station and it rides whichever train is out
there serving it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import re

import aiohttp
from PIL import ImageDraw

from rackticker import (Module, Plugin, Provider, Snapshot, draw_text, draw_tiny, new_frame, plot,
                        text_width, tiny_width)

API = "https://api-v3.amtraker.com/v3"
UA = {"User-Agent": "RackTicker onboard (+https://github.com/costamesatechsolutions/rackticker)"}
WHITE, DIM, AMBER, RED, GREEN = (238, 240, 238), (78, 82, 90), (255, 176, 20), (255, 70, 50), (90, 220, 120)
RAIL, SOON = (120, 126, 136), (150, 200, 255)
# A few trains worth riding when you have no particular one in mind.
FAVOURITES = {"coast_starlight": ("11", "14"), "california_zephyr": ("5", "6"),
              "empire_builder": ("7", "8", "27", "28"), "southwest_chief": ("3", "4"),
              "sunset_limited": ("1", "2"), "texas_eagle": ("21", "22"),
              "lake_shore_limited": ("48", "49"), "crescent": ("19", "20"),
              "silver_star": ("91", "92"), "auto_train": ("52", "53"),
              "cardinal": ("50", "51"), "city_of_new_orleans": ("58", "59"),
              "adirondack": ("68", "69"), "pacific_surfliner": ("774", "777", "785", "796")}
STRIP_Y = 19          # the rail itself, with names above and the count below
TRIM = (" Amtrak Station", " Transportation Center", " Santa Fe Depot", " Union Station", " Union",
        " Penn Station", " King Street", " Station")


def short(name):
    """A stop's name as the strip map has room for it."""
    name = str(name or "").split(",")[0].strip()
    for tail in TRIM:
        if name.endswith(tail) and len(name) > len(tail) + 2:
            name = name[: -len(tail)]
            break
    return name.replace("-", " ").strip()


def _moment(value):
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def journey(run, now=None):
    """The train's stops, and which one it is heading for.

    Amtraker marks a stop 'Station' or 'Departed' once the train has called there
    and 'Enroute' while it is still ahead, so the first stop still ahead is the
    one the carriage display would be pointing at."""
    stops = []
    for stop in run.get("stations") or []:
        moment = _moment(stop.get("dep") or stop.get("arr") or stop.get("schDep") or stop.get("schArr"))
        planned = _moment(stop.get("schArr") or stop.get("schDep"))
        stops.append({"code": str(stop.get("code") or ""), "name": short(stop.get("name")),
                      "at": moment, "planned": planned,
                      "done": str(stop.get("status") or "").lower() in ("station", "departed")})
    if not stops:
        return None
    ahead = next((index for index, stop in enumerate(stops) if not stop["done"]), len(stops) - 1)
    now = now or datetime.now(timezone.utc)
    late = 0
    following = stops[ahead]
    if following["at"] and following["planned"]:
        late = round((following["at"] - following["planned"]).total_seconds() / 60)
    minutes = None
    if following["at"]:
        minutes = max(0, round((following["at"] - now).total_seconds() / 60))
    # How far between the last stop and the next one the train is, by the clock it
    # is keeping: this is what slides the dot along the rail.
    behind = stops[ahead - 1] if ahead else None
    progress = 0.0
    if behind and behind["at"] and following["at"]:
        leg = (following["at"] - behind["at"]).total_seconds()
        if leg > 0:
            progress = min(1.0, max(0.0, (now - behind["at"]).total_seconds() / leg))
    return {"route": str(run.get("routeName") or ""), "number": str(run.get("trainNum") or ""),
            "from": short(run.get("origName")), "to": short(run.get("destName")),
            "stops": stops, "next": ahead, "late": late, "minutes": minutes,
            "progress": progress,
            "moving": str(run.get("trainState") or "") == "Active",
            "speed": round(float(run.get("velocity") or 0))}


class Onboard(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None

    async def _json(self, url):
        async with self.session.get(url, headers=UA) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _numbers(self):
        """Which trains to consider, from the setting."""
        settings = self.context.settings
        chosen = str(settings.get("train") or "").strip()
        if chosen in FAVOURITES:
            return list(FAVOURITES[chosen])
        if re.fullmatch(r"\d{1,4}", chosen):
            return [chosen]
        station = str(settings.get("station") or "").strip().upper()
        if re.fullmatch(r"[A-Z]{3}", station):
            payload = await self._json(f"{API}/stations/{station}")
            trains = ((payload or {}).get(station) or {}).get("trains") or []
            seen = []
            for train_id in trains:
                number = str(train_id).split("-")[0]
                if number and number not in seen:
                    seen.append(number)
            return seen[:8]
        return list(FAVOURITES["coast_starlight"])

    async def fetch(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6))
        numbers = await self._numbers()
        if not numbers:
            raise ValueError("Name a train number, a route, or a station code")

        async def one(number):
            try:
                return await self._json(f"{API}/trains/{number}")
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return {}

        rides = []
        for payload in await asyncio.gather(*(one(number) for number in numbers)):
            for runs in (payload or {}).values():
                for run in runs or []:
                    ride = journey(run)
                    # A train that has not left, or has finished, is not a ride.
                    if ride and ride["moving"] and 0 < ride["next"] < len(ride["stops"]):
                        rides.append(ride)
        if not rides:
            raise ValueError("None of those trains is running right now")
        # The one with the most left to go: the longest ride to watch.
        rides.sort(key=lambda ride: len(ride["stops"]) - ride["next"], reverse=True)
        return Snapshot({"ride": rides[0], "others": len(rides) - 1})

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class Carriage(Module):
    name = "onboard"

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data and snap.data.get("ride"))

    def render(self, context):
        frame = new_frame()
        snap = context.snapshots.get(self.name)
        ride = (snap.data or {}).get("ride") if snap else None
        if not ride:
            draw_text(frame, "Onboard", 2, 12, DIM, mixed=True)
            return frame
        t = context.animation_time
        self._header(frame, ride)
        self._strip(frame, ride, t)
        self._footer(frame, ride, t)
        return frame

    @staticmethod
    def _header(frame, ride):
        """The line you are on, and whether it is running to time."""
        title = f"{ride['route']} {ride['number']}".strip()
        draw_tiny(frame, title.upper()[:30], 0, 0, AMBER)
        if ride["late"] >= 5:
            note = f"{ride['late']} LATE"
            draw_tiny(frame, note, 128 - tiny_width(note), 0, RED)
        elif ride["speed"] > 5:
            note = f"{ride['speed']} MPH"
            draw_tiny(frame, note, 128 - tiny_width(note), 0, DIM)

    @staticmethod
    def _strip(frame, ride, t):
        """The rail with its stops, and the train sliding between them.

        A window of the line around the train: enough behind to see where it came
        from, and as much ahead as there is room for."""
        stops, here = ride["stops"], ride["next"]
        draw = ImageDraw.Draw(frame)
        first = max(0, here - 2)
        window = stops[first:first + 7] or stops[-7:]
        if len(window) < 7:
            first = max(0, len(stops) - 7)
            window = stops[first:]
        gap = 127 // max(1, len(window) - 1) if len(window) > 1 else 127
        draw.line((0, STRIP_Y, 127, STRIP_Y), fill=DIM)
        # The part already travelled is drawn solid: the line behind you is done.
        travelled = (here - first) * gap
        if travelled > 0:
            draw.line((0, STRIP_Y, min(127, travelled), STRIP_Y), fill=RAIL)
        for index, stop in enumerate(window):
            x = min(126, index * gap)
            done = stop["done"]
            colour = RAIL if done else SOON if first + index == here else WHITE
            draw.rectangle((x, STRIP_Y - 2, x + 1, STRIP_Y + 1), fill=colour)
            if first + index == here:          # the stop being pulled into
                name = stop["name"][:14]
                wide = tiny_width(name)
                draw_tiny(frame, name, max(0, min(128 - wide, x - wide // 2)), STRIP_Y - 9, WHITE)
        # The train itself, between the stop behind it and the one ahead, where the
        # timetable says it has got to. It breathes a little so it reads as running.
        last = max(0, (here - 1 - first)) * gap
        span = max(1, travelled - last)
        breath = math.sin(t * 2) * .6 if ride["moving"] else 0
        train = last + span * ride.get("progress", 0) + breath
        train = max(0, min(123, train - 2))
        pixels = frame.load()
        for dx in range(4):
            plot(frame, pixels, round(train) + dx, STRIP_Y - 4, GREEN)
            plot(frame, pixels, round(train) + dx, STRIP_Y - 3, GREEN)
        plot(frame, pixels, round(train) + 4, STRIP_Y - 3, (200, 255, 200))   # a headlight

    @staticmethod
    def _footer(frame, ride, t):
        """Next stop and how long, then where the whole train is going."""
        stops, here = ride["stops"], ride["next"]
        following = stops[here]
        left = len(stops) - here - 1
        if math.floor(t / 4) % 2 == 0 or not ride["to"]:
            label = f"NEXT {following['name']}"
            when = "" if ride["minutes"] is None else (
                "NOW" if ride["minutes"] <= 0 else f"{ride['minutes']}M")
        else:
            label = f"TO {ride['to']}"
            when = f"{left} STOPS" if left != 1 else "1 STOP"
        room = 128 - (tiny_width(when) + 3 if when else 0)
        while label and tiny_width(label) > room:
            label = label[:-1]
        draw_tiny(frame, label, 0, 25, WHITE)
        if when:
            draw_tiny(frame, when, 128 - tiny_width(when), 25, GREEN if when != "NOW" else AMBER)


def validate(settings):
    train = str(settings.get("train") or "").strip()
    if train and train not in FAVOURITES and not re.fullmatch(r"\d{1,4}", train):
        raise ValueError("train must be a route, an Amtrak train number, or empty")
    station = str(settings.get("station") or "").strip()
    if station and not re.fullmatch(r"[A-Za-z]{3}", station):
        raise ValueError("station must be a three-letter Amtrak code, like LAX")


plugin = Plugin(
    "onboard", "Onboard", module=Carriage, provider=Onboard,
    defaults={"train": "coast_starlight", "station": "", "refresh_seconds": 120},
    choices={"train": ("", *FAVOURITES)},
    help={"train": "a route to ride, or type an Amtrak train number",
          "station": "a three-letter Amtrak station code to ride whatever is serving it (overrides the route "
                     "only when no route is set)"},
    validate_settings=validate,
    ui={"refresh_seconds": {"advanced": True}},
)
