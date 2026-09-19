"""Read-only public prediction-market odds board; no trading capability.

Each event gets a card in two beats: the question, word-wrapped and still so it
can be read whole, then the answer slides up -- a big YES probability with a
filling bar, or the leading outcomes. Nothing moves while you are reading.
"""
import asyncio
import json
from itertools import zip_longest
import math
import time

import aiohttp
from PIL import Image, ImageDraw

from rackticker import Plugin, Provider, Snapshot, Module, new_frame, draw_text, offload
from app.core.fonts import draw_tiny, text_width, tiny_width
from app.core.fx import dim, ease_out, triangle
from app.core.renderer import GREEN, MUTED, RED, WHITE
from app.core.story import Storyboard
from app.modules.base import missing, stale_marker


POLYMARKET = ("https://gamma-api.polymarket.com/events?active=true&closed=false"
              "&limit=30&order=volume24hr&ascending=false")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2/events?status=open&limit=200&with_nested_markets=true"
POLY_BLUE, KALSHI_GREEN = (46, 120, 255), (0, 214, 150)
PAGE_SECONDS = 4.0
QUESTION_LINES = 2
SLIDE_SECONDS = .35


def _contested(outcomes):
    """Drop settled or foregone outcomes (a board full of 100% says nothing), and
    prop-line events whose outcomes share a label ("TOTAL KILLS OVER" twice)."""
    labels = [row["label"].upper() for row in outcomes]
    if len(set(labels)) < len(labels):
        return []
    # One question's outcomes are mutually exclusive and sum to ~100%. A bundle
    # of unrelated props ("MATCH WINNER 96%", "MAP 3 ROUNDS 90%") says nothing.
    if len(outcomes) > 1 and sum(row["probability"] for row in outcomes) > 130:
        return []
    live = [row for row in outcomes if .5 < row["probability"] < 99.5]
    if len(outcomes) > 1:
        # Long shots under 2% fill a whole page with "<1%"; a board needs two real contenders.
        live = [row for row in live if row["probability"] >= 2]
        return live if len(live) >= 2 else []
    return live


def _title(text):
    return " ".join(str(text).split()).rstrip("?").strip()


def _yes_probability(market):
    prices = market["outcomePrices"]
    prices = json.loads(prices) if isinstance(prices, str) else prices
    outcomes = market.get("outcomes") or []
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    yes = next((i for i, value in enumerate(outcomes) if str(value).strip().lower() == "yes"), 0)
    return float(prices[yes]) * 100


