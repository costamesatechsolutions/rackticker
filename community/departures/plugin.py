"""Departures: live station boards from Budapest, Rome, Milan, Florence, Venice,
Naples and Zürich, each drawn the way that country's boards look.

Live data, no keys: MÁV (Hungary), ViaggiaTreno (Trenitalia) and the Swiss
open transport API. While a station sleeps, "Clock" can replay its timetable at
your own time of day, so the board is busy when you are awake.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo

import aiohttp
from PIL import Image, ImageDraw

from rackticker import Module, Plugin, Provider, Snapshot, draw_text, draw_tiny, new_frame, text_width, tiny_width

UA = {"User-Agent": "RackTicker departures (+https://github.com/costamesatechsolutions/rackticker)"}

# id: (station name, city, time zone, source, source id, board style)
STATIONS = {
    "budapest_keleti": ("Budapest-Keleti", "Budapest", "Europe/Budapest", "mav", "005510017", "mav"),
    "roma_termini": ("Roma Termini", "Roma", "Europe/Rome", "trenitalia", "S08409", "trenitalia"),
    "milano_centrale": ("Milano Centrale", "Milano", "Europe/Rome", "trenitalia", "S01700", "trenitalia"),
    "firenze_smn": ("Firenze S.M.N.", "Firenze", "Europe/Rome", "trenitalia", "S06421", "trenitalia"),
    "venezia_sl": ("Venezia S. Lucia", "Venezia", "Europe/Rome", "trenitalia", "S02593", "trenitalia"),
    "napoli_centrale": ("Napoli Centrale", "Napoli", "Europe/Rome", "trenitalia", "S09218", "trenitalia"),
    "zurich_hb": ("Zürich HB", "Zürich", "Europe/Zurich", "sbb", "Zürich HB", "sbb"),
}
TOUR = tuple(STATIONS)

WHITE, YELLOW, AMBER, RED, GREEN = (236, 238, 236), (255, 206, 40), (255, 150, 20), (255, 60, 45), (80, 220, 120)
GREY, DIM = (150, 156, 160), (70, 74, 78)
# Board looks: text colours, words, and how trains are painted in the platform scene.
STYLES = {
    "mav": {"time": WHITE, "dest": YELLOW, "track": (30, 60, 150), "accent": (60, 120, 230), "mixed": True,
            "departures": ("Induló", "vonatok"), "late": "késik", "cancelled": "Törölve", "track_word": "vágány",
            "train": ((40, 80, 190), (225, 228, 232), (240, 196, 40))},
    "trenitalia": {"time": YELLOW, "dest": YELLOW, "track": (70, 74, 82), "accent": (230, 40, 40), "mixed": False,
                   "departures": ("Partenze", ""), "late": "rit.", "cancelled": "CANCELLATO", "track_word": "binario",
                   "train": ((205, 210, 215), (220, 30, 30), (60, 64, 70))},
    "sbb": {"time": WHITE, "dest": WHITE, "track": (20, 60, 140), "accent": (230, 30, 30), "mixed": True,
            "departures": ("Abfahrt", ""), "late": "ca.", "cancelled": "fällt aus", "track_word": "Gleis",
            "train": ((225, 30, 30), (240, 240, 240), (40, 40, 44))},
}
# Category badges: (background, text) per style; unknown categories get the style accent.
BADGES = {
    "FR": ((220, 25, 25), WHITE), "FA": ((120, 126, 134), WHITE),
    "IC": ((40, 90, 210), WHITE), "ICN": ((20, 40, 120), WHITE), "EC": ((40, 90, 210), WHITE),
    "EN": ((60, 40, 140), WHITE), "REG": ((40, 150, 70), WHITE), "RV": ((40, 150, 70), WHITE),
    "IR": ((220, 30, 30), WHITE), "RE": ((220, 30, 30), WHITE), "RJX": ((200, 30, 30), WHITE),
    "RJ": ((200, 30, 30), WHITE), "S": ((84, 90, 98), WHITE), "TGV": ((150, 20, 60), WHITE),
    "ICE": ((150, 20, 30), WHITE), "FB": ((190, 30, 40), WHITE),
}
ROWS, ROW_Y = 3, (1, 11, 21)
TITLE_SECONDS, PAGE_SECONDS, SCENE_SECONDS = 3.5, 7.0, 6.5
NOTICE_SPEED, NOTICES = 32, 3   # announcements crawl at 32 px/s; at most three per visit
# The station's own announcement, per board style: (heading, late, platform, cancelled).
# {train} is "FR 9612", {dest} the destination, {time} the planned departure.
ANNOUNCE = {
    "trenitalia": ("AVVISO", "{train} per {dest} delle {time}: ritardo {delay} minuti",
                   "{train} per {dest} delle {time} parte dal binario {track}",
                   "{train} per {dest} delle {time} è cancellato"),
    "mav": ("Figyelem", "A {time}-kor induló {train} vonat {dest} felé kb. {delay} percet késik",
            "A {time}-kor induló {train} vonat {dest} felé a {track}. vágányról indul",
            "A {time}-kor induló {train} vonat {dest} felé ma nem közlekedik"),
    "sbb": ("Information", "{train} nach {dest}, Abfahrt {time}: ca. {delay} Minuten später",
            "{train} nach {dest}, Abfahrt {time}: heute ab Gleis {track}",
            "{train} nach {dest}, Abfahrt {time}: fällt aus"),
}


def notices(rows, style_name):
    """What the station would announce about the trains on the board: long delays,
    changed platforms and cancellations, in its own language."""
    heading, late, moved, cancelled = ANNOUNCE[style_name]
    said = []
    for row in rows:
        train = f"{row['kind']} {row['number']}".strip() if row["number"] != row["kind"] else row["kind"]
        values = {"train": train, "dest": row["destination"] if style_name != "trenitalia"
                  else row["destination"].upper(), "time": row["time"].strftime("%H:%M"),
                  "delay": row["delay"], "track": row["track"]}
        if row["cancelled"]:
            said.append(cancelled.format(**values))
        elif row["delay"] >= 10:
            said.append(late.format(**values))
        elif row.get("moved") and row["track"]:
            said.append(moved.format(**values))
    return heading, said[:NOTICES]


def _parse_time(value, zone):
    if not value:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=zone)


def board_moment(settings, zone, now=None):
    """The station time the board shows: real, or your clock's time of day there."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(zone)
    mode = settings["clock"]
    if mode == "auto":
        mode = "station" if 6 <= local.hour < 23 else "mine"
    if mode == "station":
        return local, True
    mine = now.astimezone()
    return local.replace(hour=mine.hour, minute=mine.minute, second=mine.second), False


