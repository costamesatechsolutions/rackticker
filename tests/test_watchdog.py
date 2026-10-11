import os
import socket
import tempfile
import unittest
from unittest import mock

from app.core import watchdog


class WatchdogTest(unittest.TestCase):
    def test_no_socket_means_nothing_to_do(self):
        with mock.patch.object(watchdog, "_address", ""):
            self.assertFalse(watchdog.notify("WATCHDOG=1"))

    def test_beats_reach_systemd_at_most_every_few_seconds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "notify")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
                server.bind(path)
                server.settimeout(1)
                with mock.patch.object(watchdog, "_address", path), mock.patch.object(watchdog, "_sent", 0.0):
                    watchdog.beat(100.0)
                    watchdog.beat(101.0)
                    watchdog.beat(100.0 + watchdog.BEAT_SECONDS)
                self.assertEqual(server.recv(64), b"WATCHDOG=1")
                self.assertEqual(server.recv(64), b"WATCHDOG=1")
                server.settimeout(0.05)
                with self.assertRaises(socket.timeout):
                    server.recv(64)

    def test_unit_has_a_watchdog(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "deploy", "rackticker.service")) as file:
            unit = file.read()
        self.assertIn("WatchdogSec=", unit)
        self.assertIn("NotifyAccess=main", unit)


if __name__ == "__main__":
    unittest.main()
