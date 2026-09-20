"""Vegas race & sports book: a matchup card per game with team logos, lines
that flip between spread and moneyline, big live scores, and
fireworks when your team scores.

This plugin only draws. It reads the slate from whichever provider feeds the
built-in "sports" screen (the free_sports plugin supplies lines from ESPN).
"""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import math
import time

from PIL import Image, ImageColor, ImageDraw

from rackticker import Plugin, Module, new_frame
from app.core.fonts import draw_text, draw_tiny, text_width, tiny_width
from app.core.fx import Lettering, bulb_border, ease_out, mix
from app.core.story import Storyboard
from app.modules.base import missing

LAMP, DULL = (255, 176, 0), (112, 86, 32)
RED, GREEN, WHITE = (255, 72, 52), (90, 235, 120), (236, 240, 236)
# League labels in bright, saturated LED colours. Mixing dark brand colours
# with white made pastels that read as pink and lavender on a matrix.
LEAGUES = {"NFL": (70, 140, 255), "NBA": (255, 120, 40), "MLB": (255, 70, 70), "NHL": (215, 225, 235),
           "WNBA": (255, 140, 60), "NCAAF": (80, 210, 110), "NCAAM": (80, 170, 255),
           "HOCKE": (215, 225, 235), "FOOTB": (70, 140, 255)}
STATUS_RANK = {"live": 0, "goal": 0, "pregame": 1, "final": 2}
CELEBRATION_SECONDS = 12
FLASH_SECONDS, BANNER_SECONDS = 180, 2.6   # a big play leads its game's card for a while
# The matchup sits centred below the header; a bottom scores crawl only
# repeated the games the cards already rotate through, and pulled the eye.
# The card fills the panel below the header. It used to stop three rows short,
# which left the football field a two-pixel sliver under the scores.
BODY_TOP, BODY_HEIGHT = 11, 21


def slate(snapshot):
    if not snapshot:
        return []
    games = list(snapshot.metadata.get("slate") or ()) or ([snapshot.data] if snapshot.data else [])
    return sorted(games, key=lambda game: STATUS_RANK.get(game.status, 1))


def team_color(team):
    try:
        color = ImageColor.getrgb(team.color)
    except ValueError:
        return WHITE
    # Too dark to read on a black panel: use white rather than a washed-out pastel.
    return WHITE if sum(color) < 200 else color


def fit_tiny(text, width):
    text = str(text).upper()
    while text and tiny_width(text) > width:
        text = text[:-1].rstrip()
    return text


@lru_cache(maxsize=160)
def logo_image(png):
    """RGBA logo plus whether it is too dark to read on unlit LEDs."""
    try:
        image = Image.open(BytesIO(png)).convert("RGBA")
    except (OSError, ValueError):
        return None, False
    visible = [(r + g + b) / 3 for r, g, b, a in image.getdata() if a > 128]
    return image, bool(visible) and sum(visible) / len(visible) < 70




