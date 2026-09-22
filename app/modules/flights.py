"""Flights overhead, the way airport boards and flight-wall displays show them.

One card per aircraft, nearest first, and everything on it stays put: nothing
scrolls and nothing turns over. The airline's mark at full height on the left; on
the right the airline and flight number, the city it left, the city it is going to
in full (the airport codes are small, at the end of each line) and a bar showing
how far along it is, with the time left.
"""
from functools import lru_cache
from io import BytesIO
import re

from PIL import Image, ImageDraw

from app.modules.base import Module, missing, stale_marker
from app.core.aircraft import TYPES, mixed, type_name
from app.core.airlines import airline, display_name
from app.core.fonts import centered, draw_text, draw_tiny, text_width, tiny_width
from app.core.fx import ease_out, mix
from app.core.renderer import new_frame, AMBER, GREEN, WHITE, MUTED

LIVE_SOURCES = ("local_adsb", "adsb_network")
INFO_X = 35
INFO_WIDTH = 128 - INFO_X
IDENTITY_SECONDS = 10.0     # the whole story on one card; long enough to read it and look up
SPOT_LIMIT = 4
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
# The aircraft on the progress bar: seen from above, nose to the right. Five rows, so it
# sits in the strip under the two city lines without touching either.
PLANE_ROWS = ("..#....",
              "..##...",
              "#######",
              "..##...",
              "..#....")
PLANE = tuple((x, y) for y, row in enumerate(PLANE_ROWS) for x, mark in enumerate(row) if mark == "#")
PLANE_WIDE = len(PLANE_ROWS[0])
TRACK = (52, 58, 60)
LABEL = (200, 206, 206)


@lru_cache(maxsize=16)
def logo_image(png):
    if not png:
        return None
    try:
        return Image.open(BytesIO(png)).convert("RGBA")
    except (OSError, ValueError):
        return None


ARROW = ">"


def _fit_tiny(text, width):
    while text and tiny_width(text) > width:
        text = text[:-1].rstrip()
    return text


def _fit(text, width, scale=1, lower=False):
    while text and text_width(text, scale, lower) > width:
        text = text[:-1].rstrip()
    return text


def duration(minutes):
    minutes = int(minutes)
    return f"{minutes} MIN" if minutes < 60 else f"{minutes // 60}H {minutes % 60:02d}M"


def where(row):
    """Height and where to look: '3,200FT↓ 1.4MI NE'."""
    rate = row.get("vertical_rate")
    arrow = "" if rate is None else "↑" if rate > 250 else "↓" if rate < -250 else ""
    height = f"{row['altitude']:,}FT{arrow}" if row.get("altitude") is not None else ""
    return f"{height} {row['distance']:.1f}MI {compass(row.get('bearing'))}".strip()


def compass(bearing):
    return COMPASS[round((bearing or 0) / 45) % 8]


def flight_number(callsign):
    """("UNITED 1234", "UA1234") from an ICAO callsign like UAL1234."""
    carrier = airline(callsign)
    if not carrier:
        return callsign, callsign
    number = callsign[3:].lstrip("0") or callsign[3:]
    return f"{carrier[1]} {number}", f"{carrier[0]}{number}"


def rows_from(snap):
    """Aircraft as plain rows, nearest first. The nearest carries its Flight's
    route even from providers that do not detail the others."""
    rows = [dict(row) for row in snap.metadata.get("nearby") or ()]
    f = snap.data
    if f and (not rows or not rows[0].get("callsign")):
        rows[:1] = [{"callsign": f.callsign, "distance": f.distance_miles, "bearing": f.bearing_deg}]
    if f and rows:
        first = rows[0]
        first.update({"callsign": f.callsign, "type": "" if f.aircraft == "ADS-B" else f.aircraft,
                      "altitude": f.altitude_ft, "vertical_rate": f.vertical_rate,
                      "origin": f.origin if f.origin != "---" else first.get("origin", ""),
                      "destination": f.destination if f.destination != "---" else first.get("destination", "")})
        if snap.metadata.get("cities") and not first.get("cities"):
            first["cities"] = list(snap.metadata["cities"])
    return [row for row in rows if row.get("callsign")]


