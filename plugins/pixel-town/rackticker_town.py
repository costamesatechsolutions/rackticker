"""Pixel Town: a tiny living city that runs on real-world time.

The sky follows your clock (sunrise, sunset, moon and stars), office windows
light up as evening comes, the weather screen's conditions fall on the street,
aircraft from the flight feed fly over towing their callsign, and people, cars
and a taco truck keep the town busy. The town keeps living between visits.
"""
from __future__ import annotations

from functools import lru_cache
import math
import random
import re

from PIL import Image, ImageDraw

from rackticker import Plugin, Module, new_frame
from app.core.fonts import draw_tiny, tiny_width
from app.core.fx import dim, mix, plot, sprite, stamp

SKY_KEYS = ((0.0, (3, 5, 20), (12, 18, 44)), (5.2, (3, 5, 20), (12, 18, 44)),
            (6.3, (40, 28, 80), (220, 110, 64)), (7.6, (16, 52, 120), (80, 140, 190)),
            (16.8, (16, 52, 120), (80, 140, 190)), (18.3, (60, 28, 88), (232, 104, 56)),
            (19.5, (7, 9, 32), (28, 28, 66)), (24.0, (3, 5, 20), (12, 18, 44)))
WINDOW_KEYS = ((0, .26), (3, .05), (6, .14), (8, .08), (12, .04), (17, .2), (20, .62), (23, .38), (24, .26))
# Even at 4 AM a few night owls, cabs and delivery drivers keep the town alive.
BUSY_KEYS = ((0, .3), (5, .25), (7, .6), (12, .9), (18, .85), (22, .45), (24, .3))
SKINS = ((255, 214, 170), (224, 172, 120), (176, 120, 80), (120, 80, 52))
SHIRTS = ((230, 60, 60), (60, 140, 240), (250, 200, 60), (80, 200, 120), (240, 240, 240), (200, 90, 200))
CAR_COLORS = ((210, 40, 40), (230, 230, 230), (40, 90, 210), (250, 200, 30), (110, 110, 124), (40, 160, 90))
VAN_COLORS = ((235, 235, 228), (60, 150, 220), (235, 140, 40), (90, 170, 100))
# A taco truck: the taco on its roof, a striped awning over the serving window, a cab
# with its own window, and wheels. t shell, l lettuce, r salsa, a/s awning stripes.
TRUCK = ("....ttttt.........",
         "...tlrlrlt........",
         "..oooooooooooo....",
         ".oasasasasasao....",
         ".owyyyyyyyyywoooo.",
         ".owyyyyyyyyywobbo.",
         ".owwwwwwwwwwwobbo.",
         ".ooooooooooooooooo",
         "..gkg.......gkg...")
CAR = ("..ggggg..", ".cgggggc.", "ccccccccc", ".kk...kk.")
# A delivery van: taller box body, a cab window at the front, a logo panel.
VAN = ("..ccccccccc..", ".gcccccccccc.", "ccccccccccccc", ".kk.......kk.")
BIRD = ("w.w", ".w.")
# Shops at street level, so the pavement is somewhere people are going, not a strip
# of grey. Each one: where it starts, how wide, and the hour it closes.
SHOPS = ((6, 7, 21, (210, 70, 60)), (26, 6, 18, (70, 150, 210)),
         (48, 7, 23, (240, 180, 60)), (68, 6, 20, (80, 180, 120)))
PLANE = ("...w...", "wwwwwww", "..www..")
STREET_Y, TRUCK_X = 25, 92
# Customers queue at the serving window, on the pavement beside the truck, one
# behind the other. Three deep is all the space there is, and all it needs.
WINDOW_X, QUEUE_GAP, QUEUE_DEPTH = TRUCK_X - 4, 4, 3
SERVE_SECONDS = (2.5, 5.0)     # how long an order takes at the window
CARRY_SECONDS = 6.0            # how long the taco is still in hand afterwards


def shade(frame, pixels, x, y, factor=.4):
    """Darken one pixel. plot() adds light the way an LED does; a shadow is the
    opposite of that, so it has to be written rather than blended."""
    if 0 <= x < frame.size[0] and 0 <= y < frame.size[1]:
        red, green, blue = pixels[x, y]
        pixels[x, y] = (int(red * factor), int(green * factor), int(blue * factor))


