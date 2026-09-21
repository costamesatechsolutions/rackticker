"""Pixel-native LED signage effects: easing, colour, sprites, particles and
animated lettering.

Everything draws whole pixels on the frame; nothing antialiases. Effects are
pure functions of (seed, time) so every sink and emulator shows identical
pixels, while callers vary the seed per run so a sign never replays the same
"movie" twice.
"""
from __future__ import annotations

import colorsys
import math
import random
from functools import lru_cache

from PIL import Image

from app.core.fonts import glyph_spans, text_mask, text_width

WIDTH, HEIGHT = 128, 32
EFFECTS = ("assemble", "drop", "slot", "typewriter", "wave", "scroll_stop",
           "chomp", "split", "sparkle", "fireworks")


# --- easing and colour ------------------------------------------------------

def clamp01(value):
    return 0.0 if value <= 0 else 1.0 if value >= 1 else value


def ease_out(t):
    return 1 - (1 - clamp01(t)) ** 3


def ease_in(t):
    return clamp01(t) ** 3


def ease_in_out(t):
    t = clamp01(t)
    return 4 * t * t * t if t < .5 else 1 - (-2 * t + 2) ** 3 / 2


def bounce(t):
    t = clamp01(t)
    n, d = 7.5625, 2.75
    if t < 1 / d:
        return n * t * t
    if t < 2 / d:
        t -= 1.5 / d
        return n * t * t + .75
    if t < 2.5 / d:
        t -= 2.25 / d
        return n * t * t + .9375
    t -= 2.625 / d
    return n * t * t + .984375


def hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (round(r * 255), round(g * 255), round(b * 255))


def dim(color, amount):
    r, g, b = color
    return (max(0, min(255, round(r * amount))), max(0, min(255, round(g * amount))),
            max(0, min(255, round(b * amount))))


def mix(a, b, t):
    t = clamp01(t)
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def plot(frame, pixels, x, y, color):
    """Max-blend one pixel so overlapping light adds up like real LEDs."""
    x, y = math.floor(x), math.floor(y)
    if x < 0 or y < 0:
        return
    try:
        old = pixels[x, y]
    except IndexError:              # off the right or bottom edge of the picture
        return
    pixels[x, y] = (max(old[0], color[0]), max(old[1], color[1]), max(old[2], color[2]))


# --- sprites -----------------------------------------------------------------

@lru_cache(maxsize=256)
def _sprite(art, palette, flip):
    colors = dict(palette)
    image = Image.new("RGBA", (max(map(len, art)), len(art)))
    pixels = image.load()
    for y, row in enumerate(art):
        for x, key in enumerate(row):
            if key in colors:
                pixels[x, y] = (*colors[key], 255)
    return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip else image


def sprite(art, palette, flip=False):
    """ASCII art -> cached RGBA sprite. Characters missing from palette are clear."""
    return _sprite(tuple(art), tuple(sorted(palette.items())), flip)


def stamp(frame, image, x, y):
    frame.paste(image, (math.floor(x), math.floor(y)), image)


def triangle(frame, x, y, up, color):
    """A 5×3 market arrow; crisp at 1× where font arrows look spindly."""
    pixels = frame.load()
    rows = ((2, 2), (1, 3), (0, 4))
    for row, (left, right) in enumerate(rows if up else rows[::-1]):
        for column in range(left, right + 1):
            plot(frame, pixels, x + column, y + row, color)


# --- lights ------------------------------------------------------------------

@lru_cache(maxsize=8)
def _perimeter(width, height, inset):
    left, top, right, bottom = inset, inset, width - 1 - inset, height - 1 - inset
    points = [(x, top) for x in range(left, right)]
    points += [(right, y) for y in range(top, bottom)]
    points += [(x, bottom) for x in range(right, left, -1)]
    points += [(left, y) for y in range(bottom, top, -1)]
    return tuple(points)


