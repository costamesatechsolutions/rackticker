import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

import rackticker
from app.core.config import validate_config
from app.core.models import Message, SystemStatus
from app.core.plugins import PluginRegistry
from app.core.renderer import validate_frame
from app.core.story import Storyboard
from app.modules.base import RenderContext

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hello_example", ROOT / "examples/hello-plugin/rackticker_hello.py")
HELLO = importlib.util.module_from_spec(spec)
spec.loader.exec_module(HELLO)


class SdkTests(unittest.TestCase):
    def test_public_toolkit_names_resolve(self):
        for name in rackticker.__all__:
            self.assertTrue(hasattr(rackticker, name), name)

    def test_hello_example_animates_migrates_and_holds(self):
        registry = PluginRegistry()
        registry.register(HELLO.plugin)
        config = validate_config({"plugins": {"hello": {"title": "OLD", "message": "HI"}},
                                  "modules": {"hello": {"enabled": True}},
                                  "playlist": [{"id": "hello", "module": "hello"}]}, registry)
        self.assertEqual(config["plugins"]["hello"]["message"], "OLD|HI")
        module, frames = HELLO.Hello(), set()
        for step in range(5 * 30):
            context = RenderContext(datetime.now(timezone.utc), step / 30, config, {}, Message(), SystemStatus())
            frames.add(validate_frame(module.render(context)).tobytes())
            if step == 30:
                self.assertTrue(module.hold(context))
        self.assertGreater(len(frames), 10)

    def test_storyboard_holds_current_item_and_replays_interrupted_one(self):
        board = Storyboard()
        build = lambda _visit: [("a", 2), ("b", 2), ("c", 2)]
        board.sync(0, build)
        self.assertEqual(board.current(0)[0], "a")
        board.sync(1, build)
        board.current(1)
        self.assertTrue(board.hold())
        board.sync(2.5, build)
        self.assertEqual(board.current(2.5)[0], "b")
        self.assertFalse(board.hold())
        board.sync(0, build)  # Next playlist visit: replay "b", which had just started.
        self.assertEqual(board.current(0)[0], "b")


if __name__ == "__main__":
    unittest.main()
