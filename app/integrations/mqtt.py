"""A small MQTT 3.1.1 client for asyncio: connect, publish, subscribe, keepalive.

Enough for Home Assistant discovery and commands at QoS 0, with no extra
package to install on the Pi.
"""
from __future__ import annotations

import asyncio
import struct


class MQTTError(ConnectionError):
    pass


def _string(value):
    data = value.encode() if isinstance(value, str) else bytes(value)
    return struct.pack(">H", len(data)) + data


def _length(size):
    out = bytearray()
    while True:
        digit, size = size % 128, size // 128
        out.append(digit | (0x80 if size else 0))
        if not size:
            return bytes(out)


def packet(kind, body=b""):
    return bytes([kind]) + _length(len(body)) + body


CONNACK_ERRORS = {1: "unsupported protocol", 2: "client id rejected", 3: "broker unavailable",
                  4: "wrong username or password", 5: "not authorised"}


class MQTTClient:
    def __init__(self, host, port=1883, client_id="rackticker", username="", password="",
                 will=None, keepalive=30):
        self.host, self.port, self.client_id = host, port, client_id
        self.username, self.password, self.will, self.keepalive = username, password, will, keepalive
        self.reader = self.writer = None
        self.packet_id = 0
        self.messages = asyncio.Queue()
        self.tasks = []

    async def connect(self, timeout=8):
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout)
        flags = 0x02
        payload = _string(self.client_id)
        if self.will:
            topic, message = self.will
            flags |= 0x04 | 0x20  # will, retained
            payload += _string(topic) + _string(message)
        if self.username:
            flags |= 0x80
            payload += _string(self.username)
            if self.password:
                flags |= 0x40
                payload += _string(self.password)
        header = _string("MQTT") + bytes([4, flags]) + struct.pack(">H", self.keepalive)
        self.writer.write(packet(0x10, header + payload))
        await self.writer.drain()
        kind, body = await asyncio.wait_for(self._read(), timeout)
        if kind >> 4 != 2 or len(body) < 2:
            raise MQTTError("Unexpected reply from the MQTT broker")
        if body[1]:
            raise MQTTError(f"MQTT broker refused the connection: {CONNACK_ERRORS.get(body[1], body[1])}")
        self.tasks = [asyncio.create_task(self._pump()), asyncio.create_task(self._ping())]

    async def _read(self):
        first = (await self.reader.readexactly(1))[0]
        size, shift = 0, 0
        while True:
            byte = (await self.reader.readexactly(1))[0]
            size |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
            if shift > 21:
                raise MQTTError("Malformed MQTT packet")
        return first, await self.reader.readexactly(size) if size else b""

    async def _pump(self):
        try:
            while True:
                kind, body = await self._read()
                if kind >> 4 == 3:  # PUBLISH
                    length = struct.unpack(">H", body[:2])[0]
                    topic = body[2:2 + length].decode("utf-8", "replace")
                    offset = 2 + length + (2 if (kind >> 1) & 3 else 0)
                    await self.messages.put((topic, body[offset:]))
        except (asyncio.IncompleteReadError, ConnectionError, OSError, MQTTError):
            await self.messages.put(None)  # connection lost

    async def _ping(self):
        while True:
            await asyncio.sleep(self.keepalive * .6)
            await self._send(packet(0xC0))

    async def _send(self, data):
        self.writer.write(data)
        await self.writer.drain()

    async def publish(self, topic, payload, retain=False):
        payload = payload.encode() if isinstance(payload, str) else payload
        await self._send(packet(0x30 | (1 if retain else 0), _string(topic) + payload))

    async def subscribe(self, *topics):
        self.packet_id = self.packet_id % 65535 + 1
        body = struct.pack(">H", self.packet_id) + b"".join(_string(topic) + b"\x00" for topic in topics)
        await self._send(packet(0x82, body))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        if self.writer:
            try:
                await self._send(packet(0xE0))
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), 2)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                pass
