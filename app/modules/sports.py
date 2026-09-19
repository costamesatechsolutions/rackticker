"""Single-game scoreboard: logos and team stripes either side, big scores once
play starts, the matchup and line before it, league lettering you can read."""
from io import BytesIO
from functools import lru_cache
import math

from PIL import Image, ImageColor, ImageDraw
from app.modules.base import Module, missing, stale_marker
from app.core.fonts import draw_text, draw_tiny, text_width, tiny_width
from app.core.fx import mix
from app.core.renderer import new_frame, WHITE, AMBER, MUTED, GREEN

LEAGUE_COLORS = {"NFL": (20, 90, 220), "NBA": (230, 80, 30), "MLB": (200, 30, 40), "NHL": (120, 130, 150),
                 "WNBA": (255, 120, 40), "NCAAF": (40, 140, 70), "NCAAM": (90, 60, 200)}


@lru_cache(maxsize=64)
def _logo_image(png):
    try:
        return Image.open(BytesIO(png)).convert("RGBA")
    except (OSError, ValueError):
        return None


def _rgb(color):
    try:
        return ImageColor.getrgb(color)
    except ValueError:
        return WHITE


def _fit_tiny(text, width):
    text = str(text).upper()
    while text and tiny_width(text) > width:
        text = text[:-1].rstrip()
    return text


class SportsModule(Module):
    name = "sports"

    def refresh_interval(self, context):
        snap = context.snapshots.get(self.name)
        if snap and snap.data and (snap.data.status in ("live", "goal") or snap.data.secondary):
            return 1 / context.config["display"]["fps"]
        return float("inf")

    def render(self, context):
        snap = context.snapshots.get(self.name)
        if not snap or not snap.data:
            return missing("SPORTS")
        game = snap.data
        frame = new_frame()
        draw = ImageDraw.Draw(frame)
        live = game.status in ("live", "goal")
        league = (game.league or "").upper()[:6]
        x = 0
        if league:
            draw_text(frame, league, 0, 0, mix(LEAGUE_COLORS.get(league, (150, 150, 160)), (255, 255, 255), .35))
            x = text_width(league) + 5
        if live and math.floor(context.animation_time * 2) % 2 == 0:
            draw.rectangle((x, 2, x + 2, 4), fill=(255, 72, 52))
        x += 5 if live else 0
        draw_tiny(frame, _fit_tiny(game.detail, 128 - x), x, 1, GREEN if live else AMBER if game.status == "pregame" else MUTED)
        for px in range(0, 128, 2):
            draw.point((px, 7), fill=(40, 46, 46))
        for team, left in ((game.away, True), (game.home, False)):
            # A two-pixel rectangle stays perfectly vertical at any team colour.
            stripe = 0 if left else 126
            draw.rectangle((stripe, 9, stripe + 1, 24), fill=_rgb(team.color))
            mark_x = 3 if left else 109
            logo = _logo_image(team.logo_png) if team.logo_png else None
            if logo:
                frame.paste(logo, (mark_x, 9), logo)
            else:
                draw.rounded_rectangle((mark_x, 9, mark_x + 15, 24), radius=2, fill=mix(_rgb(team.color), (0, 0, 0), .3))
                draw_tiny(frame, team.abbreviation, mark_x + 8 - tiny_width(team.abbreviation) // 2, 14, WHITE)
            if game.status == "pregame":
                name_x = 22 if left else 106 - text_width(team.abbreviation)
                draw_text(frame, team.abbreviation, name_x, 12, WHITE)
            else:
                score = str(team.score)
                leading = team.score >= max(game.home.score, game.away.score)
                score_x = 58 - text_width(score, 2) if left else 70
                draw_text(frame, score, score_x, 10, WHITE if leading else MUTED, 2, True)
        if game.status == "pregame":
            draw_text(frame, "@", 64 - text_width("@", 2) // 2, 10, MUTED, 2, True)
        else:
            draw_text(frame, "-", 61, 14, MUTED)
        if game.secondary:
            line = _fit_tiny(game.secondary, 124)
            draw_tiny(frame, line, 64 - tiny_width(line) // 2, 26, AMBER)
        if live:
            phase = math.floor(context.animation_time * 16) % 8
            for px in range(phase, 128, 8):
                draw.point((px, 31), fill=GREEN)
        return stale_marker(frame, snap)
