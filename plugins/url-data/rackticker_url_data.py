"""Data from a link: put any value from a JSON web address on the panel.

Paste the address and the path to the field (for example `bitcoin.usd`,
`data.0.temperature` or `stats.members`). Up to three values rotate as cards:
your label, the value in big letters, and an optional unit. No code, no plugin
to write. For data that needs a key, use an address that already includes it
(a Home Assistant or Node-RED endpoint on your network works well).
"""
from __future__ import annotations

import asyncio
import math
import re

import aiohttp

from rackticker import AMBER, MUTED, WHITE, Module, Plugin, Provider, Snapshot, draw_text, new_frame, text_width

SLOTS = 3
CARD_SECONDS = 6.0


def pick(data, path):
    """Follow `a.b.0.c` through dicts and lists; None when the path is missing."""
    for part in [piece for piece in re.split(r"[.\[\]]", path) if piece]:
        if isinstance(data, list) and part.lstrip("-").isdigit():
            index = int(part)
            data = data[index] if -len(data) <= index < len(data) else None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
        if data is None:
            return None
    return data


def display(value, decimals):
    if isinstance(value, str):  # many APIs send numbers as text ("81280.50")
        try:
            value = float(value.replace(",", "")) if re.fullmatch(r"-?[\d,]*\.?\d+", value.strip()) else value
        except ValueError:
            pass
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, (int, float)) and math.isfinite(value):
        number = round(float(value), decimals) if decimals >= 0 else float(value)
        if abs(number) >= 10000:
            return f"{number:,.0f}"
        return f"{number:,.{max(0, decimals)}f}" if decimals >= 0 else f"{number:g}"
    if isinstance(value, (dict, list)):
        return "?"
    return str(value)[:40]


class Links(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.values = {}

    async def fetch(self):
        settings = self.context.settings
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),
                                                 headers={"User-Agent": "RackTicker url_data"})

        async def one(slot):
            url = settings[f"url_{slot}"].strip()
            if not url:
                return None
            async with self.session.get(url) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
            return pick(payload, settings[f"path_{slot}"].strip())

        results = await asyncio.gather(*(one(slot) for slot in range(1, SLOTS + 1)), return_exceptions=True)
        cards = []
        for slot, result in zip(range(1, SLOTS + 1), results):
            if not settings[f"url_{slot}"].strip():
                continue
            if isinstance(result, Exception):
                print(f"link {slot}: {type(result).__name__}: {result}")
                result = self.values.get(slot)  # keep the last good value through a hiccup
            else:
                self.values[slot] = result
            if result is None:
                continue
            cards.append({"label": settings[f"label_{slot}"] or f"Value {slot}",
                          "value": display(result, int(settings[f"decimals_{slot}"])),
                          "unit": settings[f"unit_{slot}"]})
        return Snapshot(cards)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class Cards(Module):
    name = "url_data"

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data)

    def render(self, context):
        frame = new_frame()
        snap = context.snapshots.get(self.name)
        cards = snap.data if snap and snap.data else []
        if not cards:
            return frame
        t = context.animation_time
        card = cards[int(t // CARD_SECONDS) % len(cards)]
        local = t % CARD_SECONDS
        draw_text(frame, card["label"], 0, 0, AMBER, mixed=True)
        value, unit = card["value"], card["unit"]
        unit_width = text_width(unit) + 3 if unit else 0
        scale = 2 if text_width(value, 2) + unit_width <= 128 else 1
        x = (128 - text_width(value, scale) - unit_width) // 2
        rise = max(0, round((1 - min(1, local / .3)) * 6)) if len(cards) > 1 else 0
        draw_text(frame, value, x, (12 if scale == 2 else 16) + rise, WHITE, scale, scale == 2)
        if unit:
            draw_text(frame, unit, x + text_width(value, scale) + 3, 19 + rise, MUTED)
        return frame


def validate(settings):
    for slot in range(1, SLOTS + 1):
        url = settings[f"url_{slot}"].strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError(f"Link {slot} must start with http:// or https://")
        if not -1 <= settings[f"decimals_{slot}"] <= 6:
            raise ValueError("decimals must be -1 (as is) to 6")
    if not 30 <= settings["refresh_seconds"] <= 3600:
        raise ValueError("refresh_seconds must be 30–3600")


defaults, ui, help_text = {"refresh_seconds": 60}, {"refresh_seconds": {"advanced": True}}, {}
for slot in range(1, SLOTS + 1):
    defaults.update({f"label_{slot}": "", f"url_{slot}": "", f"path_{slot}": "", f"unit_{slot}": "",
                     f"decimals_{slot}": -1})
    ui.update({f"label_{slot}": {"label": f"Card {slot}: label"}, f"url_{slot}": {"label": f"Card {slot}: link"},
               f"path_{slot}": {"label": f"Card {slot}: field"}, f"unit_{slot}": {"label": f"Card {slot}: unit"},
               f"decimals_{slot}": {"label": f"Card {slot}: decimals", "advanced": True}})
defaults.update({"label_1": "Bitcoin", "url_1": "https://api.coinbase.com/v2/prices/BTC-USD/spot",
                 "path_1": "data.amount", "unit_1": "USD", "decimals_1": 0})
help_text = {"path_1": "Where the value is in the JSON, e.g. data.amount or items.0.price",
             "decimals_1": "-1 shows the number as it comes"}

plugin = Plugin("url_data", "Data from a link", module=Cards, provider=Links, defaults=defaults,
                validate_settings=validate, help=help_text, ui=ui)
