"""Public plugin API for RackTicker (API version 1).

Everything a community screen needs lives here: the plugin contract, the
canonical 128×32 frame, bitmap fonts, LED effects and a storyboard for
headline-style content. Import from `rackticker`, never from `app.*`, so your
plugin keeps working as the core evolves.
"""
from app.plugin_api import Plugin, PluginContext, Module, RenderContext, Provider, FrameSink, Snapshot
from app.core.renderer import (new_frame, validate_frame, WIDTH, HEIGHT, AMBER, BLUE, GREEN, MUTED, RED, WHITE,
                               crawl_once_x, loop_strip, scrolling_text, clipped_text)
from app.core.fonts import draw_text, centered, draw_tiny, text_width, tiny_width, wrap_text
from app.core.fx import (Lettering, Particles, EFFECTS, bounce, bulb_border, chase_bar, dim, ease_in, ease_in_out,
                         ease_out, hsv, mix, plot, sprite, stamp, triangle)
from app.core.story import Storyboard
from app.core.offload import offload
from app.core.models import Flight, Game, Team, Race, Message, SystemStatus, PriorityEvent

__all__ = ["Plugin", "PluginContext", "Module", "RenderContext", "Provider",
           "FrameSink", "Snapshot", "new_frame", "validate_frame", "WIDTH", "HEIGHT",
           "AMBER", "BLUE", "GREEN", "MUTED", "RED", "WHITE",
           "crawl_once_x", "loop_strip", "scrolling_text", "clipped_text",
           "draw_text", "centered", "draw_tiny", "text_width", "tiny_width", "wrap_text",
           "Lettering", "Particles", "EFFECTS", "bounce", "bulb_border", "chase_bar", "dim",
           "ease_in", "ease_in_out", "ease_out", "hsv", "mix", "plot", "sprite", "stamp", "triangle",
           "Storyboard", "offload",
           "Flight", "Game", "Team", "Race", "Message", "SystemStatus", "PriorityEvent"]
