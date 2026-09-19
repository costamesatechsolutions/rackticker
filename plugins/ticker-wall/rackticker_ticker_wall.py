"""Shop sign: your own words on the storefront kind of LED sign that spells
them out of flying pixels, drops letters in, spins slot reels and chases
marquee bulbs. It shows only what you write (or each style's classics);
live data has its own screens.

Styles: taqueria (with a taco parade), Vegas (with a working slot machine),
arena (with a noise meter) and Times Square. Every phrase gets a random effect
and colour treatment, so no two passes play the same.
"""
from __future__ import annotations

import math
import random
import time

from PIL import ImageDraw

from rackticker import Plugin, Module, new_frame
from app.core.fonts import draw_tiny, text_width, tiny_width, draw_text
from app.core.fx import (Lettering, bulb_border, dim, ease_out, hsv, plot, sprite, stamp)
from app.core.story import Storyboard


ORDER = ("taqueria", "vegas", "arena", "times_square")
THEMES = {
    "taqueria": {"palette": ((70, 220, 90), (255, 255, 255), (255, 60, 50), (255, 190, 40)),
                 "modes": ("alternate", "chase", "fire", "rainbow", "gradient"),
                 "effects": ("assemble", "drop", "slot", "wave", "chomp", "sparkle", "split", "fireworks"),
                 "border": ((70, 220, 90), (255, 255, 255), (255, 60, 50)), "interlude": "parade"},
    "vegas": {"palette": ((255, 200, 40), (255, 70, 50), (255, 255, 255)),
              "modes": ("chase", "alternate", "gradient", "rainbow"),
              "effects": ("slot", "sparkle", "fireworks", "assemble", "drop"),
              "border": ((255, 214, 70), (255, 90, 40)), "interlude": "slots"},
    "arena": {"palette": ((50, 130, 255), (255, 255, 255), (255, 60, 60)),
              "modes": ("alternate", "chase", "gradient"),
              "effects": ("drop", "split", "typewriter", "assemble", "wave"),
              "bars": True, "interlude": "meter"},
    "times_square": {"palette": ((255, 60, 50), (255, 255, 255), (70, 150, 255)),
                     "modes": ("solid", "alternate", "gradient", "rainbow"),
                     "effects": ("scroll_stop", "typewriter", "wave", "split", "sparkle"),
                     "bars": True, "interlude": None},
}
INTERLUDE_SECONDS = {"parade": 5.6, "slots": 6.8, "meter": 5.2}
# Each style's own words, used when you have not written any.
CLASSICS = {"taqueria": "TACOS / BURRITOS / QUESADILLAS|AL PASTOR / ASADA / CARNITAS|SALSA VERDE / SALSA ROJA",
            "vegas": "JACKPOT|WINNER WINNER|LUCKY 7S|ALL IN",
            "arena": "MAKE SOME NOISE|DEFEND THE RACK|LET'S GO",
            "times_square": "RACKTICKER IS LIVE|RACK ENHANCEMENT|WILL FIT IN 2U"}
UP, DOWN, GOLD, SKY, WHITE_LIGHT = (90, 235, 120), (255, 80, 60), (255, 200, 60), (90, 170, 255), (255, 255, 255)


def _fit_tiny(text, width=124):
    text = str(text).upper()
    while text and tiny_width(text) > width:
        text = text[:-1].rstrip()
    return text


TACO = ("..g.r.G.g.R.g...", ".gGrRgGgRrgGrg..", "yygGrRgGrRgGgyy.", "yYyyyyyyyyyyyYy.",
        "yYYYYYYYYYYYYYy.", ".yYYYYYYYYYYYy..", "..yyYYYYYYYYyy..", "....yyyyyyyy....")
CHILI = ("...........gg", "..........g..", "..rrrrrrrrg..", ".rRRRRRRRRr..",
         "rRWRRRRRRr...", "rRRRRRRrr....", ".rrrr........")
LIME = ("..GGGGG..", ".GllwllG.", "GlwlwlwlG", "GllwwwllG", "GwwwwwwwG",
        "GllwwwllG", "GlwlwlwlG", ".GllwllG.", "..GGGGG..")
CACTUS = ("...GG....", "..GggG...", "..GggG.G.", "G.GggG.gG", "gGGggGGgG", "gggggggG.",
          ".GGggG...", "..GggG...", "..GggG...", "bbbbbbbbb", ".bbbbbbb.")
FOOD = {"y": (196, 132, 24), "Y": (255, 204, 70), "g": (50, 170, 50), "G": (130, 240, 90),
        "r": (190, 30, 20), "R": (255, 90, 50), "W": (255, 220, 200), "l": (120, 220, 80),
        "w": (220, 255, 180), "b": (190, 90, 40)}
SEVEN = ("rrrrrrrr.", "rRRRRRRr.", "......rr.", ".....rr..", "....rr...",
         "...rr....", "...rr....", "..rr.....", "..rr.....")
CHERRY = ("......gg.", ".....g...", "....g.g..", "...g...g.", ".rr...rr.",
          "rWrr.rWrr", "rrrr.rrrr", ".rr...rr.", ".........")
