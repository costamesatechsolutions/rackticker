"""Run CPU-heavy provider work (JSON/XML parsing, image decoding) away from the
render loop.

A long ``json.loads`` or image decode holds Python's GIL, so moving it to a
thread still freezes frames; on a Raspberry Pi a 1 MB market feed stalled the
crawl for ~300 ms. When the app enables the helper process, work runs in a
separate interpreter instead and only the compact result comes back.

``await offload(function, *args)`` needs a module-level function whose module is
importable (installed plugins are). Anything that cannot run in the helper -- a
plugin loaded from a file path, or the helper being disabled as in tests -- runs
in a thread, so callers never need to care which path was taken.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import multiprocessing
import pickle

log = logging.getLogger("offload")
_enabled = False
_pool: concurrent.futures.ProcessPoolExecutor | None = None
_local_only: set[str] = set()


def enable():
    """Start using a helper process (called by the application entry point)."""
    global _enabled
    _enabled = True


def _executor():
    global _pool
    if _pool is None:
        # "spawn" starts a clean interpreter: no inherited threads, sockets or GPIO.
        _pool = concurrent.futures.ProcessPoolExecutor(
            max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    return _pool


async def offload(function, *args):
    global _pool
    name = f"{getattr(function, '__module__', '')}.{getattr(function, '__qualname__', '')}"
    if _enabled and name not in _local_only:
        try:
            return await asyncio.get_running_loop().run_in_executor(_executor(), function, *args)
        except (ImportError, AttributeError, pickle.PicklingError) as exc:
            # The helper cannot import or receive this function; use a thread
            # from now on. A genuine bug inside it simply raises again there.
            log.info("running %s in a thread: %s", name, exc)
            _local_only.add(name)
        except concurrent.futures.process.BrokenProcessPool:
            log.warning("helper process died; restarting it")
            _pool = None
    return await asyncio.to_thread(function, *args)


def shutdown():
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
