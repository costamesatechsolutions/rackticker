"""Network-style news desk: rotating channels (top stories, U.S., world, money,
sports, showbiz, tech) from any RSS/Atom feeds, drawn three ways:

* headline - the whole headline on two lines that roll up like live captions,
             paced to be read, not watched go by (the default)
* breaking - a TV lower third: channel bumper wipe, then the headline crawls
* zipper   - the Times Square news zipper: amber lamp-bank lettering between
             red, white and blue chase lights

Every headline finishes before the playlist moves on. No keys.
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
from app.core.fonts import draw_tiny, text_mask, text_width, tiny_width, wrap_text
from app.core.fx import ease_in, ease_in_out, ease_out
from app.core.renderer import MUTED, WHITE
from app.core.story import Storyboard
from app.modules.base import missing, stale_marker

# Headlines start fully on screen and sit still long enough to read the
# opening words before the crawl begins.
ENTRY_X = 4
READ_PAUSE = 1.6
BUMPER_SECONDS = 1.15
# Label bar + gap + 2x mixed-case headline (18 rows) run flush to the panel's last
# row (31) instead of stopping short of it.
LABEL_BAR_BOTTOM = 10
HEADLINE_Y = LABEL_BAR_BOTTOM + 4
# Headline cards: a slimmer label bar, then two 5x7 lines on a 10-row pitch. Two lines
# hold about 40 letters, where a 2x crawl only ever shows ten of them at once.
CARD_BAR_BOTTOM = 8
CARD_TOP = 12
LINE_PITCH = 10
LINE_WIDTH = 126
READ_CPS = 12          # letters a second someone across the room reads comfortably
REVEAL_SECONDS = .4
ROLL_SECONDS = .3
ZIPPER_BATCH = 4
WARM_PAUSE = .12
BREAKING_RED = (235, 30, 30)
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


def crawl_x(t, speed, start=ENTRY_X):
    """Left edge of 2x lettering crawling left. 2x letters are drawn on a 2-pixel grid,
    so they step two LEDs at a time, at twice the configured speed: just as smooth to
    the eye as 1x text at that speed, and the headline is read in half the time."""
    return start - 2 * math.floor(max(0.0, t) * speed + 1e-6)


def crawl_seconds(width, speed):
    return (ENTRY_X + width) / (2 * speed)


@lru_cache(maxsize=MAX_ROWS)
def headline_lines(title):
    return tuple(wrap_text(title, LINE_WIDTH, mixed=True))


@lru_cache(maxsize=MAX_ROWS)
def card_plan(title):
    """[(start, dwell)] for each view of a headline card: view v shows lines v and v+1.
    Each view stays up long enough to read what it adds, then rolls up one line."""
    lines = headline_lines(title)
    views, at = [], REVEAL_SECONDS
    for view in range(max(1, len(lines) - 1)):
        fresh = lines[:2] if view == 0 else lines[view + 1:view + 2]
        letters = sum(len(line) for line in fresh)
        dwell = max(2.0, .8 + letters / READ_CPS) if view == 0 else max(1.5, .4 + letters / READ_CPS)
        views.append((at, dwell))
        at += dwell + ROLL_SECONDS
    return tuple(views)


def card_seconds(title):
    start, dwell = card_plan(title)[-1]
    return start + dwell + .2


def card_scroll(title, t):
    """How far the card has rolled up, in pixels, at t seconds into it."""
    plan = card_plan(title)
    for view, (start, dwell) in enumerate(plan[:-1]):   # the last view stays put
        roll = start + dwell
        if t < roll:
            return view * LINE_PITCH
        if t < roll + ROLL_SECONDS:
            return round((view + ease_in_out((t - roll) / ROLL_SECONDS)) * LINE_PITCH)
    return (len(plan) - 1) * LINE_PITCH


@lru_cache(maxsize=MAX_ROWS)
def card_mask(title):
    """Every line of a headline, stacked on the card's pitch, as one mask."""
    lines = headline_lines(title)
    mask = Image.new("1", (128, max(1, len(lines)) * LINE_PITCH))
    for index, line in enumerate(lines):
        glyphs = text_mask(line, 1, False, True)
        mask.paste(glyphs, (1, index * LINE_PITCH))
    return mask


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
        self.warming = None

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
                self._warm_later(self.rows)
            elif not self.rows:
                raise ConnectionError(str(next(iter(results), "No news feeds configured")))
            self.cache_until = now + settings["refresh_seconds"]
        return Snapshot({"items": self.rows}, source="rss", metadata={"rotation_size": len(self.rows)})

    def _warm_later(self, rows):
        """Draw the zipper strips ahead of their turn, but gently. All sixteen at once kept
        the interpreter busy for over a second on a Pi and the crawl on screen stuttered
        (and every other feed's reply queued up behind it); one every so often is not felt."""
        if self.warming is not None:
            self.warming.cancel()
        self.warming = asyncio.get_running_loop().create_task(self._warm(list(rows)))

    async def _warm(self, rows):
        for start in range(0, len(rows), ZIPPER_BATCH):
            zipper_strip(tuple((row["channel"], row["title"]) for row in rows[start:start + ZIPPER_BATCH]))
            await asyncio.sleep(WARM_PAUSE)

    async def close(self):
        if self.warming is not None:
            self.warming.cancel()
        if self.session is not None and not self.session.closed:
            await self.session.close()


