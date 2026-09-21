"""CNBC-style market tape: a flipping index header over a continuous two-deck
crawl of the day's movers and your watchlist. Key-free Yahoo chart quotes."""
from __future__ import annotations

import asyncio
from datetime import datetime
from functools import lru_cache
import math
import re
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from PIL import Image, ImageDraw

from rackticker import Plugin, Provider, Snapshot, Module, new_frame, draw_text
from app.core.fonts import draw_tiny, text_width, tiny_width
from app.core.fx import dim, ease_out, triangle
from app.core.renderer import AMBER, GREEN, MUTED, RED, WHITE, loop_strip
from app.modules.base import missing, stale_marker


API = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=5m&range=1d"
SPARK = "https://query1.finance.yahoo.com/v7/finance/spark?symbols={}&range=1d&interval=15m"
SCREENER = ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
            "?formatted=false&scrIds={}&count=12&start=0")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RackTicker/0.2)"}
INDICES = (("^GSPC", "S&P"), ("^DJI", "DOW"), ("^IXIC", "NAS"), ("^RUT", "R2K"),
           ("^VIX", "VIX"), ("^TNX", "10YR"), ("CL=F", "OIL"), ("GC=F", "GOLD"), ("BTC-USD", "BTC"))
NEWS = "https://query1.finance.yahoo.com/v1/finance/search?q={}&quotesCount=0&newsCount=8"
PAGE_SECONDS = 3.5
TAPE_Y = 11
BLOCK_PAUSE = .02
# Company news is looked up a few symbols per refresh and kept this long.
NEWS_SECONDS, NEWS_PER_REFRESH, NEWS_MAX_HOURS = 900, 6, 24
NAME_SUFFIX = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|"
                         r"group|sa|nv|ag|se|class [a-c]|common stock|ordinary shares|the)\.?$", re.I)


def symbols(value):
    return [part.strip().upper() for part in value.split(",") if part.strip()]


def _closes(values):
    return [float(value) for value in values or []
            if value is not None and math.isfinite(float(value))]


def company_name(value, symbol=""):
    """'Meta Platforms, Inc.' -> 'META PLATFORMS': the name a person would say."""
    name = str(value or "").strip()
    while True:
        shorter = NAME_SUFFIX.sub("", name).strip(" ,.")
        if shorter == name or not shorter:
            break
        name = shorter
    name = name.upper()
    if not name or name == symbol or len(name) > 18:
        # Very long legal names read worse than the ticker itself.
        words, name = name.split(), ""
        for word in words:
            if len(name) + len(word) + 1 > 18:
                break
            name = f"{name} {word}".strip()
    return "" if name == symbol else name


def headline_for(payload, symbol, name, now):
    """Newest recent headline that is actually about this company, not a
    market wrap that merely tags it."""
    marker = (name.split() or [""])[0]
    words = {symbol.upper()} | ({marker} if len(marker) >= 3 else set())
    for item in sorted((payload or {}).get("news") or [], key=lambda n: -(n.get("providerPublishTime") or 0)):
        title = " ".join(str(item.get("title") or "").split())
        age = (now - float(item.get("providerPublishTime") or 0)) / 3600
        if not title or not 0 <= age <= NEWS_MAX_HOURS:
            continue
        if words & set(re.findall(r"[A-Z0-9&]+", title.upper().replace("'S", ""))):
            publisher = re.sub(r"\.(com|net|org)$", "", str(item.get("publisher") or ""), flags=re.I)
            return {"title": title[:90], "publisher": publisher[:24], "hours": age}
    return None


def _row(symbol, price, previous, closes):
    if not math.isfinite(price) or not math.isfinite(previous) or previous <= 0:
        raise ValueError("Invalid finance price")
    return {"symbol": str(symbol).upper()[:10], "price": price, "previous": previous,
            "change": (price - previous) / previous * 100, "delta": price - previous,
            "closes": closes[-40:] or [previous, price], "category": "WATCH"}


