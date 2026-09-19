"""Nonblocking client for the privileged HUB75 matrix companion service.

Wire format: eight Unix datagrams containing one canonical 128×32 row-major
RGB888 framebuffer. The burst is sent without scheduler yields; the companion
swaps only after all chunks arrive.
"""
from __future__ import annotations

import asyncio
import errno
import socket
import struct
from pathlib import Path

from app.core.renderer import validate_frame
from app.outputs.base import FrameSink

FRAME_BYTES = 128 * 32 * 3
CHUNKS = 8
CHUNK_BYTES = FRAME_BYTES // CHUNKS
HEADER = struct.Struct("!4sIBB")


class Hub75Sink(FrameSink):
    def __init__(self, socket_path="/run/rackticker/matrix.sock"):
        self.socket_path = str(Path(socket_path))
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.connected = False
        self.closed = False
        self.sequence = 0
        self.pixels = None
        self.tasks = set()
        self.lock = asyncio.Lock()

    async def _send(self, pixels):
        async with self.lock:
            if not self.connected:
                try:
                    self.socket.connect(self.socket_path)
                    self.connected = True
                except (FileNotFoundError, ConnectionRefusedError):
                    return
            self.sequence = (self.sequence + 1) & 0xFFFFFFFF
            sequence, brightness = self.sequence, self.brightness
            for index in range(CHUNKS):
                start = index * CHUNK_BYTES
                packet = HEADER.pack(b"RTK1", sequence, brightness, index)
                packet += pixels[start:start + CHUNK_BYTES]
                for _ in range(2):
                    try:
                        self.socket.send(packet)
                        break
                    except OSError as exc:
                        if exc.errno in (errno.ENOENT, errno.ECONNREFUSED):
                            self.connected = False
                            return
                        if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS):
                            raise
                        await asyncio.sleep(0)
                else:
                    return

    async def display(self, frame):
        pixels = validate_frame(frame).tobytes()
        if len(pixels) != FRAME_BYTES:
            raise ValueError("Expected a 128×32 RGB frame")
        self.pixels = pixels
        await self._send(pixels)

    def set_brightness(self, value):
        super().set_brightness(value)
        if self.pixels is not None:
            try:
                task = asyncio.get_running_loop().create_task(self._send(self.pixels))
            except RuntimeError:
                return
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def close(self):
        if not self.closed:
            self.closed = True
            for task in self.tasks:
                task.cancel()
            self.socket.close()
