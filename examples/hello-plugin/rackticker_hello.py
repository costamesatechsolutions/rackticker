"""Copy this package to your own GitHub repository. No core edits required.

A marquee sign in about forty lines: each phrase flies in with a random LED
effect inside chasing bulbs, and the playlist waits for the phrase on screen.
"""
import random

from rackticker import EFFECTS, Lettering, Module, Plugin, Storyboard, bulb_border, new_frame


def phrases(settings):
    return [part.strip() for part in settings["message"].split("|") if part.strip()]


class Hello(Module):
    name = "hello"

    def __init__(self):
        # resume=False: start from the first phrase on every playlist visit.
        self.board = Storyboard(resume=False)

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]  # Animated: redraw every frame.

    def _current(self, context):
        settings = context.config["plugins"][self.name]

        def build(_visit):
            rng = random.Random()  # A fresh seed per visit: never the same show twice.
            letterings = [Lettering(text, rng.choice(EFFECTS), ((255, 178, 89), (90, 200, 255)),
                                    "alternate", rng.randrange(1 << 30)) for text in phrases(settings)]
            return [(lettering, lettering.duration) for lettering in letterings]
        self.board.sync(context.animation_time, build, context.scene)
        return self.board.current(context.animation_time, build)

    def hold(self, context):
        return bool(self._current(context)) and self.board.hold()

    def render(self, context):
        frame = new_frame()
        current = self._current(context)
        if current:
            lettering, local, _ = current
            lettering.draw(frame, local)
        bulb_border(frame, context.animation_time, ((255, 178, 89), (255, 255, 255)))
        return frame


def migrate(settings):
    title = settings.pop("title", None)  # Version 1 had separate title/message fields.
    if title and "message" in settings:
        settings["message"] = f"{title}|{settings['message']}"
    return settings


def validate(settings):
    items = phrases(settings)
    if not 1 <= len(items) <= 8 or any(len(item) > 20 for item in items):
        raise ValueError("message must be 1–8 phrases of up to 20 characters, separated by |")


plugin = Plugin(name="hello", label="Hello rack", module=Hello,
                defaults={"message": "HELLO RACK|MAKE IT YOURS"},
                validate_settings=validate, migrate_settings=migrate)
