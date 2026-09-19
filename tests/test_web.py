"""Integration tests use an ephemeral localhost port, never the user's running app."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from app.web.server import create_app, RUNTIME


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "config.json"
        self.app = create_app(self.path)
        self.app[RUNTIME].config["display"]["transition"] = "cut"
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"X-RackTicker":"1"}

    async def asyncTearDown(self):
        await self.client.close()
        self.folder.cleanup()

    async def post(self, route, body):
        return await self.client.post(route, json=body, headers=self.headers)

    async def test_static_ui_and_rejected_cross_origin_mutations(self):
        for route in ("/", "/static/app.js", "/static/styles.css"):
            response = await self.client.get(route)
            self.assertEqual(response.status,200)
        response = await self.client.post("/api/control",json={"action":"next"})
        self.assertEqual(response.status,403)
        response = await self.client.post("/api/control",json={"action":"next"},
                                          headers={**self.headers,"Origin":"https://untrusted.example"})
        self.assertEqual(response.status,403)

    async def test_settings_persist_and_invalid_updates_leave_them_intact(self):
        response = await self.client.get("/api/config")
        config = await response.json()
        config["modules"]["clock"]["hour_format"] = "24"
        response = await self.client.put("/api/config",json=config,headers=self.headers)
        self.assertEqual(response.status,200)
        self.assertEqual(json.loads(self.path.read_text())["modules"]["clock"]["hour_format"],"24")
        original = self.path.read_bytes()
        response = await self.client.put("/api/config",json={"display":{"brightness":900}},headers=self.headers)
        self.assertEqual(response.status,400)
        self.assertEqual(original,self.path.read_bytes())

    async def test_websocket_png_and_runtime_match_byte_for_byte(self):
        await self.post("/api/control",{"action":"preview","module":"sports"})
        runtime = self.app[RUNTIME]
        # Wait on a real observable frame notification, not a fixed test delay.
        notification = runtime.sink.subscribe()
        notification.clear()
        await asyncio.wait_for(notification.wait(),1)
        runtime.sink.unsubscribe(notification)
        ws = await self.client.ws_connect("/ws")
        try:
            while True:
                message = await asyncio.wait_for(ws.receive(),1)
                if message.type == WSMsgType.BINARY: break
            self.assertEqual(len(message.data),12288)
            response = await self.client.get("/api/frame.png")
            exported = Image.open(BytesIO(await response.read()))
            self.assertEqual(exported.size,(128,32))
            self.assertEqual(exported.mode,"RGB")
            self.assertEqual(message.data, exported.tobytes())
            self.assertEqual(message.data, runtime.frame.tobytes())
        finally: await ws.close()

    async def test_priority_event_and_fault_recovery_api(self):
        response = await self.post("/api/scenario",{"module":"flight","scenario":"united","interrupt":True})
        data = await response.json()
        self.assertTrue(data["interrupt_accepted"])
        self.assertEqual(data["state"]["scheduler"]["kind"],"interrupt")
        await self.post("/api/control",{"action":"fault","enabled":True})
        response = await self.client.get("/api/state")
        self.assertTrue((await response.json())["providers"]["sports"]["stale"])
        await self.post("/api/control",{"action":"fault","enabled":False})
        self.assertFalse(self.app[RUNTIME].snapshots["sports"].stale)

    async def test_all_preview_endpoints_and_custom_message(self):
        for module in self.app[RUNTIME].modules:
            response = await self.post("/api/control",{"action":"preview","module":module})
            self.assertEqual(response.status,200)
            self.assertEqual((await response.json())["scheduler"]["module"],module)
        response = await self.post("/api/scenario",{"module":"message","message":{"title":"GARAGE","body":"DOOR OPEN","scrolling":False}})
        self.assertEqual(response.status,200)
        self.assertEqual(self.app[RUNTIME].message.body,"DOOR OPEN")
        for body in ({"action":"preview","module":"unknown"},{"action":"demo","enabled":"yes"}):
            response = await self.post("/api/control",body)
            self.assertEqual(response.status,400)
        response = await self.post("/api/scenario",{"module":"message","message":{"body":""}})
        self.assertEqual(response.status,400)

    async def test_sequence_export_is_real_zip_of_rgb_frames(self):
        response = await self.client.get("/api/sequence.zip")
        self.assertEqual(response.status,200)
        with zipfile.ZipFile(BytesIO(await response.read())) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["mode"],"RGB")
            self.assertEqual((manifest["width"],manifest["height"]),(128,32))
            for entry in manifest["frames"]:
                self.assertEqual(Image.open(BytesIO(archive.read(entry["file"]))).size,(128,32))

    async def test_catalog_and_source_offer(self):
        response = await self.client.get("/api/catalog")
        catalog = await response.json()
        self.assertEqual(catalog["api_version"], 1)
        self.assertEqual({item["name"] for item in catalog["modules"]}, set(self.app[RUNTIME].modules))
        self.assertTrue(all(item["available"] for item in catalog["modules"]))
        response = await self.client.get("/api/source.zip")
        self.assertEqual(response.status, 200)
        with zipfile.ZipFile(BytesIO(await response.read())) as archive:
            self.assertIn("rackticker-source/app/main.py", archive.namelist())
        response = await self.client.get("/static/LICENSE")
        self.assertIn("GNU AFFERO", await response.text())


if __name__ == "__main__": unittest.main()
