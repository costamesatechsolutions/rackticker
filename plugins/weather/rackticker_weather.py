"""Graphical weather using Open-Meteo's key-free forecast API.

Two pages: an animated sky with current conditions, and a 12-hour bar chart.
Every text element has a fixed column; nothing overlaps at any temperature.
"""
from __future__ import annotations

from datetime import datetime
import math
import time

import aiohttp
from PIL import ImageDraw

from rackticker import Plugin, Provider, Snapshot, Module, new_frame, draw_text
from app.core.fonts import draw_tiny, text_width, tiny_width
from app.core.fx import dim, ease_out, mix, plot
from app.core.renderer import AMBER, BLUE, MUTED, WHITE, transition
from app.modules.base import missing, stale_marker


API = "https://api.open-meteo.com/v1/forecast"
PAGE_SECONDS = 7.0
COLD, WARM, HOT = (73, 170, 255), (255, 190, 60), (255, 70, 50)
RIGHT_X = 84


def condition(code):
    if code == 0: return "CLEAR", "sun"
    if code in (1, 2): return "PT CLDY", "partly"
    if code == 3: return "CLOUDY", "cloud"
    if code in (45, 48): return "FOG", "fog"
    if 51 <= code <= 57: return "DRIZZLE", "rain"
    if 58 <= code <= 67 or 80 <= code <= 82: return "RAIN", "rain"
    if 71 <= code <= 77 or 85 <= code <= 86: return "SNOW", "snow"
    if 95 <= code <= 99: return "STORM", "storm"
    return "WEATHER", "cloud"


def _clock(value):
    stamp = datetime.fromisoformat(value)
    return stamp.strftime("%I:%M").lstrip("0")


def normalize(payload):
    try:
        current = payload["current"]
        daily = payload["daily"]
        label, icon = condition(int(current["weather_code"]))
        values = {"temperature": round(float(current["temperature_2m"])),
                  "feels": round(float(current["apparent_temperature"])),
                  "wind": round(float(current["wind_speed_10m"])),
                  "label": label, "icon": icon, "is_day": bool(current.get("is_day", 1)),
                  "sunrise": _clock(daily["sunrise"][0]), "sunset": _clock(daily["sunset"][0])}
        highs, lows = daily.get("temperature_2m_max"), daily.get("temperature_2m_min")
        values["high"] = round(float(highs[0])) if highs else None
        values["low"] = round(float(lows[0])) if lows else None
        values["hourly"] = _hourly(payload.get("hourly") or {}, current.get("time"))
        values["days"] = _days(daily)
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid weather response") from exc
    if any(not math.isfinite(values[key]) for key in ("temperature", "feels", "wind")):
        raise ValueError("Invalid weather values")
    return values


def _days(daily):
    """The next four days after today, when the forecast includes them."""
    times, codes = daily.get("time") or [], daily.get("weather_code") or []
    highs, lows = daily.get("temperature_2m_max") or [], daily.get("temperature_2m_min") or []
    rows = []
    for stamp, code, high, low in list(zip(times, codes, highs, lows))[1:5]:
        if None in (code, high, low):
            continue
        rows.append({"day": datetime.fromisoformat(stamp).strftime("%a").upper(),
                     "icon": condition(int(code))[1], "high": round(float(high)), "low": round(float(low))})
    return rows


def _hourly(hourly, now):
    times, temps = hourly.get("time") or [], hourly.get("temperature_2m") or []
    rain = hourly.get("precipitation_probability") or [0] * len(times)
    if not times or not now:
        return []
    start = next((i for i, value in enumerate(times) if value >= now[:13]), 0)
    rows = []
    for stamp, temp, chance in list(zip(times, temps, rain))[start:start + 12]:
        if temp is None:
            continue
        hour = datetime.fromisoformat(stamp).hour
        rows.append({"hour": f"{hour % 12 or 12}{'A' if hour < 12 else 'P'}",
                     "temp": round(float(temp)), "rain": int(chance or 0)})
    return rows


