"""Flights overhead, the way airport boards and flight-wall displays show them.

Spotlight: one aircraft at a time with the airline's tail mark at full height,
the airline and flight number, the route in big airport codes, and one detail
line that flips between the far-end city, the aircraft and where to look.
Board: with several aircraft about, a departures-style list of the nearest
three comes first. An aircraft almost overhead flies across the panel before
its spotlight.
"""
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw

from app.modules.base import Module, missing, stale_marker
from app.core.aircraft import mixed, type_name
from app.core.airlines import airline, display_name
from app.core.fonts import centered, draw_text, draw_tiny, text_width, tiny_width
from app.core.fx import ease_in_out, ease_out, mix, sprite, stamp
from app.core.renderer import new_frame, AMBER, GREEN, WHITE, MUTED

LIVE_SOURCES = ("local_adsb", "adsb_network")
INFO_X = 35
INFO_WIDTH = 128 - INFO_X
IDENTITY_SECONDS = 10.0     # the whole story on one card: logo, flight, route, aircraft, where
FACT_SECONDS = 2.5          # the card's bottom line turns over through the facts
BOARD_SECONDS = 5.0         # the list of nearby aircraft, when there are several
FLYBY_SECONDS = 2.2         # an aircraft this close crosses the panel first
OVERHEAD_MILES = 2.0
SPOT_LIMIT = 4
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
# The marker between the two airport codes. At seven pixels it was a cross with a
# bump; a jet needs a nose, a tail fin and a wing before it reads as one.
PLANE_ROWS = (".##..........",
              "###..........",
              "#############",
              "........####.",
              ".......####..",
              "........##...")