# --- sources -----------------------------------------------------------------------

async def mav(session, code, when, zone):
    body = {"type": "StationInfo", "travelDate": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "stationNumberCode": code, "minCount": "0", "maxCount": "24"}
    headers = {**UA, "Language": "en", "UserSessionId": "''", "UserId": "Unknown"}
    async with session.post("https://jegy-a.mav.hu/IK_API_PROD/api/InformationApi/GetTimetable",
                            json=body, headers=headers) as response:
        response.raise_for_status()
        payload = await response.json(content_type=None)
    rows = []
    for train in ((payload.get("stationSchedulerDetails") or {}).get("departureScheduler") or []):
        planned = _parse_time(train.get("start"), zone)
        if not planned:
            continue
        info = train.get("havarianInfok") or {}
        sign_info = train.get("viszonylatiJel") or {}
        sign = sign_info.get("jel")
        kind = sign or (train.get("kind") or {}).get("sortName") or ""
        name = str(train.get("fullName") or "")
        rows.append({"time": planned, "delay": int(info.get("aktualisKeses") or 0),
                     "kind": "" if kind == "BESZ" else kind, "number": str(train.get("code") or ""),
                     "color": sign_info.get("fontSzinKod") or "",
                     "name": name[name.find("(") + 1:name.find(")")].title() if "(" in name else "",
                     "destination": (train.get("endStation") or {}).get("name") or "",
                     "track": str(train.get("startTrack") or ""), "cancelled": False,
                     "moved": train.get("startTrackType") == "FactPlanDifference"})
    return rows