class WeatherProvider(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.cached = None
        self.cache_until = 0.0

    async def fetch(self):
        settings = self.context.settings
        now = time.monotonic()
        if self.cached is None or now >= self.cache_until:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4),
                                                     headers={"User-Agent": "RackTicker/0.2"})
            if settings["latitude"] == 0 and settings["longitude"] == 0:
                raise ValueError("Set your home location in Settings")
            params = {"latitude": settings["latitude"], "longitude": settings["longitude"],
                      "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
                      "hourly": "temperature_2m,precipitation_probability",
                      "daily": "sunrise,sunset,temperature_2m_max,temperature_2m_min,weather_code",
                      "temperature_unit": settings["temperature_unit"],
                      "wind_speed_unit": "mph" if settings["temperature_unit"] == "fahrenheit" else "kmh",
                      "timezone": "auto", "forecast_days": 5}
            async with self.session.get(API, params=params) as response:
                response.raise_for_status()
                self.cached = normalize(await response.json(content_type=None))
            self.cache_until = now + settings["refresh_seconds"]
        return Snapshot(self.cached, source="open_meteo")

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()


def _cloud(draw, x, y, color):
    draw.ellipse((x, y + 4, x + 9, y + 11), fill=color)
    draw.ellipse((x + 6, y, x + 16, y + 10), fill=color)
    draw.ellipse((x + 12, y + 4, x + 20, y + 11), fill=color)
    draw.rectangle((x + 3, y + 8, x + 17, y + 11), fill=color)


def sky(frame, kind, t, is_day=True, x=1, y=4):
    """Animated 22×24 icon at (x, y)."""
    draw = ImageDraw.Draw(frame)
    pixels = frame.load()
    yellow, cloud = (255, 204, 40), (176, 198, 208)
    if kind in ("sun", "partly"):
        cx, cy = (x + 11, y + 10) if kind == "sun" else (x + 8, y + 7)
        if is_day:
            draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=yellow)
            for ray in range(8):
                angle = ray * math.pi / 4 + t * .7
                for radius in (6.5, 8):
                    plot(frame, pixels, cx + round(math.cos(angle) * radius),
                         cy + round(math.sin(angle) * radius), dim(yellow, .95 if radius < 7 else .55))
        else:
            draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(236, 232, 200))
            draw.ellipse((cx - 2, cy - 7, cx + 7, cy + 3), fill=(0, 0, 0))
            for n, (sx, sy) in enumerate(((x + 18, y + 2), (x + 2, y + 16), (x + 20, y + 13))):
                if math.floor(t * 2 + n) % 3:
                    plot(frame, pixels, sx, sy, (200, 210, 255))
    if kind == "fog":
        # A cloud with mist drifting under it: bare lines don't read as weather.
        _cloud(draw, x + 1 + round(math.sin(t * .9) * 1.2), y + 1, dim(cloud, .85))
        for row, gy in enumerate((y + 15, y + 19)):
            shift = math.floor(t * (4 + row * 2)) % 6
            for gx in range(x, x + 22):
                if (gx + shift) % 6 < 4:
                    pixels[gx, gy] = dim(cloud, .8 - row * .2)
        return
    if kind in ("partly", "cloud", "rain", "snow", "storm"):
        drift = round(math.sin(t * .9) * 1.2)
        top = y + (9 if kind == "partly" else 4)
        _cloud(draw, x + 1 + drift, top, (96, 104, 116) if kind == "storm" else cloud)
        bottom = top + 12
        if kind in ("rain", "storm"):
            for n, dx in enumerate((4, 9, 14, 19)):
                fall = (t * 22 + n * 5.5) % 9
                gy = bottom + math.floor(fall)
                if gy + 1 < 32:
                    draw.line((x + dx, gy, x + dx - 1, gy + 1), fill=BLUE)
        if kind == "snow":
            for n, dx in enumerate((4, 10, 16, 20)):
                fall = (t * 6 + n * 2.7) % 9
                plot(frame, pixels, x + dx + round(math.sin(t * 2 + n)), bottom + math.floor(fall), WHITE)
        if kind == "storm" and (t % 2.4) < .3:
            draw.line((x + 12, bottom - 1, x + 9, bottom + 4, x + 12, bottom + 4, x + 9, bottom + 9),
                      fill=(255, 236, 90))


