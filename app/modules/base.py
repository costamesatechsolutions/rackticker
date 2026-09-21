from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from PIL import Image
from app.core.models import Snapshot, Message, SystemStatus
from app.core.fonts import centered, draw_text, draw_tiny, tiny_width
from app.core.renderer import new_frame, RED, MUTED


@dataclass
class RenderContext:
    now: datetime
    animation_time: float
    config: dict
    snapshots: dict[str, Snapshot]
    message: Message
    system: SystemStatus
    # Changes whenever a new scene starts (next screen, preview, interrupt), so
    # stateful screens can reset even if two scenes both begin at time zero.
    scene: int = 0


class Module(ABC):
    name = ""
    event_priority = 10

    def available(self, context: RenderContext) -> bool:
        return True

    def refresh_interval(self, context: RenderContext) -> float:
        """Infinity for static data; runtime invalidates these on data/config changes."""
        return float("inf")

    def ready(self, context: RenderContext) -> bool:
        """False while the screen has not yet got its first picture together (an installed
        plugin's process is still drawing it); the display keeps the previous screen up
        rather than flash an empty frame."""
        return True

    def hold(self, context: RenderContext) -> bool:
        """Return True while mid-story (a crawl lap, a headline) so the playlist
        waits for it to finish instead of cutting it off. Bounded by the scheduler."""
        return False

    @abstractmethod
    def render(self, context: RenderContext) -> Image.Image:
        pass


def missing(title):
    frame = new_frame()
    centered(frame, title, 5, MUTED)
    centered(frame, "NO DATA", 18)
    return frame


def stale_marker(frame, snapshot):
    if snapshot.stale:
        # Never silently present cached data as live, but a feed hiccup should
        # not wipe a quarter of the layout: a compact corner badge says it.
        label = "CACHED"
        width = tiny_width(label) + 3
        frame.paste((60, 0, 0), (128 - width, 26, 128, 32))
        draw_tiny(frame, label, 128 - width + 2, 27, RED)
    return frame
