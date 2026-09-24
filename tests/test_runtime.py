import asyncio
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from pathlib import Path
import socket
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch
from PIL import Image

from app.core.config import validate_config
from app.core.runtime import Runtime
from app.core.models import utcnow
from app.core.renderer import validate_frame
from app.outputs.browser import BrowserSink
from app.outputs.base import FrameSink
from app.outputs.hub75 import Hub75Sink


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.r = Runtime(validate_config({"display":{"transition":"cut"}}), BrowserSink())
        for name in self.r.providers: await self.r.refresh_provider(name)
        self.now = time.monotonic()

    async def tick(self, dt=.1):
        self.now += dt
        await self.r.step(dt,self.now)

    async def test_all_modules_and_scenarios_emit_rgb128x32(self):
        for name, provider in self.r.providers.items():
            for scenario in provider.scenarios:
                await self.r.scenario(name,scenario)
                self.assertIsNotNone(validate_frame(self.r.modules[name].render(self.r.context())).getbbox())
        for module in self.r.modules.values():
            validate_frame(module.render(self.r.context()))

    async def test_png_and_multiple_sinks_receive_canonical_bytes(self):
        class Capture(FrameSink):
            async def display(self, frame): self.frame = frame
            async def close(self): pass
        capture = Capture()
        self.r.outputs.append(capture)
        self.r.preview("sports")
        await self.tick()
        image = Image.open(BytesIO(self.r.png()))
        self.assertEqual(image.mode,"RGB")
        self.assertEqual(image.size,(128,32))
        self.assertEqual(image.tobytes(),self.r.sink.pixels)
        self.assertIs(capture.frame, self.r.frame)
        before = self.r.png()
        self.r.set_brightness(10)
        self.assertEqual(before, self.r.png())
        self.assertEqual(image.tobytes(),self.r.sink.pixels)

    async def test_module_exception_skips_to_next_screen(self):
        with patch.object(self.r.modules["clock"],"render",side_effect=RuntimeError("bad module")):
            await self.tick()
            self.assertIn("clock",self.r.failed_until)
            await self.tick()
            self.assertEqual(self.r.scheduler.current.module,"tixclock")
            self.assertIsNotNone(self.r.frame.getbbox())

    async def test_provider_failure_retains_data_marks_stale_and_recovers(self):
        original = self.r.snapshots["sports"]
        async def down(): raise ConnectionError("no route to host")
        with patch.object(self.r.providers["sports"],"fetch",down):
            await self.r.refresh_provider("sports")
            # one slow or failed reply is forgiven; an outage is only called one once it goes on
            self.assertFalse(self.r.snapshots["sports"].stale)
            self.assertEqual(self.r.snapshots["sports"].error, "no route to host")
            for _ in range(2):
                await self.r.refresh_provider("sports")
        self.assertTrue(self.r.snapshots["sports"].stale)
        await self.r.refresh_provider("sports")            # a good reply clears it at once
        self.assertFalse(self.r.snapshots["sports"].stale)
        original = self.r.snapshots["sports"]
        self.r.provider_fault = True                       # the developer's simulated outage shows straight away
        await self.r.refresh_provider("sports")
        stale = self.r.snapshots["sports"]
        self.assertEqual(stale.data,original.data)
        self.assertEqual(stale.updated_at,original.updated_at)
        self.assertTrue(stale.stale)
        stale_frame = self.r.modules["sports"].render(self.r.context())
        self.r.provider_fault = False
        await self.r.refresh_provider("sports")
        fresh_frame = self.r.modules["sports"].render(self.r.context())
        self.assertNotEqual(stale_frame.crop((0,24,128,32)).tobytes(), fresh_frame.crop((0,24,128,32)).tobytes())
        self.assertFalse(self.r.snapshots["sports"].stale)

    async def test_a_failing_provider_is_asked_less_often_and_again_at_once_when_it_recovers(self):
        from app.providers.base import retry_seconds
        self.assertEqual([retry_seconds(n) for n in range(1, 7)], [5, 10, 20, 40, 60, 60])
        asked = []
        async def down():
            asked.append(time.monotonic())
            raise ConnectionError("429 too many requests")
        with patch.object(self.r.providers["sports"], "fetch", down):
            for _ in range(3):
                await self.r.refresh_provider("sports")
        self.assertGreater(self.r.retry_at["sports"] - time.monotonic(), 15)
        await self.r.refresh_provider("sports")             # it answers again
        self.assertNotIn("sports", self.r.retry_at)
        with patch.object(self.r.providers["sports"], "fetch", down):
            await self.r.refresh_provider("sports")
        self.r.apply_config(self.r.config)                  # new settings: ask again straight away
        self.assertEqual(self.r.retry_at, {})

    async def test_old_provider_timestamp_is_stale(self):
        old = replace(self.r.snapshots["sports"], updated_at=utcnow()-timedelta(minutes=2))
        async def fetch(): return old
        with patch.object(self.r.providers["sports"],"fetch",fetch): await self.r.refresh_provider("sports")
        self.assertTrue(self.r.snapshots["sports"].stale)

    async def test_provider_io_does_not_block_rendering(self):
        async def slow(): await asyncio.sleep(10)
        with patch.object(self.r.providers["sports"],"fetch",slow):
            task = asyncio.create_task(self.r.refresh_provider("sports"))
            try:
                await asyncio.wait_for(self.tick(),.5)
                self.assertFalse(task.done())
                self.assertIsNotNone(self.r.frame.getbbox())
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def test_flight_eligibility_and_disabled_interrupt(self):
        self.assertNotIn("flight-1",self.r.eligible())
        await self.r.scenario("flight","united")
        self.assertIn("flight-1",self.r.eligible())
        await self.r.scenario("flight","high")
        self.assertNotIn("flight-1",self.r.eligible())
        self.r.config["modules"]["sports"]["enabled"] = False
        self.assertFalse(self.r.interrupt("sports"))

    async def test_static_frames_are_not_redrawn_or_resent(self):
        self.r.preview("sports")
        with patch.object(self.r.modules["sports"],"render", wraps=self.r.modules["sports"].render) as render:
            await self.tick()
            first = self.r.sink.sequence
            for _ in range(20): await self.tick()
            self.assertEqual(render.call_count,1)
            self.assertEqual(self.r.sink.sequence,first)

    async def test_slow_animation_does_not_slow_playlist(self):
        self.r.config["simulator"]["animation_speed"] = .1
        await self.tick(3)
        self.assertAlmostEqual(self.r.animation_clock,.3)
        self.assertAlmostEqual(self.r.scheduler.current.elapsed,3)

    async def test_sequence_export_is_bounded_and_canonical(self):
        self.r.preview("sports")
        for _ in range(50): await self.tick(.11)
        self.assertEqual(len(self.r.history),30)
        with zipfile.ZipFile(BytesIO(self.r.sequence_zip())) as z:
            self.assertEqual(len(z.namelist()),31)
            image = Image.open(BytesIO(z.read("frame-029.png")))
            self.assertEqual(image.tobytes(),self.r.frame.tobytes())

    async def test_slow_browser_notifications_are_bounded(self):
        event = self.r.sink.subscribe()
        for i in range(60):
            await self.r.sink.display(Image.new("RGB",(128,32),(i,0,0)))
        self.assertTrue(event.is_set())
        self.assertEqual(self.r.sink.pixels,Image.new("RGB",(128,32),(59,0,0)).tobytes())
        self.assertEqual(len(self.r.sink.listeners),1)
        self.r.sink.unsubscribe(event)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"),
                         "HUB75 transport uses Unix-domain datagram sockets")
    async def test_hub75_sink_sends_brightness_and_canonical_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "matrix.sock")
            receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            receiver.bind(path)
            receiver.settimeout(1)
            sink = Hub75Sink(path)
            sink.set_brightness(7)
            frame = Image.new("RGB", (128, 32), (1, 2, 3))
            receive = asyncio.create_task(asyncio.to_thread(
                lambda: [receiver.recv(2000) for _ in range(8)]))
            await asyncio.sleep(0)
            await sink.display(frame)
            packets = await asyncio.wait_for(receive, 1)
            self.assertTrue(all(packet[:4] == b"RTK1" for packet in packets))
            self.assertTrue(all(packet[8] == 7 for packet in packets))
            chunks = sorted(packets, key=lambda packet: packet[9])
            self.assertEqual(b"".join(packet[10:] for packet in chunks), frame.tobytes())
            await sink.close()
            receiver.close()