class FlightModule(Module):
    name = "flight"
    event_priority = 20

    def available(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and not snap.stale and snap.data
                    and snap.data.distance_miles <= context.config["modules"][self.name]["max_distance_miles"])

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _plan(self, rows, limit):
        """[(kind, payload, seconds)] for one pass through the sky: one full card for
        each aircraft near enough to matter, nearest first."""
        spots = [row for row in rows if row["distance"] <= limit][:SPOT_LIMIT] or rows[:1]
        return [("identity", row, IDENTITY_SECONDS) for row in spots]

    def hold(self, context):
        # Finish the aircraft on screen rather than cutting its details off.
        snap = context.snapshots.get(self.name)
        if (not snap or not snap.data) and getattr(self, "last", (None,))[0] == context.scene:
            snap = self.last[1]
        if not snap or not snap.data:
            return False
        plan = self._plan(rows_from(snap), context.config["modules"][self.name]["max_distance_miles"])
        return context.animation_time < sum(seconds for *_, seconds in plan)

    def render(self, context):
        snap = context.snapshots.get(self.name)
        if snap and snap.data:
            self.last = (context.scene, snap)
        elif getattr(self, "last", (None,))[0] == context.scene:
            # The aircraft just left the radius mid-visit: finish its card rather
            # than flash an empty-sky screen.
            snap = self.last[1]
        if not snap or not snap.data:
            return missing("RECEIVER OFFLINE" if snap and (snap.error or snap.stale) else "CLEAR SKIES")
        settings = context.config["modules"][self.name]
        if settings["layout"] == "minimal":
            return self._minimal(snap)
        rows = rows_from(snap)
        plan = self._plan(rows, settings["max_distance_miles"])
        total = sum(seconds for *_, seconds in plan)
        t = context.animation_time % total
        # A lone aircraft settles in once; it does not re-enter every pass.
        arrive = len(plan) > 1 or context.animation_time < total
        for kind, payload, seconds in plan:
            if t < seconds:
                break
            t -= seconds
        frame = new_frame()
        logos = snap.metadata.get("logos") or {}
        self._identity(frame, payload, logos, t, settings["layout"] == "detail", arrive)
        return stale_marker(frame, snap)

    # --- one card per aircraft ---------------------------------------------------

    def _identity(self, frame, row, logos, t, detail, arrive=True):
        """Who it is, where from, where to, and how far along: all of it, still."""
        carrier = airline(row["callsign"])
        logo = logo_image(logos.get(carrier[0])) if carrier else None
        mark = Image.new("RGB", (32, 32))
        if logo:
            mark.paste(logo, (16 - logo.width // 2, 16 - logo.height // 2), logo)
        elif carrier:
            self._badge(mark, carrier)
        else:
            self._tail(mark)
        # The whole card rises into place, like a board flap settling.
        rise = round((1 - ease_out(min(1, t / .35))) * 20) if arrive else 0
        card = Image.new("RGB", (128, 32))
        card.paste(mark, (0, 0))
        name, short = flight_number(row["callsign"])
        if carrier:  # Names as the airline writes them: "United 1432", "JetBlue 88".
            name = f"{display_name(carrier[1])} {name.split()[-1]}"
        lower = bool(carrier)
        origin, destination = row.get("origin"), row.get("destination")
        inferred = self._inferred(row) if not (origin and destination) else None
        if (origin and destination) or inferred:
            self._route(card, row, name, short, lower, origin, destination, inferred)
        else:
            self._unrouted(card, row, name, short, lower)
        frame.paste(card.crop((0, 0, 128, 32 - rise)), (0, rise))

    @staticmethod
    def _aircraft(code):
        """The aircraft in as many words as the line holds: "Boeing 737-800" before
        "737-800", and "F/A-18" before the raw "F18S". Whole words only: a name cut
        mid-word ("Citation Longitud") looks like a fault."""
        code = (code or "").strip().upper()
        if code and code not in TYPES:   # an ICAO code we have no name for stays a code
            return _fit(code, INFO_WIDTH)
        names = [mixed(type_name(code, long=True)), mixed(type_name(code, long=False))]
        for name in list(names):
            words = name.split()
            names.extend(" ".join(words[:n]) for n in range(len(words) - 1, 0, -1))
        names.append((code or "").upper())
        return next((n for n in names if n and text_width(n, 1, True) <= INFO_WIDTH), "") or "Aircraft"

    @staticmethod
    def _inferred(row):
        """Route databases key on the callsign, and airlines reuse them, so many flights have
        no route we can trust. Where the aircraft is and how it moves still says which
        airport it is landing at or has just left."""
        if row.get("phase") in ("arriving", "departing") and row.get("airport"):
            return ("Leaving", "Landing at")[row["phase"] == "arriving"], row["airport"], \
                str(row.get("airport_city") or row["airport"])
        return None

    def _title(self, card, row, name, short, lower, room=INFO_WIDTH, type_code=True):
        """The airline and flight number on the top line, the aircraft type beside it if it fits."""
        title = name if text_width(name, 1, lower) <= room else short
        title = _fit(title, room, 1, lower)
        draw_text(card, title, INFO_X, 0, WHITE, mixed=lower)
        if row.get("type") and type_code:
            gap = text_width(title, 1, lower) + 5
            for plane in (type_name(row["type"], long=False), row["type"]):
                if tiny_width(plane) + gap <= INFO_WIDTH:
                    draw_tiny(card, plane, 128 - tiny_width(plane), 1, MUTED)
                    break

    def _route(self, card, row, name, short, lower, origin, destination, inferred):
        self._title(card, row, name, short, lower)
        if inferred:
            lead, code, city = inferred
            first, first_code, second, second_code = lead, "", city, code
            first_colour = LABEL
        else:
            cities = [city.title() for city in row.get("cities") or []]
            known = len(cities) == 2
            first, first_code = (cities[0], origin) if known else (origin, "")
            second, second_code = (cities[1], destination) if known else (destination, "")
            first_colour = LABEL
        self._line(card, first, first_code, 9, first_colour)
        self._line(card, second, second_code, 18, AMBER)      # where it is going, in the bar's colour
        self._bottom(card, row, inferred)

    @staticmethod
    def _line(card, city, code, y, colour):
        """A city in full, with its airport code small at the end of the line when there is room."""
        candidates = [city] + ([shorter] if (shorter := re.split(r"[-/]", city)[0].strip()) != city else [])
        for tag in ((code,) if code else ()) + ("",):
            room = INFO_WIDTH - (tiny_width(tag) + 4 if tag else 0)
            text = next((c for c in candidates if text_width(c, 1, True) <= room), None)
            if text is not None:
                draw_text(card, text, INFO_X, y, colour, mixed=True)
                if tag:
                    draw_tiny(card, tag, 128 - tiny_width(tag), y + 1, MUTED)
                return
        # A very long name: the small font has room for all of it; failing that, a cut one.
        if tiny_width(city.upper()) <= INFO_WIDTH:
            draw_tiny(card, city.upper(), INFO_X, y + 1, colour)
        else:
            draw_text(card, _fit(candidates[-1], INFO_WIDTH, 1, True), INFO_X, y, colour, mixed=True)

    def _bottom(self, card, row, inferred):
        """The strip under the cities: how far along it is and how long is left; where it is
        and how it is moving when the far end of the trip is not known."""
        left = row.get("minutes_left")
        clock = "" if left is None or left >= 900 else (f"{left}M" if left < 100 else f"{left // 60}H{left % 60:02d}M")
        progress = row.get("progress")
        if progress is None or inferred:
            text = where(row) if inferred is None else self._motion(row)
            draw_tiny(card, _fit_tiny(text, INFO_WIDTH), INFO_X, 27, GREEN)
            return
        right = 127 - (tiny_width(clock) + 4 if clock else 0)
        self._bar(card, INFO_X + 1, right - 1, 29, max(0.0, min(1.0, float(progress))))
        if clock:
            draw_tiny(card, clock, 128 - tiny_width(clock), 27, AMBER)

    @staticmethod
    def _motion(row):
        """Height and speed, for a flight whose route is only a guess."""
        rate = row.get("vertical_rate")
        arrow = "" if rate is None else "↑" if rate > 250 else "↓" if rate < -250 else ""
        parts = []
        if row.get("altitude") is not None:
            parts.append(f"{row['altitude']:,}FT{arrow}")
        if row.get("speed"):
            parts.append(f"{round(row['speed'])}KT")
        return "  ".join(parts) or where(row)

    @staticmethod
    def _bar(card, left, right, y, progress):
        """The flight as a line from one city to the other, the aircraft where it has got to."""
        draw = ImageDraw.Draw(card)
        span = right - left
        draw.line((left, y, right, y), fill=TRACK, width=1)
        draw.line((left, y + 1, right, y + 1), fill=TRACK, width=1)
        here = left + round(span * progress)
        if here > left:
            draw.line((left, y, here, y), fill=AMBER)
            draw.line((left, y + 1, here, y + 1), fill=AMBER)
        for end in (left, right):                      # the two airports
            draw.rectangle((end - 1, y - 1, end, y + 2), fill=WHITE if end == left else MUTED)
        x0 = max(left, min(right - PLANE_WIDE, here - PLANE_WIDE // 2))
        for dx, dy in PLANE:
            card.putpixel((x0 + dx, y - 2 + dy), WHITE)

    def _unrouted(self, card, row, name, short, lower):
        """No published route (private and military flights, and callsigns whose route on file
        is somebody else's): the aircraft is the story.

        Four lines on the grid a routed card uses, so the two look like the same screen:
        who it is, what it is, how it is flying, and where to look for it. A 5x7 line is
        seven rows and its descenders hang two more, so 0, 9 and 19 with a small line at
        27 leaves rows 18 and 26 empty: two clear gaps, and no descender ever lands on
        the line below it.
        Drawing the name at 2x used to look better on a 737-800 and ran straight through
        the height and speed on everything else; the type goes on its own line instead.
        """
        # The type is spelled out below, so the top line keeps the full width for the airline.
        self._title(card, row, name, short, lower, type_code=False)
        # The aircraft in as many words as the line holds: "Boeing 737-800" before "737-800",
        # and the raw type code when it is one we have no name for.
        code = row.get("type") or ""
        draw_text(card, self._aircraft(code), INFO_X, 9, AMBER, mixed=True)
        # Height and speed, each dropped whole rather than cut in half.
        motion = self._motion(row)
        parts = motion.split("  ")
        while parts and text_width("  ".join(parts)) > INFO_WIDTH:
            parts.pop()
        if parts:
            draw_text(card, "  ".join(parts), INFO_X, 19, GREEN)
        # Where to look, and what it is doing while you look: the one thing a card about a
        # plane you can hear overhead is for.
        where_text = f"{row['distance']:.1f}MI {compass(row.get('bearing'))}"
        rate = row.get("vertical_rate")
        if row.get("phase") == "overflight":
            where_text += "  OVERFLIGHT"
        elif rate is not None and abs(rate) > 250:
            where_text += "  CLIMBING" if rate > 0 else "  DESCENDING"
        draw_tiny(card, _fit_tiny(where_text, INFO_WIDTH), INFO_X, 27, MUTED)

    @staticmethod
    def _badge(image, carrier):
        code, _, color = carrier
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, 30, 30), radius=4, fill=color, outline=mix(color, (255, 255, 255), .45))
        ink = (0, 0, 0) if sum(color) > 520 else (255, 255, 255)
        draw_text(image, code, 16 - text_width(code, 2) // 2, 9, ink, 2, True)

    @staticmethod
    def _tail(image):
        """General aviation has no airline mark: a small plane silhouette instead."""
        for dx, dy in PLANE:
            ImageDraw.Draw(image).rectangle((9 + dx * 2, 11 + dy * 2, 10 + dx * 2, 12 + dy * 2), fill=MUTED)

    @staticmethod
    def _minimal(snap):
        f = snap.data
        frame = new_frame()
        name, short = flight_number(f.callsign)
        draw_text(frame, name if text_width(name) <= 124 else short, 2, 0, WHITE)
        centered(frame, f"{f.distance_miles:.1f} MI {compass(f.bearing_deg)}", 10, WHITE, 2, True)
        route = f"{f.origin} > {f.destination}" if f.origin != "---" or f.destination != "---" else "NEARBY"
        centered(frame, route, 25, AMBER)
        return stale_marker(frame, snap)
