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
from .http_support import ACCESS_LOG_FORMAT
from .lifecycle import heartbeat_pid, ledger, pid_running
from .logsetup import enable_crash_tracebacks, setup_daemon_logging
from .process_reaper import process_in_job
from .server import create_app, wait_runtime_ready
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
        "--where",
        action="store_true",
        help=(
            "print where swe-mux is installed, whether its commands are on PATH, "
            "and how to run it when they are not, then exit"
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
    return resolve_daemon_config(argument_parser, args), args


def resolve_daemon_config(
    argument_parser: argparse.ArgumentParser, args: argparse.Namespace
) -> Config:
    """Apply the parsed flags to the config, or fail with a usage error.

    Split out of `load_daemon_config` so `main` can answer `--where` from the
    parsed arguments alone. That ordering is the whole point of the flag: a
    config that does not load is one of the states someone runs `--where` in, and
    a command that first refuses over `invalid config:` would be useless in
    exactly the case it exists for.
    """
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
    return config


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
    # The access format carries the correlation id, so a request line in
    # `access.log` joins the lines it caused in `daemon.log`.
    runner = web.AppRunner(app, access_log_format=ACCESS_LOG_FORMAT)
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
            asyncio.create_task(_auto_enable_mobile_voice(app, config.port))
        await shutdown_event.wait()
    finally:
        await runner.cleanup()


async def _auto_enable_mobile_voice(app: web.Application, port: int) -> None:
    """Bring up the private HTTPS mobile-voice proxy in the background.

    Tailscale Serve on 443 gives phones a secure-context origin for microphone
    capture while the direct 100.x HTTP listener stays as a fallback. This is
    best-effort: the daemon still runs if Tailscale is absent or HTTPS is not
    yet approved for the tailnet, and Settings exposes the same one-tap setup.

    Waits for the runtime first. The listeners now bind before the runtime is
    built, and Serve reclamation decides whether an existing route is abandoned
    by asking whether a swe-mux daemon answers health on the port behind it - a
    question this daemon would answer "no" about *itself* for the whole of its
    own startup.
    """
    log = logging.getLogger(__name__)
    try:
        await wait_runtime_ready(app)
    except Exception:  # noqa: BLE001 - a failed build already stops the daemon
        return
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


#: How long a successor waits for its predecessor to finish draining before it
#: starts its own database work. Sized against the predecessor's teardown, which
#: stops ~30 services and closes thirteen store connections; well under the 90s
#: this gate already tolerates for the port itself.
PREDECESSOR_DRAIN_TIMEOUT_SECONDS = 20.0


def wait_for_predecessor_exit(
    data_dir: Path, timeout_seconds: float = PREDECESSOR_DRAIN_TIMEOUT_SECONDS
) -> None:
    """Second half of the successor start gate: let the predecessor finish writing.

    Freeing the port is not the end of a daemon's life. `runner.cleanup()` closes
    the listener *first* and only then runs `_teardown_runtime`, which is where
    the last durable writes happen - the terminal ledger, the recovery rows, the
    telemetry and notification-decision rows that describe the restart itself.
    A successor that started the moment the port opened spent that whole window
    holding `mux.db` for its own integrity check and schema work, and the
    predecessor's writes came back `database is locked` and were dropped
    (measured across a 2026-08-23 restart: ten of them, in four subsystems,
    silently).

    So the successor waits for the predecessor *process*, which the heartbeat
    record names, rather than for its socket. Bounded, and a timeout is a
    warning and not a refusal: a wedged predecessor must not stop a restart, and
    `sqlite_store`'s drain widening is the second line for exactly that case.
    """
    log = logging.getLogger(__name__)
    pid = heartbeat_pid(data_dir)
    if pid <= 0 or pid == os.getpid() or not pid_running(pid):
        return
    log.info("waiting for predecessor daemon pid %d to finish its shutdown drain", pid)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not pid_running(pid):
            log.info("predecessor daemon pid %d has exited; continuing startup", pid)
            return
        time.sleep(0.25)
    log.warning(
        "predecessor daemon pid %d has not exited after %.0fs; starting anyway "
        "(its last writes may be lost to a database lock)",
        pid,
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


def _print_path_hint(config: Config) -> None:
    """Tell a first-time user where their commands are, when they are unreachable.

    This is the one moment the confused user is definitely present: they got the
    daemon started somehow - most likely with `python -m swe_mux`, because
    nothing else worked - and they are watching this terminal. Anywhere else the
    advice arrives too early (during `pip install`, whose output we cannot hook)
    or too late.

    Three properties it has to keep. It is **silent when nothing is wrong**, so
    it costs a healthy install nothing and never trains anyone to skip it. It
    prints **once per start**, which a daemon process gets for free. And it is
    **concrete**: the directory, the exact command for this installer, and the
    fallback that works with no PATH at all - never "add swe-mux to your PATH".

    Also logged, at WARNING, because the terminal it prints to is gone by the
    time anyone investigates and `daemon.log` is not.
    """
    from .install_location import detect_install_location, path_hint_lines

    try:
        location = detect_install_location()
        lines = path_hint_lines(location)
    except OSError as exc:
        # A diagnostic must never be the reason a daemon does not start.
        logging.getLogger(__name__).debug("install-location probe failed: %s", exc)
        return
    if not lines:
        return
    print("\n".join(["", *lines, ""]), flush=True)
    logging.getLogger(__name__).warning(
        "swe-mux commands are installed in %s but not reachable from PATH; fix with: %s",
        location.bin_dir,
        location.path_fix_lines()[0],
        extra={
            "install_kind": location.kind,
            "scripts_dir": str(location.bin_dir),
            "unreachable": ",".join(command.name for command in location.unreachable),
        },
    )
    ledger(
        config.data_dir,
        f"daemon pid {os.getpid()}: swe-mux commands at {location.bin_dir} are not on PATH "
        f"({location.kind} install)",
    )


def main(argv: Sequence[str] | None = None) -> None:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if args.where:
        # Answered before the config is touched, and before logging is set up:
        # this is the command for someone whose install answers nothing else, so
        # every step it does not need is a step that can deny them the answer.
        from .install_location import (
            detect_install_location,
            installed_version,
            render_where,
        )

        print(render_where(detect_install_location(), version=installed_version()))
        return
    config = resolve_daemon_config(argument_parser, args)
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
    _print_path_hint(config)
    if args.relaunch_wait:
        wait_for_port_free(config.host, config.port)
        # Then for the predecessor's *drain*, which happens after the port frees.
        wait_for_predecessor_exit(config.data_dir)
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
