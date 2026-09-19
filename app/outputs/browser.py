"""Latest-frame fan-out: a slow browser can never backpressure the renderer.

Wire format: one binary WebSocket message = 12,288 bytes, row-major RGB888.
Text messages contain status JSON; rendering requires only the binary payload.
Each browser has one notification Event, not an unbounded frame queue.
"""
import asyncio
import logging

from app.outputs.base import FrameSink
from app.core.renderer import validate_frame, new_frame

log = logging.getLogger("output")


class BrowserSink(FrameSink):
    def __init__(self):
        self.pixels = new_frame().tobytes()
        self.sequence = 0
        self.state = {}
        self.state_revision = 0
        self.listeners = set()
        self.closed = False

    def _notify(self):
        for listener in self.listeners:
            listener.set()

    async def display(self, frame):
        pixels = validate_frame(frame).tobytes()
        if pixels != self.pixels:
            self.pixels = pixels
            self.sequence += 1
            self._notify()

    def set_state(self, state):
        self.state = state
        self.state_revision += 1
        self._notify()

    def subscribe(self):
        event = asyncio.Event()
        event.set()
        self.listeners.add(event)
        log.info("BrowserSink connected clients=%s", len(self.listeners))
        return event

    def unsubscribe(self, event):
        self.listeners.discard(event)
        log.info("BrowserSink disconnected clients=%s", len(self.listeners))

    async def close(self):
        self.closed = True
        self._notify()
