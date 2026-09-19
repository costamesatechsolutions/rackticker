"""Custom alerts: an amber title bar over the message, which flies in with an
LED effect (or crawls when it is too long) and holds until fully shown."""
import random

from PIL import ImageDraw

from app.modules.base import Module
from app.core.fonts import centered, text_width
from app.core.fx import Lettering
from app.core.renderer import new_frame, AMBER

ENTRANCES = ("assemble", "drop", "slot", "sparkle", "scroll_stop", "split")


class MessageModule(Module):
    name = "message"
    event_priority = 30

    def __init__(self):
        self.cache = (None, None)

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _lettering(self, context):
        m = context.message
        key = (m.title, m.body, m.scrolling, context.scene)
        if self.cache[0] != key:
            rng = random.Random()
            effect = rng.choice(ENTRANCES) if m.scrolling else "sparkle"
            self.cache = (key, Lettering(m.body, effect, ((255, 255, 255), AMBER), "solid",
                                         rng.randrange(1 << 30), hold=4, y=12))
        return self.cache[1]

    def hold(self, context):
        return context.animation_time < self._lettering(context).duration

    def render(self, context):
        frame = new_frame()
        lettering = self._lettering(context)
        # Letters that fly in from above pass behind the title bar, not over it.
        lettering.draw(frame, context.animation_time % lettering.duration)
        ImageDraw.Draw(frame).rectangle((0, 0, 127, 9), fill=AMBER)
        title = context.message.title
        while text_width(title) > 124:
            title = title[:-1]
        centered(frame, title, 1, (0, 0, 0))
        return frame
