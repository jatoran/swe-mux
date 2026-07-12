from __future__ import annotations

import argparse
import logging
from pathlib import Path

from aiohttp import web

from .config import load_config
from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="muxd", description="swe-mux local daemon")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    logging.basicConfig(
        level=logging.DEBUG if args.dev else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    web.run_app(
        create_app(config), host=config.host, port=config.port, print=lambda s: print(s, flush=True)
    )


if __name__ == "__main__":
    main()
