"""Back-to-back playback of variable-length items (headlines, markets, sign
phrases) against a screen's scene clock.

A Storyboard never swaps data under an item that is on screen: new data is
adopted only between laps or at the next playlist visit. It resumes where the
previous visit stopped, and `hold()` keeps the playlist on this screen until
the item that was showing when the dwell expired has finished.
"""
from __future__ import annotations

import math


class Storyboard:
    def __init__(self, resume=True):
        self.resume = resume
        self.items = []          # [(payload, seconds)]
        self.offset = 0
        self.base = 0.0
        self.last_t = None
        self.visits = 0
        self.steps = 0           # items started since the visit began
        self.serial = 0
        self.key = None
        self.hold_serial = None
        self.scene = None

    def sync(self, t, build, scene=None):
        """Call every frame. `build(visit)` returns [(payload, seconds)].
        Pass `context.scene` so a new scene always starts a new visit."""
        fresh = (self.last_t is None or t < self.last_t - 1e-6
                 or (scene is not None and scene != self.scene))
        self.scene = scene
        if fresh or not self.items:
            items = [item for item in build(self.visits) if item[1] > 0]
            if fresh:
                if self.resume and self.items and items:
                    # The playlist leaves as the next item starts; replay that one.
                    self.offset = (self.offset + self.resume_steps()) % len(items)
                elif not self.resume:
                    self.offset = 0
                self.visits += 1
                self.base, self.steps, self.hold_serial = 0.0, 0, None
            self.items = items
        self.last_t = t

    def current(self, t, build=None):
        """(payload, local seconds, duration) for scene time t, or None."""
        if not self.items:
            return None
        total = sum(seconds for _, seconds in self.items)
        if t - self.base >= total:
            laps = math.floor((t - self.base) / total)
            self.base += laps * total
            self.offset = (self.offset + laps * len(self.items)) % len(self.items)
            if build:
                items = [item for item in build(self.visits) if item[1] > 0]
                if items:
                    self.offset %= len(items)
                    self.items = items
        local = t - self.base
        count = len(self.items)
        for step in range(count):
            payload, seconds = self.items[(self.offset + step) % count]
            if local < seconds or step == count - 1:
                key = (round(self.base, 6), step)
                if key != self.key:
                    self.key = key
                    self.serial += 1
                    self.steps += 1
                return payload, min(local, seconds), seconds
            local -= seconds
        return None

    def resume_steps(self):
        return max(0, self.steps - 1)

    def hold(self):
        if not self.items:
            return False
        if self.hold_serial is None:
            self.hold_serial = self.serial
        return self.serial == self.hold_serial
