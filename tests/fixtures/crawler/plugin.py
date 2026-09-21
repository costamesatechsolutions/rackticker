import math
import time

from rackticker import Module, Plugin, new_frame


class Crawl(Module):
    name = "crawler"

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def render(self, context):
        if context.config["plugins"]["crawler"].get("lag"):
            time.sleep(0.004)
        frame = new_frame()
        frame.putpixel((math.floor(context.animation_time * 30 + 1e-6) % 128, 5), (255, 255, 255))
        return frame


plugin = Plugin("crawler", "Crawler", module=Crawl, defaults={"lag": False})
