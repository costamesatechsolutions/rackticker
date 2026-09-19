"""Classic TIX Clock: four groups of counted squares, rendered in pixels.

The four fields follow the original physical layout: 1×3, 3×3, 2×3 and 3×3.
The number of lit squares is the corresponding time digit; the occupied
positions change at a bounded, deterministic interval so the same wall-clock
bucket renders identically for every output sink and connected emulator. The
algorithm is original RackTicker code and does not copy any hardware firmware.
"""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from itertools import combinations
import random
import time

from PIL import ImageDraw

from app.core.fonts import centered
from app.core.fx import mix
from app.core.renderer import AMBER, BLUE, GREEN, RED, new_frame
from app.modules.base import Module, RenderContext


GROUP_COLORS = (RED, GREEN, BLUE, AMBER)
FADE_SECONDS = .35
GROUP_COLUMNS = (1, 3, 2, 3)
GROUP_GRIDS = tuple(
    tuple((column, row) for row in range(3) for column in range(columns))
    for columns in GROUP_COLUMNS
)


def shade(color, amount):
    return tuple(round(channel * amount) for channel in color)


def highlight(color):
    return tuple(round(channel + (255 - channel) * .32) for channel in color)


def tix_digits(now, hour_format="12"):
    """Return the four digits represented by the groups, e.g. 12:34 -> (1,2,3,4)."""
    hour = now.hour if hour_format == "24" else (now.hour % 12 or 12)
    return tuple(int(character) for character in f"{hour:02d}{now.minute:02d}")


@lru_cache(maxsize=64)
def _patterns(group, digit):
    return tuple(frozenset(combo) for combo in combinations(GROUP_GRIDS[group], digit))


@lru_cache(maxsize=256)
def _cycle(group, digit, cycle):
    """One shuffled pass over every possible arrangement. Walking whole passes
    means a pattern never returns until all the others have been shown."""
    order = list(range(len(_patterns(group, digit))))
    # No global RNG state: rendering remains reproducible and testable.
    random.Random(cycle * 7919 + group * 9176 + digit * 101).shuffle(order)
    return order


def lit_positions(digit, bucket, group):
    """Choose the represented count from the field's original-size grid."""
    if not 0 <= group < len(GROUP_GRIDS):
        raise ValueError("TIX group must be in the range 0–3")
    grid = GROUP_GRIDS[group]
    if not 0 <= digit <= len(grid):
        raise ValueError("TIX digit does not fit its field")
    patterns = _patterns(group, digit)
    count = len(patterns)
    if count == 1:  # All or nothing lit: the field simply stays solid.
        return patterns[0]
    cycle, index = divmod(bucket, count)
    order = _cycle(group, digit, cycle)
    choice = order[index]
    # A new pass must not open with the pattern the last one ended on.
    if index <= 1 and count > 2 and _cycle(group, digit, cycle - 1)[-1] == order[0]:
        choice = order[1 - index]
    return patterns[choice]


class TixClockModule(Module):
    name = "tixclock"

    def refresh_interval(self, context: RenderContext) -> float:
        # Animate only while squares cross-fade just after each reshuffle.
        interval = min(60, max(1, float(context.config["modules"][self.name]["update_interval"])))
        into = time.time() % interval
        return 1 / context.config["display"]["fps"] if into < FADE_SECONDS + .05 else max(.05, interval - into)

    def render(self, context: RenderContext):
        settings = context.config["modules"][self.name]
        interval = float(settings["update_interval"])
        now, stamp = context.now, time.time()
        # The fade tracks the live clock; fixed timestamps (exports, tests) render settled.
        live = abs(stamp - now.timestamp()) < 2
        if live:
            # context.now is taken once per wall second, a moment before this
            # frame. Read both the pattern and its fade from one instant, or a
            # frame straddling the boundary flashes the pattern from two ago.
            now += timedelta(seconds=int(stamp) - int(now.timestamp()))
            bucket = int(stamp // interval)
            progress = min(1.0, (stamp % interval) / FADE_SECONDS)
        else:
            bucket = int(now.timestamp() // interval)
            progress = 1.0
        digits = tix_digits(now, settings["hour_format"])
        previous = tix_digits(now - timedelta(seconds=interval), settings["hour_format"])
        frame = new_frame()
        draw = ImageDraw.Draw(frame)

        # Field proportions match the physical clock. With the label hidden,
        # 7×7 cells use nearly the full panel height without adding a title.
        labelled = settings["show_label"]
        cell, step = (5, 6) if labelled else (7, 8)
        panel_widths = tuple(columns * step + 3 for columns in GROUP_COLUMNS)
        panel_top, panel_bottom = (1, 23) if labelled else (1, 29)
        grid_top = 4
        gaps = (4, 10, 4) if labelled else (5, 12, 5)
        total_width = sum(panel_widths) + sum(gaps)
        group_xs = [int((128 - total_width) / 2)]
        for panel_width, gap in zip(panel_widths, gaps):
            group_xs.append(group_xs[-1] + panel_width + gap)

        for group, digit in enumerate(digits):
            group_x = group_xs[group]
            panel_width = panel_widths[group]
            color = GROUP_COLORS[group]
            draw.rounded_rectangle(
                (group_x, panel_top, group_x + panel_width - 1, panel_bottom),
                radius=2,
                fill=(2, 5, 8),
                outline=shade(color, .24),
            )
            lit = lit_positions(digit, bucket, group)
            before = lit if progress >= 1 else lit_positions(previous[group], bucket - 1, group)
            for column, row in GROUP_GRIDS[group]:
                x = group_x + 2 + column * step
                y = grid_top + row * step
                box = (x, y, x + cell - 1, y + cell - 1)
                on_now, on_before = (column, row) in lit, (column, row) in before
                level = 1.0 if on_now and on_before else progress if on_now else (1 - progress) if on_before else 0.0
                if level <= 0:
                    draw.rectangle(box, fill=shade(color, .075))
                    continue
                if level < 1:
                    draw.rectangle(box, fill=mix(shade(color, .075), color, level))
                    continue
                draw.rectangle(box, fill=color)
                shine = highlight(color)
                draw.line((x, y, x + cell - 1, y), fill=shine)
                draw.line((x, y, x, y + cell - 1), fill=shine)
                draw.point((x + cell - 1, y + cell - 1), fill=shade(color, .62))

        if settings["show_label"]:
            centered(frame, "TIX", 25, shade((180, 190, 190), .72))
        return frame
