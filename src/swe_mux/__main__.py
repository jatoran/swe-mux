from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from aiohttp import web

from .config import LOOPBACK_HOSTS, Config, load_config
from .server import create_app
from .tailscale import enable_mobile_voice_serve, listener_hosts


def parser() -> argparse.ArgumentParser:
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
    parser.add_argument(
        "--shutdown",
        action="store_true",
        help=(
            "kill-server: reap every session owned by the PTY supervisor and stop "
            "it, then exit (with pty_supervisor_enabled, Ctrl-C only detaches)"
        ),
    )
    return parser


def load_daemon_config(
    argv: Sequence[str] | None = None,
) -> tuple[Config, argparse.Namespace]:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ValueError as exc:
        argument_parser.error(f"invalid config: {exc}")
    if args.host:
        if args.host not in LOOPBACK_HOSTS:
            argument_parser.error(
                "--host controls the local listener and must be loopback; "
                "the Tailscale listener is detected automatically"
            )
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.local_only:
        config.tailnet_enabled = False
    return config, args


async def serve(config: Config, *, desktop_control_token: str | None = None) -> None:
    hosts = await listener_hosts(config.host, config.tailnet_enabled)
    if config.tailnet_enabled and len(hosts) == 1:
        logging.getLogger(__name__).warning(
            "Tailscale listener requested but no active Tailscale IPv4 address was "
            "detected; continuing on localhost"
        )
    shutdown_event = asyncio.Event()
    app = create_app(
        config,
        desktop_control_token=desktop_control_token,
        desktop_shutdown_event=shutdown_event if desktop_control_token else None,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    sites: list[web.TCPSite] = []
    try:
        for host in hosts:
            site = web.TCPSite(runner, host=host, port=config.port)
            await site.start()
            sites.append(site)
            rendered_host = f"[{host}]" if ":" in host else host
            print(f"======== Running on http://{rendered_host}:{config.port} ========", flush=True)
        if config.tailnet_enabled:
            asyncio.create_task(_auto_enable_mobile_voice(config.port))
        await shutdown_event.wait()
    finally:
        await runner.cleanup()


async def _auto_enable_mobile_voice(port: int) -> None:
    """Bring up the private HTTPS mobile-voice proxy in the background.

    Tailscale Serve on 443 gives phones a secure-context origin for microphone
    capture while the direct 100.x HTTP listener stays as a fallback. This is
    best-effort: the daemon still runs if Tailscale is absent or HTTPS is not
    yet approved for the tailnet, and Settings exposes the same one-tap setup.
    """
    log = logging.getLogger(__name__)
    try:
        result = await enable_mobile_voice_serve(port)
    except Exception:  # noqa: BLE001 - startup helper must never crash the daemon
        log.exception("mobile-voice HTTPS auto-setup raised")
        return
    status = result.get("status")
    if status == "ready":
        log.info("mobile-voice HTTPS ready at %s", result.get("url"))
    elif status == "authorization_required":
        log.warning(
            "mobile-voice HTTPS needs a one-time Tailscale approval; enable it from "
            "Settings > Voice. %s",
            result.get("authorization_url") or "",
        )
    else:
        log.info("mobile-voice HTTPS not configured: %s", result.get("diagnostic"))


def main(argv: Sequence[str] | None = None) -> None:
    config, args = load_daemon_config(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.dev else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.shutdown:
        from .supervisor_client import kill_server

        stopped = asyncio.run(kill_server(config))
        print(
            "PTY supervisor stopped; all supervised sessions were reaped."
            if stopped
            else "No PTY supervisor is running for this config."
        )
        return
    token = os.environ.get("SWE_MUX_DESKTOP_CONTROL_TOKEN") or None
    try:
        asyncio.run(serve(config, desktop_control_token=token))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
