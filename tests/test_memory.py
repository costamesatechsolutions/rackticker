"""The display's memory: handed back to the system, and swap that lives in RAM."""
from pathlib import Path
import subprocess
import unittest

from app.core import memory
from app.core.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


class MemoryTests(unittest.TestCase):
    def test_trimming_is_harmless_wherever_it_runs(self):
        memory.trim()
        memory.trim()

    def test_the_memory_script_is_valid_and_best_effort(self):
        script = ROOT / "deploy" / "rackticker-memory.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        text = script.read_text()
        self.assertIn("swapon --priority 100", text)             # ahead of the swap file on the SD card
        self.assertNotIn("set -e", text)                        # a kernel without zram must not fail the boot
        self.assertTrue(text.rstrip().endswith("exit 0"))

    def test_the_unit_runs_that_script_and_is_installed_and_enabled(self):
        unit = (ROOT / "deploy" / "rackticker-memory.service").read_text()
        self.assertIn("/opt/rackticker/current/deploy/rackticker-memory.sh start", unit)
        self.assertIn("Before=rackticker.service", unit)
        installer = (ROOT / "deploy" / "install-release.sh").read_text()
        self.assertIn("rackticker-memory", installer)
        self.assertIn("MALLOC_ARENA_MAX", (ROOT / "deploy" / "rackticker.service").read_text())

    def test_a_stall_is_recorded_with_what_it_was_doing(self):
        from app.core.config import validate_config
        from app.outputs.browser import BrowserSink
        runtime = Runtime(validate_config({}), BrowserSink())
        runtime.note_slow("stall", "news", .3)
        entry = runtime.slow[0]
        for key in ("cpu_ms", "faults", "gc_ms"):
            self.assertIn(key, entry)
