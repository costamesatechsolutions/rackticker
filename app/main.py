import argparse
import logging
from pathlib import Path

from aiohttp import web
from app.core.config import ConfigError
from app.core import offload
from app.core.plugin_manager import PluginManager
from app.web.server import create_app


def main():
    parser = argparse.ArgumentParser(description="RackTicker • canonical 128×32 RGB matrix emulator")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1; use 0.0.0.0 for trusted LAN)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", type=Path, default=Path("config/config.json"))
    parser.add_argument("--output", choices=("browser", "hub75"), default="browser")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--plugin", action="append", default=[], metavar="NAME",
                        help="Also switch on a pip-installed plugin package by entry-point name (repeatable)")
    parser.add_argument("--list-plugins", action="store_true", help="List available plugins without loading their code")
    args = parser.parse_args()
    if args.list_plugins:
        manager = PluginManager(args.config.resolve().parent, cli=args.plugin)
        for name, kind in sorted(manager.known().items()):
            print(f"{name:<16} {kind}")
        for folder, error in manager.errors.items():
            print(f"{folder:<16} error: {error}")
        return
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
    # Parse feeds in a helper process so data refreshes never stall the display.
    offload.enable()
    try:
        # Plugins are switched on and off in Settings; --plugin adds pip packages.
        app = create_app(args.config, args.plugin, output=args.output, bundled_by_default=True)
    except (ConfigError, OSError) as exc:
        parser.error(str(exc))
    logging.getLogger("app").info("128x32 RGB | 2 × 64x32 P2.5 | 320x80 mm | http://%s:%s", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