def normalize(payload, requested):
    """One chart response -> quote row (fallback when batch spark fails)."""
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        row = _row(meta.get("symbol") or requested, float(meta["regularMarketPrice"]),
                   float(meta.get("chartPreviousClose") or meta["previousClose"]),
                   _closes(result["indicators"]["quote"][0]["close"]))
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid finance quote response") from exc
    row["currency"] = str(meta.get("currency") or "").upper()[:4]
    row["name"] = company_name(meta.get("shortName") or meta.get("longName"), row["symbol"])
    return row


def spark_rows(payload):
    rows = {}
    results = ((payload or {}).get("spark") or {}).get("result") or []
    for item in results if isinstance(results, list) else []:
        try:
            response = item["response"][0]
            meta = response["meta"]
            closes = _closes(((response.get("indicators") or {}).get("quote") or [{}])[0].get("close"))
            row = _row(item.get("symbol") or meta["symbol"], float(meta["regularMarketPrice"]),
                       float(meta.get("chartPreviousClose") or meta["previousClose"]), closes)
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            continue
        row["name"] = company_name(meta.get("shortName") or meta.get("longName"), row["symbol"])
        rows[row["symbol"]] = row
    return rows


def market_state(now=None):
    ny = (now or datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    minutes = ny.hour * 60 + ny.minute
    if ny.weekday() >= 5:
        return "CLOSED"
    if 570 <= minutes < 960:
        return "LIVE"
    if 240 <= minutes < 570:
        return "PRE"
    if 960 <= minutes < 1200:
        return "AFTER"
    return "CLOSED"


class FinanceProvider(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.data = None
        self.cache_until = 0.0
        self.news = {}  # symbol -> (checked monotonic, raw search payload)

    async def _json(self, url):
        async with self.session.get(url) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _discover(self):
        async def screen(name):
            payload = await self._json(SCREENER.format(name))
            try:
                return payload["finance"]["result"][0]["quotes"]
            except (KeyError, IndexError, TypeError):
                return []
        results = await asyncio.gather(screen("day_gainers"), screen("day_losers"),
                                       screen("most_actives"), return_exceptions=True)
        gainers, losers, active = [result if isinstance(result, list) else [] for result in results]

        def liquid(rows, count):
            return [str(row.get("symbol") or "") for row in rows
                    if float(row.get("regularMarketVolume") or 0) >= 1_000_000][:count]
        return ([(symbol, "GAINER") for symbol in liquid(gainers, 4)] +
                [(symbol, "LOSER") for symbol in liquid(losers, 4)] +
                [(symbol, "ACTIVE") for symbol in liquid(active, 4)])

    async def _quotes(self, wanted):
        chunks = [wanted[index:index + 20] for index in range(0, len(wanted), 20)]
        results = await asyncio.gather(*(self._json(SPARK.format(",".join(quote(s, safe="") for s in chunk)))
                                         for chunk in chunks), return_exceptions=True)
        quotes = {}
        for result in results:
            if not isinstance(result, Exception):
                quotes.update(spark_rows(result))
        if not quotes:  # Batch endpoint unavailable: fall back to per-symbol charts.
            charts = await asyncio.gather(*(self._json(API.format(quote(s, safe=""))) for s in wanted[:14]),
                                          return_exceptions=True)
            for symbol, payload in zip(wanted, charts):
                try:
                    quotes[symbol] = normalize(payload, symbol)
                except (ValueError, TypeError):
                    continue
        return quotes

    async def fetch(self):
        settings = self.context.settings
        now = time.monotonic()
        if self.data is None or now >= self.cache_until:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.5),
                                                     headers=HEADERS)
            movers = await self._discover() if settings["mode"] == "auto" else []
            tape, seen = [], set()
            for symbol, category in movers + [(s, "WATCH") for s in symbols(settings["symbols"])]:
                if symbol and symbol not in seen:
                    tape.append((symbol, category))
                    seen.add(symbol)
            wanted = list(dict.fromkeys([s for s, _ in INDICES] + [s for s, _ in tape]))
            quotes = await self._quotes(wanted)
            indices = [dict(quotes[s], label=label) for s, label in INDICES if s in quotes]
            rows = [dict(quotes[s], category=category) for s, category in tape if s in quotes]
            await self._refresh_news([row["symbol"] for row in rows], now)
            wall = time.time()
            for row in rows:
                payload = self.news.get(row["symbol"], (0, None))[1]
                row["news"] = headline_for(payload, row["symbol"], row.get("name", ""), wall) if payload else None
            if not indices and not rows:
                raise ConnectionError("Finance feed returned no usable quotes")
            data = {"indices": indices, "tape": rows or indices}
            # Draw the tape before publishing, a symbol at a time with a breath between: all at
            # once held the interpreter long enough to stutter the crawl on screen, and
            # rebuilding it inside a render is a hitch in the middle of the crawl.
            key = _key(data["tape"])
            for row in key:
                tape_block(row)
                await asyncio.sleep(BLOCK_PAUSE)
            tape_strip(key)
            self.data = data
            self.cache_until = now + settings["refresh_seconds"]
        return Snapshot(self.data, source="finance_chart",
                        metadata={"rotation_size": len(self.data["tape"])})

    async def _refresh_news(self, wanted, now):
        due = [s for s in wanted if now - self.news.get(s, (-NEWS_SECONDS, None))[0] >= NEWS_SECONDS]
        due = due[:NEWS_PER_REFRESH]
        results = await asyncio.gather(*(self._json(NEWS.format(quote(s, safe=""))) for s in due),
                                       return_exceptions=True)
        for symbol, payload in zip(due, results):
            self.news[symbol] = (now, None if isinstance(payload, Exception) else payload)
        for symbol in list(self.news):
            if symbol not in wanted:
                del self.news[symbol]

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()


