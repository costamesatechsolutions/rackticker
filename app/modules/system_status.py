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
            draw_text(frame, "\u2713 OK" if ok else "\u00d7 OFF", 84, y, GREEN if ok else RED)
        unit = context.config["modules"][self.name]["temperature_unit"]
        temp = status.rack_temp_c * 9 / 5 + 32 if unit == "F" else status.rack_temp_c
        # The bottom line is the temperature until something needs saying; a failed
        # update has to reach the people who never open the control page.
        if status.notice:
            draw_text(frame, status.notice, 3, 23, RED)
        else:
            draw_text(frame, "RACK", 3, 23, MUTED)
            # A Pi runs warm by design: amber once it is working hard, red near throttling.
            heat = RED if status.rack_temp_c >= 75 else AMBER if status.rack_temp_c >= 65 else GREEN
            draw_text(frame, f"{round(temp)}\u00b0{unit}", 84, 23, heat)
        return frame
