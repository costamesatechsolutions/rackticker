import asyncio
import socket
import unittest
from unittest import mock

from app.integrations import home_assistant
from app.integrations.home_assistant import HomeAssistant


class RetryTests(unittest.TestCase):
    """A broker that cannot be found is said once, in words that say what to fix."""

    def test_a_missing_broker_is_logged_once_and_retried_ever_more_slowly(self):
        bridge = HomeAssistant(runtime=None, store=None)
        waits = []

        async def session(settings):
            raise socket.gaierror(-2, "Name or service not known")

        async def sleep(seconds):
            waits.append(seconds)
            if len(waits) >= 10:
                raise asyncio.CancelledError

        bridge._session = session
        with mock.patch.object(home_assistant.asyncio, "sleep", sleep), \
                self.assertLogs("home_assistant", "WARNING") as logs:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(bridge._run({"host": "core-mosquito"}))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("IP address", bridge.status["error"])
        self.assertEqual(waits[-1], home_assistant.MAX_RETRY_SECONDS)


if __name__ == "__main__":
    unittest.main()
