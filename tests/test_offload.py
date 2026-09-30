import asyncio
import operator
import time
import unittest

from app.core import offload


class HelperRecyclingTests(unittest.TestCase):
    """The helper process is replaced every so often, and no parse waits for it."""

    def setUp(self):
        self.saved = offload.TASKS_PER_HELPER
        offload.TASKS_PER_HELPER = 3
        offload.enable()

    def tearDown(self):
        offload.shutdown()
        offload.TASKS_PER_HELPER = self.saved
        offload._enabled = False

    def test_a_warm_helper_takes_over_and_work_never_stalls(self):
        async def go():
            pools, slowest = set(), 0.0
            for n in range(40):
                start = time.monotonic()
                self.assertEqual(await asyncio.wait_for(offload.offload(operator.add, n, 1), 20), n + 1)
                pools.add(id(offload._pool))
                if n > 0:   # the first call starts the first helper
                    slowest = max(slowest, time.monotonic() - start)
                await asyncio.sleep(.05)
            return pools, slowest
        pools, slowest = asyncio.run(go())
        self.assertGreater(len(pools), 1)       # it was replaced
        self.assertLess(slowest, 1.0)           # and nothing waited for a new one to start


if __name__ == "__main__":
    unittest.main()
