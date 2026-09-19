"""Home Assistant discovery and commands against a tiny in-process MQTT broker."""
import asyncio
import json
from pathlib import Path
import struct
import tempfile
import unittest

from app.integrations.mqtt import _string, packet
from app.web.server import RUNTIME, create_app


class FakeBroker:
    def __init__(self):
        self.published = {}
        self.writer = None
        self.connected = asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_server(self.client, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def client(self, reader, writer):
        self.writer = writer
        try:
            while True:
                first = (await reader.readexactly(1))[0]
                size, shift = 0, 0
                while True:
                    byte = (await reader.readexactly(1))[0]
                    size |= (byte & 0x7F) << shift
                    shift += 7
                    if not byte & 0x80:
                        break
                body = await reader.readexactly(size) if size else b""
                kind = first >> 4
                if kind == 1:
                    writer.write(packet(0x20, b"\x00\x00"))
                    self.connected.set()
                elif kind == 3:
                    length = struct.unpack(">H", body[:2])[0]
                    self.published[body[2:2 + length].decode()] = body[2 + length:].decode()
                elif kind == 8:
                    writer.write(packet(0x90, body[:2] + b"\x00"))
                elif kind == 12:
                    writer.write(packet(0xD0))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def send(self, topic, payload):
        self.writer.write(packet(0x30, _string(topic) + payload.encode()))
        await self.writer.drain()


class HomeAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_brightness_power_and_screen_commands(self):
        broker = FakeBroker()
        port = await broker.start()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"home_assistant": {"enabled": True, "host": "127.0.0.1", "port": port,
                                                           "password": "hunter2", "username": "ha"}}))
            app = create_app(path)
            runtime = app[RUNTIME]
            await runtime.start()
            runtime.home_assistant.reconfigure(runtime.config)
            try:
                await asyncio.wait_for(broker.connected.wait(), 5)
                for _ in range(100):
                    if "rackticker/rackticker/light/state" in broker.published:
                        break
                    await asyncio.sleep(.05)
                config = json.loads(broker.published["homeassistant/select/rackticker/screen/config"])
                self.assertIn("Clock", config["options"])
                self.assertIn("homeassistant/switch/rackticker/show_clock/config", broker.published)
                await broker.send("rackticker/rackticker/light/set", '{"state": "ON", "brightness": 40}')
                await broker.send("rackticker/rackticker/show/tixclock/set", "ON")
                await asyncio.sleep(.3)
                self.assertEqual(runtime.config["display"]["brightness"], 40)
                self.assertEqual(runtime.scheduler.current.module, "tixclock")
                await broker.send("rackticker/rackticker/light/set", '{"state": "OFF"}')
                await asyncio.sleep(.3)
                self.assertEqual(runtime.sink.brightness, 0)
                self.assertFalse(runtime.power)
                self.assertEqual(json.loads(broker.published["rackticker/rackticker/light/state"])["state"], "OFF")
                self.assertEqual(broker.published["rackticker/rackticker/show/tixclock/state"], "ON")
            finally:
                await runtime.home_assistant.close()
                await runtime.close()
                broker.server.close()
