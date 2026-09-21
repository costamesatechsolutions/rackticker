"""Give memory back to the system.

A Raspberry Pi 3 has 512 MB, and Python's allocator (glibc's malloc under it) keeps
freed memory in per-thread pools rather than returning it. After a plugin has parsed
a big feed that memory sits there, and the kernel, short of room, pushes something
else out to the SD card; the next frame that needs it waits for the card. Asking
malloc to hand its free pages back is cheap (a millisecond or two) and keeps the
processes at the size of what they are really using.
"""
from __future__ import annotations

import ctypes
import sys

_trim = None


def trim():
    """Return freed heap memory to the operating system. Quietly does nothing where it cannot."""
    global _trim
    if _trim is None:
        _trim = False
        if sys.platform.startswith("linux"):
            try:
                _trim = ctypes.CDLL("libc.so.6").malloc_trim
            except (OSError, AttributeError):
                pass
    if _trim:
        try:
            _trim(0)
        except Exception:
            pass
