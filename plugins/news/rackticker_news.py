"""Network-style news desk: rotating channels (top stories, U.S., world, money,
sports, showbiz, tech) from any RSS/Atom feeds, drawn two ways:

* breaking - a TV lower third: channel bumper wipe, then the headline crawls
* zipper   - the Times Square news zipper: amber lamp-bank lettering between
             red, white and blue chase lights

Every headline finishes crawling before the playlist moves on. No keys.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
import html
from itertools import zip_longest
import math
import re
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import aiohttp
from PIL import Image, ImageDraw

from rackticker import Plugin, Provider, Snapshot, Module, new_frame, draw_text, offload
from app.core.fonts import draw_tiny, text_width, tiny_width
from app.core.fx import ease_in, ease_out
from app.core.renderer import MUTED, WHITE, crawl_once_x
from app.core.story import Storyboard
from app.modules.base import missing, stale_marker

# Headlines start fully on screen and sit still long enough to read the
# opening words before the crawl begins.
ENTRY_X = 4
READ_PAUSE = 1.6
BUMPER_SECONDS = 1.15
ZIPPER_BATCH = 4
MAX_ROWS = 64
OLD_DEFAULT = "https://feeds.bbci.co.uk/news/technology/rss.xml"
DEFAULT_CHANNELS = "|".join((
    "TOP=https://feeds.nbcnews.com/nbcnews/public/news",
    "US=https://abcnews.go.com/abcnews/usheadlines",
    "WORLD=https://feeds.bbci.co.uk/news/world/rss.xml",
    "POLITICS=https://www.cbsnews.com/latest/rss/politics",
    "MONEY=https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "SPORTS=https://www.espn.com/espn/rss/news",
    "SHOWBIZ=https://www.cbsnews.com/latest/rss/entertainment",
    "TECH=https://feeds.bbci.co.uk/news/technology/rss.xml",
))
OUTLETS = (("cnbc", "CNBC"), ("nbcnews", "NBC"), ("abcnews", "ABC"), ("cbsnews", "CBS"), ("bbc", "BBC"),
           ("npr.org", "NPR"), ("espn", "ESPN"), ("reuters", "REUTERS"), ("apnews", "AP"),
           ("nytimes", "NYT"), ("foxnews", "FOX"), ("theverge", "VERGE"), ("wsj", "WSJ"))
CHANNEL_COLORS = (("TOP", (215, 28, 40)), ("BREAK", (215, 28, 40)), ("BUSINESS", (0, 150, 85)),
                  ("MONEY", (0, 150, 85)), ("MARKET", (0, 150, 85)), ("POLITIC", (125, 60, 200)),
                  ("WORLD", (0, 130, 200)), ("SPORT", (235, 105, 0)), ("SHOWBIZ", (205, 40, 150)),
                  ("ENTERTAIN", (205, 40, 150)), ("TECH", (0, 160, 165)), ("SCIENCE", (50, 150, 110)),
                  ("LOCAL", (200, 135, 0)), ("WEATHER", (40, 115, 220)), ("US", (30, 75, 205)),
                  ("NATION", (30, 75, 205)))


def channel_color(label):
    upper = label.upper()
    return next((color for key, color in CHANNEL_COLORS if key in upper), (215, 28, 40))


def outlet(url):
    host = urlsplit(url).netloc.lower()
    return next((name for key, name in OUTLETS if key in host), host.removeprefix("www.").split(".")[0].upper()[:7])


def parse_channels(value):
    rows = []
    for part in (value or "").split("|"):
        part = part.strip()
        if not part:
            continue
        label, separator, url = part.partition("=")
        if not separator:
            label, url = "NEWS", part
        rows.append((" ".join(label.upper().split()), url.strip()))
    return rows


def _when(node):
    for tag in ("pubDate", "{*}published", "{*}updated"):
        found = node.find(tag)
        if found is not None and found.text:
            try:
                value = (parsedate_to_datetime(found.text.strip()) if tag == "pubDate"
                         else datetime.fromisoformat(found.text.strip().replace("Z", "+00:00")))
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return None
    return None


def entries(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("Invalid news feed XML") from exc
    nodes = root.findall("./channel/item") or root.findall(".//{*}entry")
    found, seen = [], set()
    for node in nodes:
        title = node.find("title")
        if title is None:
            title = node.find("{*}title")
        raw = (title.text if title is not None else "") or ""
        # Some feeds double-encode entities or wrap titles in HTML.
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(raw))).split()).strip()
        if text and text not in seen:
            seen.add(text)
            found.append({"title": text[:140], "published": _when(node)})
    if not found:
        raise ValueError("News feed contains no headlines")
    return found[:30]


def headlines(xml_text):
    return [entry["title"] for entry in entries(xml_text)]


def fresh(rows, hours, now=None):
    """Newest first, and nothing older than `hours`: some feeds keep day-old stories at
    the top. Undated stories are kept, after the dated ones."""
    now = now or datetime.now(timezone.utc)
    dated = [row for row in rows if row.get("published")]
    recent = sorted((row for row in dated if (now - row["published"]).total_seconds() <= hours * 3600),
                    key=lambda row: row["published"], reverse=True)
    return recent + [row for row in rows if not row.get("published")]


class NewsProvider(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.rows = []
        self.cache_until = 0.0

    async def _channel(self, label, url):
        async with self.session.get(url) as response:
            response.raise_for_status()
            body = await response.text(errors="replace")
        if len(body) > 2_000_000:
            raise ValueError("News feed is too large")
        parsed = await offload(entries, body)
        return [dict(row, channel=label, outlet=outlet(url))
                for row in fresh(parsed, self.context.settings["max_age_hours"])[:10]]

    async def fetch(self):
        settings = self.context.settings
        now = time.monotonic()
        if not self.rows or now >= self.cache_until:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),
                                                     headers={"User-Agent": "RackTicker/0.2"})
            channels = parse_channels(settings["channels"])
            results = await asyncio.gather(*(self._channel(label, url) for label, url in channels),
                                           return_exceptions=True)
            groups = [result for result in results if isinstance(result, list)]
            if groups:
                # Round-robin across channels so the desk never dwells on one beat.
                seen, rows = set(), []
                for row in (row for group in zip_longest(*groups) for row in group if row):
                    if row["title"] not in seen:
                        seen.add(row["title"])
                        rows.append(row)
                self.rows = rows[:MAX_ROWS]
                # Build the zipper strips here, on the provider thread, not mid-render.
                for start in range(0, len(self.rows), ZIPPER_BATCH):
                    zipper_strip(tuple((row["channel"], row["title"]) for row in self.rows[start:start + ZIPPER_BATCH]))
            elif not self.rows:
                raise ConnectionError(str(next(iter(results), "No news feeds configured")))
            self.cache_until = now + settings["refresh_seconds"]
        return Snapshot({"items": self.rows}, source="rss", metadata={"rotation_size": len(self.rows)})

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()


def _age(published):
    if not published:
        return ""
    minutes = max(0, int((datetime.now(timezone.utc) - published).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}M"
    if minutes < 48 * 60:
        return f"{minutes // 60}H"
    return f"{minutes // 1440}D"


@lru_cache(maxsize=1)
def lamp_bank():
    bank = Image.new("RGB", (128, 20))
    pixels = bank.load()
    for y in range(0, 20, 2):
        for x in range(y // 2 % 2, 128, 2):
            pixels[x, y] = (26, 14, 2)
    return bank


# Holds every batch of the 64-headline desk; a smaller cache rebuilt them all on
# each visit, a visible hitch on a Pi.
@lru_cache(maxsize=MAX_ROWS // ZIPPER_BATCH + 2)
def zipper_strip(key):
    """[(channel, title)] -> (strip, mask, spans): tags in channel colour, headlines
    in lamp amber, and each story's x range so the screen can caption its source."""
    parts, spans, width = [], [], 0
    for index, (channel, title) in enumerate(key):
        parts.append((width, channel, title))
        end = width + text_width(channel, 2) + 10 + text_width(title, 2, True) + 34
        spans.append((width, end, index))
        width = end
    # 18 rows: 2× lettering is 14 tall and lowercase g, p, y hang 4 below it.
    strip = Image.new("RGB", (max(1, width), 18))
    draw = ImageDraw.Draw(strip)
    for x, channel, title in parts:
        draw_text(strip, channel, x, 0, channel_color(channel), 2, True)
        x += text_width(channel, 2) + 10
        draw_text(strip, title, x, 0, (255, 176, 20), 2, True, mixed=True)
        x += text_width(title, 2, True) + 9
        for index, color in enumerate(((255, 60, 50), (255, 255, 255), (60, 120, 255))):
            cx = x + index * 6
            draw.polygon(((cx + 2, 5), (cx + 4, 7), (cx + 2, 9), (cx, 7)), fill=color)
    mask = strip.convert("L").point(lambda value: 255 if value else 0)
    return strip, mask, tuple(spans)