def bulb_border(frame, t, colors, spacing=4, speed=10, inset=0):
    """Casino marquee: bulbs chase clockwise around the edge."""
    pixels = frame.load()
    phase = math.floor(t * speed + 1e-6)
    for index, (x, y) in enumerate(_perimeter(*frame.size, inset)):
        step = (index + phase) % spacing
        if step == 0:
            pixels[x, y] = colors[((index + phase) // spacing) % len(colors)]
        elif step == 1:
            pixels[x, y] = dim(colors[((index + phase) // spacing) % len(colors)], .22)


def chase_bar(frame, y, t, colors, segment=6, gap=3, speed=24, reverse=False):
    pixels = frame.load()
    period = segment + gap
    phase = math.floor(t * speed + 1e-6) % (period * len(colors))
    for x in range(frame.width):
        position = (x - phase if reverse else x + phase) % (period * len(colors))
        if position % period < segment:
            pixels[x, y] = colors[position // period]


# --- particles ---------------------------------------------------------------

class Particles:
    """Bounded, stateful particle field for simulations (games, confetti)."""

    def __init__(self, limit=360):
        self.items = []
        self.limit = limit

    def emit(self, x, y, vx, vy, life, color):
        if len(self.items) < self.limit:
            self.items.append([x, y, vx, vy, life, life, color])

    def burst(self, rng, x, y, count, speed, colors, life=(.5, 1.2), lift=0):
        for _ in range(count):
            angle = rng.random() * math.tau
            velocity = speed * (.35 + rng.random() * .65)
            self.emit(x, y, math.cos(angle) * velocity, math.sin(angle) * velocity - lift,
                      rng.uniform(*life), rng.choice(colors))

    def step(self, dt, gravity=0.0, drag=0.0):
        alive = []
        keep = max(0.0, 1 - drag * dt)
        for item in self.items:
            item[4] -= dt
            if item[4] <= 0:
                continue
            item[2] *= keep
            item[3] = item[3] * keep + gravity * dt
            item[0] += item[2] * dt
            item[1] += item[3] * dt
            if -8 < item[0] < WIDTH + 8 and item[1] < HEIGHT + 8:
                alive.append(item)
        self.items = alive

    def draw(self, frame):
        pixels = frame.load()
        for x, y, _, _, life, total, color in self.items:
            plot(frame, pixels, x, y, dim(color, .35 + .65 * life / total))


# --- lettering ---------------------------------------------------------------

@lru_cache(maxsize=128)
def text_points(text, scale=1, smooth=False):
    mask = text_mask(text, scale, smooth)
    pixels = mask.load()
    return tuple((x, y) for y in range(mask.height) for x in range(mask.width) if pixels[x, y])


@lru_cache(maxsize=128)
def point_letters(text, scale=1, smooth=False):
    """Letter index for each lit point, computed once instead of per pixel per frame."""
    spans = glyph_spans(text, scale)
    result = []
    for x, _ in text_points(text, scale, smooth):
        result.append(next((i for i, (_, start, width) in enumerate(spans) if start <= x < start + width), 0))
    return tuple(result)


def fit_scale(text, width=124):
    """Largest lettering that fits: smooth 2× headline, else 1×, else None (crawl)."""
    if text_width(text, 2) <= width:
        return 2
    if text_width(text) <= width:
        return 1
    return None


def paint(mode, palette, t, x, row, index, height):
    if mode == "rainbow":
        return hsv(x / 110 + t * .4)
    if mode == "chase":
        return palette[(index + math.floor(t * 5)) % len(palette)]
    if mode == "alternate":
        return palette[index % len(palette)]
    if mode == "fire":
        return mix((255, 236, 90), (255, 40, 0), row / max(1, height - 1))
    if mode == "gradient" and len(palette) > 1:
        return mix(palette[0], palette[1], row / max(1, height - 1))
    return palette[0]


def draw_glyph(frame, char, x, y, color, scale, smooth, clip=None):
    """Paste one glyph, optionally clipped to a vertical band (top, bottom)."""
    mask = text_mask(char, scale, smooth)
    x, y = math.floor(x), math.floor(y)
    top, bottom = clip if clip else (y, y + mask.height)
    crop_top, crop_bottom = max(0, top - y), min(mask.height, bottom - y)
    if crop_top >= crop_bottom:
        return
    if (crop_top, crop_bottom) != (0, mask.height):
        mask = mask.crop((0, crop_top, mask.width, crop_bottom))
    frame.paste(color, (x, y + crop_top, x + mask.width, y + crop_top + mask.height), mask)


@lru_cache(maxsize=32)
def _scatter(text, scale, smooth, seed):
    rng = random.Random(seed)
    return tuple((rng.uniform(-50, WIDTH + 50), rng.uniform(-30, HEIGHT + 30), rng.uniform(0, .55),
                  rng.uniform(-90, 90), rng.uniform(-75, 5))
                 for _ in text_points(text, scale, smooth))


def effect_duration(name, text, hold=2.2, speed=40):
    scale = fit_scale(text)
    if scale is None or name == "scroll_stop" and scale is None:
        return (WIDTH + text_width(text)) / speed + .4
    if name == "chomp":
        return 1.6 + hold * .5 + (WIDTH + 30) / 56
    if name == "fireworks":
        return 1.4 + hold + .9
    return 1.6 + hold + .9


class Lettering:
    """Draws one phrase with a named effect at time t in [0, duration]."""

    def __init__(self, text, effect="assemble", palette=((255, 166, 48),), mode="solid",
                 seed=0, hold=2.2, y=None, speed=40):
        self.text = " ".join(str(text).upper().split()) or " "
        self.scale = fit_scale(self.text)
        self.effect = effect if self.scale else "crawl"
        self.palette = tuple(palette)
        self.mode = mode
        self.seed = seed
        self.speed = speed
        self.smooth = self.scale == 2
        scale = self.scale or 1
        self.width = text_width(self.text, scale)
        self.height = 7 * scale
        self.x0 = (WIDTH - self.width) // 2
        self.y0 = (HEIGHT - self.height) // 2 if y is None else y
        self.duration = effect_duration(effect, self.text, hold, speed)
        self.out_start = self.duration - .9

    def done(self, t):
        return t >= self.duration

    def draw(self, frame, t):
        getattr(self, f"_{self.effect}")(frame, max(0.0, t))

    # Pixel-level colour so rainbow/fire modes flow across letters.
    def _points(self, frame, t, transform):
        pixels = frame.load()
        scale = self.scale or 1
        for (x, y), index in zip(text_points(self.text, scale, self.smooth),
                                 point_letters(self.text, scale, self.smooth)):
            result = transform(x, y, index)
            if result:
                plot(frame, pixels, result[0], result[1], result[2])

    def _color(self, t, x, y, index):
        return paint(self.mode, self.palette, t, self.x0 + x, y, index, self.height)

    def _crawl(self, frame, t):
        x = WIDTH - math.floor(t * self.speed + 1e-6)
        y = (HEIGHT - 7) // 2
        for char, start, _ in glyph_spans(self.text):
            if -8 < x + start < WIDTH:
                draw_glyph(frame, char, x + start, y, paint(self.mode, self.palette, t, x + start, 3, 0, 7), 1, False)

    def _assemble(self, frame, t):
        scatter = _scatter(self.text, self.scale, self.smooth, self.seed)
        points = text_points(self.text, self.scale, self.smooth)
        letters = point_letters(self.text, self.scale, self.smooth)
        pixels = frame.load()
        out = t - self.out_start
        for (x, y), (sx, sy, delay, vx, vy), index in zip(points, scatter, letters):
            tx, ty = self.x0 + x, self.y0 + y
            base = self._color(t, x, y, index)
            if out < 0:
                p = ease_out((t - delay) / 1.05)
                color = mix((255, 255, 255), base, p * p)
                plot(frame, pixels, sx + (tx - sx) * p, sy + (ty - sy) * p, color)
            else:
                plot(frame, pixels, tx + vx * out, ty + vy * out + 70 * out * out,
                     dim(base, 1 - out / .9))

    def _drop(self, frame, t):
        spans = glyph_spans(self.text, self.scale)
        for i, (char, start, _) in enumerate(spans):
            if char == " ":
                continue
            fall = bounce((t - .1 - i * .085) / .65)
            y = self.y0 - (1 - fall) * (self.y0 + self.height + 2)
            if t > self.out_start:
                y += ease_in((t - self.out_start - i * .035) / .6) * (HEIGHT + 2)
            draw_glyph(frame, char, self.x0 + start, y,
                       paint(self.mode, self.palette, t, self.x0 + start, 0, i, 1), self.scale, self.smooth)

    def _slot(self, frame, t):
        reel = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$#&%"
        clip = (self.y0, self.y0 + self.height)
        for i, (char, start, _) in enumerate(glyph_spans(self.text, self.scale)):
            if char == " ":
                continue
            x = self.x0 + start
            color = paint(self.mode, self.palette, t, x, 0, i, 1)
            lock = .3 + i * .12
            if t > self.out_start:
                lock_out = self.out_start + i * .05
                if t > lock_out:
                    drop = ease_in((t - lock_out) / .45) * self.height
                    draw_glyph(frame, char, x, self.y0 + drop, color, self.scale, self.smooth, clip)
                    continue
            if t >= lock:
                settle = 1 - ease_out((t - lock) / .22)
                offset = round(settle * self.height * .35)
                draw_glyph(frame, char, x, self.y0 + offset, color, self.scale, self.smooth, clip)
                if offset:
                    draw_glyph(frame, reel[(i * 7 + self.seed) % len(reel)], x,
                               self.y0 + offset - self.height - 1, dim(color, .55), self.scale, self.smooth, clip)
                continue
            spin = t * 16 + i * 2.3
            step = math.floor(spin)
            offset = round((spin - step) * (self.height + 1))
            current = reel[(step * 7 + i * 5 + self.seed) % len(reel)]
            upcoming = reel[((step + 1) * 7 + i * 5 + self.seed) % len(reel)]
            draw_glyph(frame, current, x, self.y0 + offset, dim(color, .7), self.scale, self.smooth, clip)
            draw_glyph(frame, upcoming, x, self.y0 + offset - self.height - 1, dim(color, .7),
                       self.scale, self.smooth, clip)

    def _typewriter(self, frame, t):
        spans = glyph_spans(self.text, self.scale)
        shown = math.floor((t - .15) / .085)
        if t > self.out_start:
            shown = len(spans) - math.floor((t - self.out_start) / .04)
        shown = max(0, min(len(spans), shown))
        for i, (char, start, _) in enumerate(spans[:shown]):
            draw_glyph(frame, char, self.x0 + start, self.y0,
                       paint(self.mode, self.palette, t, self.x0 + start, 0, i, 1), self.scale, self.smooth)
        if math.floor(t * 4) % 2 == 0 or shown < len(spans):
            cursor = self.x0 + (spans[shown - 1][1] + spans[shown - 1][2] + self.scale if shown else 0)
            if cursor < WIDTH - 1:
                frame.paste(self.palette[-1], (cursor, self.y0, cursor + self.scale * 2, self.y0 + self.height))

    def _wave(self, frame, t):
        reveal = ease_out(t / .9) * (self.width + 2)
        hide = ease_in((t - self.out_start) / .8) * (self.width + 2) if t > self.out_start else -1
        amplitude = 2.6 if self.scale == 2 else 3.4

        def transform(x, y, index):
            if x > reveal or x < hide:
                return None
            tx = self.x0 + x
            return tx, self.y0 + y + round(math.sin(tx * .23 - t * 6) * amplitude), self._color(t, x, y, index)
        self._points(frame, t, transform)

    def _scroll_stop(self, frame, t):
        if t < .9:
            x = WIDTH + (self.x0 - WIDTH) * ease_out(t / .9)
        elif t > self.out_start:
            x = self.x0 - ease_in((t - self.out_start) / .85) * (self.x0 + self.width + 2)
        else:
            x = self.x0
        for i, (char, start, _) in enumerate(glyph_spans(self.text, self.scale)):
            draw_glyph(frame, char, x + start, self.y0,
                       paint(self.mode, self.palette, t, x + start, 0, i, 1), self.scale, self.smooth)
        if .9 <= t <= self.out_start:
            self._twinkles(frame, t, 5)

    def _split(self, frame, t):
        half = self.height // 2
        if t < 1:
            shift = (1 - ease_out(t / 1)) * (WIDTH + self.width)
        elif t > self.out_start:
            shift = -ease_in((t - self.out_start) / .85) * (WIDTH + self.width)
        else:
            shift = 0

        def transform(x, y, index):
            dx = -shift if y < half else shift
            return self.x0 + x + dx, self.y0 + y, self._color(t, x, y, index)
        self._points(frame, t, transform)

    def _sparkle(self, frame, t):
        glow = ease_out(t / .8) if t < self.out_start else 1 - ease_in((t - self.out_start) / .9)

        def transform(x, y, index):
            return self.x0 + x, self.y0 + y, dim(self._color(t, x, y, index), glow)
        self._points(frame, t, transform)
        self._twinkles(frame, t, 9)

    def _twinkles(self, frame, t, count):
        pixels = frame.load()
        bucket = math.floor(t * 10)
        rng = random.Random(self.seed * 131 + bucket)
        for _ in range(count):
            x = rng.randrange(max(1, self.x0 - 6), min(WIDTH - 1, self.x0 + self.width + 6))
            y = rng.randrange(max(1, self.y0 - 4), min(HEIGHT - 1, self.y0 + self.height + 4))
            plot(frame, pixels, x, y, (255, 255, 255))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                plot(frame, pixels, x + dx, y + dy, (90, 90, 110))

    def _chomp(self, frame, t):
        spans = glyph_spans(self.text, self.scale)
        start_eat = 1.6 + (self.out_start - 1.6 - (WIDTH + 30) / 56 + .9)
        mouth_x = -14 + max(0.0, t - start_eat) * 56
        for i, (char, start, width) in enumerate(spans):
            gx = self.x0 + start
            if gx + width / 2 < mouth_x + 6:
                continue
            fall = ease_out((t - i * .06) / .5)
            draw_glyph(frame, char, gx, self.y0 - (1 - fall) * 20,
                       paint(self.mode, self.palette, t, gx, 0, i, 1), self.scale, self.smooth)
        if t >= start_eat:
            opening = math.floor(t * 10) % 2
            cy = HEIGHT // 2
            stamp(frame, sprite(CHOMPER[opening], {"y": (255, 220, 0)}), mouth_x, cy - 6)
            ghost_x = mouth_x - 22
            stamp(frame, sprite(GHOST, {"r": (255, 70, 90), "w": (255, 255, 255), "b": (40, 80, 255)}),
                  ghost_x, cy - 6)

    def _fireworks(self, frame, t):
        pixels = frame.load()
        rng = random.Random(self.seed)
        launches = [(i * .55 + rng.uniform(0, .25), rng.randrange(14, 114), rng.randrange(5, 14),
                     hsv(rng.random())) for i in range(max(3, math.ceil(self.duration / .55)))]
        for launch, x, top, color in launches:
            age = t - launch
            if age < 0:
                continue
            if age < .6:
                y = HEIGHT - (HEIGHT - top) * ease_out(age / .6)
                plot(frame, pixels, x, y, (255, 230, 170))
                plot(frame, pixels, x, y + 1, (120, 80, 40))
                continue
            burst = age - .6
            if burst > 1.3:
                continue
            spark = random.Random(x * 97 + top)
            for n in range(28):
                angle = n / 28 * math.tau + spark.random() * .2
                speed = 20 + spark.random() * 18
                px = x + math.cos(angle) * speed * burst
                py = top + math.sin(angle) * speed * burst + 26 * burst * burst
                plot(frame, pixels, px, py, dim(color, 1 - burst / 1.3))
        # Letters join the first bursts instead of waiting on a dark panel.
        if t > .9:
            reveal = ease_out((t - .9) / .6)
            fade = 1 - ease_in((t - self.out_start) / .9) if t > self.out_start else 1

            def transform(x, y, index):
                if x > reveal * (self.width + 1):
                    return None
                return self.x0 + x, self.y0 + y, dim(self._color(t, x, y, index), fade)
            self._points(frame, t, transform)


CHOMPER = (
    ("...yyyyy...", ".yyyyyyyyy.", "yyyyyyyyyyy", "yyyyyyyy...", "yyyyy......",
     "yyyy.......", "yyyyy......", "yyyyyyyy...", "yyyyyyyyyyy", ".yyyyyyyyy.", "...yyyyy..."),
    ("...yyyyy...", ".yyyyyyyyy.", "yyyyyyyyyyy", "yyyyyyyyyyy", "yyyyyyyyyyy",
     "yyyyyyy....", "yyyyyyyyyyy", "yyyyyyyyyyy", "yyyyyyyyyyy", ".yyyyyyyyy.", "...yyyyy..."),
)
GHOST = ("...rrrrr...", ".rrrrrrrrr.", "rrwwrrrwwrr", "rwwbbrwwbbr", "rwwbbrwwbbr",
         "rrwwrrrwwrr", "rrrrrrrrrrr", "rrrrrrrrrrr", "rrrrrrrrrrr", "rr.rrr.rrr.", "r...r...r..")