def mini_sky(frame, kind, x, y):
    """Static 13×12 icon for forecast columns."""
    draw = ImageDraw.Draw(frame)
    if kind in ("sun", "partly"):
        draw.ellipse((x + 1, y, x + 7, y + 6), fill=(255, 204, 40))
    if kind in ("partly", "cloud", "rain", "snow", "storm"):
        color = (96, 104, 116) if kind == "storm" else (176, 198, 208)
        offset = 2 if kind == "partly" else 0
        draw.ellipse((x + offset, y + 4, x + offset + 6, y + 9), fill=color)
        draw.ellipse((x + offset + 4, y + 2, x + offset + 11, y + 9), fill=color)
    if kind in ("rain", "storm"):
        for dx in (3, 6, 9):
            draw.point((x + dx, y + 11), fill=BLUE)
    if kind == "snow":
        for dx in (3, 6, 9):
            draw.point((x + dx, y + 11), fill=WHITE)
    if kind == "storm":
        draw.line((x + 7, y + 8, x + 5, y + 11), fill=(255, 236, 90))
    if kind == "fog":
        color = (150, 160, 170)
        draw.ellipse((x, y + 2, x + 6, y + 7), fill=color)
        draw.ellipse((x + 4, y, x + 11, y + 7), fill=color)
        draw.line((x + 1, y + 9, x + 11, y + 9), fill=dim(color, .75))
        draw.line((x, y + 11, x + 9, y + 11), fill=dim(color, .55))


def heat(temp, low, high):
    span = max(1, high - low)
    ratio = (temp - low) / span
    return mix(COLD, WARM, ratio * 2) if ratio < .5 else mix(WARM, HOT, (ratio - .5) * 2)


