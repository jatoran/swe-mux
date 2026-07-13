from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from aiohttp import web

from .config import LOOPBACK_HOSTS, load_config
from .server import create_app
from .tailscale import listener_hosts


def main() -> None:
    parser = argparse.ArgumentParser(prog="muxd", description="swe-mux local daemon")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="disable the direct Tailscale listener for this run",
    )
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except ValueError as exc:
        parser.error(f"invalid config: {exc}")
    if args.host:
        if args.host not in LOOPBACK_HOSTS:
            parser.error(
                "--host controls the local listener and must be loopback; "
                "the Tailscale listener is detected automatically"
            )
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.local_only:
        config.tailnet_enabled = False
    logging.basicConfig(
        level=logging.DEBUG if args.dev else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    hosts = asyncio.run(listener_hosts(config.host, config.tailnet_enabled))
    if config.tailnet_enabled and len(hosts) == 1:
        logging.getLogger(__name__).warning(
            "Tailscale listener requested but no active Tailscale IPv4 address was "
            "detected; continuing on localhost"
        )
    web.run_app(
        create_app(config),
        host=hosts,
        port=config.port,
        print=lambda message: print(message, flush=True),
    )


if __name__ == "__main__":
    main()
