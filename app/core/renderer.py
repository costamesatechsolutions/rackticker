"""Pixel primitives, clipped scrolling, transitions, and the canonical frame boundary."""
from __future__ import annotations

import math
import random
from functools import lru_cache
from PIL import Image

from app.core.fonts import draw_text, text_width
from app.core.fx import bounce, ease_in_out

WIDTH, HEIGHT = 128, 32
AMBER = (255, 166, 48)
WHITE = (232, 240, 235)
MUTED = (106, 139, 139)
GREEN = (91, 231, 150)
RED = (255, 83, 70)
BLUE = (73, 170, 255)


def new_frame():
    return Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))


def validate_frame(frame):
    if not isinstance(frame, Image.Image) or frame.size != (WIDTH, HEIGHT) or frame.mode != "RGB":
        raise ValueError("Output must be exactly 128x32 RGB")
    return frame


def clipped_text(frame, text, box, color=WHITE, scale=1, offset=0):
    """box is (x, y, width, height). Pixels outside it are never touched."""
    x, y, width, height = map(int, box)
    if width <= 0 or height <= 0:
        return
    layer = Image.new("RGB", (width, height))
    draw_text(layer, text, int(offset), 0, color, scale)
    frame.paste(layer, (x, y))


def scroll_positions(width, viewport, elapsed, speed=24, gap=32):
    """Start readable at x=0; repeat after text width + an actual blank gap."""
    if width <= viewport:
        return (0,)
    period = width + gap
    offset = math.floor(max(0, elapsed) * speed + 1e-6) % period
    return (-offset, period - offset)


def scrolling_text(frame, text, box, elapsed, speed=24, gap=32, color=WHITE, scale=1):
    x, y, width, height = map(int, box)
    if width <= 0 or height <= 0:
        return
    layer = Image.new("RGB", (width, height))
    for offset in scroll_positions(text_width(text, scale), width, elapsed, speed, gap):
        draw_text(layer, text, offset, 0, color, scale)
    frame.paste(layer, (x, y))


def loop_strip(frame, strip, box, t, speed):
    """Seamless endless crawl of a prebuilt strip (content + trailing gap).
    Build the strip once per data change; each frame is then only two pastes."""
    x, y, width, height = map(int, box)
    offset = math.floor(max(0.0, t) * speed + 1e-6) % max(1, strip.width)
    layer = Image.new("RGB", (width, height))
    position = -offset
    while position < width:
        layer.paste(strip, (position, 0))
        position += strip.width
    frame.paste(layer, (x, y))


def crawl_once_x(t, speed, viewport=WIDTH):
    """Left edge of text that enters at the right and exits at the left."""
    return viewport - math.floor(max(0.0, t) * speed + 1e-6)


# "dissolve" is still there to choose, but is not in the rotation: a random field of dots
# on a PWM panel reads as flicker.
AUTO_TRANSITIONS = ("slide_left", "wipe", "drop", "slide_up")


def transition_seconds(kind, configured):
    return {"ticker": 1.6, "dissolve": .7, "drop": .75, "wipe": .55}.get(kind, configured)


@lru_cache(maxsize=1)
def _dissolve_ranks():
    order = list(range(WIDTH * HEIGHT))
    random.Random(8128).shuffle(order)
    ranks = bytearray(WIDTH * HEIGHT)
    for rank, index in enumerate(order):
        ranks[index] = rank * 255 // (WIDTH * HEIGHT - 1)
    return Image.frombytes("L", (WIDTH, HEIGHT), bytes(ranks))


def transition(old, new, progress, kind="cut"):
    validate_frame(old)
    validate_frame(new)
    if kind == "cut" or progress >= 1:
        return new
    if progress <= 0:
        return old
    out = new_frame()
    if kind in ("slide_left", "ticker"):
        offset = int(WIDTH * ease_in_out(progress)) if kind == "slide_left" else int(WIDTH * progress)
        out.paste(old, (-offset, 0))
        out.paste(new, (WIDTH - offset, 0))
    elif kind == "slide_up":
        offset = int(HEIGHT * ease_in_out(progress))
        out.paste(old, (0, -offset))
        out.paste(new, (0, HEIGHT - offset))
    elif kind == "wipe":
        edge = int(WIDTH * ease_in_out(progress))
        out.paste(old)
        out.paste(new.crop((0, 0, edge, HEIGHT)), (0, 0))
        if 0 < edge < WIDTH:
            out.paste((255, 255, 255), (edge, 0, edge + 1, HEIGHT))
            out.paste((70, 70, 80), (min(WIDTH - 1, edge + 1), 0, min(WIDTH, edge + 2), HEIGHT))
    elif kind == "dissolve":
        threshold = int(progress * 256)
        mask = _dissolve_ranks().point(lambda value: 255 if value < threshold else 0)
        out = Image.composite(new, old, mask)
    elif kind == "drop":
        top = round((bounce(progress) - 1) * HEIGHT)
        out.paste(old, (0, top + HEIGHT))
        out.paste(new, (0, top))
    else:
        raise ValueError(f"Unknown transition: {kind}")
    return out
