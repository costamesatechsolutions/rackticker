from app.modules.base import Module
from app.core.fonts import draw_text
from app.core.renderer import new_frame, MUTED, GREEN, RED, AMBER


class SystemStatusModule(Module):
    name = "system_status"

    def render(self, context):
        frame = new_frame()
        status = context.system
        for y, name, ok in ((1, "INTERNET", status.internet), (12, "HA", status.home_assistant)):
            draw_text(frame, name, 3, y, MUTED)
            draw_text(frame, "✓ OK" if ok else "× OFF", 84, y, GREEN if ok else RED)
        unit = context.config["modules"][self.name]["temperature_unit"]
        temp = status.rack_temp_c * 9 / 5 + 32 if unit == "F" else status.rack_temp_c
        draw_text(frame, "RACK", 3, 23, MUTED)
        draw_text(frame, f"{round(temp)}°{unit}", 84, 23, RED if status.rack_temp_c >= 35 else AMBER)
        return frame
