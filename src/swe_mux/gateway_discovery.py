"""The terminal gateway's discovery record, and ending the process it names, from any process.

`development/DAEMON_LOAD_ISOLATION.md` items 29 and 33. The gateway writes
`terminal-gateway.json` into the data directory once its listeners are bound
(`terminal_gateway.py`). Two kinds of process read it: a daemon finding the gateway it
left running (`gateway_host.py`), and the staged swap, which has to end that gateway
before it renames the bundle the gateway runs from (`bundle_apply.py`).

**Importable without the daemon and without aiohttp**, which is why this is a module
of its own. The swap also runs inside the frozen console client (`swemux
update-apply`), whose bundle carries neither (`packaging/swe_mux_cli.spec`), and
`terminal_core` imports aiohttp at module scope. So the control protocol's version
and its `quit` message are restated here rather than imported - the way
`terminal_gateway` restates the supervisor's protocol - and a test holds the copies
equal.

**Asked first, terminated second, and never a recycled pid.** `stop_gateway` sends
`quit` over the gateway's own control port, authenticated by the token in its record,
so the gateway closes every browser with 4011 ("restarting, reconnect") and removes
its record on the way out. Only a gateway still there after the grace is terminated,
and only when its pid still has the creation time the record names; a record without
one can be asked but never signalled, because a pid alone does not say which process
it is.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .framed_ipc import HandshakeRefused, PeerClosed, connect

log = logging.getLogger(__name__)

DISCOVERY_FILENAME = "terminal-gateway.json"
#: `terminal_core.protocol.GATEWAY_PROTOCOL_VERSION` and `QUIT`, restated: importing
#: `terminal_core` would bring aiohttp with it (see the module docstring).
GATEWAY_PROTOCOL = 1
QUIT = "quit"

#: How long a gateway asked to quit gets before it is terminated.
QUIT_GRACE_SECONDS = 10.0
#: How long a terminated (and then a killed) gateway gets to disappear.
TERMINATE_GRACE_SECONDS = 5.0
POLL_SECONDS = 0.1
#: How long a gateway asked to quit gets to close the connection it was asked on,
#: which it does on its way out.
QUIT_CLOSE_WAIT_SECONDS = 5.0
#: How long sending `quit` may take: the connection and handshake, the send, and the
#: wait for the gateway to close its end.
QUIT_SEND_SECONDS = 15.0
#: Two readings of one process's creation time agree to well inside this.
CREATED_AT_TOLERANCE_SECONDS = 0.001

#: Every field a usable record carries, and its type.
RECORD_FIELDS: dict[str, type | tuple[type, ...]] = {
    "pid": int,
    "port": int,
    "control_port": int,
    "token": str,
    "build_id": str,
    "protocol": int,
    "daemon_port": int,
}

#: What `stop_gateway` came to.
STOP_NONE = "none"
STOP_QUIT = "quit"
STOP_TERMINATED = "terminated"
STOP_KILLED = "killed"
STOP_STUCK = "stuck"
STOP_UNIDENTIFIED = "unidentified"


def discovery_path(data_dir: Path) -> Path:
    return Path(data_dir) / DISCOVERY_FILENAME


def read_discovery(data_dir: Path) -> dict[str, Any] | None:
    """The gateway's discovery record, or None when there is no readable one."""
    try:
        record = json.loads(discovery_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def usable(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """`record` when it carries every field a reader relies on, typed; else None."""
    if record is None:
        return None
    for key, kind in RECORD_FIELDS.items():
        value = record.get(key)
        if not isinstance(value, kind) or isinstance(value, bool):
            return None
    return record


def identity(record: dict[str, Any]) -> tuple[int, float] | None:
    """`(pid, created_at)` - which process the record names - or None without a time."""
    created = record.get("created_at")
    if not isinstance(created, (int, float)) or isinstance(created, bool):
        return None
    return int(record["pid"]), float(created)


def process_alive(who: tuple[int, float]) -> bool:
    """Whether that exact process is still running. An unreadable one counts as alive.

    Unreadable is not gone: a reading that cannot be made never authorizes treating
    the process as ended, and a caller that would signal it checks identity again.
    A zombie is gone: on POSIX an exited process stays in the table until its parent
    waits on it, holding nothing, and counting it would wait out a process that has
    already left.
    """
    import psutil

    try:
        process = psutil.Process(who[0])
        if abs(float(process.create_time()) - who[1]) >= CREATED_AT_TOLERANCE_SECONDS:
            return False
        return bool(process.status() != psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return True


def live_identity(data_dir: Path) -> tuple[int, float] | None:
    """The identity of this data directory's gateway when it is running, else None."""
    record = usable(read_discovery(data_dir))
    if record is None:
        return None
    who = identity(record)
    if who is None or not process_alive(who):
        return None
    return who


async def send_quit(record: dict[str, Any], *, name: str = "terminal-gateway-quit") -> bool:
    """Send `quit` over a connection of the caller's own; whether it was sent.

    A gateway that speaks another protocol cannot be asked: the request is not
    expressible to it, and the caller terminates it instead.
    """
    if usable(record) is None or record.get("protocol") != GATEWAY_PROTOCOL:
        return False
    try:
        peer = await connect(
            "127.0.0.1",
            int(record["control_port"]),
            token=str(record["token"]),
            protocol=GATEWAY_PROTOCOL,
            name=name,
        )
    except HandshakeRefused as exc:
        log.info("could not ask terminal gateway pid %s to quit: %s", record["pid"], exc)
        return False
    try:
        peer.notify({"t": QUIT})
        await peer.drain()
        # Then wait for the gateway to close its end, reading whatever it sends,
        # rather than closing ours first. A gateway treats every authenticated
        # connection as its daemon and sends it what it holds on arrival; closing with
        # that unread makes Windows reset the connection, and the reset discards the
        # gateway's unread copy of `quit` (2026-10-18: accepted and aborted inside a
        # millisecond on the operator's machine, and terminated ten seconds later).
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(peer.wait_closed(), QUIT_CLOSE_WAIT_SECONDS)
    except PeerClosed:
        return False
    finally:
        await peer.close("asked the gateway to quit")
    return True


@dataclass(frozen=True)
class GatewayStop:
    """What `stop_gateway` did: one of the `STOP_*` outcomes, the pid, and a sentence."""

    outcome: str
    pid: int | None
    detail: str

    @property
    def stopped(self) -> bool:
        """Whether no gateway for that data directory is running any more."""
        return self.outcome in {STOP_NONE, STOP_QUIT, STOP_TERMINATED, STOP_KILLED}


def stop_gateway(
    data_dir: Path,
    *,
    quit_grace: float = QUIT_GRACE_SECONDS,
    terminate_grace: float = TERMINATE_GRACE_SECONDS,
    say: Callable[[str], None] | None = None,
) -> GatewayStop:
    """End this data directory's gateway: ask it to quit, and terminate it if it stays.

    Synchronous, for a process with no event loop of its own - the swap, run by the
    checkout's redeploy script or by the frozen console client. The quit is one
    bounded round trip on a private loop.
    """
    tell = say or (lambda _line: None)
    record = usable(read_discovery(data_dir))
    if record is None:
        return GatewayStop(STOP_NONE, None, "no terminal gateway is recorded")
    pid = int(record["pid"])
    who = identity(record)
    if who is not None and not process_alive(who):
        return GatewayStop(STOP_NONE, pid, f"terminal gateway pid {pid} has already exited")
    tell(f"asking terminal gateway pid {pid} to quit (its browsers are told to reconnect)")
    sent = _send_quit_blocking(record)
    if not sent:
        tell(f"terminal gateway pid {pid} could not be asked to quit")
    if who is None:
        # Asked, and nothing more: a pid without its creation time names no process.
        if _wait(lambda: not _pid_exists(pid), quit_grace):
            return GatewayStop(STOP_QUIT, pid, f"terminal gateway pid {pid} quit")
        return GatewayStop(
            STOP_UNIDENTIFIED,
            pid,
            f"terminal gateway pid {pid} is still running and its record carries no "
            "creation time, so it was not signalled",
        )
    if _wait(lambda: not process_alive(who), quit_grace if sent else 0.0):
        return GatewayStop(STOP_QUIT, pid, f"terminal gateway pid {pid} quit")
    tell(f"terminal gateway pid {pid} is still running; terminating it")
    _signal(who, kill=False)
    if _wait(lambda: not process_alive(who), terminate_grace):
        return GatewayStop(STOP_TERMINATED, pid, f"terminal gateway pid {pid} was terminated")
    # A stopped process holds a termination request until it runs again.
    _signal(who, kill=True)
    if _wait(lambda: not process_alive(who), terminate_grace):
        return GatewayStop(STOP_KILLED, pid, f"terminal gateway pid {pid} was killed")
    return GatewayStop(STOP_STUCK, pid, f"terminal gateway pid {pid} could not be ended")


def _send_quit_blocking(record: dict[str, Any]) -> bool:
    async def bounded() -> bool:
        try:
            return await asyncio.wait_for(send_quit(record), QUIT_SEND_SECONDS)
        except TimeoutError:
            return False

    try:
        return asyncio.run(bounded())
    except RuntimeError:
        # Called with an event loop already running in this thread: termination
        # after the grace is still available, and the caller is told it was not asked.
        return False


def _wait(done: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    # unsupervised-loop-ok: bounded by its deadline; a synchronous wait in the swap.
    while True:
        if done():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def _pid_exists(pid: int) -> bool:
    """Whether `pid` names a process that has not exited (a zombie has; see above)."""
    import psutil

    try:
        return bool(psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return True


def _signal(who: tuple[int, float], *, kill: bool) -> None:
    """Terminate - or ``kill`` - exactly that process, never a recycled pid."""
    import psutil

    try:
        process = psutil.Process(who[0])
        if abs(process.create_time() - who[1]) >= CREATED_AT_TOLERANCE_SECONDS:
            return
        if kill:
            process.kill()
        else:
            process.terminate()
    except (psutil.Error, OSError):
        return


__all__ = [
    "DISCOVERY_FILENAME",
    "GATEWAY_PROTOCOL",
    "QUIT",
    "RECORD_FIELDS",
    "STOP_KILLED",
    "STOP_NONE",
    "STOP_QUIT",
    "STOP_STUCK",
    "STOP_TERMINATED",
    "STOP_UNIDENTIFIED",
    "GatewayStop",
    "discovery_path",
    "identity",
    "live_identity",
    "process_alive",
    "read_discovery",
    "send_quit",
    "stop_gateway",
    "usable",
]
