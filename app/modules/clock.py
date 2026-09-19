import math
import time
from datetime import timedelta

from PIL import ImageDraw

from app.modules.base import Module
from app.core.fonts import centered, draw_text, glyph_width, text_width
from app.core.fx import draw_glyph, ease_out
from app.core.renderer import new_frame, AMBER, BLUE, MUTED, WHITE

ROLL_SECONDS = .3


LEAF = (110, 220, 90)


def _smoke(frame, stamp):
    """It's 4:20 somewhere: soft wisps curling up in the free corner on the right."""
    for wisp in range(7):
        phase = (stamp * .3 + wisp / 7) % 1
        wx = 118 + round(math.sin(phase * 6.5 + wisp * 1.7) * 3)
        wy = 21 - round(phase * 20)
        level = round(190 * (1 - phase) ** .7)
        for dx, dy in ((0, 0), (1, 0), (0, -1)):
            if 0 <= wx + dx < 128 and 0 <= wy + dy < 32:
                frame.putpixel((wx + dx, wy + dy), (level, level, level + 12))


def clock_text(now, twelve, seconds):
    text = f"{(now.hour % 12 or 12) if twelve else now.hour:02d}:{now.minute:02d}"
    if twelve:
        text = text.lstrip("0")
    return text + (f":{now.second:02d}" if seconds else "")


class ClockModule(Module):
    name = "clock"

    def refresh_interval(self, context):
        # Animate only while digits roll just after each second ticks over.
        if context.config["modules"][self.name]["style"] != "desk":
            return 1
        into = time.time() % 1
        return 1 / context.config["display"]["fps"] if into < ROLL_SECONDS + .05 else max(.02, 1 - into)

    def render(self, context):
        frame = new_frame()
        settings = context.config["modules"][self.name]
        twelve = settings["hour_format"] == "12"
        now = context.now
        suffix = now.strftime("%p") if twelve else "24H"
        if settings["style"] == "desk":
            return self._desk(frame, now, twelve, suffix, settings["show_seconds"])
        clock = clock_text(now, twelve, settings["show_seconds"])
        scale = 2 if settings["show_seconds"] else 3
        width = text_width(clock, scale)
        x = max(1, (128 - width - (16 if twelve else 0)) // 2)
        draw_text(frame, clock, x, 1 if scale == 3 else 4, AMBER, scale)
        if twelve:
            draw_text(frame, suffix, x + width + 4, 15 if scale == 3 else 11, AMBER)
        centered(frame, now.strftime("%a %b ") + str(now.day), 25, MUTED)
        return frame

    @staticmethod
    def _desk(frame, now, twelve, suffix, seconds):
        # context.now is refreshed once per wall second, a moment before this
        # frame's own timestamp. Align both to one instant, or the digits roll
        # from a stale second right at the boundary and visibly flicker.
        stamp = time.time()
        lag = int(stamp) - int(now.timestamp())
        if 0 <= lag <= 2:
            now += timedelta(seconds=lag)
            into = stamp % 1
        else:
            into = 1.0
        clock = clock_text(now, twelve, seconds)
        before = clock_text(now - timedelta(seconds=1), twelve, seconds)
        scale = 3 if text_width(clock, 3) <= 94 else 2
        height = 7 * scale
        top = 1 if scale == 3 else 5
        width = text_width(clock, scale)
        x = max(1, (128 - width - (text_width(suffix) + 4 if twelve else 0)) // 2)
        progress = ease_out(into / ROLL_SECONDS)
        cursor, section = x, 0
        colors = (WHITE, BLUE, AMBER)
        four_twenty = now.hour % 12 == 4 and now.minute == 20
        if four_twenty:
            colors = (LEAF, LEAF, AMBER)
        for index, char in enumerate(clock):
            color = MUTED if char == ":" else colors[min(section, len(colors) - 1)]
            old = before[index] if len(before) == len(clock) else char
            if old != char and progress < 1:
                # Old digit rolls up and out while the new one rises into place.
                shift = round(progress * (height + 2))
                clip = (top, top + height)
                draw_glyph(frame, old, cursor, top - shift, color, scale, False, clip)
                draw_glyph(frame, char, cursor, top + height + 2 - shift, color, scale, False, clip)
            else:
                draw_text(frame, char, cursor, top, color, scale)
            cursor += (glyph_width(char) + 1) * scale
            if char == ":":
                section += 1
        if twelve:
            draw_text(frame, suffix, cursor + 2, 4 if scale == 3 else 8, MUTED)
        if four_twenty:
            _smoke(frame, stamp)
        # One blank row separates the date from the seconds rail on row 31.
        centered(frame, now.strftime("%a %b ").upper() + str(now.day), 23 if scale == 3 else 22, MUTED)
        if seconds:
            draw = ImageDraw.Draw(frame)
            for second in range(60):
                draw.point((4 + second * 2, 31), fill=BLUE if second <= now.second else (10, 24, 32))
        return frame
