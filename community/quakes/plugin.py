"""Quakes: earthquakes around you, from the U.S. Geological Survey.

A seismograph drum of the last 24 hours with a spike for every quake (taller for
bigger ones), then the latest shakes: magnitude, where, how far from you and how
long ago. A quake at or above the alert size within the alert distance takes
over the display once.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import random

import aiohttp
from PIL import ImageDraw

from rackticker import Module, Plugin, Provider, Snapshot, draw_text, draw_tiny, new_frame, text_width, tiny_width

USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
WHITE, GREY, DIM = (236, 238, 236), (140, 146, 150), (40, 44, 48)
TRACE = (90, 230, 120)
DRUM_SECONDS, ROW_SECONDS = 7.0, 7.0


def magnitude_color(value):
    return (90, 230, 120) if value < 3 else (255, 190, 40) if value < 4.5 else (255, 70, 50)


def miles(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def place(text):
    """"16 km WSW of Johannesburg, CA" -> "Johannesburg"."""
    text = str(text or "")
    if " of " in text:
        text = text.split(" of ", 1)[1]
    return text.split(",")[0].strip() or "Unknown"


class Feed(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.alerted = set()

    async def fetch(self):
        settings = self.context.settings
        home = (settings["latitude"], settings["longitude"])
        if home == (0, 0):
            raise ValueError("Set your home location in Settings")
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        params = {"format": "geojson", "latitude": home[0], "longitude": home[1], "orderby": "time",
                  "maxradiuskm": round(settings["radius_miles"] * 1.609), "starttime": since, "minmagnitude": "1.5"}
        async with self.session.get(USGS, params=params) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        quakes = []
        for feature in payload.get("features") or []:
            props, coords = feature.get("properties") or {}, (feature.get("geometry") or {}).get("coordinates")
            if props.get("mag") is None or not coords:
                continue
            quakes.append({"id": feature.get("id"), "magnitude": float(props["mag"]), "place": place(props.get("place")),
                           "time": props["time"] / 1000, "miles": round(miles(home, (coords[1], coords[0]))),
                           "depth_km": round(coords[2] or 0)})
        now = datetime.now(timezone.utc).timestamp()
        for quake in quakes:
            if (quake["magnitude"] >= settings["alert_magnitude"] and quake["miles"] <= settings["alert_miles"]
                    and now - quake["time"] < 900 and quake["id"] not in self.alerted):
                self.alerted.add(quake["id"])
                self.context.emit_event("quakes", 15)
        return Snapshot(quakes)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


def ago(seconds):
    minutes = max(0, int(seconds // 60))
    return f"{minutes}M" if minutes < 60 else f"{minutes // 60}H"


class Seismograph(Module):
    name = "quakes"
    event_priority = 25

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data)

    def hold(self, context):
        return context.animation_time < DRUM_SECONDS + ROW_SECONDS

    def render(self, context):
        frame = new_frame()
        snap = context.snapshots.get(self.name)
        quakes = snap.data if snap and snap.data else []
        if not quakes:
            draw_text(frame, "No quakes nearby", 2, 12, GREY, mixed=True)
            return frame
        t = context.animation_time
        now = context.now.timestamp()
        if t < DRUM_SECONDS:
            self._drum(frame, quakes, now, t)
        else:
            self._latest(frame, quakes, now, t - DRUM_SECONDS)
        return frame

    @staticmethod
    def _drum(frame, quakes, now, t):
        """24 hours across the panel, newest on the right; the pen draws in as you watch."""
        draw = ImageDraw.Draw(frame)
        draw_tiny(frame, "24H", 0, 0, GREY)
        biggest = max(quakes, key=lambda quake: quake["magnitude"])
        label = f"BIGGEST M{biggest['magnitude']:.1f}"
        draw_tiny(frame, label, 128 - tiny_width(label), 0, magnitude_color(biggest["magnitude"]))
        for hour in range(0, 25, 6):
            x = round(hour / 24 * 127)
            draw.line((x, 30, x, 31), fill=DIM)
        spikes = {}
        for quake in quakes:
            x = round(127 - (now - quake["time"]) / 86400 * 127)
            if 0 <= x < 128:
                spikes[x] = max(spikes.get(x, 0), quake["magnitude"])
        drawn = min(128, round(t / 2.2 * 128))
        rng = random.Random(int(now // 3600))
        previous = None
        for x in range(drawn):
            wiggle = rng.uniform(-.8, .8) + math.sin(x * 1.7 + t * 9) * .4
            size = spikes.get(x)
            amplitude = 0 if size is None else min(11, size ** 1.6)
            y = round(18 + wiggle + (amplitude if x % 2 else -amplitude))
            color = TRACE if size is None else magnitude_color(size)
            if previous is not None:
                draw.line((x - 1, previous, x, y), fill=color)
            previous = y
            if size is not None and size >= 2.5:
                text = f"{size:.1f}"
                draw_tiny(frame, text, min(128 - tiny_width(text), max(0, x - tiny_width(text) // 2)), 25, color)

    @staticmethod
    def _latest(frame, quakes, now, local):
        rows = quakes[:3]
        for index, quake in enumerate(rows):
            if local < index * .2:
                continue
            y = 1 + index * 11
            size = f"{quake['magnitude']:.1f}"
            draw_text(frame, size, 0, y, magnitude_color(quake["magnitude"]))
            right = f"{quake['miles']}MI {ago(now - quake['time'])}"
            draw_tiny(frame, right, 128 - tiny_width(right), y + 1, GREY)
            room = 128 - text_width(size) - 4 - tiny_width(right) - 4
            # Whole words only; a name cut mid-word reads as broken.
            words = quake["place"].split()
            while len(words) > 1 and text_width(" ".join(words), 1, True) > room:
                words.pop()
            name = " ".join(words)
            while name and text_width(name, 1, True) > room:
                name = name[:-1]
            draw_text(frame, name, text_width(size) + 4, y, WHITE, mixed=True)


plugin = Plugin(
    "quakes", "Quakes", module=Seismograph, provider=Feed,
    defaults={"latitude": 0.0, "longitude": 0.0, "radius_miles": 250, "alert_magnitude": 4.0, "alert_miles": 100},
    help={"latitude": "Leave at 0 to use the home location from Settings",
          "alert_magnitude": "A quake this size or bigger within the alert distance takes over the display"},
    ui={"latitude": {"type": "location", "label": "Location"}, "longitude": {"advanced": True},
        "radius_miles": {"type": "slider", "min": 25, "max": 600, "step": 25, "unit": "mi", "label": "Show quakes within"},
        "alert_magnitude": {"type": "slider", "min": 2.5, "max": 7, "step": .5, "label": "Take over at magnitude"},
        "alert_miles": {"type": "slider", "min": 10, "max": 300, "step": 10, "unit": "mi", "label": "Take over within"}},
)