BELL = ("....y....", "...yyy...", "..yYYYy..", "..yYYYy..", ".yYYYYYy.",
        ".yYYYYYy.", "yyyyyyyyy", "....y....", ".........")
DIAMOND = ("....c....", "...cCc...", "..cCCCc..", ".cCCWCCc.", "cCCCCCCCc",
           ".cCCCCCc.", "..cCCCc..", "...cCc...", "....c....")
REEL = {"y": (220, 150, 20), "Y": (255, 214, 60), "g": (60, 190, 60), "r": (220, 30, 40),
        "R": (255, 70, 70), "W": (255, 230, 230), "c": (40, 170, 255), "C": (140, 230, 255)}
SYMBOLS = (SEVEN, CHERRY, BELL, DIAMOND)


def _items(value):
    return [item.strip().upper() for item in value.split("|") if item.strip()]


def split_phrases(item):
    """Break an item on '/' then greedily into words that fit the big lettering."""
    phrases = []
    for part in (piece.strip() for piece in item.split("/")):
        line = ""
        for word in part.split():
            candidate = f"{line} {word}".strip()
            if not line or text_width(candidate, 2) <= 124:
                line = candidate
            else:
                phrases.append(line)
                line = word
        if line:
            phrases.append(line)
    return phrases


class TickerWall(Module):
    name = "ticker_wall"

    def __init__(self):
        self.board = Storyboard(resume=False)
        self.settings = None

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _build(self, visit):
        settings = self.settings
        style = settings["style"]
        rng = random.Random(time.time_ns() ^ (visit * 7919))
        theme = style if style in ORDER else ORDER[visit % len(ORDER)]
        spec = THEMES[theme]
        words = settings["words"].strip() or CLASSICS[theme]
        items, last = [], None
        for phrase in (p for item in _items(words) for p in split_phrases(item)):
            effect = rng.choice([e for e in spec["effects"] if e != last])
            last = effect
            shift = rng.randrange(len(spec["palette"]))
            palette = spec["palette"][shift:] + spec["palette"][:shift]
            lettering = Lettering(phrase, effect, palette, rng.choice(spec["modes"]),
                                  rng.randrange(1 << 30), hold=max(3.0, settings["scene_seconds"] / 3))
            items.append(({"theme": theme, "lettering": lettering}, lettering.duration))
        if spec["interlude"]:
            items.append(({"theme": theme, "interlude": spec["interlude"], "seed": rng.randrange(1 << 30)},
                          INTERLUDE_SECONDS[spec["interlude"]]))
        return items

    def _segment(self, context):
        self.settings = context.config["plugins"][self.name]
        self.board.sync(context.animation_time, self._build, context.scene)
        return self.board.current(context.animation_time, self._build)

    def hold(self, context):
        segment = self._segment(context)
        # Finish the whole theme pass on the first lap, then at least the current phrase.
        if segment and context.animation_time < sum(seconds for _, seconds in self.board.items):
            return True
        return bool(segment) and self.board.hold()

    def render(self, context):
        segment = self._segment(context)
        frame = new_frame()
        if not segment:
            return frame
        payload, local, _ = segment
        t = context.animation_time
        spec = THEMES[payload["theme"]]
        if "interlude" in payload:
            {"parade": parade, "slots": slots, "meter": meter}[payload["interlude"]](frame, local, payload["seed"], t)
        else:
            payload["lettering"].draw(frame, local)
        # Marquee bulbs belong to the Vegas sign alone; running bars elsewhere only
        # pulled the eye away from the words.
        if payload["theme"] == "vegas" and spec.get("border"):
            bulb_border(frame, t, spec["border"], speed=14)
        return frame


def parade(frame, t, seed, _clock):
    rng = random.Random(seed)
    order = [TACO, CHILI, LIME, CACTUS, TACO]
    rng.shuffle(order)
    title = "HOT & FRESH" if seed % 2 else "ORDER UP!"
    x0 = (128 - text_width(title)) // 2
    for index, char in enumerate(title):
        color = ((70, 220, 90), (255, 255, 255), (255, 60, 50))[(index + math.floor(t * 6)) % 3]
        draw_text(frame, char, x0 + sum(text_width(c) + 1 for c in title[:index]), 3, color)
    for index, art in enumerate(order):
        x = 128 + index * 30 - t * 50
        hop = abs(math.sin(t * 7 + index)) * 4
        image = sprite(art, FOOD)
        stamp(frame, image, x, 29 - image.height - round(hop))
    pixels = frame.load()
    for x in range(1, 127):
        if (x + math.floor(t * 50)) % 4 == 0:
            plot(frame, pixels, x, 29, (120, 70, 30))


def _reel_symbol(frame, art, x, y, top, bottom):
    image = sprite(art, REEL)
    crop_top, crop_bottom = max(0, top - y), min(image.height, bottom - y)
    if crop_top < crop_bottom:
        part = image.crop((0, crop_top, image.width, crop_bottom))
        frame.paste(part, (x, y + crop_top), part)