PLANE = tuple((x, y) for y, row in enumerate(PLANE_ROWS) for x, mark in enumerate(row) if mark == "#")
PLANE_WIDE = len(PLANE_ROWS[0])
AIRLINER = sprite((
    "..........WW........",
    ".........WWW........",
    "WWWWWWWWWWWWWWWWWW..",
    "WBBWBBWBBWBBWBBWWWWW",
    "WWWWWWWWWWWWWWWWWWW.",
    "......RRRRR.........",
    ".....RRRR...........",
), {"W": (235, 238, 240), "B": (40, 110, 200), "R": (150, 156, 164)})
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
        """[(kind, payload, seconds)] for one pass through the sky."""
        spots = [row for row in rows if row["distance"] <= limit][:SPOT_LIMIT] or rows[:1]
        plan = []
        if spots and spots[0]["distance"] <= OVERHEAD_MILES:
            plan.append(("flyby", spots[0], FLYBY_SECONDS))
        if len(rows) >= 2:
            plan.append(("board", rows[:3], BOARD_SECONDS))
        for row in spots:
            plan.append(("identity", row, IDENTITY_SECONDS))
        return plan

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
        if kind == "flyby":
            self._flyby(frame, payload, t)
        elif kind == "board":
            self._board(frame, payload, t)
        else:
            self._identity(frame, payload, logos, t, settings["layout"] == "detail", arrive)
        return stale_marker(frame, snap)

    # --- spotlight -----------------------------------------------------------

    def _identity(self, frame, row, logos, t, detail, arrive=True):
        """Who it is: the airline's mark, flight number, route and aircraft."""
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
        # How long is left is worth more than the airline's full name, so the name
        # gives up its room first: "JetBlue 1524" becomes "B6 1524" to make space.
        minutes = row.get("minutes_left")
        ticking = "" if minutes is None or minutes >= 900 else (
            f"{minutes}M" if minutes < 100 else f"{minutes // 60}H{minutes % 60:02d}M")
        budget = INFO_WIDTH - (tiny_width(ticking) + 4 if ticking else 0)
        title = name if text_width(name, 1, lower) <= budget else short
        draw_text(card, _fit(title, budget, 1, lower), INFO_X, 0, WHITE, mixed=lower)
        origin, destination = row.get("origin"), row.get("destination")
        kind = row.get("type")
        if origin and destination:
            draw_text(card, origin, INFO_X, 9, WHITE, 2, True)
            x = INFO_X + text_width(origin, 2) + 3
            for dx, dy in PLANE:
                card.putpixel((x + dx, 13 + dy), AMBER)
            draw_text(card, destination, x + PLANE_WIDE + 3, 9, WHITE, 2, True)
            # Everything at once underneath: the cities in full, and how long is left.
            cities = [city.upper() for city in row.get("cities") or []]
            line = f"{cities[0]} {ARROW} {cities[1]}" if len(cities) == 2 else where(row)
            left = row.get("minutes_left")
            # The aircraft goes beside the cities; the time left sits by the flight number.
            plane = type_name(row["type"], long=False) if row.get("type") else ""
            # "5H19" read as a number cut off halfway, so the minutes say so.
            clock = "" if left is None or left >= 900 else (
                f"{left}M" if left < 100 else f"{left // 60}H{left % 60:02d}M")
            room = INFO_WIDTH
            # The big codes already say from and to, so when the aircraft will not fit
            # beside both cities, name the city it is heading for.
            if plane and len(cities) == 2 and tiny_width(line) + 5 + tiny_width(plane) > INFO_WIDTH:
                shorter = f"{ARROW} {cities[1]}"
                if tiny_width(shorter) + 5 + tiny_width(plane) <= INFO_WIDTH:
                    line = shorter
            if plane and tiny_width(line) + 5 + tiny_width(plane) <= INFO_WIDTH:
                draw_tiny(card, plane, 128 - tiny_width(plane), 26, AMBER)
                room = INFO_WIDTH - tiny_width(plane) - 5
            elif clock and tiny_width(line) + 5 + tiny_width(clock) <= INFO_WIDTH:
                draw_tiny(card, clock, 128 - tiny_width(clock), 26, AMBER)
                room = INFO_WIDTH - tiny_width(clock) - 5
                clock = ""
            if clock and text_width(title, 1, lower) + 4 + tiny_width(clock) <= INFO_WIDTH:
                draw_tiny(card, clock, 128 - tiny_width(clock), 1, AMBER)
                clock = ""
            draw_tiny(card, _fit_tiny(line, room), INFO_X, 26, WHITE if len(cities) == 2 else GREEN)
        else:
            # No published route (private and military flights): the aircraft is the story.
            label = mixed(type_name(kind, long=False)) or "Aircraft"
            size = 2 if text_width(label, 2, True) <= INFO_WIDTH else 1
            draw_text(card, _fit(label, INFO_WIDTH, size, True), INFO_X, 9 if size == 2 else 13, AMBER, size,
                      size == 2, mixed=True)
            draw_text(card, _fit(where(row), INFO_WIDTH), INFO_X, 25, GREEN)
        frame.paste(card.crop((0, 0, 128, 32 - rise)), (0, rise))

    @staticmethod
    def facts(row):
        """The bottom line's facts, in turn: the far city, the aircraft, where to look,
        and how long it has flown and has to go. Each fits the line or is left out."""
        found = []
        cities = [city.upper() for city in row.get("cities") or []]
        if len(cities) == 2:
            # The far end of the trip: home is the other one, so "FROM DETROIT" on the way in.
            found.append((f"FROM {cities[0]}" if row.get("local") == "destination" else f"TO {cities[1]}", WHITE))
        if row.get("type"):
            label = type_name(row["type"])
            found.append((label if text_width(label) <= INFO_WIDTH else type_name(row["type"], long=False), AMBER))
        found.append((where(row), GREEN))
        left, flown = row.get("minutes_left"), row.get("minutes_flown")
        if left is not None:
            found.append((f"{duration(left)} TO GO", AMBER))
        if flown is not None:
            found.append((f"IN AIR {duration(flown)}", MUTED))
        fitted = []
        for text, color in found:
            if text_width(text) > INFO_WIDTH and text.startswith(("TO ", "FROM ")):
                text = text.split(" ", 1)[1]  # "TO SALT LAKE CITY" too long: the city alone
            if text_width(text) <= INFO_WIDTH:
                fitted.append((text, color))
        return fitted

    def _fact(self, row, t):
        facts = self.facts(row) or [("", WHITE)]
        return facts[int(t // FACT_SECONDS) % len(facts)]

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

    # --- board ---------------------------------------------------------------

    @staticmethod
    def _board(frame, rows, t):
        """Nearest aircraft as a departures board: flight, route, distance."""
        for index, row in enumerate(rows):
            y = 1 + index * 11
            # Rows drop in one after another like flaps turning over.
            delay = index * .12
            if t < delay:
                continue
            rise = round((1 - ease_out(min(1, (t - delay) / .3))) * 6)
            carrier = airline(row["callsign"])
            chip = carrier[2] if carrier else (90, 96, 104)
            ImageDraw.Draw(frame).rectangle((0, y + rise, 1, y + 6 + rise), fill=mix(chip, (255, 255, 255), .25))
            _, short = flight_number(row["callsign"])
            draw_text(frame, short[:7], 4, y + rise, WHITE)
            if row.get("origin") and row.get("destination"):
                route, color = f"{row['origin']}>{row['destination']}", AMBER
            else:
                route, color = type_name(row.get("type"), long=False)[:8], LABEL
            draw_text(frame, route, 48, y + rise, color)
            distance = f"{row['distance']:.0f}MI" if row["distance"] >= 1 else "HERE"
            draw_text(frame, distance, 128 - text_width(distance), y + rise, GREEN)

    # --- overhead ------------------------------------------------------------

    @staticmethod
    def _flyby(frame, row, t):
        """The aircraft crosses the panel trailing a contrail, then OVERHEAD."""
        progress = ease_in_out(min(1, t / (FLYBY_SECONDS - .4)))
        x = -12 + progress * 140
        draw = ImageDraw.Draw(frame)
        for tail in range(0, int(x) + 1):
            fade = max(0, 1 - (x - tail) / 70)
            if fade > 0:
                level = round(120 * fade)
                draw.point((tail, 21), fill=(level, level, level))
                if tail % 2:
                    draw.point((tail, 20), fill=(level // 2, level // 2, level // 2))
        stamp(frame, AIRLINER, x, 16)
        if progress > .35:
            visible = min(1, (progress - .35) / .25)
            draw_text(frame, "OVERHEAD", 1, 1, mix((0, 0, 0), AMBER, visible))
            _, short = flight_number(row["callsign"])
            draw_text(frame, short, 127 - text_width(short), 1, mix((0, 0, 0), LABEL, visible))

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