def _curve(keys, hour):
    for (h0, *a), (h1, *b) in zip(keys, keys[1:]):
        if h0 <= hour <= h1:
            p = (hour - h0) / max(1e-6, h1 - h0)
            if isinstance(a[0], tuple):
                return tuple(mix(x, y, p) for x, y in zip(a, b))
            return a[0] + (b[0] - a[0]) * p
    return keys[-1][1:] if isinstance(keys[-1][1], tuple) else keys[-1][1]


@lru_cache(maxsize=4)
def sky_image(bucket):
    top, bottom = _curve(SKY_KEYS, bucket / 60)
    image = Image.new("RGB", (128, 32))
    draw = ImageDraw.Draw(image)
    for y in range(32):
        draw.line((0, y, 127, y), fill=mix(top, bottom, y / 31))
    return image


def city(seed=11):
    rng = random.Random(seed)
    buildings, x = [], 0
    while x < 128:
        # Fewer, broader buildings read as a skyline instead of visual noise.
        width, height = rng.randint(14, 24), rng.randint(8, 16)
        color = rng.choice(((34, 38, 52), (46, 40, 48), (30, 44, 54), (50, 46, 42), (38, 34, 46)))
        top = STREET_Y - height
        windows = tuple((wx, wy, rng.random(), rng.choice(((255, 206, 110), (255, 236, 170), (150, 200, 255))))
                        for wy in range(top + 2, STREET_Y - 1, 3) for wx in range(x + 2, min(127, x + width - 1), 3))
        buildings.append((x, width, top, color, windows))
        x += width + rng.randint(0, 2)
    return tuple(buildings)


