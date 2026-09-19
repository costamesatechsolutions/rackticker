"""Measure this machine; does not claim Raspberry Pi performance."""
import argparse
import asyncio
import time
from app.core.config import validate_config
from app.core.runtime import Runtime
from app.outputs.browser import BrowserSink


async def measure(seconds):
    runtime = Runtime(validate_config({"display":{"transition":"cut"}}),BrowserSink())
    await runtime.start()
    try:
        for module in ("sports","message"):
            runtime.preview(module)
            before_frames = runtime.frame_count
            start = time.monotonic()
            cpu = time.process_time()
            await asyncio.sleep(seconds)
            elapsed = time.monotonic()-start
            cpu_used = time.process_time()-cpu
            print(f"{module:8s} wall={elapsed:.2f}s cpu={cpu_used:.4f}s "
                  f"one_core={100*cpu_used/elapsed:.2f}% changed_frames={runtime.frame_count-before_frames}")
    finally: await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=float,default=10)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30: parser.error("--seconds must be from 1 to 30")
    asyncio.run(measure(args.seconds))