def _price(value):
    if value >= 10000:
        return f"{value:,.0f}"
    if value >= 1000:
        return f"{value:,.1f}"
    if value >= 1:
        return f"{value:.2f}"
    return f"{value:.4f}"


def _delta(value):
    return f"{value:+,.0f}" if abs(value) >= 1000 else f"{value:+.2f}"


def age_label(hours):
    return f"{max(1, round(hours * 60))} MIN AGO" if hours < 1 else f"{round(hours)} HR AGO"


def _key(rows):
    def news(row):
        item = row.get("news")
        return (item["title"], item["publisher"], age_label(item["hours"])) if item else None
    return tuple((row["symbol"], round(row["price"], 4), round(row["change"], 3), round(row["delta"], 4),
                  tuple(round(v, 4) for v in row["closes"]), round(row.get("previous", 0), 4),
                  row.get("name", ""), news(row))
                 for row in rows)


def _layout(row):
    """Where each part of one symbol's block sits, relative to the block's left edge."""
    symbol, price, change, delta, closes, previous, name, news = row
    top, bottom = _price(price), _delta(delta)
    pct = f"{change:+.2f}%"
    label = text_width(symbol) + (5 + text_width(name) if name else 0)
    c2 = max(label, text_width(top)) + 5
    c3 = c2 + 7 + max(text_width(pct), text_width(bottom)) + 5
    end = c3 + 24
    if news:
        end += 9 + max(text_width(news[0], 1, True), text_width(f"{news[1]} {news[2]}".strip()))
    return top, bottom, pct, c2, c3, end


@lru_cache(maxsize=96)
def tape_block(row):
    """One symbol's part of the tape: SYMBOL name / price, ▲+pct / +delta, an intraday
    sparkline, then the company's latest headline when it has one. Cached by content,
    so a refresh only draws what changed and the provider can draw them one at a time."""
    symbol, price, change, delta, closes, previous, name, news = row
    top, bottom, pct, c2, c3, end = _layout(row)
    block = Image.new("RGB", (end + 8, 21))
    draw = ImageDraw.Draw(block)
    color = GREEN if change >= 0 else RED
    draw_text(block, symbol, 0, 1, WHITE)
    if name:
        draw_text(block, name, text_width(symbol) + 5, 1, AMBER)
    draw_text(block, top, 0, 12, (176, 196, 196))
    triangle(block, c2, 3, change >= 0, color)
    draw_text(block, pct, c2 + 7, 1, color)
    draw_text(block, bottom, c2 + 7, 12, dim(color, .72))
    if len(closes) > 1:
        low, high = min(closes + (previous,)), max(closes + (previous,))
        span = high - low or 1
        if previous:
            base = 18 - round((previous - low) * 17 / span)
            for bx in range(c3, c3 + 24, 3):
                draw.point((bx, base), fill=(60, 66, 66))
        points = [(c3 + round(i * 23 / (len(closes) - 1)), 18 - round((v - low) * 17 / span))
                  for i, v in enumerate(closes)]
        draw.line(points, fill=color)
    separator = c3 + 24 + 7
    if news:
        title, publisher, age = news
        draw_text(block, title, c3 + 33, 1, WHITE, mixed=True)
        draw_text(block, f"{publisher} {age}".strip(), c3 + 33, 12, MUTED)
        separator = c3 + 33 + max(text_width(title, 1, True), text_width(f"{publisher} {age}".strip())) + 7
    for y in range(3, 19, 2):
        draw.point((separator, y), fill=(70, 58, 30))
    return block