BUILDINGS = city()
# The tallest building near the middle carries the town's rooftop sign.
SIGN = max((b for b in BUILDINGS if 30 <= b[0] <= 70), key=lambda b: STREET_Y - b[2],
           default=BUILDINGS[len(BUILDINGS) // 2])


@lru_cache(maxsize=4)
def skyline(bucket):
    hour = bucket / 12
    occupied = _curve(WINDOW_KEYS, hour) * .75
    daylight = 7.5 <= hour <= 17.5
    layer = Image.new("RGBA", (128, 32))
    draw = ImageDraw.Draw(layer)
    for x, width, top, color, windows in BUILDINGS:
        draw.rectangle((x, top, x + width - 1, STREET_Y - 1), fill=(*color, 255))
        draw.line((x, top, x + width - 1, top), fill=(*mix(color, (255, 255, 255), .18), 255))
        for wx, wy, threshold, glow in windows:
            if threshold < occupied:
                fill = glow
            elif daylight:
                fill = mix(color, (120, 150, 180), .18)
            else:
                fill = dim(color, .7)
            layer.putpixel((wx, wy), (*fill, 255))
    for pole in range(14, 128, 34):
        draw.line((pole, STREET_Y - 7, pole, STREET_Y - 1), fill=(90, 90, 100, 255))
        draw.point((pole + 1, STREET_Y - 7), fill=(90, 90, 100, 255))
    return layer


class Town(Module):
    name = "town"

    def __init__(self):
        self.rng = random.Random()
        self.people, self.cars, self.clouds = [], [], []
        self.plane, self.plane_wait = None, 3.0
        self.people_wait = self.car_wait = 0.0
        self.last_t, self.scene = None, None
        for _ in range(4):
            self._spawn_person(self.rng.uniform(0, 128))
        for lane in (0, 1):
            self._spawn_car(lane, self.rng.uniform(0, 120))
        self.clouds = [[self.rng.uniform(0, 128), self.rng.uniform(2, 9), self.rng.uniform(.8, 2.2)] for _ in range(4)]
        self.birds = []
        self.cat = None

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _spawn_person(self, x=None):
        direction = self.rng.choice((-1, 1))
        self.people.append({"x": x if x is not None else (-4.0 if direction > 0 else 131.0), "dir": direction,
                            "speed": self.rng.uniform(5, 10), "shirt": self.rng.choice(SHIRTS),
                            "skin": self.rng.choice(SKINS), "hungry": self.rng.random() < .45,
                            "pause": 0.0, "fed": False, "dog": self.rng.random() < .08,
                            "slot": None, "carry": 0.0})

    def _spawn_car(self, lane, x=None):
        direction = 1 if lane == 0 else -1
        # One vehicle in five is a van: something bigger to look at, and it holds
        # the traffic up behind it the way a van does.
        van = self.rng.random() < .2
        self.cars.append({"x": x if x is not None else (-14.0 if direction > 0 else 140.0), "dir": direction,
                          "lane": lane, "speed": self.rng.uniform(12, 20) if van else self.rng.uniform(18, 34),
                          "color": self.rng.choice(VAN_COLORS if van else CAR_COLORS), "van": van})

    @staticmethod
    def _slot_x(slot):
        """Where the person this far back in the queue stands."""
        return WINDOW_X - slot * QUEUE_GAP

    def _joins_queue(self, person):
        """A hungry passer-by tacks on to the back of the queue as they reach it."""
        taken = [p["slot"] for p in self.people if p["slot"] is not None]
        slot = len(taken)
        if slot >= QUEUE_DEPTH or slot in taken:
            return False
        # Only as they arrive at the spot, so nobody teleports to the window.
        if abs(person["x"] - self._slot_x(slot)) > person["speed"] * .12 + 1:
            return False
        person["slot"], person["pause"] = slot, 0.0
        return True

    def _queue_step(self, person, dt, rng, truck_open):
        """Shuffle up, wait your turn, take the taco and go."""
        target = self._slot_x(person["slot"])
        if not truck_open:                      # the shutter came down: everyone drifts off
            person["slot"], person["fed"] = None, True
            return
        if abs(person["x"] - target) > .5:      # walk up to your place in the queue
            person["x"] += math.copysign(min(abs(target - person["x"]), person["speed"] * dt), target - person["x"])
            return
        person["x"] = target
        if person["slot"] > 0:                  # not your turn yet
            return
        if person["pause"] <= 0:
            person["pause"] = rng.uniform(*SERVE_SECONDS)
            return
        person["pause"] -= dt
        if person["pause"] > 0:
            return
        person["slot"], person["fed"], person["carry"] = None, True, CARRY_SECONDS
        for other in self.people:               # everyone behind takes a step forward
            if other["slot"]:
                other["slot"] -= 1

    def _simulate(self, dt, hour, weather, flight):
        rng = self.rng
        busy = _curve(BUSY_KEYS, hour)
        wet = weather in ("rain", "storm", "snow")
        truck_open = 11 <= hour < 14 or 17 <= hour < 22
        self.people_wait -= dt
        if self.people_wait <= 0 and len(self.people) < 6:
            self._spawn_person()
            self.people_wait = rng.uniform(1.2, 5) / max(.12, busy)
        for person in self.people:
            if person["carry"] > 0:
                person["carry"] -= dt
            if person["slot"] is not None:
                self._queue_step(person, dt, rng, truck_open)
                continue
            if truck_open and person["hungry"] and not person["fed"] and self._joins_queue(person):
                continue
            person["x"] += person["dir"] * person["speed"] * dt * (1.5 if wet else 1)
        self.people = [p for p in self.people if -8 < p["x"] < 136]
        self.car_wait -= dt
        if self.car_wait <= 0:
            lane = rng.randrange(2)
            if not any(c["lane"] == lane and (c["x"] < 14 if lane == 0 else c["x"] > 114) for c in self.cars):
                self._spawn_car(lane)
            self.car_wait = rng.uniform(.8, 4) / max(.15, busy)
        for car in self.cars:
            ahead = [c for c in self.cars if c is not car and c["lane"] == car["lane"]
                     and 0 < (c["x"] - car["x"]) * car["dir"] < 14]
            speed = min(car["speed"], min((c["speed"] for c in ahead), default=car["speed"]))
            car["x"] += car["dir"] * speed * dt
        self.cars = [c for c in self.cars if -14 < c["x"] < 142]
        for cloud in self.clouds:
            cloud[0] = (cloud[0] + cloud[2] * dt) % 150
        daylight = 6.5 < hour < 19.5
        if daylight and not self.birds and rng.random() < dt * .18:
            direction = rng.choice((-1, 1))
            flock = rng.randrange(2, 4)
            self.birds = [{"x": (-6.0 if direction > 0 else 134.0) - n * rng.uniform(4, 7) * direction,
                           "y": rng.uniform(3, 9), "dir": direction, "speed": rng.uniform(9, 14)}
                          for n in range(flock)]
        for bird in self.birds:
            bird["x"] += bird["dir"] * bird["speed"] * dt
            bird["y"] += math.sin(bird["x"] * .12) * dt * 1.2
        self.birds = [b for b in self.birds if -10 < b["x"] < 138] if daylight else []
        # Somebody has to be out at 3 AM, and in a town this size it is a cat.
        if self.cat:
            self.cat["x"] += self.cat["dir"] * 11 * dt
            if not -6 < self.cat["x"] < 134:
                self.cat = None
        elif not daylight and rng.random() < dt * .05:
            direction = rng.choice((-1, 1))
            self.cat = {"x": -5.0 if direction > 0 else 133.0, "dir": direction}
        if self.plane:
            self.plane["x"] += self.plane["dir"] * 16 * dt
            if not -60 < self.plane["x"] < 190:
                self.plane, self.plane_wait = None, rng.uniform(8, 16)
        else:
            self.plane_wait -= dt
            if self.plane_wait <= 0:
                direction = rng.choice((-1, 1))
                label = flight.callsign if flight else ""
                self.plane = {"x": -10.0 if direction > 0 else 138.0, "dir": direction, "label": label}

    def render(self, context):
        t = context.animation_time
        if self.last_t is None or context.scene != self.scene or t < self.last_t:
            dt, self.scene = 1 / 30, context.scene
        else:
            dt = min(.2, t - self.last_t)
        self.last_t = t
        now = context.now
        hour = now.hour + now.minute / 60 + now.second / 3600
        weather_snap = context.snapshots.get("weather")
        weather = weather_snap.data if weather_snap and isinstance(weather_snap.data, dict) else {}
        kind = weather.get("icon", "sun")
        flight_snap = context.snapshots.get("flight")
        flight = flight_snap.data if flight_snap and not flight_snap.stale else None
        self._simulate(dt, hour, kind, flight)
        frame = sky_image(math.floor(hour * 60)).copy()
        draw = ImageDraw.Draw(frame)
        pixels = frame.load()
        self._celestial(frame, draw, pixels, hour, t, kind)
        self._clouds(draw, kind)
        self._birds(frame, pixels, self.birds, t)
        self._plane(frame, pixels, t)
        frame.paste(skyline(math.floor(hour * 12)), (0, 0), skyline(math.floor(hour * 12)))
        self._sign(frame, draw, now, weather, t, context.config["plugins"][self.name]["town_name"])
        night = hour < 6.5 or hour > 19
        self._street(frame, draw, pixels, night, hour, t)
        self._weather(frame, draw, pixels, kind, t)
        return frame

    @staticmethod
    def _celestial(frame, draw, pixels, hour, t, kind):
        overcast = kind in ("cloud", "rain", "storm", "snow", "fog")
        if 6 <= hour <= 18:
            p = (hour - 6) / 12
            x, y = round(4 + p * 120), round(17 - math.sin(p * math.pi) * 14)
            color = (255, 214, 90) if not overcast else (200, 190, 150)
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)
        else:
            p = ((hour - 18) % 24) / 12
            x, y = round(4 + p * 120), round(17 - math.sin(p * math.pi) * 13)
            if not overcast:
                for n in range(12):
                    sx, sy = (n * 53) % 128, (n * 29) % 14
                    # Steady stars: on/off twinkling reads as flicker on LEDs.
                    plot(frame, pixels, sx, sy, (150, 160, 200) if n % 3 else (104, 112, 146))
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(236, 232, 200))
            draw.ellipse((x - 1, y - 3, x + 3, y + 1), fill=pixels[0, max(0, y - 3)])

    def _clouds(self, draw, kind):
        count = {"sun": 0, "partly": 1, "cloud": 2, "fog": 2, "rain": 2, "storm": 3, "snow": 2}.get(kind, 0)
        color = (80, 84, 96) if kind in ("rain", "storm") else (180, 190, 205)
        for x, y, _ in self.clouds[:count]:
            x, y = round(x) - 12, round(y)
            draw.ellipse((x, y + 1, x + 7, y + 5), fill=color)
            draw.ellipse((x + 4, y - 1, x + 12, y + 5), fill=color)
            draw.rectangle((x + 2, y + 3, x + 11, y + 5), fill=color)

    def _plane(self, frame, pixels, t):
        if not self.plane:
            return
        x, y = round(self.plane["x"]), 3
        stamp(frame, sprite(PLANE, {"w": (230, 232, 240)}, flip=self.plane["dir"] < 0), x, y)
        blink = math.floor(t * 1.2) % 2
        plot(frame, pixels, x + (6 if self.plane["dir"] > 0 else 0), y + 1, (255, 60, 60) if blink else (60, 255, 90))
        label = self.plane["label"]
        if label:
            width = tiny_width(label) + 2
            bx = x - width - 2 if self.plane["dir"] > 0 else x + 9
            for rope in range(2):
                plot(frame, pixels, bx + (width + rope if self.plane["dir"] > 0 else -1 - rope), y + 3, (120, 120, 130))
            ImageDraw.Draw(frame).rectangle((bx, y + 1, bx + width - 1, y + 7), fill=(240, 236, 220))
            draw_tiny(frame, label, bx + 1, y + 2, (200, 40, 40))

    @staticmethod
    def _sign(frame, draw, now, weather, t, town_name):
        x, width, top, _, _ = SIGN
        items = [f"{now.hour % 12 or 12}:{now.minute:02d}"]
        if "temperature" in weather:
            items.append(f"{weather['temperature']}°")
        items.append(town_name)
        text = items[math.floor(t / 6) % len(items)]
        # One fixed sign size for every message, so the sign never jumps.
        sign_width = max(tiny_width(item) for item in (*items, "12:59", "100°")) + 6
        sx = max(0, min(127 - sign_width, x + width // 2 - sign_width // 2))
        draw.rectangle((sx, top - 9, sx + sign_width - 1, top - 2), fill=(16, 14, 18), outline=(70, 60, 40))
        # Its legs stand on its own roof, even when the sign is wider than the building.
        for leg in (max(sx + 2, x + 1), min(sx + sign_width - 3, x + width - 2)):
            draw.line((leg, top - 1, leg + 1, top - 1), fill=(70, 60, 40))
        draw_tiny(frame, text, sx + sign_width // 2 - tiny_width(text) // 2, top - 8, (255, 176, 20))

    def _street(self, frame, draw, pixels, night, hour, t):
        # A pavement for people to walk on. Without it they were drawn against
        # whatever window happened to be behind them and read as floating specks.
        draw.rectangle((0, STREET_Y - 6, 127, STREET_Y - 1), fill=(38, 38, 46) if night else (52, 52, 60))
        draw.rectangle((0, STREET_Y - 6, 127, STREET_Y - 6), fill=(58, 58, 68) if night else (74, 74, 84))
        self._shops(draw, hour)
        draw.rectangle((0, STREET_Y, 127, STREET_Y), fill=(70, 70, 76))
        draw.rectangle((0, STREET_Y + 1, 127, 31), fill=(24, 24, 28))
        for x in range(0, 128, 8):
            draw.line((x, 29, x + 3, 29), fill=(90, 80, 40))
        if night:
            for pole in range(14, 128, 34):
                plot(frame, pixels, pole + 1, STREET_Y - 7, (255, 220, 140))
                for spread in range(-2, 3):
                    plot(frame, pixels, pole + 1 + spread, STREET_Y, (110, 96, 64))
        truck_open = 11 <= hour < 14 or 17 <= hour < 22
        # Open: the roof taco and serving window light up and the awning's bulbs chase.
        # Closed: the shutter is down and the sign is dark, but it is still a taco truck.
        chase = truck_open and math.floor(t * 2) % 2
        palette = {"o": (220, 60, 50), "w": (228, 228, 218), "k": (22, 22, 30), "b": (120, 180, 220),
                   "g": (120, 120, 128),
                   "y": (255, 204, 90) if truck_open else (70, 72, 80),
                   "t": (255, 190, 40) if truck_open else (110, 84, 30),
                   "l": (90, 220, 90) if truck_open else (40, 90, 40),
                   "r": (240, 60, 40) if truck_open else (110, 40, 30),
                   "a": (255, 255, 255) if chase else (220, 60, 50),
                   "s": (220, 60, 50) if chase else (255, 255, 255)}
        # People on the pavement pass BEHIND the truck, which is what a truck parked
        # at the kerb does to the view. Drawn over it, they walked through its side.
        for person in sorted(self.people, key=lambda p: p["x"]):
            self._person(frame, pixels, person, t)
        stamp(frame, sprite(TRUCK, palette), TRUCK_X, STREET_Y - len(TRUCK))
        if truck_open:
            self._truck_life(frame, pixels, t)
        if self.cat:
            self._cat(frame, pixels, self.cat, t)
        for lane in (0, 1):
            for car in self.cars:
                if car["lane"] == lane:
                    self._car(frame, pixels, car, night)

    @staticmethod
    def _shops(draw, hour):
        """Lit shopfronts at street level: somewhere for everyone to be walking to."""
        for x, width, closes, awning in SHOPS:
            open_now = 8 <= hour < closes
            glass = (255, 208, 130) if open_now else (46, 44, 56)
            draw.rectangle((x, STREET_Y - 5, x + width - 1, STREET_Y - 2), fill=(30, 30, 38))
            # Its own awning, so a row of shops is a street rather than a pattern.
            draw.rectangle((x, STREET_Y - 5, x + width - 1, STREET_Y - 5),
                           fill=awning if open_now else dim(awning, .35))
            draw.rectangle((x + 1, STREET_Y - 4, x + width - 2, STREET_Y - 3), fill=glass)
            # A doorway, dark whether the lights are on or not.
            draw.rectangle((x + width - 2, STREET_Y - 4, x + width - 2, STREET_Y - 2), fill=(24, 22, 30))

    @staticmethod
    def _birds(frame, pixels, birds, t):
        for bird in birds:
            flap = math.floor(t * 7 + bird["x"] * .3) % 2
            x, y = round(bird["x"]), round(bird["y"])
            plot(frame, pixels, x + 1, y + (1 if flap else 0), (228, 230, 238))
            for dx in (0, 2):
                plot(frame, pixels, x + dx, y + (0 if flap else 1), (228, 230, 238))

    @staticmethod
    def _truck_life(frame, pixels, t):
        """What an open taco truck looks like from across the street: somebody in the
        window, and steam off the griddle."""
        top = STREET_Y - len(TRUCK)
        cook = TRUCK_X + 6
        # Dark against the lit window, the way you actually see someone serving.
        # Written straight to the pixels: plot() blends like light, and shade is
        # the absence of light, so a silhouette drawn with it would never appear.
        for x, y in ((cook, top + 4), (cook - 1, top + 5), (cook, top + 5), (cook + 1, top + 5)):
            if 0 <= x < frame.size[0] and 0 <= y < frame.size[1]:
                pixels[x, y] = (86, 50, 30)
        # Three wisps on their own slow cycles, so the steam never pulses in time.
        for wisp in range(3):
            phase = (t * .8 + wisp * .37) % 1
            y = top + 2 - phase * 5
            if y < 0:
                continue
            x = cook + 2 + wisp + math.sin(phase * 4 + wisp) * 1.4
            plot(frame, pixels, round(x), round(y), dim((235, 238, 245), .85 - phase * .55))

    @staticmethod
    def _person(frame, pixels, person, t):
        x = round(person["x"])
        step = person["slot"] is None and person["pause"] <= 0 and math.floor(t * 6 + person["speed"]) % 2
        top = STREET_Y - 5
        plot(frame, pixels, x + 1, top, person["skin"])
        # A solid two-row body. One row over a single pixel drew a plus sign, which
        # is what a person a few pixels tall looks like when you skimp on the middle.
        for row in (top + 1, top + 2):
            for dx in range(3):
                plot(frame, pixels, x + dx, row, person["shirt"])
        for sx in range(3):   # a shadow at their feet is what puts them on the ground
            shade(frame, pixels, x + sx, top + 5)
        legs = ((x, x + 2) if not step else (x + 1,))
        for lx in legs:   # darker than the pavement, or the legs disappear into it
            for row in (top + 3, top + 4):
                if 0 <= lx < frame.size[0]:
                    pixels[lx, row] = (26, 26, 38)
        if person["carry"] > 0:   # walking away with the taco they just paid for
            hand = x + (3 if person["dir"] > 0 else -1)
            plot(frame, pixels, hand, top + 2, (255, 190, 40))
            plot(frame, pixels, hand, top + 1, (220, 60, 50))
        if person["dog"]:
            dx = x - 4 * person["dir"]
            for px, py in ((0, 1), (1, 1), (2, 1), (3, 1), (0, 2), (3, 2), (3 if person["dir"] > 0 else 0, 0)):
                plot(frame, pixels, dx + px, top + 2 + py, (170, 120, 70))

    @staticmethod
    def _cat(frame, pixels, cat, t):
        x, y = round(cat["x"]), STREET_Y - 2
        nose = 3 if cat["dir"] > 0 else 0
        for dx in range(4):
            plot(frame, pixels, x + dx, y + 1, (60, 58, 66))
        plot(frame, pixels, x + nose, y, (60, 58, 66))                  # head
        plot(frame, pixels, x + (0 if cat["dir"] > 0 else 3),
             y + round(math.sin(t * 4) * .5), (60, 58, 66))             # tail, flicking
        plot(frame, pixels, x + nose, y, (150, 220, 120) if math.floor(t * 2) % 2 else (60, 58, 66))

    @staticmethod
    def _car(frame, pixels, car, night):
        y = STREET_Y + 1 if car["lane"] == 0 else STREET_Y + 3
        x = round(car["x"])
        body = VAN if car.get("van") else CAR
        stamp(frame, sprite(body, {"c": car["color"], "g": (110, 170, 220), "k": (12, 12, 16)},
                            flip=car["dir"] < 0), x, y)
        length = len(body[0]) - 1
        front, back = (x + length, x) if car["dir"] > 0 else (x, x + length)
        plot(frame, pixels, back, y + 2, (255, 40, 40))
        if night:
            plot(frame, pixels, front, y + 2, (255, 244, 200))
            for reach in range(1, 5):
                plot(frame, pixels, front + reach * car["dir"], y + 2, dim((255, 230, 160), .5 - reach * .1))

    @staticmethod
    def _weather(frame, draw, pixels, kind, t):
        if kind in ("rain", "storm"):
            for n in range(24 if kind == "storm" else 16):
                x = (n * 37 + math.floor(t * 12)) % 128
                y = (n * 13 + t * 40 * (1 + n % 3 * .2)) % 34 - 2
                plot(frame, pixels, x, y, (90, 140, 230))
                plot(frame, pixels, x, y + 1, (60, 100, 180))
            if kind == "storm" and (t % 6.1) < .1:
                draw.line((70, 0, 64, 8, 69, 8, 62, 18), fill=(255, 250, 200))
        elif kind == "snow":
            for n in range(34):
                x = (n * 41 + round(math.sin(t + n) * 2)) % 128
                y = (n * 17 + t * 8) % 32
                plot(frame, pixels, x, y, (235, 240, 250))
        elif kind == "fog":
            for row, y in enumerate((12, 17, 22)):
                shift = math.floor(t * (3 + row)) % 10
                for x in range(128):
                    if (x + shift) % 10 < 7:
                        plot(frame, pixels, x, y, (70, 76, 84))


def validate(settings):
    name = settings.get("town_name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9 ]{1,10}", name):
        raise ValueError("town_name must be 1–10 letters, numbers or spaces")


plugin = Plugin("town", "Pixel Town", module=Town, defaults={"town_name": "RACKVILLE"}, validate_settings=validate,
                help={"town_name": "Shown on the rooftop sign (up to 10 characters)"})