class NewsModule(Module):
    name = "news"

    def __init__(self):
        self.board = Storyboard()

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _story(self, context):
        snap = context.snapshots.get(self.name)
        rows = snap.data["items"] if snap and isinstance(snap.data, dict) else []
        settings = context.config["plugins"][self.name]
        speed = context.config["display"]["scroll_speed"]

        def build(visit):
            style = settings["style"] if settings["style"] != "auto" else ("breaking", "zipper")[visit % 2]
            if style == "zipper":
                items = []
                for start in range(0, len(rows), ZIPPER_BATCH):
                    key = tuple((row["channel"], row["title"]) for row in rows[start:start + ZIPPER_BATCH])
                    strip = zipper_strip(key)[0]
                    items.append((("zipper", rows[start:start + ZIPPER_BATCH], key),
                                  READ_PAUSE + (ENTRY_X + strip.width) / speed))
                return items
            items, previous = [], None
            for row in rows:
                bumper = row["channel"] != previous
                previous = row["channel"]
                seconds = (READ_PAUSE + (ENTRY_X + text_width(row["title"], 2, True)) / speed + .5
                           + (BUMPER_SECONDS if bumper else 0))
                items.append((("breaking", row, bumper), seconds))
            return items
        self.board.sync(context.animation_time, build, context.scene)
        return self.board.current(context.animation_time, build)

    def hold(self, context):
        return bool(self._story(context)) and self.board.hold()

    def render(self, context):
        snap = context.snapshots.get(self.name)
        story = self._story(context)
        if not snap or not story:
            return missing("NEWS")
        payload, local, duration = story
        frame = new_frame()
        speed = context.config["display"]["scroll_speed"]
        if payload[0] == "zipper":
            self._zipper(frame, payload[1], payload[2], local, context)
        else:
            self._breaking(frame, payload[1], payload[2], local, duration, context.animation_time, speed)
        return stale_marker(frame, snap)

    @staticmethod
    def _bumper(frame, label, local, color):
        draw = ImageDraw.Draw(frame)
        enter = ease_out(local / .35)
        leave = ease_in((local - .8) / .35) if local > .8 else 0
        left, right = round(leave * 128), round(enter * 128) - 1
        if right >= left:
            draw.rectangle((left, 4, right, 27), fill=color)
            if right < 127:
                draw.rectangle((right, 4, right, 27), fill=(255, 255, 255))
        if .3 < local < .95:
            scale = 2 if text_width(label, 2) <= 120 else 1
            layer = Image.new("RGB", (128, 32))
            x = (128 - text_width(label, scale)) // 2
            draw_text(layer, label, x, 16 - 7 * scale // 2, (255, 255, 255), scale, scale == 2)
            mask = layer.convert("L").point(lambda value: 255 if value else 0)
            mask.paste(0, (0, 0, left, 32))
            frame.paste(layer, (0, 0), mask)

    def _breaking(self, frame, row, bumper, local, duration, t, speed):
        color = channel_color(row["channel"])
        if bumper and local < BUMPER_SECONDS:
            self._bumper(frame, row["channel"], local, color)
            return
        story_t = local - (BUMPER_SECONDS if bumper else 0)
        draw = ImageDraw.Draw(frame)
        label = row["channel"]
        label_right = text_width(label) + 3
        draw.rectangle((0, 0, label_right, 8), fill=color)
        draw_text(frame, label, 2, 1, (255, 255, 255))
        glint = math.floor((t % 3.2) * 45) - 4
        for y in range(9):
            x = glint + (8 - y) // 3
            if 0 <= x <= label_right:
                frame.putpixel((x, y), tuple(min(255, c + 90) for c in frame.getpixel((x, y))))
        meta = " ".join(part for part in (row.get("outlet", ""), _age(row["published"])) if part)
        if label_right + 4 + tiny_width(meta) <= 128:
            draw_tiny(frame, meta, 128 - tiny_width(meta), 2, MUTED)
        draw_text(frame, row["title"], crawl_once_x(max(0.0, story_t - READ_PAUSE), speed, ENTRY_X), 11, WHITE, 2, True,
                  mixed=True)

    @staticmethod
    def _zipper(frame, rows, key, local, context):
        frame.paste(lamp_bank(), (0, 8))
        strip, mask, spans = zipper_strip(key)
        x = crawl_once_x(max(0.0, local - READ_PAUSE), context.config["display"]["scroll_speed"], ENTRY_X)
        frame.paste(strip, (x, 9), mask)
        now = context.now
        clock = f"{now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}"
        draw_tiny(frame, "NEWS", 1, 2, (255, 176, 20))
        draw_tiny(frame, clock, 127 - tiny_width(clock), 2, (255, 176, 20))
        # Caption the story crossing the middle of the panel with its source, up in the
        # top bar: under the headline it cut off the letters that hang below the line.
        centre = 64 - x
        current = next((rows[index] for start, end, index in spans if start <= centre < end), None)
        if current:
            caption = " ".join(part for part in (current.get("outlet", ""), _age(current["published"])) if part)
            room = 128 - 2 * (max(tiny_width("NEWS"), tiny_width(clock)) + 4)
            if caption and tiny_width(caption) <= room:
                draw_tiny(frame, caption, 64 - tiny_width(caption) // 2, 2, channel_color(current["channel"]))


def migrate(settings):
    settings.pop("cycle_seconds", None)
    feed, label = settings.pop("feed_url", None), settings.pop("source_label", None)
    if feed and "channels" not in settings and feed.strip() != OLD_DEFAULT:
        clean = re.sub(r"[^A-Za-z0-9 &]", "", label or "NEWS")[:14] or "NEWS"
        settings["channels"] = "|".join(f"{clean}={url}" for url in re.split(r"[\s|]+", feed) if url)
    return settings


def validate(settings):
    value = settings.get("channels")
    channels = parse_channels(value) if isinstance(value, str) else []
    if not 1 <= len(channels) <= 8 or any(
            not re.fullmatch(r"[A-Z0-9 &]{1,14}", label) or not url.startswith("https://") or len(url) > 300
            for label, url in channels):
        raise ValueError("channels must be 1–8 LABEL=https://feed entries separated by |")
    if settings.get("style") not in ("auto", "breaking", "zipper"):
        raise ValueError("style must be auto, breaking or zipper")
    age = settings.get("max_age_hours")
    if isinstance(age, bool) or not isinstance(age, (int, float)) or not 1 <= age <= 72:
        raise ValueError("max_age_hours must be 1–72")
    refresh = settings.get("refresh_seconds")
    if isinstance(refresh, bool) or not isinstance(refresh, (int, float)) or not 120 <= refresh <= 3600:
        raise ValueError("refresh_seconds must be 120–3600")


plugin = Plugin("news", "News desk", module=NewsModule, provider=NewsProvider,
                defaults={"channels": DEFAULT_CHANNELS, "style": "auto", "refresh_seconds": 300, "max_age_hours": 12},
                validate_settings=validate, migrate_settings=migrate,
                choices={"style": ("auto", "breaking", "zipper")},
                help={"channels": "Up to 8 LABEL=https://rss-feed entries separated by |",
                      "style": "breaking is a TV lower third, zipper is Times Square; auto alternates"},
    ui={"refresh_seconds": {"advanced": True},
        "max_age_hours": {"type": "slider", "min": 1, "max": 72, "unit": "h", "label": "Skip stories older than"}, "channels": {"advanced": True, "label": "Channels (LABEL=feed URL, separated by |)"}})
