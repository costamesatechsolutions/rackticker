"""Output boundary. Every sink receives the same unmodified 128×32 RGB image.

Brightness is output intensity, like HUB75 PWM duty, not an edit to pixel data.
PNG exports therefore retain the original RGB values at every brightness.
Sinks must not mutate a received frame or block on slow external consumers.
"""
from abc import ABC, abstractmethod
from app.core.renderer import new_frame


class FrameSink(ABC):
    brightness = 100

    @abstractmethod
    async def display(self, frame):
        pass

    def set_brightness(self, value):
        if not 0 <= value <= 100:
            raise ValueError("Brightness must be 0–100")
        self.brightness = value

    async def clear(self):
        await self.display(new_frame())

    @abstractmethod
    async def close(self):
        pass
