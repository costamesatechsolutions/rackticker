"""Deterministic scheduler driven by elapsed monotonic time, never by rendering.

An interrupt suspends the exact playlist cursor, including its elapsed dwell.
Higher priorities can nest (depth bounded to 8). Equal/lower priority events
are coalesced while an interrupt is active, avoiding a stale event backlog.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

from app.core.models import PriorityEvent
from app.core.playlist import PlaylistEntry

log = logging.getLogger("playlist")
HOLD_LIMIT = 75
# An interrupt never yanks a screen before anyone could read it; it waits until
# the current screen has had this long, and is dropped if still waiting later.
MIN_READ_SECONDS = 12
PENDING_TTL = 45
# A screen that says it has nothing to show is given this long to change its mind before
# the playlist moves on: a feed that blinks, a plugin restarting or a plane at the edge of
# range must not snatch the screen from someone who is reading it.
GONE_GRACE = 4.0
# ...and a screen that is midway through something (a plane's card, a headline) is let
# finish it, up to this long, even once it has nothing new to show.
GONE_FINISH = 15.0


@dataclass
class Cursor:
    id: str
    module: str
    duration: float
    elapsed: float = 0
    kind: str = "playlist"
    priority: int = 0
    held: bool = False
    gone: float = 0.0       # how long the screen has been saying it has nothing to show


class Scheduler:
    def __init__(self, entries: list[PlaylistEntry]):
        self.entries = entries
        self.index = -1
        self.current = None
        self.suspended = []
        self.paused = False
        self.revision = 0
        self.pending = None
        self._hold = None

    def _switch(self, cursor):
        self.current = cursor
        self.revision += 1
        if cursor:
            log.info("%s -> %s", cursor.kind, cursor.module)

    def next(self, eligible):
        self.suspended.clear()
        for _ in self.entries:
            self.index = (self.index + 1) % len(self.entries)
            entry = self.entries[self.index]
            if entry.enabled and entry.id in eligible:
                self._switch(Cursor(entry.id, entry.module, entry.duration))
                return
        if self.current is not None:
            self._switch(None)

    def tick(self, dt, eligible, hold=None):
        """`hold(cursor)` lets a screen finish what it is showing (a crawl lap,
        a headline) before the playlist moves on, bounded by HOLD_LIMIT."""
        self._hold = hold
        if self.current is None:
            self.next(eligible)
        c = self.current
        if c and c.kind == "playlist":
            if c.id in eligible:
                c.gone = 0.0
            else:
                c.gone += max(0, dt)
                if c.gone >= GONE_GRACE and not (c.gone < GONE_FINISH and hold and hold(c)):
                    self.next(eligible)
        if self.paused or self.current is None:
            return
        if self.pending:
            event, waited = self.pending
            waited += max(0, dt)
            self.pending = None if waited > PENDING_TTL else (event, waited)
        remaining_dt = max(0, dt)
        # Bound catch-up after a machine wakes; do not spin over hours of missed entries.
        for _ in range(64):
            c = self.current
            if c is None:
                return
            left = c.duration - c.elapsed
            if remaining_dt < left:
                c.elapsed += remaining_dt
                self._release_pending()
                return
            if (hold and c.kind == "playlist" and remaining_dt < 5
                    and c.elapsed < c.duration + HOLD_LIMIT and hold(c)):
                c.elapsed += remaining_dt
                c.held = True
                self._release_pending()
                return
            remaining_dt -= max(0, left)
            if self.pending and c.kind == "playlist":
                # A waiting event starts at the natural break between screens,
                # and the playlist then continues with a fresh next screen.
                event, _ = self.pending
                self.pending = None
                self.next(eligible)
                self._start(event)
            elif self.suspended:
                resumed = self.suspended.pop()
                self._switch(resumed)
                if resumed.kind == "playlist" and resumed.id not in eligible:
                    self.next(eligible)
            else:
                self.next(eligible)
            if remaining_dt <= 0:
                return

    def _mid_read(self, c):
        """True while an automatic event should keep waiting: the screen hasn't had its
        minimum read time yet, or (like the natural duration-expiry path) it is mid-story
        and hold() says so. Bounded by PENDING_TTL, so a screen that never breaks just
        loses the automatic event rather than being cut off mid-crawl."""
        if c.elapsed < MIN_READ_SECONDS:
            return True
        return bool(self._hold and self._hold(c))

    def interrupt(self, event: PriorityEvent, defer: bool = False):
        """`defer` is for automatic events (a plane passing): they wait for the
        current screen's read time, and for it to finish what it is showing.
        Manual interrupts start immediately."""
        c = self.current
        if c and c.kind == "interrupt" and event.priority <= c.priority:
            return False
        if len(self.suspended) >= 8:
            return False
        if defer and c and c.kind == "playlist" and self._mid_read(c):
            if self.pending and event.priority <= self.pending[0].priority:
                return False
            self.pending = (event, 0.0)
            return True
        self._start(event)
        return True

    def _release_pending(self):
        """Start a waiting event once the current screen has been readable long enough
        and has finished what it was showing."""
        c = self.current
        if not self.pending or c is None:
            return
        event, _ = self.pending
        if c.kind == "interrupt" and event.priority <= c.priority:
            return
        if c.kind != "playlist" or not self._mid_read(c):
            self.pending = None
            self._start(event)

    def _start(self, event):
        if self.current:
            self.suspended.append(self.current)
        self._switch(Cursor("event", event.module, event.duration, kind="interrupt", priority=event.priority))

    def preview(self, module):
        self.pending = None
        self.suspended.clear()
        self._switch(Cursor("preview", module, float("inf"), kind="preview"))

    def resume(self, eligible):
        self.paused = False
        if self.current and self.current.kind == "preview":
            # Return to the currently indexed playlist item, at the start of its dwell.
            self.index -= 1
            self.next(eligible)

    def replace(self, entries, eligible):
        previous_id = self.current.id if self.current else None
        self.entries = entries
        self.suspended.clear()
        for index, entry in enumerate(entries):
            if entry.id == previous_id and entry.id in eligible:
                self.index = index
                self.current.duration = entry.duration
                self.current.elapsed = min(self.current.elapsed, entry.duration)
                return
        self.index = -1
        self.next(eligible)

    def status(self):
        c = self.current
        return {"module": c.module if c else None, "entry_id": c.id if c else None,
                "kind": c.kind if c else "idle", "paused": self.paused,
                "held": bool(c and c.held),
                "pending": self.pending[0].module if self.pending else None,
                "elapsed": round(c.elapsed, 2) if c else 0,
                "duration": c.duration if c and c.kind != "preview" else None,
                "remaining": round(max(0, c.duration - c.elapsed), 2) if c and c.kind != "preview" else None}
