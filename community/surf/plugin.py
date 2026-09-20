"""Surf: the waves at your break, key-free.

Open-Meteo's marine forecast for wave height, swell period and direction and
water temperature; NOAA's tide predictions for the next high or low. The wave
rolling along the bottom is sized and paced by the real swell.
"""
from __future__ import annotations

from datetime import datetime
import math

import aiohttp
from PIL import ImageDraw

from rackticker import (Module, Plugin, Provider, Snapshot, draw_text, draw_tiny, new_frame, text_width,
                        tiny_width, triangle)

MARINE = "https://marine-api.open-meteo.com/v1/marine"
TIDES = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
# Break: (name, latitude, longitude just offshore, NOAA tide station)
SPOTS = {
    "huntington": ("Huntington Pier", 33.650, -118.010, "9410580"),
    "newport": ("The Wedge", 33.590, -117.885, "9410580"),
    "trestles": ("Trestles", 33.378, -117.595, "9410230"),
    "blacks": ("Black's Beach", 32.880, -117.260, "9410230"),
    "malibu": ("Malibu", 34.030, -118.680, "9410840"),
    "steamer_lane": ("Steamer Lane", 36.950, -122.030, "9413745"),
    "mavericks": ("Mavericks", 37.490, -122.505, "9414290"),
    "ocean_beach": ("Ocean Beach SF", 37.760, -122.515, "9414290"),
    "pipeline": ("Pipeline", 21.668, -158.055, "1612340"),
}
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
WHITE, GREY, AMBER, SEA, FOAM = (236, 238, 236), (140, 146, 150), (255, 176, 20), (30, 110, 220), (200, 235, 255)


def nearest(latitude, longitude):
    return min(SPOTS, key=lambda spot: (SPOTS[spot][1] - latitude) ** 2 + (SPOTS[spot][2] - longitude) ** 2)