def age_minutes(published, now=None):
    if not published:
        return None
    return max(0, int(((now or datetime.now(timezone.utc)) - published).total_seconds() // 60))


def breaking(row, now=None):
    """A story that is less than a minute old is not "0M AGO": it is breaking."""
    return age_minutes(row.get("published"), now) == 0


def _age(published, long=False, now=None):
    if not published:
        return ""
    minutes = age_minutes(published, now)
    if minutes == 0:
        return "BREAKING"
    if minutes < 60:
        age = f"{minutes}M"
    elif minutes < 48 * 60:
        age = f"{minutes // 60}H"
    else:
        age = f"{minutes // 1440}D"
    # "9H" beside a clock reads as a time of day; "9H AGO" does not.
    return f"{age} AGO" if long else age


def _caption(row, room, now=None):
    """The outlet and how old the story is, as fully as there is room to say it."""
    if breaking(row, now):
        # The chip already says BREAKING; the caption says who reported it.
        text = row.get("outlet", "")
        return text if tiny_width(text) <= room else ""
    for long in (True, False):
        text = " ".join(part for part in (row.get("outlet", ""), _age(row["published"], long, now)) if part)
        if tiny_width(text) <= room:
            return text
    return ""


def flashing(t):
    """Two beats a second: the lamp on a breaking story."""
    return int(t * 2) % 2 == 0


def caption_colour(row, t=0.0, now=None):
    """Fresh stories glow; old ones sit back."""
    minutes = age_minutes(row.get("published"), now)
    if minutes is not None and minutes <= 15:
        return (255, 176, 20)
    return MUTED


LAMP_BANK_H = 24  # down to the panel's last row: rows 8-31, none left black beneath the strip


@lru_cache(maxsize=1)
def lamp_bank():
    bank = Image.new("RGB", (128, LAMP_BANK_H))
    pixels = bank.load()
    for y in range(0, LAMP_BANK_H, 2):
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
            # Auto is mostly headline cards, which can actually be read, with the
            # Times Square zipper every third visit for the show of it.
            style = settings["style"] if settings["style"] != "auto" else ("headline", "headline", "zipper")[visit % 3]
            if style == "zipper":
                items = []
                for start in range(0, len(rows), ZIPPER_BATCH):
                    key = tuple((row["channel"], row["title"]) for row in rows[start:start + ZIPPER_BATCH])
                    strip = zipper_strip(key)[0]
                    items.append((("zipper", rows[start:start + ZIPPER_BATCH], key),
                                  READ_PAUSE + crawl_seconds(strip.width, speed)))
                return items
            items, previous = [], None
            for row in rows:
                # A story that has just broken gets its own bumper, whatever channel it is on.
                bumper = row["channel"] != previous or breaking(row)
                previous = row["channel"]
                if style == "headline":
                    seconds = card_seconds(row["title"])
                else:
                    seconds = READ_PAUSE + crawl_seconds(text_width(row["title"], 2, True), speed) + .5
                items.append(((style, row, bumper), seconds + (BUMPER_SECONDS if bumper else 0)))
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
        elif payload[0] == "headline":
            self._headline(frame, payload[1], payload[2], local, context.animation_time)
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

    @staticmethod
    def _label(frame, row, t, bottom):
        """The channel chip (BREAKING, flashing, for a story under a minute old) with
        the outlet and the story's age at the other end of the bar."""
        hot = breaking(row)
        color = BREAKING_RED if hot else channel_color(row["channel"])
        draw = ImageDraw.Draw(frame)
        label = "BREAKING" if hot else row["channel"]
        label_right = text_width(label) + 3
        lit = not hot or flashing(t)
        draw.rectangle((0, 0, label_right, bottom), fill=color if lit else (255, 255, 255))
        draw_text(frame, label, 2, 1, (255, 255, 255) if lit else color)
        glint = math.floor((t % 3.2) * 45) - 4
        for y in range(0 if not hot else bottom + 1, bottom + 1):  # a glint on red would come out pink
            x = glint + (bottom - y) // 3
            if 0 <= x <= label_right:
                frame.putpixel((x, y), tuple(min(255, c + 90) for c in frame.getpixel((x, y))))
        meta = _caption(row, 127 - label_right - 4)
        if meta:
            draw_tiny(frame, meta, 127 - tiny_width(meta), 2, caption_colour(row))

    def _headline(self, frame, row, bumper, local, t):
        """The whole headline, two lines at a time, rolling up a line once the eye
        has had time to take in what is there."""
        hot = breaking(row)
        if bumper and local < BUMPER_SECONDS:
            self._bumper(frame, "BREAKING" if hot else row["channel"], local,
                         BREAKING_RED if hot else channel_color(row["channel"]))
            return
        story_t = local - (BUMPER_SECONDS if bumper else 0)
        self._label(frame, row, t, CARD_BAR_BOTTOM)
        title = row["title"]
        mask = card_mask(title)
        # A one-line headline sits in the middle of the space under the bar.
        top = CARD_TOP if len(headline_lines(title)) > 1 else CARD_TOP + LINE_PITCH // 2
        # One row above the first line's capitals: the descenders of a line that has
        # rolled away (the g of "signals") would otherwise linger there as stray dots.
        window_top = CARD_TOP - 1
        scroll = card_scroll(title, story_t)
        # Only what shows between the bar and the panel's last row: lines rolling
        # away leave under the bar rather than over it.
        source_top = window_top - top + scroll
        window = mask.crop((0, source_top, 128, source_top + 32 - window_top))
        # Lettering is typed on from the left as the story arrives.
        if story_t < REVEAL_SECONDS:
            edge = round(128 * ease_out(story_t / REVEAL_SECONDS))
            window.paste(0, (edge, 0, 128, window.height))
        frame.paste(WHITE, (0, window_top, 128, 32), window)

    def _breaking(self, frame, row, bumper, local, duration, t, speed):
        hot = breaking(row)
        color = BREAKING_RED if hot else channel_color(row["channel"])
        if bumper and local < BUMPER_SECONDS:
            self._bumper(frame, "BREAKING" if hot else row["channel"], local, color)
            return
        story_t = local - (BUMPER_SECONDS if bumper else 0)
        self._label(frame, row, t, LABEL_BAR_BOTTOM)
        draw_text(frame, row["title"], crawl_x(story_t - READ_PAUSE, speed), HEADLINE_Y,
                  WHITE, 2, True, mixed=True)

    @staticmethod
    def _zipper(frame, rows, key, local, context):
        lamp_top = 8
        frame.paste(lamp_bank(), (0, lamp_top))
        strip, mask, spans = zipper_strip(key)
        # Centred in the lamp bank, which now runs to the panel's last row: no black
        # band was left beneath the strip.
        strip_y = lamp_top + (LAMP_BANK_H - strip.height) // 2
        x = crawl_x(local - READ_PAUSE, context.config["display"]["scroll_speed"])
        frame.paste(strip, (x, strip_y), mask)
        now = context.now
        clock = f"{now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}"
        draw_tiny(frame, "NEWS", 1, 2, (255, 176, 20))
        draw_tiny(frame, clock, 127 - tiny_width(clock), 2, (255, 176, 20))
        # Caption the story crossing the middle of the panel with its source, up in the
        # top bar: under the headline it cut off the letters that hang below the line.
        centre = 64 - x
        current = next((rows[index] for start, end, index in spans if start <= centre < end), None)
        if current:
            room = 128 - 2 * (max(tiny_width("NEWS"), tiny_width(clock)) + 4)
            if breaking(current):
                caption = "BREAKING" if tiny_width("BREAKING") <= room else ""
                colour = BREAKING_RED if flashing(context.animation_time) else (255, 255, 255)
            else:
                caption, colour = _caption(current, room), channel_color(current["channel"])
            if caption:
                draw_tiny(frame, caption, 64 - tiny_width(caption) // 2, 2, colour)


STYLES = ("auto", "headline", "breaking", "zipper")


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
    if settings.get("style") not in STYLES:
        raise ValueError("style must be auto, headline, breaking or zipper")
    age = settings.get("max_age_hours")
    if isinstance(age, bool) or not isinstance(age, (int, float)) or not 1 <= age <= 72:
        raise ValueError("max_age_hours must be 1–72")
    refresh = settings.get("refresh_seconds")
    if isinstance(refresh, bool) or not isinstance(refresh, (int, float)) or not 120 <= refresh <= 3600:
        raise ValueError("refresh_seconds must be 120–3600")


plugin = Plugin("news", "News desk", module=NewsModule, provider=NewsProvider,
                defaults={"channels": DEFAULT_CHANNELS, "style": "auto", "refresh_seconds": 300, "max_age_hours": 12},
                validate_settings=validate, migrate_settings=migrate,
                choices={"style": STYLES},
                help={"channels": "Up to 8 LABEL=https://rss-feed entries separated by |",
                      "style": "headline shows whole headlines two lines at a time, breaking crawls them "
                               "along a TV lower third, zipper is Times Square; auto is mostly headlines"},
    ui={"refresh_seconds": {"advanced": True},
        "max_age_hours": {"type": "slider", "min": 1, "max": 72, "unit": "h", "label": "Skip stories older than"}, "channels": {"advanced": True, "label": "Channels (LABEL=feed URL, separated by |)"}})
