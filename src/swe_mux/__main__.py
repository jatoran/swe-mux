from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path

from aiohttp import web

from .config import LOOPBACK_HOSTS, Config, load_config
from .lifecycle import ledger
from .logsetup import enable_crash_tracebacks, setup_daemon_logging
from .process_reaper import process_in_job
from .server import create_app
from .tailscale import enable_mobile_voice_serve, listener_hosts
from .timer_resolution import raise_timer_resolution


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
    parser.add_argument(
        "--relaunch-wait",
        action="store_true",
        help=argparse.SUPPRESS,  # successor of a self-restart: wait for the port to free
    )
    return parser


def load_daemon_config(
    argv: Sequence[str] | None = None,
) -> tuple[Config, argparse.Namespace]:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (ValueError, TypeError) as exc:
        # TypeError too: a wrong-typed scalar or an unexpected keyword reaching a
        # dataclass constructor is a config problem, and it deserves the clean
        # "invalid config" message rather than a raw traceback at startup.
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


async def serve(
    config: Config,
    *,
    desktop_control_token: str | None = None,
    relaunch_command: list[str] | None = None,
) -> None:
    hosts = await listener_hosts(
        config.host, config.tailnet_enabled, config.wsl_bridge_enabled
    )
    if config.tailnet_enabled and len(hosts) == 1:
        logging.getLogger(__name__).warning(
            "Tailscale listener requested but no active Tailscale IPv4 address was "
            "detected; continuing on localhost"
        )
    shutdown_event = asyncio.Event()
    app = create_app(
        config,
        desktop_control_token=desktop_control_token,
        desktop_shutdown_event=shutdown_event,
        relaunch_command=relaunch_command,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    sites: list[web.TCPSite] = []
    log = logging.getLogger(__name__)
    try:
        for index, host in enumerate(hosts):
            site = web.TCPSite(runner, host=host, port=config.port)
            try:
                await site.start()
            except OSError as exc:
                if index == 0:
                    # The loopback listener is the daemon. Nothing works without
                    # it, but the message should say why rather than traceback.
                    raise SystemExit(
                        f"cannot bind {host}:{config.port} ({exc}); "
                        "another daemon may already be running on this port"
                    ) from exc
                # A tailnet or WSL-adapter address reported before its interface
                # was actually plumbed (the ordinary login-autostart race) used to
                # kill the whole daemon instead of degrading the way the
                # no-address-detected path already does.
                log.warning(
                    "could not bind the secondary listener on %s:%s (%s); "
                    "continuing on localhost",
                    host,
                    config.port,
                    exc,
                )
                continue
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


def relaunch_command_for(config: Config, args: argparse.Namespace) -> list[str]:
    """The exact command a self-restarting daemon uses to start its successor."""
    from .desktop import daemon_command

    command = daemon_command(config.config_path or config.data_dir / "config.toml")
    if args.dev:
        command.append("--dev")
    if args.local_only:
        command.append("--local-only")
    command.append("--relaunch-wait")
    return command


def wait_for_port_free(host: str, port: int, timeout_seconds: float = 90.0) -> None:
    """Successor start gate: the predecessor still holds the listener while it
    unwinds; binding would fail, so wait for the port to actually free up."""
    import socket

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                pass
        except OSError:
            return
        time.sleep(0.25)
    logging.getLogger(__name__).warning(
        "predecessor daemon still holds port %d after %.0fs; attempting startup anyway",
        port,
        timeout_seconds,
    )


def _warn_if_inside_job(config: Config) -> None:
    """Loud breadcrumb for the poisoned-launch state while the daemon is healthy.

    A daemon inside a Job object was almost certainly (re)launched from a shell
    inside a session; it will be silently terminated when that session's
    kill-on-close Job goes down. Breakaway spawns prevent this for the standard
    relaunch paths, so hitting it means an old Job (pre-BREAKAWAY_OK) or an
    unusual launch route.
    """
    if not process_in_job():
        return
    message = (
        "daemon is running inside a Windows Job object (launched from within a "
        "session?); it may be terminated when that Job closes. Relaunch it from "
        "the desktop app or a terminal outside swe-mux."
    )
    logging.getLogger(__name__).warning(message)
    ledger(config.data_dir, f"daemon pid {os.getpid()}: {message}")


def main(argv: Sequence[str] | None = None) -> None:
    config, args = load_daemon_config(argv)
    setup_daemon_logging(config.data_dir, level="DEBUG" if args.dev else config.log_level)
    if args.shutdown:
        from .supervisor_client import _discovery_pid, _pid_running, kill_server

        pid = _discovery_pid(config.data_dir)
        stopped = asyncio.run(kill_server(config))
        if stopped:
            print("PTY supervisor stopped; all supervised sessions were reaped.")
        elif pid > 0 and _pid_running(pid):
            # "Unreachable" and "absent" are different answers, and reporting the
            # first as the second sends the next investigation the wrong way.
            print(
                f"A PTY supervisor (pid {pid}) is running but could not be reached "
                "or terminated; check supervisor.log."
            )
        else:
            print("No PTY supervisor is running for this config.")
        return
    enable_crash_tracebacks(config.data_dir)
    # Before the event loop starts. Every `asyncio.sleep` here, and therefore every
    # round trip between a keystroke and its echo, is quantized to the Windows timer
    # tick without this: a 0.5 ms sleep measured 15.6 ms. See `timer_resolution`.
    raise_timer_resolution()
    _warn_if_inside_job(config)
    if args.relaunch_wait:
        wait_for_port_free(config.host, config.port)
    token = os.environ.get("SWE_MUX_DESKTOP_CONTROL_TOKEN") or None
    try:
        asyncio.run(
            serve(
                config,
                desktop_control_token=token,
                relaunch_command=relaunch_command_for(config, args),
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
