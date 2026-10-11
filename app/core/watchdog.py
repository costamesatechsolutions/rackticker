"""Tell systemd the display is still drawing.

rackticker.service sets WatchdogSec: if the render loop stops calling `beat()` for
that long (a deadlock, a wedged thread holding the interpreter, a stuck page-in),
systemd kills and restarts the display instead of leaving the panel on its last frame
for hours. Outside systemd (a laptop, tests) there is no NOTIFY_SOCKET and this does
nothing.
"""
from __future__ import annotations

import os
import socket
import time

BEAT_SECONDS = 5

# Read once, at import, and taken out of the environment: plugins' own processes are
# started later and must not answer for the display.
_path = os.environ.pop("NOTIFY_SOCKET", "")
_address = "\0" + _path[1:] if _path.startswith("@") else _path
_sent = 0.0


def notify(message: str) -> bool:
    address = _address
    if not address:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), address)
        return True
    except OSError:
        return False


def beat(now: float | None = None):
    """Called every frame; sends at most one heartbeat every few seconds."""
    global _sent
    now = time.monotonic() if now is None else now
    if now - _sent >= BEAT_SECONDS:
        _sent = now
        notify("WATCHDOG=1")