def slots(frame, t, seed, clock):
    rng = random.Random(seed)
    jackpot = rng.random() < .45
    outcome = [0, 0, 0] if jackpot else rng.sample(range(len(SYMBOLS)), 3)
    draw = ImageDraw.Draw(frame)
    draw_tiny(frame, "SLOTS", 3, 3, (255, 214, 70))
    credits = 250 + (round(ease_out((t - 4.2) / 1.5) * 777) if jackpot and t > 4.2 else 0)
    draw_tiny(frame, f"{credits:04d}", 3, 11, (255, 90, 40))
    draw_tiny(frame, "CR", 3, 18, (140, 110, 60))
    window_top, window_bottom = 7, 25
    for reel in range(3):
        x = 34 + reel * 21
        draw.rectangle((x - 2, window_top - 1, x + 18, window_bottom), outline=(255, 214, 70), fill=(24, 10, 4))
        strip = [random.Random(seed * 31 + reel * 101 + n).randrange(len(SYMBOLS)) for n in range(16)]
        strip[11] = outcome[reel]
        stop = 1.3 + reel * .75
        final = 11 * 12
        if t < stop:
            offset = final - (stop - t) * 75
        else:
            settle = t - stop
            offset = final + round(math.sin(settle * 22) * 3.5 * math.exp(-settle * 7))
        base = math.floor(offset / 12)
        for index in range(base - 2, base + 3):
            y = 12 + index * 12 - math.floor(offset)
            _reel_symbol(frame, SYMBOLS[strip[index % 16]], x + 4, y, window_top, window_bottom)
    pull = ease_out(t / .35) if t < .9 else 1 - ease_out((t - .9) / .4)
    knob = 6 + round(pull * 14)
    draw.line((104, knob + 2, 104, 24), fill=(170, 170, 180))
    draw.rectangle((103, 24, 106, 26), fill=(120, 120, 130))
    draw.ellipse((102, knob - 1, 106, knob + 3), fill=(255, 50, 60))
    settled = t > 1.3 + 2 * .75 + .5
    if settled and jackpot:
        if math.floor(clock * 6) % 2:
            label = "JACKPOT!"
            draw_tiny(frame, label, 64 - tiny_width(label) // 2 + 2, 26, (255, 230, 90))
        pixels = frame.load()
        sparks = random.Random(math.floor(t * 12))
        for _ in range(14):
            plot(frame, pixels, sparks.randrange(30, 100), sparks.randrange(1, 31), hsv(sparks.random()))
    elif settled:
        draw_tiny(frame, "SO CLOSE", 64 - tiny_width("SO CLOSE") // 2 + 2, 26, (160, 130, 80))


def meter(frame, t, seed, clock):
    draw = ImageDraw.Draw(frame)
    rng = random.Random(seed)
    title = "NOISE METER"
    draw_tiny(frame, title, 64 - tiny_width(title) // 2, 3, (255, 255, 255))
    target = 20 * min(1.0, ease_out(t / 3.4))
    for index in range(20):
        x = 4 + index * 6
        wobble = math.sin(clock * 11 + index * 1.7 + rng.random()) * 1.5
        lit = index < target + wobble
        color = ((60, 220, 90) if index < 11 else (255, 210, 50) if index < 16 else (255, 60, 50))
        draw.rectangle((x, 9, x + 4, 21), fill=color if lit else dim(color, .12))
    if t > 3.6:
        label = "LOUDER!"
        if math.floor(clock * 5) % 2:
            draw_text(frame, label, 64 - text_width(label) // 2, 23, (255, 60, 60))


def validate(settings):
    if settings.get("style") not in ("auto", *ORDER):
        raise ValueError("style must be auto, taqueria, vegas, arena or times_square")
    seconds = settings.get("scene_seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 4 <= seconds <= 60:
        raise ValueError("scene_seconds must be 4–60")
    words = settings.get("words")
    if not isinstance(words, str) or len(words) > 1000:
        raise ValueError("words must be text, phrases separated by |")


def migrate(settings):
    """Before 2026-09 the sign also re-showed live data and kept four phrase lists."""
    mode = settings.pop("mode", None)
    if "style" not in settings:
        settings["style"] = mode if mode in ORDER else "auto"
    custom = [settings.pop(f"{theme}_items", None) for theme in ORDER]
    if "words" not in settings:
        mine = [value for theme, value in zip(ORDER, custom) if value and value != CLASSICS[theme]]
        settings["words"] = "|".join(mine)
    return settings


plugin = Plugin(
    "ticker_wall", "Shop sign", module=TickerWall,
    defaults={"style": "auto", "words": "", "scene_seconds": 8},
    validate_settings=validate, migrate_settings=migrate,
    choices={"style": ("auto", *ORDER)},
    help={"style": "auto takes turns: taqueria (taco parade), Vegas (slot machine), arena, Times Square",
          "words": "Your phrases, separated by | ; a / splits one into beats. Empty uses each style's classics",
          "scene_seconds": "Longer holds each phrase longer"},
    ui={"words": {"label": "Your words"},
        "scene_seconds": {"type": "slider", "min": 4, "max": 60, "unit": "s", "label": "Phrase length"}},
)