class Sportsbook(Module):
    name = "sportsbook"

    def __init__(self):
        self.board = Storyboard()
        self.party = (None, None)

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        return bool(slate(context.snapshots.get("sports")))

    @staticmethod
    def _celebration(context):
        snap = context.snapshots.get("sports")
        party = snap.metadata.get("celebration") if snap else None
        if not party:
            return None
        age = time.monotonic() - party["at"]
        return (party, age) if 0 <= age < CELEBRATION_SECONDS else None

    def _card(self, context):
        games = slate(context.snapshots.get("sports"))
        seconds = context.config["plugins"][self.name]["card_seconds"]
        build = lambda _visit: [(game, seconds) for game in games]
        self.board.sync(context.animation_time, build, context.scene)
        return self.board.current(context.animation_time, build), games

    def hold(self, context):
        if self._celebration(context):
            return True
        card, _ = self._card(context)
        return bool(card) and self.board.hold()

    def render(self, context):
        celebration = self._celebration(context)
        if celebration:
            return self._party_frame(*celebration, context.animation_time)
        card, games = self._card(context)
        if not card:
            return missing("SPORTSBOOK")
        game, local, _ = card
        t = context.animation_time
        frame = new_frame()
        extra = getattr(game, "extra", None) or {}
        odds = self.live_line(game, extra) if context.config["plugins"][self.name]["live_odds"] else ""
        self._header(frame, game, extra, t, odds)
        body = Image.new("RGB", (128, BODY_HEIGHT))
        # The line flips once per card, halfway through, in step with the card
        # itself: a clock of its own flipped at odd moments mid-read.
        seconds = context.config["plugins"][self.name]["card_seconds"]
        flash = extra.get("flash")
        if flash and time.time() - float(extra.get("flash_at") or 0) < FLASH_SECONDS and local < BANNER_SECONDS:
            self._banner(body, flash, extra.get("flash_team", ""), game, t, extra.get("flash_who", ""))
        else:
            self._matchup(body, game, extra, 1 if local >= seconds / 2 else 0)
        roll = round((1 - ease_out(local / .35)) * BODY_HEIGHT) if local < .35 else 0
        if roll < BODY_HEIGHT:
            frame.paste(body.crop((0, 0, 128, BODY_HEIGHT - roll)), (0, BODY_TOP + roll))
        return frame

    @staticmethod
    def _header(frame, game, extra, t, odds=""):
        draw = ImageDraw.Draw(frame)
        league = (game.league or "GAME").upper()[:5]
        # Full-size lettering in a brightened league colour reads at a glance;
        # tiny white text on a coloured block did not.
        draw_text(frame, league, 0, 0, LEAGUES.get(league, (200, 210, 220)))
        live = game.status in ("live", "goal")
        x = text_width(league) + 5
        if live:
            if math.floor(t * 2) % 2 == 0:
                draw.rectangle((x, 2, x + 2, 4), fill=RED)
            x += 5
        right = extra.get("broadcast") or extra.get("book") or ""
        detail = game.detail
        # The line on a live game, in the corner the channel sits in before kick-off.
        # A score without the number it is being measured against is half the story.
        if live and odds and math.floor(t / 4) % 2 == 1:
            right = odds or right
        if live and extra.get("down"):  # football: the down and distance, like the TV bar
            detail, right = f"{game.detail}  {extra['down']}", extra.get("spot", "")
        elif game.status != "pregame" and extra.get("away_sog"):  # hockey: shots on goal
            right = f"SOG {extra['away_sog']}-{extra['home_sog']}"
        elif live and extra.get("batter"):  # baseball: who is at bat
            right = f"AB {extra['batter'].split()[-1].upper()}"
        status = fit_tiny(detail, 128 - x - (tiny_width(right) + 4 if right else 0))
        draw_tiny(frame, status, x, 1, GREEN if live else LAMP if game.status == "pregame" else DULL)
        if right and x + tiny_width(status) + 4 + tiny_width(right) <= 127:
            draw_tiny(frame, right, 127 - tiny_width(right), 1, DULL)
        for px in range(0, 128, 2):
            draw.point((px, 7), fill=(70, 50, 12))

    @staticmethod
    def live_line(game, extra):
        """The market on one line: who is favoured and by how much, then the total."""
        home, away = extra.get("home_spread", ""), extra.get("away_spread", "")
        favourite, spread = "", ""
        for side, value in (("home", home), ("away", away)):
            if value.startswith("-"):
                favourite = (game.home if side == "home" else game.away).abbreviation
                spread = value
        parts = [f"{favourite} {spread}"] if favourite and spread else []
        if extra.get("total"):
            parts.append(f"O/U {extra['total']}")
        return "  ".join(parts)[:20]

    @staticmethod
    def _mark(body, team, x):
        """16×16 logo, or a team-colour tile with the abbreviation when none is cached."""
        logo, dark = logo_image(team.logo_png) if team.logo_png else (None, False)
        draw = ImageDraw.Draw(body)
        if logo:
            if dark:  # Near-black marks disappear on a black panel; give them a tile.
                draw.rounded_rectangle((x, 1, x + 15, 16), radius=3, fill=(58, 58, 66))
            body.paste(logo, (x, 1), logo)
            return
        color = team_color(team)
        draw.rounded_rectangle((x, 1, x + 15, 16), radius=2, fill=color)
        ink = (0, 0, 0) if sum(color) > 480 else WHITE
        draw_tiny(body, team.abbreviation, x + 8 - tiny_width(team.abbreviation) // 2, 6, ink)

    def _matchup(self, body, game, extra, flip):
        self._mark(body, game.away, 0)
        self._mark(body, game.home, 112)
        if game.status != "pregame":
            leader = max(game.away.score, game.home.score)
            diamond = game.status == "live" and "bases" in extra
            for team, right_edge, left in ((game.away, None if diamond else 58, 19), (game.home, 109 if diamond else None, 70)):
                score = str(team.score)
                x = right_edge - text_width(score, 2) if right_edge else left
                draw_text(body, score, x, 2, LAMP if team.score == leader else DULL, 2, True)
            if diamond:
                self._diamond(body, extra)
            else:
                draw_text(body, "-", 61, 6, DULL)
            if game.status == "live":
                self._hockey(body, extra, math.floor(time.time() * 2) % 2 == 0)
            if game.status == "live" and extra.get("yard"):
                self._field(body, game, extra)
            ball = extra.get("ball") if game.status == "live" else None
            if ball:  # who has the ball: a football beside their logo, red inside the 20
                x = 18 if ball == "away" else 104
                draw = ImageDraw.Draw(body)
                draw.ellipse((x, 7, x + 6, 11), fill=RED if extra.get("red_zone") else (190, 110, 40))
                draw.line((x + 2, 9, x + 4, 9), fill=WHITE)
            return
        for team, side, left in ((game.away, "away", 19), (game.home, "home", None)):
            spread, moneyline = extra.get(f"{side}_spread", ""), extra.get(f"{side}_ml", "")
            record = extra.get(f"{side}_record", "")
            abbreviation = team.abbreviation
            value = (moneyline or spread) if flip else (spread or moneyline)
            color = (GREEN if value.startswith("+") else RED if value.startswith("-") else LAMP) \
                if value == moneyline and value else LAMP
            if not value:
                value, color = record, DULL
            x_name = left if left is not None else 109 - text_width(abbreviation)
            draw_text(body, abbreviation, x_name, 1, WHITE)
            if value:
                draw_text(body, value, left if left is not None else 109 - text_width(value), 10, color)
        total = extra.get("total")
        if total:
            draw_tiny(body, "O/U", 64 - tiny_width("O/U") // 2, 2, DULL)
            draw_text(body, total, 64 - text_width(total) // 2, 9, LAMP)
        else:
            draw_text(body, "@", 64 - text_width("@", 2) // 2, 2, DULL, 2, True)

    @staticmethod
    def _diamond(body, extra):
        """The TV score bug: runners on the bases, the count and the outs."""
        draw = ImageDraw.Draw(body)
        bases = extra.get("bases", "000")
        # (centre x, centre y) of first, second and third; each base a diamond.
        for occupied, (cx, cy) in zip(bases, ((72, 10), (64, 3), (56, 10))):
            shape = ((cx, cy - 3), (cx + 3, cy), (cx, cy + 3), (cx - 3, cy))
            draw.polygon(shape, fill=LAMP if occupied == "1" else (0, 0, 0), outline=LAMP if occupied == "1" else DULL)
        # The count under third, the outs under first: two dots, the third out ends the half.
        draw_tiny(body, extra.get("count", ""), 56 - tiny_width(extra.get("count", "")) // 2, 13, WHITE)
        outs = int(extra.get("outs", "0"))
        for index in range(2):
            x = 69 + index * 4
            draw.rectangle((x, 15, x + 2, 17), fill=RED if index < outs else (60, 44, 16))

    @staticmethod
    def _field(body, game, extra):
        """The field under the score, as TV draws it: the away end zone on the left
        and the home one on the right, the ball, and the yellow first-down line."""
        draw = ImageDraw.Draw(body)
        left, right, top, bottom = 18, 109, 16, 19
        yards = lambda yard: left + 4 + round((100 - yard) * (right - left - 8) / 100)
        draw.rectangle((left, top, right, bottom), fill=(18, 62, 28))
        draw.rectangle((left, top, left + 3, bottom), fill=team_color(game.away))
        draw.rectangle((right - 3, top, right, bottom), fill=team_color(game.home))
        for yard in range(10, 100, 10):   # ten-yard lines, longer every fifty
            height = bottom if yard == 50 else bottom - 1
            draw.line((yards(yard), top + 1, yards(yard), height), fill=(52, 110, 62))
        yard, ball = int(extra["yard"]), extra.get("ball")
        togo = int(extra.get("togo") or 0)
        if ball and togo:
            # The away team drives toward the home goal (yard line falling), and back.
            target = yard - togo if ball == "away" else yard + togo
            if 0 < target < 100:
                draw.line((yards(target), top, yards(target), bottom), fill=(255, 220, 0))
        spot = yards(yard)
        draw.ellipse((spot - 1, top + 1, spot + 1, bottom - 1),
                     fill=RED if extra.get("red_zone") else (210, 120, 45))
        for side, x0, step in (("away", 1, 3), ("home", 126, -3)):   # timeouts under each logo
            for n in range(int(extra.get(f"{side}_timeouts") or 0)):
                draw.point((x0 + n * step, bottom), fill=LAMP)

    @staticmethod
    def _hockey(body, extra, blink):
        """Beside each logo: PP and its clock for the team on the power play, EN for a
        team that has pulled its goalie."""
        for side, x, align in (("away", 18, "left"), ("home", 109, "right")):
            lines = []
            if extra.get("pp") == side:
                lines = [("PP", LAMP if blink else WHITE), (extra.get("pp_time", "").lstrip("0") or "", WHITE)]
            elif extra.get("empty_net") == side:
                lines = [("EN", RED if blink else WHITE)]
            for row, (text, color) in enumerate(lines):
                if text:
                    left = x if align == "left" else x - tiny_width(text) + 1
                    draw_tiny(body, text, left, 3 + row * 7, color)

    @staticmethod
    def _banner(body, call, team, game, t, who=""):
        """A big play in this game, flashed before the matchup: DOUBLE PLAY, GRAND SLAM."""
        draw = ImageDraw.Draw(body)
        on = math.floor(t * 4) % 2 == 0
        draw.rectangle((0, 0, 127, BODY_HEIGHT - 1), outline=LAMP if on else RED)
        width = text_width(call)
        draw_text(body, call, 64 - width // 2, 2, LAMP if on else WHITE)
        score = f"{game.away.abbreviation} {game.away.score}-{game.home.score} {game.home.abbreviation}"
        line = f"{who}  {score}" if who and tiny_width(f"{who}  {score}") <= 124 else score
        draw_tiny(body, fit_tiny(line, 124), 64 - tiny_width(fit_tiny(line, 124)) // 2, 11, WHITE)

    def _party_frame(self, party, age, t):
        try:
            color = ImageColor.getrgb(party["color"])
        except ValueError:
            color = LAMP
        color = mix(color, (255, 255, 255), .45) if sum(color) < 200 else color
        if self.party[0] != party["at"]:
            self.party = (party["at"], Lettering(party["call"], "fireworks", (color, (255, 255, 255)), "chase",
                                                 round(party["at"] * 1000) & 0xFFFFFF,
                                                 hold=CELEBRATION_SECONDS - 3.8, y=5))
        frame = new_frame()
        self.party[1].draw(frame, age)
        bulb_border(frame, t, (color, (255, 255, 255)), speed=18)
        draw_tiny(frame, party["line"], 64 - tiny_width(party["line"]) // 2, 25, WHITE)
        return frame


def validate(settings):
    value = settings.get("card_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 3 <= value <= 30:
        raise ValueError("card_seconds must be 3–30")


plugin = Plugin("sportsbook", "Sportsbook board", module=Sportsbook,
                defaults={"card_seconds": 8, "live_odds": True}, validate_settings=validate,
                help={"live_odds": "show the spread and total on games already under way, "
                                   "alternating with the channel or the shots on goal"},
    ui={"card_seconds": {"type": "slider", "min": 3, "max": 30, "unit": "s", "label": "Seconds per game"},
        "live_odds": {"label": "Lines on live games"}})