@lru_cache(maxsize=4)
def tape_strip(key):
    """The whole tape, two decks: the symbols' blocks side by side. Cheap once the blocks exist."""
    x, starts = 0, []
    for row in key:
        starts.append(x)
        x += _layout(row)[5] + 15
    strip = Image.new("RGB", (max(128, x), 21))
    for start, row in zip(starts, key):
        strip.paste(tape_block(row), (start, 0))
    return strip, tuple(starts)


class FinanceModule(Module):
    name = "finance"

    def __init__(self):
        self._strip = (None, None, ())
        self.symbols = ()
        self.scene = None
        self.origin = 0
        self.position = 0
        self.hold_until = None

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        """A tape of Friday's closing prices is not news on a Sunday."""
        snap = context.snapshots.get(self.name)
        if not (snap and snap.data):
            return False
        when = context.config["plugins"][self.name].get("when", "weekdays")
        state = market_state(context.now)
        if when == "open" and state not in ("LIVE", "PRE", "AFTER"):
            return False
        if when == "weekdays" and state == "CLOSED" and context.now.astimezone(
                ZoneInfo("America/New_York")).weekday() >= 5:
            return False
        return True

    def _tape(self, data):
        # The provider hands over a new dict only when quotes refresh, so the
        # identity check avoids re-hashing every sparkline point each frame.
        if self._strip[0] is not data:
            key = _key(data["tape"])
            strip, starts = tape_strip(key)
            self._reanchor(strip, starts, tuple(row[0] for row in key))
            self._strip = (data, strip, starts)
        return self._strip[1], self._strip[2]

    def _reanchor(self, strip, starts, symbols):
        """New quotes change how wide each symbol is. Swapping the tape underneath a crawl
        would make it jump, so keep whatever is at the left edge exactly where it is."""
        old_strip, old_starts, old_symbols = self._strip[1], self._strip[2], self.symbols
        self.symbols = symbols
        if old_strip is None or not old_starts:
            return
        edge = self.position % old_strip.width
        index = max((i for i, start in enumerate(old_starts) if start <= edge), default=0)
        inside = edge - old_starts[index]
        symbol = old_symbols[index] if index < len(old_symbols) else None
        found = symbols.index(symbol) if symbol in symbols else min(index, len(starts) - 1)
        width = starts[found + 1] - starts[found] if found + 1 < len(starts) else strip.width - starts[found]
        wanted = starts[found] + min(inside, max(0, width - 1))
        shift = (self.position - self.position % strip.width) + wanted - self.position
        self.origin += shift
        self.position += shift
        if self.hold_until is not None:
            self.hold_until += shift

    def _advance(self, context, strip, starts):
        """Tape position in pixels. Each visit resumes at the symbol that was on
        the left edge when the last visit ended, like a real exchange ticker."""
        if context.scene != self.scene:
            self.scene = context.scene
            edge = self.position % strip.width
            self.origin = max((start for start in starts if start <= edge), default=0)
            self.hold_until = None
        self.position = self.origin + math.floor(context.animation_time * context.config["display"]["scroll_speed"] + 1e-6)
        return self.position

    def hold(self, context):
        snap = context.snapshots.get(self.name)
        if not snap or not isinstance(snap.data, dict) or not snap.data.get("tape"):
            return False
        strip, starts = self._tape(snap.data)
        position = self._advance(context, strip, starts)
        if self.hold_until is None:
            # Finish the symbol crossing the left edge, then let the playlist move on.
            edge = position % strip.width
            following = [start for start in starts if start > edge]
            self.hold_until = position + (following[0] if following else strip.width) - edge
        return position < self.hold_until

    def render(self, context):
        snap = context.snapshots.get(self.name)
        if not snap or not isinstance(snap.data, dict) or not snap.data.get("tape"):
            return missing("MARKETS")
        data, t = snap.data, context.animation_time
        frame = new_frame()
        draw = ImageDraw.Draw(frame)
        state = market_state()
        tag = {"LIVE": GREEN, "PRE": AMBER, "AFTER": AMBER}.get(state, MUTED)
        tag_width = tiny_width(state) + 4
        draw.rectangle((0, 0, tag_width, 8), fill=tag)
        draw_tiny(frame, state, 2, 2, (0, 0, 0))
        self._header(frame, data["indices"], t, tag_width + 4)
        for x in range(0, 128, 2):
            draw.point((x, 9), fill=(64, 50, 20))
        strip, starts = self._tape(data)
        speed = context.config["display"]["scroll_speed"]
        loop_strip(frame, strip, (0, TAPE_Y, 128, 21), (self._advance(context, strip, starts) + .5) / speed, speed)
        return stale_marker(frame, snap)

    def _header(self, frame, indices, t, left):
        width = 128 - left
        layer = Image.new("RGB", (width, 9))
        if not indices:
            draw_text(layer, "MARKETS", 0, 1, AMBER)
            frame.paste(layer, (left, 0))
            return
        page = math.floor(t / PAGE_SECONDS)
        local = t - page * PAGE_SECONDS
        shift = round((1 - ease_out(local / .4)) * 9) if page and local < .4 else 0
        for index, y in ((page, 1 + shift), (page - 1, 1 + shift - 9)):
            if -8 < y < 9 and index >= 0:
                self._index(layer, indices[index % len(indices)], y, width)
        frame.paste(layer, (left, 0))

    @staticmethod
    def _index(layer, row, y, width):
        color = GREEN if row["change"] >= 0 else RED
        pct = f"{row['change']:+.2f}%"
        value = _price(row["price"])
        right = width - text_width(pct)
        draw_text(layer, pct, right, y, color)
        triangle(layer, right - 7, y + 2, row["change"] >= 0, color)
        name_width = text_width(row["label"]) + 4
        if name_width + text_width(value) <= right - 9:
            draw_text(layer, row["label"], 0, y, AMBER)
            draw_text(layer, value, name_width, y, WHITE)
        elif text_width(value) <= right - 9:
            draw_text(layer, value, 0, y, WHITE)


