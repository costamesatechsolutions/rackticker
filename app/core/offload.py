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
import importlib
import logging
import multiprocessing
import pickle
import threading

log = logging.getLogger("offload")
_enabled = False
_pool: concurrent.futures.ProcessPoolExecutor | None = None
_local_only: set[str] = set()


def enable():
    """Start using a helper process (called by the application entry point)."""
    global _enabled
    _enabled = True


# The helper imports each plugin's module the first time it parses for it, so its
# memory grows with the number of screens. Starting a fresh one every so often hands
# that memory back; on a 512 MB Pi the helper had grown to a third of what was in use.
TASKS_PER_HELPER = 250
# Python's own recycling (max_tasks_per_child) is not used: on the Pi's Python 3.11
# the retired helper was not replaced until something else nudged the pool, and for
# up to a minute nothing could be parsed, every ~17 minutes; flights, sports and news
# all timed out together. Instead the next helper is started and has imported what
# the old one had before it takes over, so no parse ever waits for a helper to start.
_lock = threading.Lock()
_spare: concurrent.futures.ProcessPoolExecutor | None = None
_spare_ready: concurrent.futures.Future | None = None
_tasks = 0
_modules: set[str] = set()


def _new_pool():
    # "spawn" starts a clean interpreter: no inherited threads, sockets or GPIO.
    return concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))


def _warm(modules):
    """Run in a new helper before it takes over: import what the work will need."""
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception:   # the work itself will say what is wrong
            pass
    return True


def _executor():
    global _pool, _spare, _spare_ready, _tasks
    with _lock:
        if _pool is None:
            _pool, _tasks = _new_pool(), 0
        if _spare_ready is not None and _spare_ready.done():
            if _spare_ready.exception() is None:
                # The old helper finishes what it was given, then exits.
                _pool, old, _tasks = _spare, _pool, 0
                old.shutdown(wait=False)
            else:
                _spare.shutdown(wait=False, cancel_futures=True)
                _tasks = 0   # keep the helper we have and try again later
            _spare = _spare_ready = None
        elif _spare is None and _tasks >= TASKS_PER_HELPER:
            _spare = _new_pool()
            _spare_ready = _spare.submit(_warm, tuple(sorted(_modules)))
        _tasks += 1
        return _pool


async def offload(function, *args):
    global _pool
    name = f"{getattr(function, '__module__', '')}.{getattr(function, '__qualname__', '')}"
    if _enabled and name not in _local_only:
        pool = _executor()
        try:
            result = await asyncio.get_running_loop().run_in_executor(pool, function, *args)
            with _lock:
                _modules.add(getattr(function, "__module__", "") or "builtins")
            return result
        except (ImportError, AttributeError, pickle.PicklingError) as exc:
            # The helper cannot import or receive this function; use a thread
            # from now on. A genuine bug inside it simply raises again there.
            log.info("running %s in a thread: %s", name, exc)
            _local_only.add(name)
        except concurrent.futures.process.BrokenProcessPool:
            log.warning("helper process died; restarting it")
            with _lock:
                if _pool is pool:
                    _pool = None
    return await asyncio.to_thread(function, *args)


def shutdown():
    global _pool, _spare, _spare_ready
    with _lock:
        for pool in (_pool, _spare):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        _pool = _spare = _spare_ready = None