async def trenitalia(session, code, when, zone):
    stamp = when.astimezone(zone).strftime("%a %b %d %Y %H:%M:%S GMT%z")
    url = f"http://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno/partenze/{code}/{stamp}"
    async with session.get(url, headers=UA) as response:
        response.raise_for_status()
        payload = await response.json(content_type=None)
    rows = []
    for train in payload or []:
        if not train.get("orarioPartenza"):
            continue
        planned = datetime.fromtimestamp(train["orarioPartenza"] / 1000, zone)
        planned_track = str(train.get("binarioProgrammatoPartenzaDescrizione") or "").strip()
        actual_track = str(train.get("binarioEffettivoPartenzaDescrizione") or "").strip()
        track = actual_track or planned_track
        rows.append({"time": planned, "delay": int(train.get("ritardo") or 0),
                     "kind": str(train.get("categoriaDescrizione") or train.get("categoria") or "").strip(),
                     "number": str(train.get("numeroTreno") or ""), "name": "",
                     "destination": str(train.get("destinazione") or ""), "track": track,
                     "moved": bool(planned_track and actual_track and planned_track != actual_track),
                     "cancelled": train.get("provvedimento") == 1})
    return rows


async def sbb(session, name, when, zone):
    params = {"station": name, "limit": "20", "datetime": when.astimezone(zone).strftime("%Y-%m-%d %H:%M")}
    async with session.get("https://transport.opendata.ch/v1/stationboard", params=params, headers=UA) as response:
        response.raise_for_status()
        payload = await response.json(content_type=None)
    rows = []
    for train in payload.get("stationboard") or []:
        stop = train.get("stop") or {}
        planned = _parse_time(stop.get("departure"), zone)
        if not planned:
            continue
        prognosis = stop.get("prognosis") or {}
        kind = str(train.get("category") or "")
        line = str(train.get("number") or "")
        rows.append({"time": planned, "delay": int(stop.get("delay") or 0),
                     "kind": f"{kind}{line}" if kind in ("S", "IR", "RE") and len(line) <= 2 else kind,
                     "number": line, "name": "", "destination": str(train.get("to") or ""),
                     "track": str(prognosis.get("platform") or stop.get("platform") or ""),
                     "moved": bool(prognosis.get("platform") and stop.get("platform")
                                   and prognosis["platform"] != stop["platform"]),
                     "cancelled": bool(stop.get("cancelled"))})
    return rows


SOURCES = {"mav": mav, "trenitalia": trenitalia, "sbb": sbb}


