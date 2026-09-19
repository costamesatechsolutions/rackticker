"""Panel colour test: five labelled colour blocks. If a block does not match its
label, the panel's colour order differs from the default; set
--led-rgb-sequence in /etc/rackticker/matrix.conf (e.g. RBG when GREEN shows blue)."""
from PIL import ImageDraw

from app.core.fonts import draw_tiny, tiny_width
from app.modules.base import Module
from app.core.renderer import new_frame

SWATCHES = (("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)), ("BLUE", (0, 0, 255)),
            ("WHITE", (255, 255, 255)), ("YELLOW", (255, 220, 0)))


class TestPatternModule(Module):
    name = "test_pattern"

    def render(self, context):
        frame = new_frame()
        draw = ImageDraw.Draw(frame)
        width = 128 // len(SWATCHES)
        for index, (label, color) in enumerate(SWATCHES):
            x = index * width + (128 - width * len(SWATCHES)) // 2
            draw.rectangle((x + 1, 1, x + width - 2, 22), fill=color)
            draw_tiny(frame, label, x + width // 2 - tiny_width(label) // 2, 26, (255, 255, 255))
        return frame