class Conditions(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None

    async def fetch(self):
        settings = self.context.settings
        spot = settings["spot"]
        if spot == "nearest":
            if settings["latitude"] == 0 and settings["longitude"] == 0:
                raise ValueError("Pick a break, or set your home location in Settings")
            spot = nearest(settings["latitude"], settings["longitude"])
        name, latitude, longitude, station = SPOTS[spot]
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        params = {"latitude": latitude, "longitude": longitude, "length_unit": "imperial",
                  "temperature_unit": "fahrenheit", "timezone": "auto",
                  "current": "wave_height,wave_period,wave_direction,swell_wave_height,swell_wave_period,"
                             "swell_wave_direction,sea_surface_temperature"}
        async with self.session.get(MARINE, params=params) as response:
            response.raise_for_status()
            current = (await response.json(content_type=None))["current"]
        tide = None
        today = datetime.now()
        tide_params = {"product": "predictions", "application": "rackticker", "datum": "MLLW", "station": station,
                       "begin_date": today.strftime("%Y%m%d"), "range": "48", "time_zone": "lst_ldt",
                       "units": "english", "interval": "hilo", "format": "json"}
        try:
            async with self.session.get(TIDES, params=tide_params) as response:
                predictions = (await response.json(content_type=None)).get("predictions") or []
            upcoming = [row for row in predictions if datetime.strptime(row["t"], "%Y-%m-%d %H:%M") > today]
            if upcoming:
                # The one after it too: two turns of the tide say which way the water
                # is going now, and how far through the swing it is.
                tide = {"high": upcoming[0]["type"] == "H", "time": upcoming[0]["t"],
                        "feet": float(upcoming[0]["v"]),
                        "then": upcoming[1]["t"] if len(upcoming) > 1 else None}
        except (aiohttp.ClientError, ValueError, KeyError):
            tide = None
        return Snapshot({"spot": name, "height": current.get("wave_height"), "period": current.get("swell_wave_period")
                         or current.get("wave_period"), "direction": current.get("swell_wave_direction")
                         or current.get("wave_direction"), "water": current.get("sea_surface_temperature"),
                         "tide": tide})

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


def face(height):
    """Wave height in the way surfers say it: '2-3 FT'."""
    if height is None:
        return "--"
    low, high = max(0, math.floor(height * .8)), max(1, math.ceil(height * 1.1))
    return f"{high} FT" if low == high or low == 0 and high <= 1 else f"{low}-{high} FT"


class Water:
    """A row of springs, each pulling on its neighbours: the cheapest thing that
    behaves like water. Swell is pushed in at the seaward edge and travels across,
    so crests move, meet and break instead of sliding past as a drawn sine."""

    # Slack springs that barely lose energy, so a wave crosses the whole panel
    # instead of dying where it was made.
    STIFFNESS, DAMPING, SPREAD = 6.0, .12, 150.0

    def __init__(self, width=128):
        self.height = [0.0] * width
        self.speed = [0.0] * width
        self.clock = 0.0

    def prime(self, size):
        """Start with a sea already running. Made from cold the panel spends the
        first seconds of every visit flat while the first wave crosses it."""
        wavelength = 34.0
        for x in range(len(self.height)):
            phase = (x - len(self.height)) / wavelength * math.tau
            self.height[x] = math.sin(phase) * size
            self.speed[x] = math.cos(phase) * size * 2.2

    def push(self, index, force):
        if 0 <= index < len(self.speed):
            self.speed[index] += force

    def make_waves(self, period, size):
        """Hold the seaward edge rising and falling: a wavemaker at the end of the
        tank. Swell that took fifteen seconds to arrive is not worth watching, so
        the real period sets the pace rather than the clock."""
        beat = min(4.0, max(1.4, period / 4))
        self.height[-1] = math.sin(self.clock / beat * math.tau) * size
        self.height[-2] = math.sin((self.clock - .05) / beat * math.tau) * size

    def step(self, dt, period, size):
        # Small fixed steps: one long frame must not blow the springs apart.
        dt = min(dt, .1)
        while dt > 0:
            slice_dt = min(dt, 1 / 60)
            dt -= slice_dt
            self.clock += slice_dt
            height, speed = self.height, self.speed
            last = len(height) - 1
            for i in range(len(height)):
                speed[i] += (-self.STIFFNESS * height[i] - self.DAMPING * speed[i]) * slice_dt
            # Water moves sideways as well as up: each column drags its neighbours.
            flow = [0.0] * len(height)
            for i in range(len(height)):
                left = height[i - 1] if i else height[0]
                right = height[i + 1] if i < last else height[last]
                flow[i] = (left + right - 2 * height[i]) * self.SPREAD * slice_dt
            for i in range(len(height)):
                speed[i] += flow[i]
                height[i] += speed[i] * slice_dt
            self.make_waves(period, size)

    def swell(self, period, size):
        """One set coming in from the sea: a push at the edge every period."""
        return self.clock % max(3.0, period) < 1 / 30


class Report(Module):
    name = "surf"

    def __init__(self):
        self.water = Water()
        self.last = None

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data and snap.data.get("height") is not None)

    def render(self, context):
        frame = new_frame()
        snap = context.snapshots.get(self.name)
        surf = snap.data if snap and snap.data else None
        if not surf:
            draw_text(frame, "Surf", 2, 12, GREY, mixed=True)
            return frame
        t = context.animation_time
        # The sea keeps running between visits, but a jump in time must not explode it.
        first = self.last is None
        dt = 1 / 30 if first or not 0 < t - self.last < .5 else t - self.last
        self.last = t
        if first:
            self.water.prime(min(6.0, 1.5 + (surf.get("height") or 1) * .8) / 2)
        level = self._level(surf.get("tide"))
        self._wave(frame, surf, dt, level)
        draw_text(frame, surf["spot"], 0, 0, AMBER, mixed=True)
        if surf.get("water") is not None:
            water = f"{round(surf['water'])}°"
            draw_text(frame, water, 128 - text_width(water), 0, SEA)
        size = face(surf["height"])
        draw_text(frame, size, 0, 10, WHITE, 2, True)
        x = text_width(size, 2) + 5
        details = []
        if surf.get("period"):
            direction = COMPASS[round((surf.get("direction") or 0) / 45) % 8]
            details.append(f"{round(surf['period'])}S {direction}")
        tide = surf.get("tide")
        if tide:
            when = datetime.strptime(tide["time"], "%Y-%m-%d %H:%M")
            clock = when.strftime("%I:%M%p").lstrip("0").replace("AM", "A").replace("PM", "P")
            details.append(f"{'HIGH' if tide['high'] else 'LOW'} {clock}")
        for index, line in enumerate(details):
            if x + tiny_width(line) <= 128:
                draw_tiny(frame, line, 128 - tiny_width(line), 11 + index * 7, GREY)
        # An arrow beside the tide line: coming in, or going out.
        if tide and len(details) > 1 and x + tiny_width(details[-1]) + 7 <= 128:
            triangle(frame, 128 - tiny_width(details[-1]) - 7, 12 + (len(details) - 1) * 7,
                     bool(tide["high"]), SEA)
        return frame

    @staticmethod
    def _level(tide, now=None):
        """How far through the swing the water is: 0 at dead low, 1 at dead high.

        The tide gives the next turn and the one after, so the swing between them
        is the same length as the one running now, counted back from the next turn."""
        if not tide or not tide.get("then"):
            return None
        now = now or datetime.now()
        try:
            turn = datetime.strptime(tide["time"], "%Y-%m-%d %H:%M")
            after = datetime.strptime(tide["then"], "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return None
        swing = (after - turn).total_seconds()
        if swing <= 0:
            return None
        done = min(1.0, max(0.0, 1 - (turn - now).total_seconds() / swing))
        return done if tide["high"] else 1 - done

    def _wave(self, frame, surf, dt, level=None):
        """Real water: springs pushed by the real swell, sitting at the real tide."""
        size = min(6.0, 1.5 + (surf.get("height") or 1) * .8)
        period = max(5.0, min(20.0, surf.get("period") or 10))
        water = self.water
        water.step(dt, period, size)
        rise = 0 if level is None else (level - .5) * 5
        draw = ImageDraw.Draw(frame)
        base = 31 - size / 2 - rise
        for x in range(128):
            top = base - water.height[x]
            row = round(top)
            if row > 31:
                continue
            draw.line((x, max(0, row), x, 31), fill=SEA)
            # Foam where the water is climbing fastest: the face of a breaking wave.
            climb = -water.speed[x]
            if climb > size * 1.5:
                draw.point((x, max(0, row)), fill=FOAM)
                if climb > size * 3 and row > 0:
                    draw.point((x, row - 1), fill=(150, 200, 250))


plugin = Plugin(
    "surf", "Surf", module=Report, provider=Conditions,
    defaults={"spot": "nearest", "latitude": 0.0, "longitude": 0.0},
    choices={"spot": ("nearest", *SPOTS)},
    help={"spot": "nearest picks the break closest to your home location"},
    ui={"latitude": {"advanced": True}, "longitude": {"advanced": True}},
)
