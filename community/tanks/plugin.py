"""Tanks: real levels as water that sloshes.

California's big reservoirs, from the state's CDEC data (daily storage against
capacity), and the International Space Station's urine tank from NASA's public
live telemetry whenever that stream is answering. Each tank is a small wave
simulation: the surface ripples, rocks now and then, and bubbles rise.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
import math
import random

import aiohttp
from PIL import ImageDraw

from rackticker import Module, Plugin, Provider, Snapshot, draw_text, draw_tiny, new_frame, text_width

CDEC = "https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet"
# Station: (name on the panel, capacity in acre-feet)
RESERVOIRS = {"SHA": ("Shasta", 4_552_000), "ORO": ("Oroville", 3_537_577), "CLE": ("Trinity", 2_447_650),
              "NML": ("New Melones", 2_400_000), "SNL": ("San Luis", 2_041_000), "DNP": ("Don Pedro", 2_030_000),
              "FOL": ("Folsom", 976_952), "PNF": ("Pine Flat", 1_000_000)}
LIGHTSTREAMER = "https://push.lightstreamer.com/lightstreamer"
ISS_ITEMS = {"NODE3000005": "Urine tank", "NODE3000008": "Waste water", "NODE3000009": "Clean water"}
WATER = ((20, 70, 190), (60, 150, 255))
URINE = ((150, 120, 10), (255, 214, 40))
GLASS, WHITE, GREY, UP, DOWN = (90, 100, 110), (236, 238, 236), (140, 146, 150), (90, 230, 120), (255, 90, 60)
PAGE_SECONDS = 6.0


async def reservoirs(session, stations):
    start = (date.today() - timedelta(days=9)).isoformat()
    params = {"Stations": ",".join(stations), "SensorNums": "15", "dur_code": "D", "Start": start,
              "End": date.today().isoformat()}
    async with session.get(CDEC, params=params) as response:
        response.raise_for_status()
        rows = await response.json(content_type=None)
    series = {}
    for row in rows:
        if row.get("value") is not None and row["value"] > 0:
            series.setdefault(row["stationId"], []).append(row["value"])
    tanks = []
    for station in stations:
        values = series.get(station)
        if not values:
            continue
        name, capacity = RESERVOIRS[station]
        level = values[-1] / capacity
        week = (values[-1] - values[max(0, len(values) - 8)]) / capacity
        tanks.append({"name": name, "level": min(1.0, level), "change": week, "unit": "WEEK", "kind": "water"})
    return tanks


async def iss(session):
    """One reading of the station's tanks from NASA's Lightstreamer feed, or [] if it is quiet."""
    form = {"LS_adapter_set": "ISSLIVE", "LS_cid": "mgQkwtwdysogQz2BJ4Ji kOj2Bg", "LS_polling": "true",
            "LS_polling_millis": "0", "LS_idle_millis": "0"}
    async with session.post(f"{LIGHTSTREAMER}/create_session.txt?LS_protocol=TLCP-2.1.0", data=form) as response:
        first = (await response.text()).splitlines()[0]
    if not first.startswith("CONOK"):
        return []
    session_id = first.split(",")[1]
    items = list(ISS_ITEMS)
    await session.post(f"{LIGHTSTREAMER}/control.txt?LS_protocol=TLCP-2.1.0&LS_session={session_id}",
                       data={"LS_reqId": "1", "LS_op": "add", "LS_subId": "1", "LS_mode": "MERGE",
                             "LS_group": " ".join(items), "LS_schema": "Value", "LS_snapshot": "true"})
    values = {}
    for _ in range(2):
        async with session.post(f"{LIGHTSTREAMER}/bind_session.txt?LS_protocol=TLCP-2.1.0",
                                data={"LS_session": session_id, "LS_polling": "true", "LS_polling_millis": "0",
                                      "LS_idle_millis": "1500"}) as response:
            for line in (await response.text()).splitlines():
                parts = line.split(",", 3)
                if parts[0] == "U" and len(parts) == 4:
                    try:
                        values[items[int(parts[2]) - 1]] = float(parts[3].split("|")[0])
                    except (ValueError, IndexError):
                        continue
        if values:
            break
    return [{"name": ISS_ITEMS[item], "level": max(0.0, min(1.0, value / 100)), "change": None, "unit": "",
             "kind": "urine" if item == "NODE3000005" else "water", "iss": True} for item, value in values.items()]


async def none():
    return []


class Levels(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.cache, self.iss_cache, self.checked = [], [], -1e9

    async def fetch(self):
        settings = self.context.settings
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),
                                                 headers={"User-Agent": "RackTicker tanks"})
        loop = asyncio.get_running_loop().time()
        if loop - self.checked >= 900:  # reservoirs change daily; the ISS stream is asked every 15 minutes
            self.checked = loop
            stations = [code.strip().upper() for code in settings["reservoirs"].split(",")
                        if code.strip().upper() in RESERVOIRS]
            water, station = await asyncio.gather(
                reservoirs(self.session, stations) if stations else none(),
                asyncio.wait_for(iss(self.session), 4) if settings["iss"] else none(), return_exceptions=True)
            if isinstance(water, Exception):
                print(f"reservoirs: {water}")
            else:
                self.cache = water
            self.iss_cache = [] if isinstance(station, Exception) else station
        tanks = self.iss_cache + self.cache
        if not tanks:
            raise ConnectionError("No tank levels yet")
        return Snapshot(tanks)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class Surface:
    """A row of springs: each column pulls toward rest and toward its neighbours,
    so a push at one end runs across as a wave and dies away."""

    def __init__(self, width, seed):
        self.width = width
        self.height = [0.0] * width
        self.speed = [0.0] * width
        self.rng = random.Random(seed)
        self.clock = 0.0
        self.bubbles = []

    def step(self, dt):
        for _ in range(max(1, round(dt / (1 / 60)))):
            self.clock += 1 / 60
            if self.rng.random() < .012:   # something bumps the tank
                self.speed[self.rng.randrange(self.width)] += self.rng.uniform(-3.5, 3.5)
            tilt = math.sin(self.clock * .9) * .12
            for i in range(self.width):
                target = tilt * (i - self.width / 2)
                self.speed[i] += (target - self.height[i]) * .06
                self.speed[i] *= .985
            for _ in range(2):
                spread = [0.0] * self.width
                for i in range(self.width):
                    if i:
                        spread[i] += (self.height[i - 1] - self.height[i]) * .2
                    if i < self.width - 1:
                        spread[i] += (self.height[i + 1] - self.height[i]) * .2
                for i in range(self.width):
                    self.speed[i] += spread[i]
            for i in range(self.width):
                self.height[i] += self.speed[i] * .5
            if self.rng.random() < .05:
                self.bubbles.append([self.rng.uniform(1, self.width - 2), 0.0])
            for bubble in self.bubbles:
                bubble[1] += .35
            self.bubbles = [bubble for bubble in self.bubbles if bubble[1] < 40]


def draw_tank(frame, surface, x, top, width, height, level, colors):
    draw = ImageDraw.Draw(frame)
    draw.rectangle((x - 1, top - 1, x + width, top + height), outline=GLASS)
    for mark in (.25, .5, .75):
        y = top + round(height * (1 - mark))
        draw.point((x + width - 2, y), fill=GLASS)
    rest = top + height - level * height
    deep, light = colors
    pixels = frame.load()
    for column in range(width):
        surface_y = rest + surface.height[column]
        for y in range(max(top, math.ceil(surface_y)), top + height):
            depth = (y - surface_y) / max(1, height)
            shade = tuple(round(l + (d - l) * min(1, depth * 1.6)) for d, l in zip(deep, light))
            pixels[x + column, y] = shade
        if top <= surface_y < top + height:
            pixels[x + column, max(top, int(surface_y))] = tuple(min(255, c + 70) for c in light)
    for bx, rise in surface.bubbles:
        y = round(top + height - 1 - rise)
        if y > rest + surface.height[int(bx)] + 1 and top <= y < top + height:
            pixels[x + int(bx), y] = tuple(min(255, c + 90) for c in light)


class TankScreen(Module):
    name = "tanks"

    def __init__(self):
        self.surfaces = {}
        self.last = None

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data)

    def render(self, context):
        frame = new_frame()
        snap = context.snapshots.get(self.name)
        tanks = snap.data if snap and snap.data else []
        if not tanks:
            draw_text(frame, "Tanks", 2, 12, GREY, mixed=True)
            return frame
        t = context.animation_time
        dt = 1 / 30 if self.last is None or t < self.last else min(.2, t - self.last)
        self.last = t
        pages = [tanks[index:index + 2] for index in range(0, len(tanks), 2)]
        page = pages[int(t // PAGE_SECONDS) % len(pages)]
        for slot, tank in enumerate(page):
            x0 = slot * 64
            surface = self.surfaces.setdefault(tank["name"], Surface(20, hash(tank["name"]) & 0xFFFF))
            surface.step(dt)
            draw_tank(frame, surface, x0 + 1, 1, 20, 30, tank["level"], URINE if tank["kind"] == "urine" else WATER)
            text_x = x0 + 25
            label = tank["name"] if text_width(tank["name"], 1, True) <= 38 else tank["name"].split()[0]
            draw_text(frame, label, text_x, 0, WHITE, mixed=True)
            percent = f"{round(tank['level'] * 100)}%"
            draw_text(frame, percent, text_x, 11, WHITE, 2 if text_width(percent, 2) <= 38 else 1, True)
            if tank.get("iss"):
                draw_tiny(frame, "ISS", text_x, 27, GREY)
            elif tank.get("change") is not None:
                change = tank["change"] * 100
                note = f"{'+' if change >= 0 else ''}{change:.1f}/WK"
                draw_tiny(frame, note, text_x, 27, UP if change >= 0 else DOWN)
        return frame


plugin = Plugin(
    "tanks", "Tanks", module=TankScreen, provider=Levels,
    defaults={"reservoirs": "SHA,ORO,FOL,CLE", "iss": True},
    help={"reservoirs": f"California reservoirs by CDEC code: {', '.join(f'{c} {n}' for c, (n, _) in RESERVOIRS.items())}",
          "iss": "Also show the space station's urine and water tanks when NASA's live feed answers"},
    ui={"reservoirs": {"type": "multi", "options": list(RESERVOIRS), "label": "Reservoirs"},
        "iss": {"label": "Space station tanks"}},
)
