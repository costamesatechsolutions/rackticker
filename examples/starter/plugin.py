"""Starter plugin: where the International Space Station is right now.

Keep the shape, replace the content. A plugin is:
  a Provider  fetches data every few seconds (network allowed here, only here)
  a Module    draws one 128×32 frame from that data (fast, no I/O)
  plugin      ties them together with settings the web page can edit
"""
import aiohttp

from rackticker import (AMBER, GREEN, MUTED, WHITE, Module, Plugin, Provider, Snapshot, centered, draw_text,
                        new_frame, text_width)

URL = "https://api.wheretheiss.at/v1/satellites/25544"


class Station(Provider):
    def __init__(self, context):
        self.context = context      # context.settings: this plugin's current settings
        self.session = None

    async def fetch(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4))
        async with self.session.get(URL) as response:
            response.raise_for_status()
            data = await response.json()
        return Snapshot({"lat": data["latitude"], "lon": data["longitude"],
                         "speed": data["velocity"], "sunlit": data["visibility"] == "daylight"})

    async def close(self):
        if self.session:
            await self.session.close()


class Screen(Module):
    name = "starter_plugin"         # must match the plugin id

    def available(self, context):
        # False skips this screen in the playlist until there is something to show.
        return bool(context.snapshots.get(self.name))

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]     # animated: draw every frame

    def render(self, context):
        frame = new_frame()
        snap = context.snapshots.get(self.name)
        if not snap or not snap.data:
            centered(frame, "NO SIGNAL", 12)
            return frame
        iss, settings = snap.data, context.config["plugins"][self.name]
        speed = iss["speed"] * (0.621371 if settings["units"] == "mph" else 1)
        draw_text(frame, "ISS", 0, 0, AMBER)
        draw_text(frame, "Sunlit" if iss["sunlit"] else "Night side", 22, 0, MUTED, mixed=True)
        # The headline number is big (2×) so it reads from across the room.
        big = f"{speed:,.0f}"
        draw_text(frame, big, 0, 10, WHITE, 2, True)
        draw_text(frame, settings["units"].upper(), text_width(big, 2) + 4, 17, MUTED)
        where = f"{abs(iss['lat']):.0f}°{'N' if iss['lat'] >= 0 else 'S'} {abs(iss['lon']):.0f}°{'E' if iss['lon'] >= 0 else 'W'}"
        draw_text(frame, where, 128 - text_width(where), 25, GREEN)
        # A little orbit marker sweeping under the header: time comes from
        # context.animation_time, never from time.time().
        x = int(context.animation_time * 20) % 128
        frame.putpixel((x, 8), AMBER)
        return frame


plugin = Plugin(
    "starter_plugin", "Starter plugin", module=Screen, provider=Station,
    defaults={"units": "mph"},
    choices={"units": ("mph", "kmh")},
    help={"units": "Speed units"},
)