def polymarket_events(payload):
    events = []
    for raw in payload if isinstance(payload, list) else []:
        try:
            markets = [m for m in raw.get("markets") or [] if isinstance(m, dict) and not m.get("closed")]
            outcomes = []
            for market in markets:
                try:
                    probability = _yes_probability(market)
                except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                change = market.get("oneDayPriceChange")
                label = market.get("groupItemTitle") or market.get("question") or "YES"
                if 0 <= probability <= 100:
                    outcomes.append({"label": _title(label), "probability": probability,
                                     "change": None if change is None else float(change) * 100})
            title = _title(raw["title"])
            binary = len(outcomes) == 1
            outcomes = _contested(outcomes)
            if not outcomes or not title:
                continue
            if binary:
                outcomes[0]["label"] = "YES"
            outcomes.sort(key=lambda row: -row["probability"])
            events.append({"source": "POLY", "title": title, "binary": binary, "outcomes": outcomes[:6],
                           "volume": float(raw.get("volume24hr") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(events, key=lambda row: row["volume"], reverse=True)[:16]


def _dollars(raw, dollar_key, cent_key):
    if raw.get(dollar_key) not in (None, ""):
        return float(raw[dollar_key])
    return float(raw.get(cent_key) or 0) / 100


def kalshi_events(payload):
    events = []
    for raw in payload.get("events", []) if isinstance(payload, dict) else []:
        try:
            outcomes, volume = [], 0.0
            for market in raw.get("markets") or []:
                bid = _dollars(market, "yes_bid_dollars", "yes_bid")
                ask = _dollars(market, "yes_ask_dollars", "yes_ask")
                last = _dollars(market, "last_price_dollars", "last_price")
                previous = _dollars(market, "previous_price_dollars", "previous_price")
                price = last or ((bid + ask) / 2 if bid and ask else bid or ask)
                volume += float(market.get("volume_24h_fp") or market.get("volume_24h") or 0)
                label = market.get("yes_sub_title") or market.get("subtitle") or "YES"
                outcomes.append({"label": _title(label), "probability": price * 100,
                                 "change": (last - previous) * 100 if previous and last else None})
            title = _title(raw.get("title") or "")
            binary = len(outcomes) == 1
            outcomes = _contested(outcomes)
            if volume <= 0 or not outcomes or not title:
                continue
            if binary:
                outcomes[0]["label"] = "YES"
            outcomes.sort(key=lambda row: -row["probability"])
            events.append({"source": "KALSHI", "title": title, "binary": binary,
                           "outcomes": outcomes[:6], "volume": volume})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(events, key=lambda row: row["volume"], reverse=True)[:16]


def polymarket_rows(raw):
    return polymarket_events(json.loads(raw))


def kalshi_rows_from(raw):
    return kalshi_events(json.loads(raw))


class MarketsProvider(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.rows = []
        self.cache_until = 0.0

    async def _raw(self, url):
        async with self.session.get(url) as response:
            response.raise_for_status()
            return await response.read()

    @staticmethod
    async def _rows(parse, raw):
        if isinstance(raw, Exception):
            return []
        try:
            return await offload(parse, raw)
        except (ValueError, TypeError, KeyError):
            return []

    async def fetch(self):
        settings = self.context.settings
        tick = time.monotonic()
        if tick >= self.cache_until or not self.rows:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=5),
                    headers={"User-Agent": "RackTicker/0.2 (+https://github.com/costamesatechsolutions/rackticker)"})
            poly, kalshi = await asyncio.gather(self._raw(POLYMARKET), self._raw(KALSHI),
                                                return_exceptions=True)
            # A market feed is ~1 MB of JSON: parse it off the render loop.
            poly_rows = await self._rows(polymarket_rows, poly)
            kalshi_rows = await self._rows(kalshi_rows_from, kalshi)
            # Venue volume units differ. Rank inside each venue, then interleave.
            rows = [row for pair in zip_longest(poly_rows, kalshi_rows) for row in pair if row]
            if rows:
                self.rows = rows
            self.cache_until = tick + settings["refresh_seconds"]
        if not self.rows:
            raise ConnectionError("Prediction market feeds returned no usable markets")
        return Snapshot(self.rows, source="public_markets", metadata={"rotation_size": len(self.rows)})

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()


def _fit(text, width):
    """Questions and outcomes read as written, in mixed case, like a sentence."""
    while text and text_width(text, 1, True) > width:
        text = text[:-1].rstrip()
    return text


def _percent(value):
    return f"{round(value)}%" if value >= 1 or value == 0 else "<1%"


def wrap(text, width, lines):
    """Greedy word wrap into pages of `lines` rows that each fit `width` pixels."""
    rows, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if text_width(trial, 1, True) <= width:
            current = trial
            continue
        if current:
            rows.append(current)
        current = _fit(word, width)
    if current:
        rows.append(current)
    return [rows[index:index + lines] for index in range(0, len(rows), lines)] or [[""]]


def question_pages(event):
    """(rows, seconds) per page: about a third of a second per word, never rushed."""
    return [(rows, max(3.0, min(6.5, 1.4 + .32 * sum(len(row.split()) for row in rows))))
            for rows in wrap(event["title"], 128, QUESTION_LINES)]


LABEL_WIDTH = 128 - 6 - 24  # left of the percentage column
LABEL_SPEED, LABEL_PAUSE = 30, 1.2


def overflow(label):
    return max(0, text_width(label.upper()) - LABEL_WIDTH)


def board_pages(event):
    """[(outcomes, seconds)]: two outcomes a page, and long names get the time
    to scroll through in full before the page turns."""
    rows = event["outcomes"][:4]
    pages = []
    for start in range(0, len(rows), 2):
        shown = rows[start:start + 2]
        longest = max(overflow(row["label"]) for row in shown)
        seconds = PAGE_SECONDS if not longest else max(PAGE_SECONDS, 2 * LABEL_PAUSE + longest / LABEL_SPEED + .6)
        pages.append((start, shown, seconds))
    return pages


def answer_seconds(event):
    return 5.0 if event["binary"] else sum(seconds for *_, seconds in board_pages(event))


def card_seconds(event, speed=None):
    return sum(seconds for _, seconds in question_pages(event)) + answer_seconds(event)


class MarketsModule(Module):
    name = "markets"

    def __init__(self):
        self.board = Storyboard()

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _card(self, context):
        snap = context.snapshots.get(self.name)
        rows = snap.data if snap and isinstance(snap.data, list) else []
        build = lambda _visit: [(row, card_seconds(row)) for row in rows]
        self.board.sync(context.animation_time, build, context.scene)
        return self.board.current(context.animation_time, build)

    def hold(self, context):
        return bool(self._card(context)) and self.board.hold()

    def render(self, context):
        snap = context.snapshots.get(self.name)
        card = self._card(context)
        if not snap or not card:
            return missing("ODDS BOARD")
        event, local, _ = card
        venue = POLY_BLUE if event["source"] == "POLY" else KALSHI_GREEN
        pages = question_pages(event)
        asking = sum(seconds for _, seconds in pages)
        # Question first, whole and still; only then the answer slides up.
        question = self._question(event, pages, min(local, asking - .01), venue)
        if local < asking:
            return stale_marker(question, snap)
        answer = new_frame()
        self._header(answer, event, venue, "ODDS")
        into = local - asking
        if event["binary"]:
            self._binary(answer, event["outcomes"][0], into, venue)
        else:
            self._board(answer, event["outcomes"], into, venue)
        if into < SLIDE_SECONDS:
            shift = round(ease_out(into / SLIDE_SECONDS) * 32)
            frame = new_frame()
            frame.paste(question, (0, -shift))
            frame.paste(answer, (0, 32 - shift))
            answer = frame
        return stale_marker(answer, snap)

    @staticmethod
    def _header(frame, event, venue, right):
        name = "POLYMARKET" if event["source"] == "POLY" else "KALSHI"
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 0, 3, 6), fill=venue)
        draw_tiny(frame, name, 6, 1, venue)
        draw_tiny(frame, right, 128 - tiny_width(right), 1, MUTED)

    def _question(self, event, pages, local, venue):
        frame = new_frame()
        elapsed, index = 0.0, 0
        for index, (_, seconds) in enumerate(pages):
            if local < elapsed + seconds:
                break
            elapsed += seconds
        rows = pages[index][0]
        label = f"{index + 1}/{len(pages)}" if len(pages) > 1 else "QUESTION"
        self._header(frame, event, venue, label)
        # Mixed case needs room for descenders: two lines of nine rows each.
        top = 11 if len(rows) == QUESTION_LINES else 15
        for line, row in enumerate(rows):
            draw_text(frame, row, 0, top + line * 10, WHITE, mixed=True)
        return frame

    @staticmethod
    def _binary(frame, outcome, local, venue):
        draw = ImageDraw.Draw(frame)
        grow = ease_out(local / .9)
        probability = outcome["probability"]
        shown = _percent(probability * grow) if probability >= 1 else _percent(probability)
        draw_text(frame, shown, 0, 9, WHITE, 2, True)
        draw_text(frame, "YES", text_width(shown, 2) + 5, 16, venue)
        change = outcome.get("change")
        if change is not None and abs(change) >= .5:
            color = GREEN if change > 0 else RED
            note = f"{abs(change):.0f} PTS TODAY"
            triangle(frame, 127 - text_width(note) - 7, 12, change > 0, color)
            draw_text(frame, note, 128 - text_width(note), 10, color)
        else:
            draw_text(frame, "STEADY", 128 - text_width("STEADY"), 10, MUTED)
        draw.rectangle((0, 27, 127, 30), fill=(14, 18, 24))
        filled = round(127 * probability / 100 * grow)
        if filled > 0:
            draw.rectangle((0, 27, filled, 30), fill=venue)

    @staticmethod
    def _board(frame, outcomes, local, venue):
        draw = ImageDraw.Draw(frame)
        pages = board_pages({"outcomes": outcomes})
        for start, shown, seconds in pages:
            if local < seconds or start == pages[-1][0]:
                break
            local -= seconds
        grow = ease_out(local / .7)
        pct_width = text_width("100%")
        for slot, outcome in enumerate(shown):
            rank = start + slot
            # A lone outcome on its page sits in the middle, not at the top.
            y = 15 if len(shown) == 1 else 10 + slot * 11
            pct = _percent(outcome["probability"])
            leader = rank == 0
            label = outcome["label"].upper()
            color = WHITE if leader else (170, 180, 180)
            extra = overflow(label)
            if extra:
                # A long name reads through once, then rests on its end: no truncation.
                shift = min(extra, max(0.0, local - LABEL_PAUSE) * LABEL_SPEED)
                strip = Image.new("RGB", (LABEL_WIDTH, 7))
                draw_text(strip, label, -round(shift), 0, color)
                frame.paste(strip, (0, y))
            else:
                draw_text(frame, label, 0, y, color)
            draw_text(frame, pct, 128 - text_width(pct), y, venue if leader else WHITE)
            draw.rectangle((0, y + 8, 127, y + 8), fill=(16, 22, 26))
            filled = round(127 * outcome["probability"] / 100 * grow)
            if filled > 0:
                draw.rectangle((0, y + 8, filled, y + 8), fill=venue if leader else dim(venue, .5))


def migrate(settings):
    settings.pop("cycle_seconds", None)  # Cards now last as long as their question.
    return settings


def validate(settings):
    value = settings.get("refresh_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 30 <= value <= 600:
        raise ValueError("refresh_seconds must be 30–600")


plugin = Plugin("markets", "Prediction markets", module=MarketsModule,
                provider=MarketsProvider,
                defaults={"refresh_seconds": 90},
                validate_settings=validate, migrate_settings=migrate,
    ui={"refresh_seconds": {"advanced": True}})