class WeatherModule(Module):
    name = "weather"

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _pages(self, data):
        return [self._now] + ([self._chart] if data.get("hourly") else []) + ([self._days] if data.get("days") else [])

    def hold(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data) and context.animation_time < len(self._pages(snap.data)) * PAGE_SECONDS

    def render(self, context):
        snap = context.snapshots.get(self.name)
        if not snap or not snap.data:
            return missing("WEATHER")
        data, t = snap.data, context.animation_time
        unit = "F" if context.config["plugins"][self.name]["temperature_unit"] == "fahrenheit" else "C"
        pages = self._pages(data)
        page = math.floor(t / PAGE_SECONDS)
        local = t - page * PAGE_SECONDS
        frame = pages[page % len(pages)](data, unit, t, local)
        if page and len(pages) > 1 and local < .5:
            previous = pages[(page - 1) % len(pages)](data, unit, t, PAGE_SECONDS)
            frame = transition(previous, frame, local / .5, "slide_up")
        return stale_marker(frame, snap)

    def _days(self, data, unit, t, local):
        frame = new_frame()
        for index, day in enumerate(data["days"][:4]):
            x = index * 32
            reveal = ease_out((local - index * .12) / .5)
            if reveal <= 0:
                continue
            draw_tiny(frame, day["day"], x + 16 - tiny_width(day["day"]) // 2, 1, AMBER)
            mini_sky(frame, day["icon"], x + 10, 7)
            high = f"{day['high']}°"
            draw_text(frame, high, x + 16 - text_width(high) // 2, 19, dim((255, 132, 96), reveal))
            low = str(day["low"])
            draw_tiny(frame, low, x + 16 - tiny_width(low) // 2, 27, dim((110, 180, 255), reveal))
            if index:
                # Near-black dots shimmer at the panel's lowest brightness steps: fewer, brighter.
                for y in range(3, 30, 4):
                    frame.putpixel((x, y), (64, 66, 74))
        return frame

    def _now(self, data, unit, t, local):
        frame = new_frame()
        sky(frame, data["icon"], t, data.get("is_day", True))
        temp = f"{data['temperature']}°"
        draw_text(frame, temp, 28, 2, WHITE, 2, True)
        label = data["label"]
        while text_width(label) > RIGHT_X - 31:
            label = label[:-1]
        draw_text(frame, label, 28, 19, AMBER)
        rows = []
        if data.get("high") is not None:
            rows += [(f"H {data['high']}°", (255, 132, 96)), (f"L {data['low']}°", (110, 180, 255))]
        rows.append((f"{data['wind']} {'MPH' if unit == 'F' else 'KMH'}", MUTED))
        if len(rows) < 3:
            rows.insert(0, (f"FL {data['feels']}°", MUTED))
        for index, (text, color) in enumerate(rows[:3]):
            draw_text(frame, text, max(RIGHT_X, 127 - text_width(text)) if text_width(text) > 43 else RIGHT_X,
                      1 + index * 9, color)
        if data.get("high") is not None and data["high"] > data["low"]:
            draw = ImageDraw.Draw(frame)
            low, high = data["low"], data["high"]
            for x in range(28, 128):
                draw.point((x, 29), fill=dim(heat(low + (high - low) * (x - 28) / 99, low, high), .55))
            marker = 28 + round(99 * min(1, max(0, (data["temperature"] - low) / (high - low))))
            draw.rectangle((marker, 27, marker, 31), fill=WHITE)
        return frame

    def _chart(self, data, unit, t, local):
        frame = new_frame()
        draw = ImageDraw.Draw(frame)
        rows = data["hourly"][:12]
        draw_tiny(frame, "NEXT", 0, 1, AMBER)
        draw_tiny(frame, f"{len(rows)}H", 0, 8, AMBER)
        wettest = max(row["rain"] for row in rows)
        if wettest >= 10:
            draw_tiny(frame, "RAIN", 0, 17, BLUE)
            draw_tiny(frame, f"{wettest}%", 0, 24, BLUE)
        temps = [row["temp"] for row in rows]
        low, high = min(temps), max(temps)
        grow = ease_out(local / .8)
        left = 20
        for index, row in enumerate(rows):
            x = left + index * 9
            height = 2 + round((row["temp"] - low) * 10 / max(1, high - low))
            top = 24 - round(height * grow)
            color = heat(row["temp"], low, high)
            draw.rectangle((x, top, x + 7, 24), fill=dim(color, .8))
            draw.rectangle((x, top, x + 7, top), fill=color)
            if row["rain"] >= 30:
                for dy in range(0, min(4, row["rain"] // 25)):
                    draw.point((x + 3 + dy % 2, top - 2 - dy * 2), fill=BLUE)
            if index % 3 == 0:
                label = str(row["temp"])
                draw_tiny(frame, label, x + 4 - tiny_width(label) // 2, top - 6 if row["rain"] < 30 else top - 11,
                          WHITE)
                draw_tiny(frame, row["hour"], x + 4 - tiny_width(row["hour"]) // 2, 27, MUTED)
        return frame


def validate(settings):
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180),
                           ("refresh_seconds", 300, 3600)):
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{key} must be {low}–{high}")
    if settings.get("temperature_unit") not in ("fahrenheit", "celsius"):
        raise ValueError("temperature_unit must be fahrenheit or celsius")


plugin = Plugin("weather", "Weather", module=WeatherModule, provider=WeatherProvider,
                defaults={"latitude": 0.0, "longitude": 0.0,
                          "temperature_unit": "fahrenheit", "refresh_seconds": 600},
                validate_settings=validate,
                choices={"temperature_unit": ("fahrenheit", "celsius")},
                help={"latitude": "Leave at 0 to use the home location from Settings"},
                ui={"latitude": {"type": "location", "label": "Location"}, "longitude": {"advanced": True},
                    "refresh_seconds": {"advanced": True}})