class Boards(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.cache = {}   # station -> (fetched monotonic, board moment, rows)

    def stations(self):
        chosen = self.context.settings["station"]
        return list(TOUR) if chosen == "tour" else [chosen]

    async def fetch(self):
        settings = self.context.settings
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        loop_now = asyncio.get_running_loop().time()
        due = [station for station in self.stations()
               if loop_now - self.cache.get(station, (-1e9,))[0] >= settings["refresh_seconds"]]
        for station in due[:2]:  # a tour refreshes two stations per poll
            name, city, zone_name, source, code, style = STATIONS[station]
            zone = ZoneInfo(zone_name)
            moment, live = board_moment(settings, zone)
            try:
                rows = await SOURCES[source](self.session, code, moment - timedelta(minutes=2), zone)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as exc:
                if station not in self.cache:
                    print(f"{station}: {type(exc).__name__}: {exc}")
                self.cache.setdefault(station, (loop_now, moment, []))
                continue
            self.cache[station] = (loop_now, moment, sorted(rows, key=lambda row: row["time"]))
        boards = {}
        for station in self.stations():
            if station not in self.cache:
                continue
            fetched, moment, rows = self.cache[station]
            boards[station] = {"rows": rows, "fetched_moment": moment.isoformat(),
                               "live": board_moment(settings, ZoneInfo(STATIONS[station][2]))[1]}
        if not boards:
            raise ConnectionError("No station board answered")
        return Snapshot(boards)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# --- drawing -------------------------------------------------------------------------

def _badge(frame, kind, x, y, style, color=""):
    """A category box with white letters: dark ink on a lit box reads as noise on LEDs."""
    if not kind:
        return x
    base = kind.rstrip("0123456789") or kind
    fill, ink = BADGES.get(base, (STYLES[style]["accent"], WHITE))
    if len(color) == 6:  # the line's own colour, as MÁV publishes it
        rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
        if 120 < sum(rgb) < 600:
            fill, ink = rgb, WHITE
    width = text_width(kind) + 3
    ImageDraw.Draw(frame).rectangle((x, y - 1, x + width - 1, y + 7), fill=fill)
    draw_text(frame, kind, x + 2, y, ink)
    return x + width + 3


def _track_box(frame, track, right, y, style, moved, t, width=None):
    """The platform number in its own lit box, right-aligned at `right`, like the
    track column of a real board. A changed platform flashes amber, as they do."""
    width = width or text_width(track) + 3
    fill = AMBER if moved and math.floor(t * 2) % 2 == 0 else style["track"]
    left = right - width + 1
    ImageDraw.Draw(frame).rectangle((left, y - 1, right, y + 7), fill=fill)
    draw_text(frame, track, left + (width - text_width(track)) // 2, y, WHITE)
    return left


def _track_label(track):
    """Zürich's paired platforms ("43/44") shortened to the first, which fits the column."""
    return track.split("/")[0].strip() if len(track) > 3 else track


# How the boards themselves shorten long names.
SHORT = (("Centrale", "C.le"), ("CENTRALE", "C.LE"), ("Aeroporto", "Aerop."), ("AEROPORTO", "AEROP."),
         ("Aéroport", "Aérop."), ("Flughafen", "Flugh."), ("Hauptbahnhof", "Hbf"), ("pályaudvar", "pu."),
         ("Santa", "S."), ("SANTA", "S."), ("San ", "S. "), ("SAN ", "S. "))


def _fits(text, width, mixed):
    """The name as the board would fit it: its usual abbreviations, then whole words."""
    if text_width(text, 1, mixed) <= width:
        return text
    for long, short in SHORT:
        text = text.replace(long, short)
        if text_width(text, 1, mixed) <= width:
            return text
    words = re.split(r"(?<=[ -])", text)
    while len(words) > 1 and text_width("".join(words), 1, mixed) > width:
        words.pop()
    text = "".join(words).rstrip(" -")
    while text and text_width(text, 1, mixed) > width:
        text = text[:-1]
    return text


class Board(Module):
    name = "departures"

    def __init__(self):
        self.visits = 0
        self.scene = None

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data and any(board["rows"] for board in snap.data.values()))

    def _station(self, context):
        snap = context.snapshots.get(self.name)
        boards = {name: board for name, board in (snap.data or {}).items() if board["rows"]} if snap else {}
        if not boards:
            return None, None
        if context.scene != self.scene:
            self.scene = context.scene
            self.visits += 1
        names = [name for name in TOUR if name in boards]
        station = names[self.visits % len(names)]
        return station, boards[station]

    def _upcoming(self, board, zone, settings):
        moment, _ = board_moment(settings, zone)
        cutoff = moment - timedelta(minutes=1)
        return [row for row in board["rows"] if row["time"] + timedelta(minutes=max(0, row["delay"])) >= cutoff][:ROWS * 2]

    def _plan(self, rows, style_name):
        pages = max(1, math.ceil(len(rows) / ROWS))
        scene = SCENE_SECONDS if rows and self.visits % 2 == 0 else 0
        heading, said = notices(rows, style_name)
        # Each announcement crawls fully across once.
        spoken = [(text, (128 + text_width(text, 1, True)) / NOTICE_SPEED + .6) for text in said]
        return scene, TITLE_SECONDS, pages, heading, spoken

    def hold(self, context):
        station, board = self._station(context)
        if not board:
            return False
        settings = context.config["plugins"][self.name]
        rows = self._upcoming(board, ZoneInfo(STATIONS[station][2]), settings)
        scene, title, pages, _, spoken = self._plan(rows, STATIONS[station][5])
        return context.animation_time < scene + title + pages * PAGE_SECONDS + sum(s for _, s in spoken)

    def render(self, context):
        station, board = self._station(context)
        frame = new_frame()
        if not board:
            draw_text(frame, "Departures", 2, 12, GREY, mixed=True)
            return frame
        name, city, zone_name, _, _, style_name = STATIONS[station]
        style = STYLES[style_name]
        zone = ZoneInfo(zone_name)
        settings = context.config["plugins"][self.name]
        moment, live = board_moment(settings, zone)
        rows = self._upcoming(board, zone, settings)
        scene, title, pages, heading, spoken = self._plan(rows, style_name)
        t = context.animation_time
        if t < scene:
            self._platform(frame, rows[0], style, style_name, t)
        elif t < scene + title:
            self._title(frame, name, moment, style, live, t - scene,
                        live and settings["times"] != "station")
        elif spoken and t >= scene + title + pages * PAGE_SECONDS:
            local = t - scene - title - pages * PAGE_SECONDS
            for text, seconds in spoken:
                if local < seconds or (text, seconds) == spoken[-1]:
                    break
                local -= seconds
            self._notice(frame, heading, text, style, local, t)
        else:
            local = t - scene - title
            page = min(pages - 1, int(local // PAGE_SECONDS))
            # Departures in your own time, unless you asked for the station's.
            shown = None if settings["times"] == "station" or not live else datetime.now().astimezone().tzinfo
            self._rows(frame, rows[page * ROWS:(page + 1) * ROWS], style, style_name, local - page * PAGE_SECONDS, t,
                       shown)
        return frame

    @staticmethod
    def _notice(frame, heading, text, style, local, t):
        """An announcement, the way the board runs one: a lit heading, then the
        message crawling through in the station's language."""
        draw = ImageDraw.Draw(frame)
        on = math.floor(t * 2) % 2 == 0
        width = text_width(heading, 1, True) + 6
        draw.rectangle((0, 0, width, 9), fill=style["accent"] if on else (0, 0, 0), outline=style["accent"])
        draw_text(frame, heading, 3, 1, WHITE, mixed=True)
        draw.line((0, 30, 127, 30), fill=DIM)
        x = 128 - round(max(0.0, local - .3) * NOTICE_SPEED)
        draw_text(frame, text, x, 16, style["dest"], mixed=True)

    @staticmethod
    def _title(frame, name, moment, style, live, t, yours=False):
        draw_text(frame, _fits(name, 128, True), 0, 0, style["accent"], mixed=True)
        clock = moment.strftime("%H:%M")
        draw_text(frame, clock, 0, 11, WHITE, 2, True)
        # The colon blinks, like the station clock it imitates.
        if math.floor(t * 2) % 2:
            ImageDraw.Draw(frame).rectangle((text_width(clock[:2], 2) + 1, 11, text_width(clock[:2], 2) + 9, 24), fill=(0, 0, 0))
        top, bottom = style["departures"]
        x = text_width(clock, 2) + 6
        draw_text(frame, top, x, 11, GREY, mixed=True)
        if bottom:
            draw_text(frame, bottom, x, 19, GREY, mixed=True)
        if yours:  # the board below lists your times; say so under the station clock
            note = "TIMES IN YOUR ZONE"
            draw_tiny(frame, note, 128 - tiny_width(note), 27, DIM)
        if not live and text_width(name, 1, True) + tiny_width("TIMETABLE") + 4 <= 128:
            # Replaying the timetable at your time of day, not the live board.
            draw_tiny(frame, "TIMETABLE", 128 - tiny_width("TIMETABLE"), 1, DIM)

    @staticmethod
    def _rows(frame, rows, style, style_name, local, t, zone=None):
        mixed = style["mixed"]
        zone = zone or rows[0]["time"].tzinfo if rows else zone
        # One track column for the page, as wide as its widest number.
        tracks = [_track_label(row["track"]) for row in rows if row["track"] and not row["cancelled"]]
        column = max((text_width(track) for track in tracks), default=0) + 3 if tracks else 0
        for index, row in enumerate(rows):
            # Rows turn over one after another, like flaps.
            delay = index * .08
            if local < delay:
                continue
            rise = round(max(0, 1 - (local - delay) / .25) * 8)
            layer = Image.new("RGB", (128, 9))
            late = row["delay"] >= 5
            showing_delay = late and math.floor(t / 2) % 2 == 1
            clock = f"+{row['delay']}'" if showing_delay else row["time"].astimezone(zone).strftime("%H:%M")
            draw_text(layer, clock, 0, 0, RED if late else style["time"])
            x = _badge(layer, row["kind"], 31, 0, style_name, row.get("color", ""))
            track = "" if row["cancelled"] else _track_label(row["track"])
            room = 128 - x - (column + 3 if column else 0)
            destination = style["cancelled"] if row["cancelled"] else row["destination"]
            text = destination if mixed else destination.upper()
            if text_width(text, 1, mixed) > room:
                # Long names scroll inside their column and rest at each end.
                extra = text_width(text, 1, mixed) - room
                phase = (local % 6) / 6
                shift = round(extra * min(1, max(0, (phase - .2) / .5)))
                strip = Image.new("RGB", (room, 9))
                draw_text(strip, text, -shift, 0, RED if row["cancelled"] else style["dest"], mixed=mixed)
                layer.paste(strip, (x, 0))
            else:
                draw_text(layer, text, x, 0, RED if row["cancelled"] else style["dest"], mixed=mixed)
            if track:
                _track_box(layer, track, 127, 0, style, row.get("moved"), t, column)
            frame.paste(layer.crop((0, 0, 128, 9 - rise)), (0, ROW_Y[index] + rise))

    @staticmethod
    def _platform(frame, row, style, style_name, t):
        """A train pulls in, waits at the platform, and leaves."""
        body, stripe, nose = style["train"]
        draw = ImageDraw.Draw(frame)
        # Platform edge with its safety line.
        draw.rectangle((0, 28, 127, 31), fill=(46, 46, 50))
        for x in range(0, 128, 4):
            draw.line((x, 28, x + 1, 28), fill=(210, 180, 40))
        # Where the train is: arriving (ease in), standing, leaving (ease out).
        arrive, stand = 2.0, 2.3
        if t < arrive:
            offset = 128 * (1 - t / arrive) ** 2
        elif t < arrive + stand:
            offset = 0
        else:
            offset = -((t - arrive - stand) / 2.0) ** 2 * 240
        x0 = round(offset) + 2
        cars = 4
        for car in range(cars):
            left = x0 + car * 44
            if left > 128 or left + 42 < 0:
                continue
            draw.rectangle((left, 13, left + 41, 26), fill=body)
            draw.rectangle((left, 22, left + 41, 23), fill=stripe)
            for window in range(4):
                wx = left + 3 + window * 10
                draw.rectangle((wx, 15, wx + 6, 19), fill=(24, 34, 46) if (car + window) % 3 else (255, 214, 120))
            draw.point((left + 42, 20), fill=(0, 0, 0))
        head = x0 - 1
        if -12 < head < 128:  # the nose, facing left: the direction of travel
            draw.polygon(((head, 26), (head, 16), (head - 9, 24), (head - 9, 26)), fill=nose)
            draw.point((head - 8, 25), fill=(255, 250, 200))
        # The platform display above the train: track, train and where it goes; while it
        # stands, the train's number and the platform announced in the local word.
        track = _track_label(row["track"])
        mixed = style["mixed"]
        if arrive <= t < arrive + stand and track:
            # "Binario 24", "Gleis 43", "2. vágány": the platform as the station announces it.
            word = style["track_word"] if mixed else style["track_word"].upper()
            parts = ((f"{track}.", WHITE), (f" {word}", style["dest"])) if style_name == "mav" \
                else ((f"{word} ", style["dest"]), (track, WHITE))
            x = 0
            for text, color in parts:
                draw_text(frame, text, x, 1, color, mixed=mixed)
                x += text_width(text, 1, mixed)
            number = f"{row['kind']} {row['number']}".strip() if row["number"] and row["number"] != row["kind"] else ""
            if number and x + text_width(number) + 6 <= 128:
                draw_text(frame, number, 128 - text_width(number), 1, GREY)
            return
        if track:
            _track_box(frame, track, text_width(track) + 2, 1, style, row.get("moved"), t)
        x = _badge(frame, row["kind"], text_width(track) + 6 if track else 0, 1, style_name, row.get("color", ""))
        destination = row["destination"] if mixed else row["destination"].upper()
        clock = row["time"].strftime("%H:%M")
        draw_text(frame, _fits(destination, 128 - x - text_width(clock) - 3, mixed), x, 1, style["dest"], mixed=mixed)
        draw_text(frame, clock, 128 - text_width(clock), 1, WHITE)


def validate(settings):
    if not 30 <= settings["refresh_seconds"] <= 900:
        raise ValueError("refresh_seconds must be 30–900")


plugin = Plugin(
    "departures", "Departures", module=Board, provider=Boards,
    defaults={"station": "tour", "clock": "auto", "times": "yours", "refresh_seconds": 90},
    choices={"station": ("tour",) + TOUR, "clock": ("auto", "station", "mine"), "times": ("yours", "station")},
    help={"station": "tour visits every station in turn",
          "times": "show departure times in your time zone or the station's",
          "clock": "station shows the real board now; mine replays the timetable at your time of day; "
                   "auto uses the real board while the station is awake"},
    ui={"refresh_seconds": {"advanced": True}},
    validate_settings=validate,
)
