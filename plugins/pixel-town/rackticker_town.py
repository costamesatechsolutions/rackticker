"""Pixel Town: a tiny living city that runs on real-world time.

Three districts side by side, and the panel is a camera looking at part of it:
a beach where the tide and the swell are the real ones, the town in the middle,
and a station at the far end where a train pulls in and people get on it.

The sky follows your clock (sunrise, sunset, moon and stars), office windows
light up as evening comes, the weather screen's conditions fall on the street,
aircraft from the flight feed fly over towing their callsign, and people, cars
and a taco truck keep the town busy. The town keeps living between visits.

Real data when you have it, and it still works when you do not: the Surf plugin
gives the beach its tide and wave height, the Departures plugin gives the
station board somewhere to go. Without them the sea keeps its own rhythm and
the trains run to the town's own timetable.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import math
import random
import re

from PIL import Image, ImageDraw

from rackticker import Plugin, Module, new_frame
from app.core.fonts import draw_tiny, tiny_width
from app.core.fx import dim, mix, plot, sprite, stamp

SKY_KEYS = ((0.0, (3, 5, 20), (12, 18, 44)), (5.2, (3, 5, 20), (12, 18, 44)),
            (6.3, (40, 28, 80), (220, 110, 64)), (7.6, (30, 100, 205), (130, 196, 238)),
            (16.8, (30, 100, 205), (130, 196, 238)), (18.3, (60, 28, 88), (232, 104, 56)),
            (19.5, (7, 9, 32), (28, 28, 66)), (24.0, (3, 5, 20), (12, 18, 44)))
WINDOW_KEYS = ((0, .26), (3, .05), (6, .14), (8, .08), (12, .04), (17, .2), (20, .62), (23, .38), (24, .26))
# Even at 4 AM a few night owls, cabs and delivery drivers keep the town alive.
BUSY_KEYS = ((0, .3), (5, .25), (7, .6), (12, .9), (18, .85), (22, .45), (24, .3))
# What the buildings are painted; the skyline dims them as the light goes.
DAY_COLORS = ((156, 86, 72), (200, 184, 150), (98, 120, 158), (112, 142, 124), (184, 142, 100), (150, 150, 164))
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
SHOPS = ((110, 7, 21, (210, 70, 60)), (122, 6, 18, (70, 150, 210)),
         (134, 7, 23, (240, 180, 60)), (172, 6, 20, (80, 180, 120)),
         (184, 7, 22, (200, 90, 180)), (196, 6, 19, (90, 190, 200)),
         (210, 7, 23, (230, 120, 50)), (232, 6, 21, (120, 160, 230)),
         (250, 7, 20, (240, 200, 90)), (264, 6, 22, (90, 200, 140)))
PLANE = ("...w...", "wwwwwww", "..www..")
# Three districts in a row, and the panel is a camera on a strip of it: the beach
# out to one end, the town in the middle, the station at the other.
WORLD, VIEW = 384, 128
BEACH_END, TOWN_END = 104, 280
STREET_Y, TRUCK_X = 25, 150
# The road runs under the town at both ends: cars come out of a dark portal and go
# back into one, so none of them ever drives onto the sand or down the railway.
ROAD_L, ROAD_R = BEACH_END - 1, TOWN_END - 18        # where a car is fully inside a portal
PORTAL_WEST, PORTAL_EAST = (BEACH_END - 1, BEACH_END + 13), (TOWN_END - 18, TOWN_END - 9)
PEOPLE_EAST = TOWN_END - 12                          # the town's walkers turn back here
TREES, BENCH_X = (119, 180, 206, 260), 240   # street trees, and a bench with somebody on it
TREE_CROWN = ((-2, 0, 0), (-1, 0, 1), (0, 0, 1), (1, 0, 0), (2, 0, 0), (-2, -1, 0), (-1, -1, 1), (0, -1, 1),
              (1, -1, 0), (2, -1, 0), (-1, -2, 1), (0, -2, 1), (1, -2, 0), (0, -3, 0))    # dx, dy, lit side
BUSKER_X = 222                                       # somebody with a guitar, between two shops
KITE_X, KID_X = 78, 69                               # a kid flying a kite on the beach
TOWELS = ((42, (60, 170, 210), (216, 52, 44)), (62, (90, 200, 120), (250, 200, 60)))  # x, towel, swimsuit
UMBRELLAS = ((220, 60, 50), (60, 140, 240), (250, 200, 60), (80, 200, 120))
# The beach, looking along it: the sea is the band above and the sand the band
# below, the same way the town puts its buildings above its road. What moves is
# the line between them — the wave running up the sand and draining back off.
HORIZON = 18                 # the sea's far edge; sky above it
PIER_END, PIER_Y = 26, 21    # a pier out over the water at the far end
SAND_ROW = 31                # people on the beach walk along the bottom of it
# Waves arrive with a little more size further along the beach, so they break
# across it rather than all at once.
SHOAL = tuple(1.5 + .9 * (x / (BEACH_END - 1)) for x in range(BEACH_END))
PALMS = (56, 92)             # rooted in the dry sand
TOWER_X, UMBRELLA_X = 74, 40
# How much light there is on the beach. The sea and the sand take the evening
# with the sky; a daylight-blue sea under a purple sunset read as a bug.
LIGHT_KEYS = ((0, .13), (5.2, .13), (6.3, .42), (7.6, 1.0), (16.8, 1.0), (18.3, .5), (19.5, .17), (24, .13))
# A palm crown, drawn out from the top of the trunk: five fronds that droop.
PALM_CROWN = ((-5, 2), (-4, 1), (-3, 0), (-2, 0), (-1, -1), (0, -1), (1, -1), (2, 0), (3, 0), (4, 1), (5, 2),
              (-4, 3), (-3, 2), (-2, 1), (-1, 1), (1, 1), (2, 1), (3, 2), (4, 3), (0, 0))
# The station: track from the tunnel mouth to the end of the world, platform above
# it, and a train that stops with its nose just clear of the tunnel.
TUNNEL_X, PLATFORM_X = TOWN_END + 2, TOWN_END + 12
TRAIN_TOP, WHEEL_Y = 24, 30       # roof, and the rail the wheels run on
CAR_LENGTH, CARRIAGES = 22, 3
TRAIN_STOP = PLATFORM_X + 4
CANOPY_Y = 16
LIVERY = ((190, 40, 44), (232, 232, 236), (40, 46, 60))   # body, band, dark trim
# Somewhere for the trains to go when the Departures plugin is not installed.
DESTINATIONS = ("PIXEL CITY", "SEACLIFF", "NORTH END", "HARBOUR", "THE PIER", "OLD TOWN")
# Customers queue at the serving window, on the pavement beside the truck, one
# behind the other. Three deep is all the space there is, and all it needs.
WINDOW_X, QUEUE_GAP, QUEUE_DEPTH = TRUCK_X - 4, 4, 3
SERVE_SECONDS = (2.5, 5.0)     # how long an order takes at the window
CARRY_SECONDS = 6.0            # how long the taco is still in hand afterwards


def ground_row(x):
    """The row a person's feet rest on at this point in the world.

    The town's pavement and the station platform are at one height and the sand
    is at another, so a walk from the beach into town climbs the ramp at the sea
    wall instead of stepping up through thin air."""
    if x >= BEACH_END - 2:
        return STREET_Y
    if x <= BEACH_END - 16:
        return SAND_ROW
    return round(SAND_ROW + (STREET_Y - SAND_ROW) * (x - (BEACH_END - 16)) / 14)


def paint(frame, pixels, x, y, colour):
    """Write a pixel, rather than blending it.

    plot() adds light the way an LED does, so anything darker than what is behind
    it comes out as a mix of the two: a brown palm trunk against a blue sky drew
    purple, and a red parasol on pale sand disappeared into it. Solid objects are
    not light, so they get written."""
    if 0 <= x < frame.size[0] and 0 <= y < frame.size[1]:
        pixels[x, y] = colour


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
    image = Image.new("RGB", (WORLD, 32))
    draw = ImageDraw.Draw(image)
    for y in range(32):
        draw.line((0, y, WORLD - 1, y), fill=mix(top, bottom, y / 31))
    return image


def city(seed=11):
    rng = random.Random(seed)
    buildings, x = [], BEACH_END
    while x < TOWN_END - 8:
        # Fewer, broader buildings read as a skyline instead of visual noise.
        width, height = min(rng.randint(14, 24), TOWN_END - x), rng.randint(8, 16)
        color = rng.choice(DAY_COLORS)
        top = STREET_Y - height
        windows = tuple((wx, wy, rng.random(), rng.choice(((255, 206, 110), (255, 236, 170), (150, 200, 255))))
                        for wy in range(top + 2, STREET_Y - 1, 3)
                        for wx in range(x + 2, min(TOWN_END - 1, x + width - 1), 3))
        buildings.append((x, width, top, color, windows))
        x += width + rng.randint(0, 2)
    return tuple(buildings)


BUILDINGS = city()
# The tallest building near the middle carries the town's rooftop sign.
def _tallest(low, high):
    return max((b for b in BUILDINGS if low <= b[0] <= high), key=lambda b: STREET_Y - b[2],
               default=BUILDINGS[len(BUILDINGS) // 2])


# A rooftop sign in each half of town, so wherever the camera is looking there is
# a whole one to read rather than half of one at the edge of the panel.
SIGNS = (_tallest(BEACH_END + 6, 180), _tallest(196, TOWN_END - 10))
SIGN = SIGNS[0]
POLES = tuple(range(BEACH_END + 12, TOWN_END - 4, 34))   # street lights, the town's own


@lru_cache(maxsize=4)
def skyline(bucket):
    hour = bucket / 12
    occupied = _curve(WINDOW_KEYS, hour) * .75
    daylight = 7.5 <= hour <= 17.5
    # Painted in daylight colours and dimmed with the light, so the same street is
    # brick and cream at noon and a dark row of windows at midnight.
    factor = .2 + .8 * max(0.0, min(1.0, (_curve(LIGHT_KEYS, hour) - .13) / .87))
    layer = Image.new("RGBA", (WORLD, 32))
    draw = ImageDraw.Draw(layer)
    signed = {sign[0] for sign in SIGNS}
    for x, width, top, day_color, windows in BUILDINGS:
        color = dim(day_color, factor)
        draw.rectangle((x, top, x + width - 1, STREET_Y - 1), fill=(*color, 255))
        draw.line((x, top, x + width - 1, top), fill=(*mix(color, (255, 255, 255), .22), 255))
        draw.line((x, top + 1, x + width - 1, top + 1), fill=(*dim(color, .8), 255))
        if x not in signed:        # what is on the roof: the sign's buildings keep theirs clear
            prop = (x * 7 + width) % 5
            dark = dim(color, .55)
            if prop == 0:          # an aerial
                draw.line((x + 3, top - 4, x + 3, top - 1), fill=(*dark, 255))
                draw.point((x + 4, top - 3), fill=(*dark, 255))
            elif prop == 1:        # an air-conditioning unit
                draw.rectangle((x + 2, top - 2, x + 5, top - 1), fill=(*dim((150, 154, 164), factor), 255))
            elif prop == 2:        # a water tank on legs
                draw.rectangle((x + width - 6, top - 3, x + width - 3, top - 2), fill=(*dim((150, 100, 70), factor), 255))
                draw.point((x + width - 6, top - 1), fill=(*dark, 255))
                draw.point((x + width - 3, top - 1), fill=(*dark, 255))
        for wx, wy, threshold, glow in windows:
            if threshold < occupied:
                fill = glow
            elif daylight:
                fill = mix(color, (150, 190, 225), .45)
            else:
                fill = dim(color, .7)
            layer.putpixel((wx, wy), (*fill, 255))
    for pole in POLES:
        draw.line((pole, STREET_Y - 7, pole, STREET_Y - 1), fill=(90, 90, 100, 255))
        draw.point((pole + 1, STREET_Y - 7), fill=(90, 90, 100, 255))
    return layer


class Sea:
    """A row of springs, each pulling on its neighbours: the cheapest thing that
    behaves like water. Swell is pushed in at the seaward edge and travels toward
    the sand, so crests move and break on the shore rather than sliding past as a
    drawn sine would."""

    STIFFNESS, DAMPING, SPREAD = 6.0, .14, 140.0

    def __init__(self, width=BEACH_END):
        self.height = [0.0] * width
        self.speed = [0.0] * width
        self.clock = 0.0
        self.prime()

    def prime(self, size=1.4):
        """Start with a sea already running, so the beach is never flat calm for
        the first few seconds of a visit."""
        for x in range(len(self.height)):
            phase = x / 26.0 * math.tau
            self.height[x] = math.sin(phase) * size
            self.speed[x] = math.cos(phase) * size * 2.0

    def step(self, dt, period, size):
        # Small fixed steps: one long frame must not blow the springs apart.
        dt = min(dt, .1)
        beat = min(4.0, max(1.5, period / 4))
        while dt > 1e-4:
            slice_dt = min(dt, 1 / 45)
            dt -= slice_dt
            self.clock += slice_dt
            height, speed, last = self.height, self.speed, len(self.height) - 1
            for i in range(last + 1):
                left = height[i - 1] if i else height[0]
                right = height[i + 1] if i < last else height[last]
                speed[i] += (-self.STIFFNESS * height[i] - self.DAMPING * speed[i]
                             + (left + right - 2 * height[i]) * self.SPREAD) * slice_dt
            for i in range(last + 1):
                height[i] += speed[i] * slice_dt
            # The wavemaker at the seaward edge, kept to the real swell's pace.
            height[0] = math.sin(self.clock / beat * math.tau) * size
            height[1] = math.sin((self.clock - .06) / beat * math.tau) * size


def tide_level(tide, now):
    """Where the water sits on the sand, from the real tide when there is one.

    NOAA gives the next turn of the tide and the one after it. Between two turns
    the water swings like a cosine, so knowing when the next one is says where in
    that swing the water is now."""
    base, swing = 25.4, 1.3
    if not isinstance(tide, dict) or not tide.get("time"):
        return base
    try:
        nxt = datetime.strptime(str(tide["time"]), "%Y-%m-%d %H:%M")
        then = datetime.strptime(str(tide["then"]), "%Y-%m-%d %H:%M") if tide.get("then") else None
    except (TypeError, ValueError):
        return base
    half = (then - nxt).total_seconds() if then else 6.2 * 3600
    if not 2 * 3600 < half < 10 * 3600:
        half = 6.2 * 3600
    # NOAA gives the turns in the station's own local time, with no zone on them,
    # and the panel's clock carries one: they have to be compared like for like.
    left = (nxt - now.replace(tzinfo=None)).total_seconds()
    through = min(1.0, max(0.0, 1 - left / half))     # 0 at the last turn, 1 at the next
    rising = bool(tide.get("high"))                   # the turn ahead is a high one
    full = (1 - math.cos(math.pi * through)) / 2 if rising else (1 + math.cos(math.pi * through)) / 2
    return base - swing * (full * 2 - 1)


class Camera:
    """The panel is a window onto a town twice its width.

    It does not sweep back and forth on a timer, which reads as a machine. It
    settles on somewhere worth watching, stays while there is something to see,
    and glides on — and it gives way to anything that turns up, like a cat."""

    EASE, ARRIVED = 1.8, 1.0      # how briskly it glides, and what counts as there
    DWELL = (7.0, 13.0)           # how long it watches one place

    def __init__(self, rng):
        self.rng = rng
        self.limit = float(WORLD - VIEW)
        self.x = self.target = min(self.limit, max(0.0, TRUCK_X - VIEW / 2))
        self.dwell = rng.uniform(*self.DWELL)
        self.view = round(self.x)   # the town x drawn at the panel's left edge

    def look_at(self, centre, urgent=False):
        """Frame something at this point in town."""
        wanted = min(self.limit, max(0.0, centre - VIEW / 2))
        if urgent or abs(wanted - self.target) > 8:
            self.target = wanted
            if urgent:
                self.dwell = max(self.dwell, 6.0)

    def step(self, dt, interests):
        self.dwell -= dt
        if self.dwell <= 0 and abs(self.x - self.target) < self.ARRIVED:
            choices = [spot for spot in interests if abs(spot - (self.x + VIEW / 2)) > VIEW / 3]
            if choices:
                self.look_at(self.rng.choice(choices))
            self.dwell = self.rng.uniform(*self.DWELL)
        # Eased, frame-rate independent: fast at first, gentle as it arrives.
        self.x += (self.target - self.x) * min(1.0, self.EASE * dt)
        self.view = round(max(0.0, min(self.limit, self.x)))
        return self.view


class Town(Module):
    name = "town"

    def __init__(self):
        self.rng = random.Random()
        self.camera = Camera(self.rng)
        self.people, self.cars, self.clouds = [], [], []
        self.plane, self.plane_wait = None, 3.0
        self.people_wait = self.car_wait = 0.0
        self.last_t, self.scene = None, None
        for _ in range(7):
            self._spawn_person(self.rng.uniform(0, WORLD))
        for lane in (0, 1):
            self._spawn_car(lane, self.rng.uniform(PORTAL_WEST[1] + 4, PORTAL_EAST[0] - 12))
        self.clouds = [[self.rng.uniform(0, WORLD), self.rng.uniform(2, 9), self.rng.uniform(.8, 2.2)]
                       for _ in range(6)]
        self.birds = []
        self.cat = None
        self.sea = Sea()
        self.surfer = {"x": 26.0, "ride": 0.0}
        self.train, self.train_wait = None, self.rng.uniform(4, 14)
        self.board = ("", "")
        self._hour = 12.0
        # With the Departures plugin the trains keep their own pace and the board shows
        # real ones. Without it the town runs a timetable of its own, and the trains
        # arrive and leave on it, so the board is never for a train that never comes.
        self.scheduled, self.served, self.wet, self.real_data = False, None, False, True

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _spawn_person(self, x=None):
        direction = self.rng.choice((-1, 1))
        self.people.append({"x": x if x is not None else (-4.0 if direction > 0 else WORLD + 3.0), "dir": direction,
                            "speed": self.rng.uniform(5, 10), "shirt": self.rng.choice(SHIRTS),
                            "skin": self.rng.choice(SKINS), "hungry": self.rng.random() < .45,
                            "pause": 0.0, "fed": False, "dog": self.rng.random() < .08,
                            "slot": None, "carry": 0.0, "board": None,
                            # Some are off to a shop: they go in, and come out with a bag.
                            "inside": 0.0, "bag": 0.0, "visited": set(),
                            # A few of them are going somewhere: they wait on the
                            # platform and get on the train when it opens its doors.
                            "traveller": self.rng.random() < .3})

    def _errand(self, person, dt, hour):
        """Somebody passing an open shop's door sometimes goes in."""
        if person["traveller"] or person["x"] < PORTAL_WEST[1] or (person["hungry"] and not person["fed"]):
            return      # those are off to the station, or to the taco truck
        reach = person["speed"] * dt * 1.6 + .6
        for index, (x, width, closes, _) in enumerate(SHOPS):
            if index in person["visited"] or not 8 <= hour < closes:
                continue
            if abs(person["x"] - (x + width - 2)) <= reach:
                person["visited"].add(index)
                if self.rng.random() < .3:
                    person["inside"] = self.rng.uniform(3, 8)
                    person["x"] = float(x + width - 2)
                return

    def _spawn_car(self, lane, x=None):
        # The road belongs to the town: it runs from the sea wall to the tunnel
        # mouth, and nothing drives onto the sand or down the railway.
        direction = 1 if lane == 0 else -1
        if x is None:
            x = float(ROAD_L) if direction > 0 else float(ROAD_R + 1)
        # One vehicle in five is a van: something bigger to look at, and it holds
        # the traffic up behind it the way a van does.
        van = self.rng.random() < .2
        # Now and then it is a patrol car, in a hurry, with its lights going.
        police = not van and self.rng.random() < .07
        self.cars.append({"x": float(x), "dir": direction,
                          "lane": lane, "speed": self.rng.uniform(12, 20) if van else self.rng.uniform(18, 34),
                          "color": self.rng.choice(VAN_COLORS if van else CAR_COLORS), "van": van})
        if police:
            self.cars[-1].update(police=True, color=(238, 238, 244), speed=self.rng.uniform(38, 46))

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

    @staticmethod
    def _doors(front):
        """Where a stopped train's doors are, in town coordinates."""
        return [front + car * CAR_LENGTH + edge for car in range(CARRIAGES) for edge in (4, 16)]

    def _trains(self, dt, hour, rng):
        """One train at a time: in from the tunnel end, doors open, away again.

        A train every few minutes all night would be a lie, so the service thins
        out after midnight the way a real one does."""
        train = self.train
        if train is None:
            if self.scheduled:
                due = self._next_due(hour)
                # Arriving takes about ten seconds, so it leaves the platform on the dot.
                if (due - hour * 60) * 60 <= 24 and self.served != due:
                    self.served = due
                    self.train = {"x": float(WORLD + 40), "speed": 62.0, "state": "arriving", "wait": 0.0,
                                  "lit": rng.random() < .8, "due": due,
                                  "to": DESTINATIONS[(due // 15) % len(DESTINATIONS)]}
                return
            self.train_wait -= dt
            if self.train_wait > 0:
                return
            self.train = {"x": float(WORLD + 40), "speed": 62.0, "state": "arriving", "wait": 0.0,
                           "lit": rng.random() < .8}
            return
        if train["state"] == "arriving":
            # Brake into the platform: hard at first, gently as it comes in.
            gap = train["x"] - TRAIN_STOP
            train["speed"] = max(3.0, min(train["speed"], 2.2 + gap * .55))
            train["x"] -= train["speed"] * dt
            if train["x"] <= TRAIN_STOP + .6:
                train["x"] = float(TRAIN_STOP)
                train["state"], train["speed"] = "stopped", 0.0
                train["wait"] = rng.uniform(7, 12)
                # Whoever was on it gets off and walks into town.
                for door in rng.sample(self._doors(TRAIN_STOP), rng.randrange(1, 4)):
                    if len(self.people) < 16:
                        self._spawn_person(float(door))
                        self.people[-1]["dir"] = -1
                        self.people[-1]["traveller"] = False
        elif train["state"] == "stopped":
            train["wait"] -= dt
            # On the town's timetable it goes when the clock says, not when it is bored.
            due = train.get("due")
            late = due is not None and hour * 60 < due and (due - hour * 60) * 60 < 90
            if train["wait"] <= 0 and not late:
                train["state"] = "leaving"
        else:
            train["speed"] = min(70.0, train["speed"] + 26 * dt)
            train["x"] -= train["speed"] * dt
            if train["x"] + CAR_LENGTH * CARRIAGES < TUNNEL_X:
                self.train = None
                quiet = hour < 5 or hour >= 23
                self.train_wait = rng.uniform(40, 70) if quiet else rng.uniform(12, 26)

    def _platform_step(self, person, dt, rng):
        """Wait on the platform, then get on the train when it opens its doors."""
        train = self.train
        if train is not None and train["state"] == "stopped":
            door = min(self._doors(train["x"]), key=lambda d: abs(d - person["x"]))
            person["board"] = door
            if abs(person["x"] - door) <= 1.0:
                person["gone"] = True          # through the doors; the train has them now
                return
            person["x"] += math.copysign(min(abs(door - person["x"]), person["speed"] * 1.4 * dt),
                                         door - person["x"])
            return
        # No train yet: stand about on the platform, shuffling a little.
        if person["board"] is None:
            person["board"] = rng.uniform(PLATFORM_X + 8, WORLD - 10)
        drift = person["board"] - person["x"]
        if abs(drift) > .6:
            person["x"] += math.copysign(min(abs(drift), person["speed"] * .5 * dt), drift)

    def _sea_step(self, dt, surf):
        """Run the water, and the one person out in it."""
        height = (surf or {}).get("height")
        period = (surf or {}).get("period")
        size = min(2.6, max(.5, float(height) / 2.6)) if isinstance(height, (int, float)) else 1.3
        self.sea.step(dt, float(period) if isinstance(period, (int, float)) and period else 9.0, size)
        surfer = self.surfer
        column = max(0, min(BEACH_END - 1, int(surfer["x"])))
        if surfer["ride"] > 0:
            surfer["ride"] -= dt
            surfer["x"] = min(BEACH_END - 6.0, surfer["x"] + 15 * dt)
        elif self.sea.height[column] > size * .8 and self.sea.speed[column] < 0:
            surfer["ride"] = self.rng.uniform(1.6, 2.8)     # up, and away with it
        else:                                               # paddle back out the back
            surfer["x"] += (26.0 - surfer["x"]) * min(1.0, dt * .5)

    def _simulate(self, dt, hour, weather, flight):
        rng = self.rng
        busy = _curve(BUSY_KEYS, hour)
        wet = weather in ("rain", "storm", "snow")
        self.wet = wet
        truck_open = 11 <= hour < 14 or 17 <= hour < 22
        self.people_wait -= dt
        if self.people_wait <= 0 and len(self.people) < 12:
            self._spawn_person()
            self.people_wait = rng.uniform(1.2, 5) / max(.12, busy)
        for person in self.people:
            if person["carry"] > 0:
                person["carry"] -= dt
            if person["bag"] > 0:
                person["bag"] -= dt
            if person["inside"] > 0:            # in the shop, out of sight
                person["inside"] -= dt
                if person["inside"] <= 0:
                    person["bag"] = 7.0
                continue
            if person["slot"] is not None:
                self._queue_step(person, dt, rng, truck_open)
                continue
            if truck_open and person["hungry"] and not person["fed"] and self._joins_queue(person):
                continue
            if person["traveller"] and person["x"] > PLATFORM_X + 4:
                self._platform_step(person, dt, rng)
                continue
            person["x"] += person["dir"] * person["speed"] * dt * (1.5 if wet else 1)
            self._errand(person, dt, hour)
            # The high street ends at the tunnel wall. Only somebody catching a train
            # goes on through the door in it; everyone else has had enough and turns back.
            if person["dir"] > 0 and PEOPLE_EAST <= person["x"] < TOWN_END and not person["traveller"]:
                person["dir"] = -1
        self.people = [p for p in self.people if -8 < p["x"] < WORLD + 8 and not p.get("gone")]
        self.car_wait -= dt
        if self.car_wait <= 0:
            lane = rng.randrange(2)
            edge = ROAD_L if lane == 0 else ROAD_R
            if not any(c["lane"] == lane and abs(c["x"] - edge) < 16 for c in self.cars):
                self._spawn_car(lane)
            self.car_wait = rng.uniform(.8, 4) / max(.15, busy)
        for car in self.cars:
            ahead = [c for c in self.cars if c is not car and c["lane"] == car["lane"]
                     and 0 < (c["x"] - car["x"]) * car["dir"] < 14]
            speed = min(car["speed"], min((c["speed"] for c in ahead), default=car["speed"]))
            car["x"] += car["dir"] * speed * dt
        self.cars = [c for c in self.cars if ROAD_L - 1 < c["x"] < ROAD_R + 2]
        for cloud in self.clouds:
            cloud[0] = (cloud[0] + cloud[2] * dt) % (WORLD + 22)
        daylight = 6.5 < hour < 19.5
        if daylight and not self.birds and rng.random() < dt * .18:
            direction = rng.choice((-1, 1))
            flock = rng.randrange(2, 4)
            self.birds = [{"x": (-6.0 if direction > 0 else WORLD + 6.0) - n * rng.uniform(4, 7) * direction,
                           "y": rng.uniform(3, 9), "dir": direction, "speed": rng.uniform(9, 14)}
                          for n in range(flock)]
        for bird in self.birds:
            bird["x"] += bird["dir"] * bird["speed"] * dt
            bird["y"] += math.sin(bird["x"] * .12) * dt * 1.2
        self.birds = [b for b in self.birds if -10 < b["x"] < WORLD + 10] if daylight else []
        # Somebody has to be out at 3 AM, and in a town this size it is a cat.
        if self.cat:
            self.cat["x"] += self.cat["dir"] * 11 * dt
            if not -6 < self.cat["x"] < WORLD + 6:
                self.cat = None
        elif not daylight and rng.random() < dt * .05:
            direction = rng.choice((-1, 1))
            self.cat = {"x": -5.0 if direction > 0 else WORLD + 5.0, "dir": direction}
        if self.plane:
            self.plane["x"] += self.plane["dir"] * 16 * dt
            if not -60 < self.plane["x"] < WORLD + 60:
                self.plane, self.plane_wait = None, rng.uniform(8, 16)
        else:
            self.plane_wait -= dt
            if self.plane_wait <= 0:
                direction = rng.choice((-1, 1))
                label = flight.callsign if flight else ""
                self.plane = {"x": -10.0 if direction > 0 else WORLD + 10.0, "dir": direction, "label": label}
        self._trains(dt, hour, rng)

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
        settings = context.config["plugins"][self.name]
        real = self.real_data = settings.get("real_data", True)
        surf_snap = context.snapshots.get("surf") if real else None
        surf = surf_snap.data if surf_snap and isinstance(surf_snap.data, dict) and not surf_snap.stale else None
        self._simulate(dt, hour, kind, flight)
        self._sea_step(dt, surf)
        self.board = self._departure(context, now)
        self._look_at_train()
        view = self.camera.step(dt, self._interests(hour))
        left, right = view, view + VIEW
        frame = sky_image(math.floor(hour * 60)).copy()
        draw = ImageDraw.Draw(frame)
        pixels = frame.load()
        self._celestial(frame, draw, pixels, hour, t, kind)
        self._clouds(draw, kind)
        self._birds(frame, pixels, self.birds, t)
        self._plane(frame, pixels, t)
        frame.paste(skyline(math.floor(hour * 12)), (0, 0), skyline(math.floor(hour * 12)))
        self._sign(frame, draw, now, weather, t, context.config["plugins"][self.name]["town_name"], view)
        night = hour < 6.5 or hour > 19
        # Ground first, right across all three districts, then everything that
        # stands on it. Drawing order is depth: whatever comes last is nearest.
        light = _curve(LIGHT_KEYS, hour)
        overcast = kind in ("cloud", "rain", "storm", "snow", "fog")
        moon = None if overcast or 6 <= hour <= 18 else round(4 + ((hour - 18) % 24) / 12 * 120)
        self._hour = hour
        self._beach(frame, draw, pixels, night, tide_level((surf or {}).get("tide"), now), t, left, right, light, moon)
        self._street(frame, draw, pixels, night, hour, t, left, right)
        self._station(frame, draw, pixels, night, t, left, right)
        for person in sorted(self.people, key=lambda p: p["x"]):
            if left - 10 < person["x"] < right + 10 and person["inside"] <= 0:
                self._person(frame, pixels, person, t, ground_row(person["x"]), self.wet)
        self._foreground(frame, draw, pixels, night, hour, t, left, right, light)
        self._weather(frame, draw, pixels, kind, t, left, right)
        # The panel is the camera's view of the town, not the whole town.
        return frame.crop((view, 0, view + VIEW, 32))

    def _foreground(self, frame, draw, pixels, night, hour, t, left, right, light=1.0):
        """Everything that passes in front of the people: the truck at the kerb,
        the traffic, the train at the platform."""
        truck_open = 11 <= hour < 14 or 17 <= hour < 22
        if left - 20 < TRUCK_X < right + 20:
            stamp(frame, sprite(TRUCK, self._truck_palette(truck_open, t)), TRUCK_X, STREET_Y - len(TRUCK))
            if truck_open:
                self._truck_life(frame, pixels, t)
        if self.cat and left - 6 < self.cat["x"] < right + 6:
            self._cat(frame, pixels, self.cat, t)
        for lane in (0, 1):
            for car in self.cars:
                if car["lane"] == lane and left - 16 < car["x"] < right + 16:
                    self._car(frame, pixels, car, night, t)
        self._portals(draw, night, left, right)
        self._train_at(frame, draw, pixels, night, t)
        self._tunnel(draw, night, left, right)
        self._beach_front(frame, draw, pixels, night, t, left, right, light)
        self._board(frame, draw, t, left, right)

    def _departure(self, context, now):
        """Where the next train goes and when, from the Departures plugin if it is
        installed. Its board is a real one, so the town's station shows what it
        shows; otherwise the town runs its own service."""
        snap = context.snapshots.get("departures") if self.real_data else None
        data = snap.data if snap and isinstance(snap.data, dict) and not snap.stale else None
        self.scheduled = False
        for board in (data or {}).values():
            for row in (board or {}).get("rows") or []:
                where = str(row.get("destination") or "").strip().upper()[:14]
                when = row.get("time")
                if not where:
                    continue
                if hasattr(when, "strftime"):
                    return where, when.strftime("%H:%M")
                text = str(when or "")
                return where, text[11:16] if len(text) >= 16 else "--:--"
        # No feed: the town's own timetable, which the trains keep.
        self.scheduled = True
        due = self._next_due(now.hour + now.minute / 60 + now.second / 3600)
        return DESTINATIONS[(due // 15) % len(DESTINATIONS)], f"{due // 60 % 24:02d}:{due % 60:02d}"

    @staticmethod
    def _next_due(hour):
        """The next departure on the town's own timetable, in minutes: every quarter
        hour, thinning to every half hour late at night the way a real service does."""
        step = 30 if hour >= 23 or hour < 5 else 15
        return (int(hour * 60 // step) + 1) * step

    @staticmethod
    def _truck_palette(truck_open, t):
        # Open: the roof taco and serving window light up and the awning's bulbs
        # chase. Closed: the shutter is down and the sign dark, but it is still a
        # taco truck.
        chase = truck_open and math.floor(t * 2) % 2
        return {"o": (220, 60, 50), "w": (228, 228, 218), "k": (22, 22, 30), "b": (120, 180, 220),
                "g": (120, 120, 128),
                "y": (255, 204, 90) if truck_open else (70, 72, 80),
                "t": (255, 190, 40) if truck_open else (110, 84, 30),
                "l": (90, 220, 90) if truck_open else (40, 90, 40),
                "r": (240, 60, 40) if truck_open else (110, 40, 30),
                "a": (255, 255, 255) if chase else (220, 60, 50),
                "s": (220, 60, 50) if chase else (255, 255, 255)}

    def _interests(self, hour):
        """Places worth pointing the camera at, right now — across all three
        districts, so the panel is a tour of the place and not just its high
        street."""
        spots = [TRUCK_X + 8]
        queue = [p["x"] for p in self.people if p["slot"] is not None]
        if queue:                       # a queue at the window is the best thing in town
            spots.append(TRUCK_X)
        spots.extend(shop[0] + shop[1] // 2 for shop in SHOPS if 8 <= hour < shop[2])
        spots.extend(sign[0] + sign[1] // 2 for sign in SIGNS)
        if self.cat:
            spots.append(self.cat["x"])
        walkers = [p["x"] for p in self.people if p["slot"] is None]
        if len(walkers) > 3:            # wherever the street is busiest
            spots.append(sum(walkers) / len(walkers))
        # The beach: the shore break, and the surfer while there is one to watch.
        spots.append(BEACH_END - 46)
        if 7 <= hour < 20:
            spots.extend((TOWER_X - 8, self.surfer["x"] + 30))
        # The station, and whatever is standing at it.
        spots.append(PLATFORM_X + 40)
        waiting = [p["x"] for p in self.people if p["board"] is not None]
        if waiting:
            spots.append(sum(waiting) / len(waiting))
        return spots

    def _look_at_train(self):
        """A train coming in is worth turning the camera for."""
        train = self.train
        if train is not None and train["state"] == "arriving" and train["x"] < WORLD + 6:
            self.camera.look_at(PLATFORM_X + 46, urgent=True)

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
                for n in range(24):
                    sx, sy = (n * 53) % WORLD, (n * 29) % 14
                    # Steady stars: on/off twinkling reads as flicker on LEDs.
                    plot(frame, pixels, sx, sy, (150, 160, 200) if n % 3 else (104, 112, 146))
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(236, 232, 200))
            draw.ellipse((x - 1, y - 3, x + 3, y + 1), fill=pixels[0, max(0, y - 3)])

    def _clouds(self, draw, kind):
        count = {"sun": 0, "partly": 1, "cloud": 2, "fog": 2, "rain": 2, "storm": 3, "snow": 2}.get(kind, 0)
        color = (80, 84, 96) if kind in ("rain", "storm") else (236, 240, 246)
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
    def _sign(frame, draw, now, weather, t, town_name, view=0):
        items = [f"{now.hour % 12 or 12}:{now.minute:02d}"]
        if "temperature" in weather:
            items.append(f"{weather['temperature']}°")
        items.append(town_name)
        # One fixed sign size for every message, so the sign never jumps.
        sign_width = max(tiny_width(item) for item in (*items, "12:59", "100°")) + 6
        for turn, (x, width, top, _, _) in enumerate(SIGNS):
            # Each sign a step along, so two in view at once never say the same thing.
            text = items[(math.floor(t / 6) + turn) % len(items)]
            sx = max(0, min(WORLD - 1 - sign_width, x + width // 2 - sign_width // 2))
            # Half a sign at the edge of the panel reads as a fault, so a sign the
            # camera cannot show whole is not drawn at all.
            if sx < view or sx + sign_width > view + VIEW:
                continue
            draw.rectangle((sx, top - 9, sx + sign_width - 1, top - 2), fill=(16, 14, 18), outline=(70, 60, 40))
            # Its legs stand on its own roof, even when the sign is wider than the building.
            for leg in (max(sx + 2, x + 1), min(sx + sign_width - 3, x + width - 2)):
                draw.line((leg, top - 1, leg + 1, top - 1), fill=(70, 60, 40))
            draw_tiny(frame, text, sx + sign_width // 2 - tiny_width(text) // 2, top - 8, (255, 176, 20))

    def _street(self, frame, draw, pixels, night, hour, t, left, right):
        """The town's own ground: pavement, shopfronts and road, between the sea
        wall at one end and the tunnel mouth at the other."""
        if right <= BEACH_END or left >= TOWN_END:
            return
        start, end = max(BEACH_END - 6, left), min(TOWN_END, right)
        # A pavement for people to walk on. Without it they were drawn against
        # whatever window happened to be behind them and read as floating specks.
        draw.rectangle((start, STREET_Y - 6, end, STREET_Y - 1), fill=(38, 38, 46) if night else (52, 52, 60))
        draw.rectangle((start, STREET_Y - 6, end, STREET_Y - 6), fill=(58, 58, 68) if night else (74, 74, 84))
        self._shops(draw, hour, left, right)
        self._planters(frame, draw, pixels, hour, left, right)
        self._busker(frame, pixels, hour, t, left, right)
        draw.rectangle((start, STREET_Y, end, STREET_Y), fill=(70, 70, 76))
        draw.rectangle((start, STREET_Y + 1, end, 31), fill=(24, 24, 28))
        for x in range(max(BEACH_END, start - start % 8), end, 8):
            draw.line((x, 29, min(end, x + 3), 29), fill=(90, 80, 40))
        # The sea wall the promenade ends at: the road does not run onto the sand.
        draw.rectangle((BEACH_END - 4, STREET_Y - 8, BEACH_END - 1, 31), fill=(44, 44, 52) if night else (78, 76, 82))
        draw.rectangle((BEACH_END - 4, STREET_Y - 8, BEACH_END - 1, STREET_Y - 8),
                       fill=(64, 64, 74) if night else (112, 110, 116))
        if night:
            for pole in POLES:
                plot(frame, pixels, pole + 1, STREET_Y - 7, (255, 220, 140))
                for spread in range(-2, 3):
                    plot(frame, pixels, pole + 1 + spread, STREET_Y, (110, 96, 64))

    def _planters(self, frame, draw, pixels, hour, left, right):
        """Trees in the gaps between the shops, and a bench with somebody on it."""
        light = max(.3, _curve(LIGHT_KEYS, hour))
        trunk, leaf, leaf_lit = dim((120, 84, 50), light), dim((44, 146, 72), light), dim((74, 186, 96), light)
        for x in TREES:
            if left - 8 < x < right + 8:
                for row in range(STREET_Y - 4, STREET_Y):
                    paint(frame, pixels, x, row, trunk)
                for dx, dy, lit in TREE_CROWN:
                    paint(frame, pixels, x + dx, STREET_Y - 5 + dy, leaf_lit if lit else leaf)
        if left - 10 < BENCH_X < right + 10:
            wood, back, leg = dim((150, 104, 60), light), dim((120, 83, 48), light), dim((70, 60, 54), light)
            for dx in range(8):
                paint(frame, pixels, BENCH_X + dx, STREET_Y - 2, wood)
                paint(frame, pixels, BENCH_X + dx, STREET_Y - 4, back)
            for post in (1, 6):
                paint(frame, pixels, BENCH_X + post, STREET_Y - 1, leg)
            if 9 <= hour < 20 and not self.wet:      # reading the paper
                x = BENCH_X + 3
                paint(frame, pixels, x + 1, STREET_Y - 8, (224, 172, 120))
                for dx in range(3):
                    paint(frame, pixels, x + dx, STREET_Y - 7, (230, 60, 60))
                    paint(frame, pixels, x + dx, STREET_Y - 6, (230, 60, 60))
                paint(frame, pixels, x, STREET_Y - 5, (26, 26, 38))
                paint(frame, pixels, x + 2, STREET_Y - 5, (26, 26, 38))
                paint(frame, pixels, x + 3, STREET_Y - 6, (240, 240, 232))   # the paper

    def _busker(self, frame, pixels, hour, t, left, right):
        """Somebody with a guitar and a hat on the pavement, notes drifting up. Out
        during the day when it is dry, gone home when it is not."""
        if self.wet or not 10 <= hour < 21 or not left - 10 < BUSKER_X < right + 10:
            return
        x, top = BUSKER_X, STREET_Y - 5
        paint(frame, pixels, x + 1, top, (224, 172, 120))
        for row in (top + 1, top + 2):
            for dx in range(3):
                paint(frame, pixels, x + dx, row, (60, 140, 240))
        for dx in (0, 2):
            for row in (top + 3, top + 4):
                paint(frame, pixels, x + dx, row, (26, 26, 38))
        for dx in range(3):
            shade(frame, pixels, x + dx, STREET_Y)
        for gx, gy in ((-2, 2), (-1, 2), (-2, 3), (-1, 3)):          # the guitar's body
            paint(frame, pixels, x + gx, top + gy, (176, 112, 50))
        for gx, gy in ((3, 1), (4, 0)):                              # and its neck
            paint(frame, pixels, x + gx, top + gy, (120, 76, 36))
        for dx in range(-7, -4):                                     # the hat, with a coin
            paint(frame, pixels, x + dx, STREET_Y, (150, 40, 40))
        if math.floor(t * 1.5) % 3 == 0:
            paint(frame, pixels, x - 6, STREET_Y - 1, (255, 214, 60))
        for n, colour in enumerate(((255, 190, 40), (90, 220, 120), (90, 170, 255))):
            phase = (t * .45 + n / 3) % 1
            nx, ny = x + 5 + round(math.sin(phase * 6 + n * 2) * 3), top - 1 - round(phase * 12)
            fade = 1 - phase * .75
            plot(frame, pixels, nx, ny, dim(colour, fade))
            plot(frame, pixels, nx + 1, ny - 1, dim(colour, fade * .8))
            plot(frame, pixels, nx + 1, ny - 2, dim(colour, fade * .8))

    def _wash(self, level):
        """The row the water reaches in each column: the edge of the wash.

        Looking along a beach, the sea is the band above and the sand the band
        below, and what moves is the line between them — the wave running up the
        sand and draining back off it."""
        height, shoal = self.sea.height, SHOAL
        return [level + height[x] * shoal[x] for x in range(BEACH_END)]

    def _beach(self, frame, draw, pixels, night, level, t, left, right, light=1.0, moon=None):
        """Sea above, sand below, and the wash line moving between them."""
        if left >= BEACH_END:
            return
        dry, wet = dim((214, 190, 138), light), dim((162, 136, 98), light)
        deep, shallow = dim((12, 58, 136), light), dim((38, 150, 214), light)
        foam = dim((232, 248, 254), max(.34, light))
        wash, speed = self._wash(level), self.sea.speed
        for x in range(max(0, left), min(BEACH_END, right)):
            edge = wash[x]
            row = max(HORIZON, min(31, int(round(edge))))
            for y in range(HORIZON, row + 1):
                # Darker out towards the horizon, lighter in the shallows: what
                # makes a flat band of blue read as water going away from you.
                pixels[x, y] = mix(deep, shallow, (y - HORIZON) / max(1, row - HORIZON + 1))
            for y in range(row + 1, 32):
                # Sand the last wave reached is still wet, and dries as it goes up.
                pixels[x, y] = wet if y - edge < 2.2 else dry
            pixels[x, row] = foam
            if speed[x] > 4.5 and row - 2 >= HORIZON:      # a crest breaking further out
                pixels[x, row - 2] = foam
        if moon is not None:
            self._moonlight(pixels, moon, wash, t, left, right)
        if light >= .5 and not self.wet and 8 <= self._hour < 17.5:
            for x, towel, suit in TOWELS:
                if left - 8 < x < right + 8:
                    for dx in range(5):
                        paint(frame, pixels, x + dx, 31, dim(towel, light))
                    paint(frame, pixels, x, 30, dim((224, 172, 120), light))
                    for dx in range(1, 4):
                        paint(frame, pixels, x + dx, 30, dim(suit, light))
                    paint(frame, pixels, x + 4, 30, dim((224, 172, 120), light))
        self._boat(frame, pixels, level, night, t)
        self._pier(frame, draw, pixels, night, left, right, light)
        self._surfer(frame, pixels, wash, night)

    @staticmethod
    def _moonlight(pixels, moon, wash, t, left, right):
        """The moon's road on the water: a column of glitter below it that widens
        towards the shore. Each glint swells and fades slowly on its own beat; a
        fast on/off twinkle reads as flicker on LEDs."""
        for y in range(HORIZON + 1, 31):
            reach = 1 + (y - HORIZON) // 2
            for x in range(moon - reach, moon + reach + 1):
                if not max(0, left) <= x < min(BEACH_END, right) or y >= wash[x] - .5 or (x * 7 + y * 13) % 3:
                    continue
                swell = .5 + .5 * math.sin(t * .9 + x * 1.7 + y * 2.3)
                shine = swell * (1 - abs(x - moon) / (reach + 1)) * .95
                if shine > .12:
                    pixels[x, y] = tuple(min(255, round(c + k * shine)) for c, k in zip(pixels[x, y], (200, 205, 170)))

    @staticmethod
    def _pier(frame, draw, pixels, night, left, right, light=1.0):
        """A pier out over the water at the far end, so the sea has something in
        it and the eye has somewhere to go."""
        if left > PIER_END:
            return
        wood, rail = dim((142, 106, 66), light), dim((184, 142, 92), light)
        draw.rectangle((0, PIER_Y, PIER_END, PIER_Y + 2), fill=wood)
        draw.rectangle((0, PIER_Y, PIER_END, PIER_Y), fill=rail)
        for post in range(1, PIER_END + 1, 5):
            for row in range(PIER_Y - 3, PIER_Y):      # the handrail's uprights
                pixels[post, row] = rail
            pixels[post, PIER_Y - 3] = rail
            for row in range(PIER_Y + 3, 32):          # legs down into the water
                shade(frame, pixels, post, row, .45)
        draw.line((0, PIER_Y - 3, PIER_END, PIER_Y - 3), fill=rail)
        if night:
            for lamp in range(3, PIER_END, 9):
                plot(frame, pixels, lamp, PIER_Y - 4, (255, 220, 140))

    def _boat(self, frame, pixels, level, night, t):
        """A little sailboat out past the break, riding whatever the sea is doing."""
        x = PIER_END + 12 + math.sin(t * .09) * 10
        column = max(0, min(BEACH_END - 1, int(x)))
        base = int(round(level + self.sea.height[column] * SHOAL[column])) - 3
        if base < HORIZON + 1 or base > 29:
            return
        hull = (40, 44, 56) if night else (52, 58, 76)
        sail = (170, 174, 186) if night else (244, 246, 250)
        lean = round(math.sin(t * .7) * .5)
        for dx in range(-2, 3):
            paint(frame, pixels, round(x) + dx, base, hull)
        for row in range(1, 5):                 # the sail, a triangle off the mast
            top = base - row
            if top < HORIZON:
                break
            paint(frame, pixels, round(x) + lean, top, (120, 108, 90) if night else (150, 132, 104))
            for spread in range(1, (5 - row) // 2 + 1):
                paint(frame, pixels, round(x) + lean + spread, top, sail)

    def _surfer(self, frame, pixels, wash, night):
        """Sitting out the back until a wave comes, then up and riding it in."""
        surfer = self.surfer
        column = max(0, min(BEACH_END - 1, int(surfer["x"])))
        x, y = round(surfer["x"]), int(round(wash[column])) - 2
        if y < HORIZON or y > 30:
            return
        board = (170, 160, 60) if night else (250, 240, 60)
        for dx in range(-1, 3):
            paint(frame, pixels, x + dx, y, board)
        paint(frame, pixels, x + 1, y - 1, (40, 44, 60) if night else (30, 34, 46))
        if surfer["ride"] > 0:
            paint(frame, pixels, x + 1, y - 2, (120, 96, 70) if night else (180, 140, 100))
            for spray in range(2):        # the wash off the back of the board
                plot(frame, pixels, x - 2 - spray, y, dim((226, 244, 252), .7 - spray * .25))

    def _beach_front(self, frame, draw, pixels, night, t, left, right, light=1.0):
        """The things standing on the sand, drawn last so they are in front."""
        if left >= BEACH_END or right < 0:
            return
        for trunk in PALMS:
            if not left - 8 < trunk < right + 8:
                continue
            sway = math.sin(t * .6 + trunk) * 1.2
            for row in range(SAND_ROW, 11, -1):
                lean = (SAND_ROW - row) / 16 * sway
                paint(frame, pixels, round(trunk + lean), row, dim((132, 92, 52), light))
            crown_x, crown_y = round(trunk + sway), 11
            green = dim((46, 158, 74), light)
            for dx, dy in PALM_CROWN:
                paint(frame, pixels, crown_x + dx, crown_y + dy, green)
            paint(frame, pixels, crown_x + 1, crown_y + 2, dim((180, 140, 60), light))
        if left - 12 < UMBRELLA_X < right + 12:      # a parasol, and a towel beside it
            for row in range(26, 31):
                paint(frame, pixels, UMBRELLA_X, row, dim((210, 206, 200), light))
            for dx in range(-4, 5):                 # the canopy, in panels
                stripe = (216, 52, 44) if (dx + 4) // 2 % 2 else (240, 240, 236)
                paint(frame, pixels, UMBRELLA_X + dx, 25, dim(stripe, light))
            for dx in (-2, -1, 0, 1, 2):
                paint(frame, pixels, UMBRELLA_X + dx, 24, dim((216, 52, 44) if dx % 2 else (240, 240, 236), light))
            for dx in range(2, 7):
                paint(frame, pixels, UMBRELLA_X + dx, 31, dim((60, 170, 210), light))
        if left - 14 < TOWER_X < right + 14:
            self._lifeguard(frame, draw, pixels, night, t)
        if not night and not self.wet and 9 <= self._hour < 18 and left - 16 < KITE_X < right + 16:
            self._kite(frame, draw, pixels, t, light)

    @staticmethod
    def _kite(frame, draw, pixels, t, light):
        """A kid on the sand and a kite riding the sea breeze above the tower."""
        kx, ky = round(KITE_X + math.sin(t * .5) * 6), round(7 + math.cos(t * .37) * 2)
        skin, shirt = dim((224, 172, 120), light), dim((80, 200, 120), light)
        paint(frame, pixels, KID_X + 1, 27, skin)
        for dx in range(3):
            paint(frame, pixels, KID_X + dx, 28, shirt)
        for dx in (0, 2):
            for row in (29, 30):
                paint(frame, pixels, KID_X + dx, row, dim((26, 26, 38), light))
        draw.line((KID_X + 3, 28, kx, ky + 2), fill=dim((220, 220, 210), .7))
        draw.polygon(((kx, ky - 2), (kx + 2, ky), (kx, ky + 2), (kx - 2, ky)), fill=(230, 50, 44))
        draw.point((kx, ky), fill=(255, 214, 60))
        for n in range(1, 6):                     # the tail, flicking in the wind
            tail = (kx + round(math.sin(t * 3 + n) * 1.5), ky + 2 + n)
            paint(frame, pixels, *tail, (60, 140, 240) if n % 2 else (255, 214, 60))

    @staticmethod
    def _lifeguard(frame, draw, pixels, night, t):
        """The tower on the dry sand, with somebody in it during the day."""
        legs = (92, 74, 54) if night else (156, 124, 84)
        for leg in (TOWER_X, TOWER_X + 8):
            draw.line((leg, 26, leg, 31), fill=legs)
        draw.rectangle((TOWER_X - 1, 21, TOWER_X + 9, 26), fill=(40, 60, 90) if night else (72, 120, 180))
        draw.rectangle((TOWER_X - 2, 20, TOWER_X + 10, 20), fill=(140, 40, 36) if night else (220, 60, 50))
        draw.rectangle((TOWER_X + 1, 22, TOWER_X + 7, 24), fill=(18, 24, 34) if night else (150, 200, 230))
        if not night:                      # the guard, watching the water
            pixels[TOWER_X + 3, 23] = (255, 214, 170)
            pixels[TOWER_X + 3, 24] = (240, 180, 40)
        flag = (240, 200, 40) if math.floor(t * .5) % 2 else (230, 70, 50)
        draw.line((TOWER_X + 10, 17, TOWER_X + 10, 20), fill=legs)
        draw.rectangle((TOWER_X + 11, 17, TOWER_X + 13, 18), fill=dim(flag, .5) if night else flag)

    @staticmethod
    def _shops(draw, hour, left=0, right=WORLD):
        """Lit shopfronts at street level: somewhere for everyone to be walking to."""
        for x, width, closes, awning in SHOPS:
            if x + width < left or x > right:
                continue
            open_now = 8 <= hour < closes
            glass = (255, 208, 130) if open_now else (46, 44, 56)
            draw.rectangle((x, STREET_Y - 5, x + width - 1, STREET_Y - 2), fill=(30, 30, 38))
            # Its own awning, so a row of shops is a street rather than a pattern.
            draw.rectangle((x, STREET_Y - 5, x + width - 1, STREET_Y - 5),
                           fill=awning if open_now else dim(awning, .35))
            draw.rectangle((x + 1, STREET_Y - 4, x + width - 2, STREET_Y - 3), fill=glass)
            # A doorway, dark whether the lights are on or not.
            draw.rectangle((x + width - 2, STREET_Y - 4, x + width - 2, STREET_Y - 2), fill=(24, 22, 30))

    def _station(self, frame, draw, pixels, night, t, left, right):
        """Track, platform and canopy. The train is drawn separately, after the
        people, so it passes in front of them the way the taco truck does."""
        if right <= TOWN_END:
            return
        start = max(TOWN_END, left)
        draw.rectangle((start, 26, right, 31), fill=(30, 27, 25) if night else (52, 47, 43))
        for x in range(max(TUNNEL_X, start - start % 5), min(WORLD, right), 5):
            draw.line((x, 28, x, 31), fill=(44, 34, 26) if night else (74, 58, 42))
        if right >= TUNNEL_X:
            draw.rectangle((TUNNEL_X, WHEEL_Y, right, WHEEL_Y), fill=(120, 126, 138) if night else (168, 174, 186))
        # Platform: the pavement's rows, one deeper, with the edge line on it.
        edge = max(PLATFORM_X, start)
        if edge <= right:
            draw.rectangle((edge, STREET_Y - 6, right, 25), fill=(46, 46, 52) if night else (86, 86, 94))
            draw.rectangle((edge, STREET_Y - 6, right, STREET_Y - 6),
                           fill=(64, 64, 72) if night else (108, 108, 118))
            draw.rectangle((edge, 25, right, 25), fill=(150, 120, 30) if night else (240, 196, 40))
        # The canopy over it, on posts.
        draw.rectangle((PLATFORM_X, CANOPY_Y - 1, WORLD - 1, CANOPY_Y), fill=(56, 56, 66) if night else (92, 94, 106))
        for post in range(PLATFORM_X + 12, WORLD - 4, 30):
            draw.line((post, CANOPY_Y + 1, post, STREET_Y - 6), fill=(60, 60, 70) if night else (100, 100, 112))
            if night:                                   # lamps under the canopy
                plot(frame, pixels, post, CANOPY_Y + 1, (255, 224, 150))
                for spread in range(-2, 3):
                    plot(frame, pixels, post + spread, STREET_Y - 6, (96, 84, 56))
        if right > WORLD - 22:
            self._station_house(draw, night)

    def _station_house(self, draw, night):
        """The station building at the end of the platform, with a clock that keeps
        the town's time, so the far end is somewhere and not just more platform."""
        x0 = WORLD - 20
        roof, brick = ((34, 32, 44), (56, 32, 32)) if night else ((76, 72, 90), (132, 70, 58))
        draw.rectangle((x0 - 1, 8, WORLD - 1, 9), fill=roof)
        draw.rectangle((x0 - 1, 8, WORLD - 1, 8), fill=dim(roof, 1.5))
        draw.rectangle((x0, 10, WORLD - 1, 25), fill=brick)
        glass = (255, 226, 150) if night else (120, 170, 210)
        for window in (x0 + 3, x0 + 15):
            draw.rectangle((window, 17, window + 2, 20), fill=glass)
        draw.rectangle((x0 + 9, 18, x0 + 11, 25), fill=(120, 90, 40) if night else (60, 38, 30))
        cx, cy = x0 + 10, 13
        face, hands = ((255, 226, 150), (40, 30, 20)) if night else ((236, 232, 220), (30, 30, 40))
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=face)
        hour = self._hour
        for length, turn in ((2, hour * 60 % 60 / 60), (1, hour % 12 / 12)):
            angle = turn * 2 * math.pi
            draw.point((cx + round(length * math.sin(angle)), cy - round(length * math.cos(angle))), fill=hands)
        draw.point((cx, cy), fill=hands)

    @staticmethod
    def _portals(draw, night, left, right):
        """The dark mouths the road goes under the town through, drawn over the cars
        so they come out of them and go back into them."""
        stone = (44, 44, 52) if night else (86, 84, 90)
        for x0, x1 in (PORTAL_WEST, PORTAL_EAST):
            if x1 < left - 2 or x0 > right + 2:
                continue
            draw.rectangle((x0, STREET_Y + 1, x1, 31), fill=(6, 6, 10))
            draw.rectangle((x0, STREET_Y + 1, x1, STREET_Y + 1), fill=stone)      # the lintel
            draw.rectangle((x0 - 1, STREET_Y + 1, x0 - 1, 31), fill=stone)         # and the jambs
            draw.rectangle((x1 + 1, STREET_Y + 1, x1 + 1, 31), fill=stone)

    @staticmethod
    def _tunnel(draw, night, left, right):
        """The mouth the line disappears into, drawn over the train so a departing
        train goes into it instead of sliding over the high street."""
        if right <= TOWN_END - 10 or left >= TUNNEL_X + 10:
            return
        wall = (34, 34, 40) if night else (62, 60, 66)
        draw.rectangle((TOWN_END - 8, 17, TUNNEL_X + 7, 31), fill=wall)
        draw.rectangle((TOWN_END - 8, 17, TUNNEL_X + 7, 17), fill=(52, 52, 60) if night else (96, 94, 100))
        # A footpath door for people catching a train; the tunnel is only for the rails.
        draw.rectangle((PEOPLE_EAST + 1, 19, PEOPLE_EAST + 5, STREET_Y), fill=(20, 18, 26))
        draw.rectangle((PEOPLE_EAST + 2, 20, PEOPLE_EAST + 4, STREET_Y), fill=(200, 160, 70) if night else (120, 100, 70))
        draw.point((PEOPLE_EAST + 3, 17), fill=(255, 190, 40))
        draw.rectangle((TUNNEL_X, 22, TUNNEL_X + 7, 31), fill=(8, 8, 12))
        draw.arc((TUNNEL_X - 1, 18, TUNNEL_X + 8, 27), 180, 360, fill=(90, 86, 92))

    def _board(self, frame, draw, t, left, right):
        """The departure board on the station roof: where the next one goes, and
        when. Real when the Departures plugin is installed, the town's own service
        when it is not."""
        where, when = self.board
        if not where or right < PLATFORM_X or left > PLATFORM_X + 80:
            return
        if self.train is not None and self.train["state"] == "stopped":
            when, where = "NOW", self.train.get("to") or where
        width = max(tiny_width(where), tiny_width(when), 30) + 6
        x = PLATFORM_X + 14
        if x < left or x + width > right:      # never half a board at the panel edge
            return
        draw.rectangle((x, 2, x + width - 1, 14), fill=(10, 10, 14), outline=(64, 58, 44))
        for leg in (x + 2, x + width - 3):
            draw.line((leg, 15, leg, CANOPY_Y - 2), fill=(64, 58, 44))
        draw_tiny(frame, where, x + width // 2 - tiny_width(where) // 2, 3, (255, 176, 20))
        colour = (90, 220, 120) if when != "NOW" else (255, 210, 60) if math.floor(t * 2) % 2 else (120, 90, 30)
        draw_tiny(frame, when, x + width // 2 - tiny_width(when) // 2, 9, colour)

    def _train_at(self, frame, draw, pixels, night, t):
        """A locomotive and its carriages, however far along the platform it is."""
        train = self.train
        if train is None:
            return
        body, band, trim = LIVERY
        if night:
            body, band = dim(body, .6), dim(band, .55)
        front = train["x"]
        moving = train["state"] != "stopped"
        for car in range(CARRIAGES):
            x = round(front + car * CAR_LENGTH)
            if x > WORLD or x + CAR_LENGTH < TUNNEL_X:
                continue
            draw.rectangle((x, TRAIN_TOP + 1, x + CAR_LENGTH - 2, 29), fill=body)
            draw.rectangle((x + 1, TRAIN_TOP, x + CAR_LENGTH - 3, TRAIN_TOP), fill=trim)
            draw.rectangle((x, 28, x + CAR_LENGTH - 2, 28), fill=band)
            glass = (255, 226, 150) if train["lit"] and (night or car % 2 == 0) else (120, 170, 210)
            for dx in range(3, CAR_LENGTH - 3):
                if dx in (4, 5, 15, 16):      # the doors
                    continue
                if dx % 4 != 3:
                    for row in (26, 27):
                        pixels[min(WORLD - 1, max(0, x + dx)), row] = glass
            for door in (4, 15):              # doorways, open and lit at a stand
                lit = (240, 236, 200) if not moving else (24, 26, 34)
                draw.rectangle((x + door, TRAIN_TOP + 1, x + door + 1, 29), fill=lit if not moving else trim)
                if not moving:
                    draw.rectangle((x + door, 25, x + door + 1, 25), fill=(250, 240, 180))
            for bogie in (3, CAR_LENGTH - 6):
                draw.rectangle((x + bogie, WHEEL_Y, x + bogie + 2, 31), fill=(20, 20, 26))
                if moving and math.floor(t * 9 + x) % 2:
                    plot(frame, pixels, x + bogie + 1, 31, (90, 90, 100))
        # The cab at the front, and its headlight coming out of the dark.
        nose = round(front)
        draw.rectangle((nose, TRAIN_TOP + 1, nose + 1, 27), fill=trim)
        if nose > TUNNEL_X:
            light = (255, 248, 210) if moving or night else (200, 200, 190)
            plot(frame, pixels, nose - 1, 27, light)
            if night or moving:
                for reach in range(1, 6):
                    plot(frame, pixels, nose - 1 - reach, 27, dim(light, .45 - reach * .07))

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
    def _person(frame, pixels, person, t, ground=STREET_Y, wet=False):
        x = round(person["x"])
        step = person["slot"] is None and person["pause"] <= 0 and math.floor(t * 6 + person["speed"]) % 2
        top = ground - 5
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
        if person["bag"] > 0:     # a shopping bag from the shop they just left
            hand = x + (3 if person["dir"] > 0 else -1)
            for row in (top + 2, top + 3):
                paint(frame, pixels, hand, row, (196, 150, 96))
        if wet and person["slot"] is None:   # an umbrella, in whatever colour it came in
            canopy = UMBRELLAS[int(person["speed"] * 10) % len(UMBRELLAS)]
            for dx in range(3):
                paint(frame, pixels, x + dx, top - 2, canopy)
            for dx in range(-1, 4):
                paint(frame, pixels, x + dx, top - 1, canopy)
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
    def _car(frame, pixels, car, night, t=0.0):
        y = STREET_Y + 1 if car["lane"] == 0 else STREET_Y + 3
        x = round(car["x"])
        body = VAN if car.get("van") else CAR
        stamp(frame, sprite(body, {"c": car["color"], "g": (110, 170, 220), "k": (12, 12, 16)},
                            flip=car["dir"] < 0), x, y)
        length = len(body[0]) - 1
        if car.get("police"):      # the roof bar, flashing red and blue
            flash = math.floor(t * 6) % 2
            paint(frame, pixels, x + 3, y - 1, (255, 40, 40) if flash else (40, 90, 255))
            paint(frame, pixels, x + 5, y - 1, (40, 90, 255) if flash else (255, 40, 40))
        front, back = (x + length, x) if car["dir"] > 0 else (x, x + length)
        plot(frame, pixels, back, y + 2, (255, 40, 40))
        if night:
            plot(frame, pixels, front, y + 2, (255, 244, 200))
            for reach in range(1, 5):
                plot(frame, pixels, front + reach * car["dir"], y + 2, dim((255, 230, 160), .5 - reach * .1))

    @staticmethod
    def _weather(frame, draw, pixels, kind, t, left=0, right=WORLD):
        # Only the weather the camera can see: the rest of it falls on a part of
        # town nobody is looking at, and costs a frame to draw.
        if kind in ("rain", "storm"):
            for n in range(48 if kind == "storm" else 32):
                x = left + (n * 37 + math.floor(t * 12)) % VIEW
                y = (n * 13 + t * 40 * (1 + n % 3 * .2)) % 34 - 2
                plot(frame, pixels, x, y, (90, 140, 230))
                plot(frame, pixels, x, y + 1, (60, 100, 180))
            if kind == "storm" and (t % 6.1) < .1:
                draw.line((left + 70, 0, left + 64, 8, left + 69, 8, left + 62, 18), fill=(255, 250, 200))
        elif kind == "snow":
            for n in range(68):
                x = left + (n * 41 + round(math.sin(t + n) * 2)) % VIEW
                y = (n * 17 + t * 8) % 32
                plot(frame, pixels, x, y, (235, 240, 250))
        elif kind == "fog":
            for row, y in enumerate((12, 17, 22)):
                shift = math.floor(t * (3 + row)) % 10
                for x in range(left, min(WORLD, right)):
                    if (x + shift) % 10 < 7:
                        plot(frame, pixels, x, y, (70, 76, 84))


def validate(settings):
    if not isinstance(settings.get("real_data", True), bool):
        raise ValueError("real_data must be on or off")
    name = settings.get("town_name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9 ]{1,10}", name):
        raise ValueError("town_name must be 1–10 letters, numbers or spaces")


plugin = Plugin("town", "Pixel Town", module=Town, defaults={"town_name": "RACKVILLE", "real_data": True},
                validate_settings=validate,
                help={"town_name": "Shown on the rooftop sign (up to 10 characters)",
                      "real_data": "Use the real tide and departures when the Surf and Departures plugins are installed. "
                                   "Off, the town keeps its own sea and timetable"})