def migrate(settings):
    settings.pop("cycle_seconds", None)  # Rotation is now a continuous tape.
    return settings


def validate(settings):
    if settings.get("mode") not in ("auto", "custom"):
        raise ValueError("mode must be auto or custom")
    value = settings.get("symbols")
    parsed = symbols(value) if isinstance(value, str) else []
    if not 1 <= len(parsed) <= 20 or len(parsed) != len(set(parsed)):
        raise ValueError("symbols must contain 1–20 unique comma-separated symbols")
    if any(not re.fullmatch(r"[A-Z0-9.^=-]{1,15}", item) for item in parsed):
        raise ValueError("symbols contains an invalid quote symbol")
    value = settings.get("refresh_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 30 <= value <= 900:
        raise ValueError("refresh_seconds must be 30–900")


plugin = Plugin("finance", "Stock tape", module=FinanceModule,
                provider=FinanceProvider,
                defaults={"mode": "auto", "symbols": "NVDA,AAPL,TSLA,MSFT,AMZN,META",
                          "when": "weekdays", "refresh_seconds": 60},
                validate_settings=validate, migrate_settings=migrate,
                choices={"mode": ("auto", "custom"), "when": ("weekdays", "open", "always")},
                help={"mode": "auto adds the day's top gainers, losers and most active",
                      "when": "weekdays skips Saturday and Sunday; open shows it only while the "
                              "market is trading, including pre- and after-hours",
                      "symbols": "Your watchlist, comma separated (stocks, ETFs, BTC-USD, CL=F…)"},
    ui={"symbols": {"type": "tags", "label": "Watchlist"}, "refresh_seconds": {"advanced": True}})
