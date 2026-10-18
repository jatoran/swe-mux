"""The daemon's side of the terminal gateway: find it or start it, keep it, stop it.

`development/archive/DAEMON_LOAD_ISOLATION.md` item 29; the process is `terminal_gateway.py`
and the protocol between the two is `terminal_core/protocol.py`.

**Found before it is started.** The gateway outlives a daemon restart on purpose, so
a starting daemon first reads `terminal-gateway.json` and connects to the gateway it
names, and starts one only when there is none. A gateway running other code - its
build id or its protocol differs from this daemon's - is asked to quit and replaced.
That restart is orchestrated rather than abrupt: the gateway closes its browsers with
4011, which every client reads as "reconnect", so a redeployed daemon never strands a
terminal on the old build. A gateway this daemon started itself is never replaced for
its build: it is this daemon's code by construction, so a disagreement there is a bug
to report, not a reason to restart it in a loop.

**Not owned the way the worker is.** The worker dies with its daemon; the gateway must
not. It is spawned outside every Job object and in a process group of its own, so
neither the daemon's death nor a Ctrl-C at the daemon's console ends it, and nothing
here ever puts it under the daemon's reaper.

**Supervised as a background loop.** One task: find or start a gateway, connect to it,
wait for the connection to close, and go round again with backoff from 0.5 s to 30 s
that resets after a minute of good service. The spawn and every discovery read run
in threads, so a slow `CreateProcess` costs the event loop nothing.

**Stopped by intent.** A daemon that is quitting tells the gateway to quit and ends it
if it does not; a daemon that is restarting only closes its connection, and the
gateway goes on carrying every terminal until the successor connects.

**Reported** as `terminal_transport` on `/api/health` - which process a browser should
open terminals on, and why - and as `terminal_gateway` on
`/api/diagnostics/background`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from .background_tasks import background
from .daemon_recovery import process_state, update_daemon
from .framed_ipc import PeerClosed, RemoteError
from .gateway_discovery import identity, send_quit, usable
from .lifecycle import ledger
from .spawn_contract import scrub_claude_session_markers
from .subprocess_flags import background_creation_flags, popen_outside_job
from .terminal_core.protocol import GATEWAY_PROTOCOL_VERSION
from .terminal_core.remote import RemoteHub
from .terminal_gateway import (
    CONSOLE_LOG_NAME,
    DEFAULT_SUBSCRIBE_POLICY,
    gateway_build_id,
    gateway_command,
    read_discovery,
)
from .terminal_placement import TerminalPlacement

log = logging.getLogger(__name__)

GATEWAY_HOST_LOOP = "terminal-gateway-host"
#: `terminal-gateway-console.log` is truncated at a spawn once it is this large: it
#: holds what the interpreter printed before logging started, which only the latest
#: run needs.
CONSOLE_MAX_BYTES = 1024 * 1024
#: From spawn to the gateway's discovery record. Generous because a fresh frozen
#: image on a loaded host pays a Defender scan before it runs a line.
READY_TIMEOUT_SECONDS = 60.0
RESTART_INITIAL_SECONDS = 0.5
RESTART_MAX_SECONDS = 30.0
#: An incarnation that served this long resets the restart backoff.
STABLE_SECONDS = 60.0
#: How long a gateway asked to quit gets before it is terminated.
QUIT_GRACE_SECONDS = 10.0
#: How long a terminated gateway gets to disappear.
TERMINATE_GRACE_SECONDS = 5.0
#: How often a starting gateway's record, or a stopping one's absence, is looked for.
POLL_SECONDS = 0.1
#: A spawn that lost the instance lock to another gateway waits this long for that
#: gateway's record, which it writes only once its listeners are bound.
LOCKED_RECORD_SECONDS = 15.0
#: Environment the gateway must not inherit: it needs no credential of the daemon's.
WITHHELD_ENVIRONMENT = ("SWE_MUX_DESKTOP_CONTROL_TOKEN",)

#: How often a connected gateway is pinged, and its sessions reconciled.
PROBE_INTERVAL_SECONDS = 5.0
#: How long one ping waits for its answer.
PROBE_TIMEOUT_SECONDS = 10.0
#: Consecutive unanswered pings - each a full timeout, with this daemon's own loop
#: running on time throughout - before the gateway is suspected hung.
HUNG_PROBES = 3
#: A ping whose timer fired this much later than it was set for measured this
#: daemon's stall, not the gateway's silence, and is not counted against it.
LATE_TIMER_FACTOR = 1.5
#: How long a live gateway may refuse its control connection before it is suspected
#: hung (it keeps its sessions until then: it is still serving their browsers).
UNREACHABLE_HUNG_SECONDS = 30.0
#: How long the gateway's own `/health` gets to answer a suspicion. Any answer at
#: all clears it: a gateway serving its browsers is not hung, whatever this daemon saw.
HEALTH_CONFIRM_SECONDS = 5.0
#: How long a terminal socket opened during this daemon's first gateway connection
#: waits to learn which process serves its session (`settle`).
SETTLE_WAIT_SECONDS = 3.0

#: `terminal_gateway.main`'s exit codes for the two refusals worth naming.
EXIT_LOCKED = 3
EXIT_BIND = 4

STATE_DISABLED = "disabled"
STATE_UNAVAILABLE = "unavailable"
STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_BACKOFF = "backoff"

TRANSPORT_GATEWAY = "gateway"
TRANSPORT_DAEMON = "daemon"

#: How an incarnation came by its gateway.
HOW_STARTED = "started"
HOW_ADOPTED = "adopted"
HOW_RECONNECTED = "reconnected"

HostsProvider = Callable[[], Awaitable[Sequence[str]]]
CommandFactory = Callable[..., list[str]]


class GatewayHost:
    """The terminal gateway this daemon drives, for the life of the daemon."""

    def __init__(
        self,
        config: Any,
        *,
        generation: str = "legacy",
        hosts: HostsProvider | None = None,
        command: CommandFactory = gateway_command,
        build_id: Callable[[], str] = gateway_build_id,
        ready_timeout: float = READY_TIMEOUT_SECONDS,
        restart_initial: float = RESTART_INITIAL_SECONDS,
        restart_max: float = RESTART_MAX_SECONDS,
        stable_seconds: float = STABLE_SECONDS,
        quit_grace: float = QUIT_GRACE_SECONDS,
        probe_interval: float = PROBE_INTERVAL_SECONDS,
        probe_timeout: float = PROBE_TIMEOUT_SECONDS,
        hung_probes: int = HUNG_PROBES,
        unreachable_hung: float = UNREACHABLE_HUNG_SECONDS,
    ) -> None:
        self.enabled = bool(getattr(config, "terminal_gateway_enabled", False))
        self.data_dir = Path(config.data_dir)
        self.daemon_port = int(config.port)
        self.port = int(config.terminal_gateway_listen_port())
        self.log_level = str(getattr(config, "log_level", "INFO") or "INFO")
        self.switch_interval_ms = float(getattr(config, "gil_switch_interval_ms", 5.0) or 5.0)
        self.hub = RemoteHub(generation=generation)
        self._hosts = hosts or (lambda: _listener_hosts(config))
        self._command = command
        self._build_id = build_id
        self._ready_timeout = ready_timeout
        self._restart_initial = restart_initial
        self._restart_max = restart_max
        self._stable_seconds = stable_seconds
        self._quit_grace = quit_grace
        self._probe_interval = probe_interval
        self._probe_timeout = probe_timeout
        self._hung_probes = hung_probes
        self._unreachable_hung = unreachable_hung
        #: Which process holds each session's terminal (`terminal_placement.py`);
        #: installed by the composition root, None in a host that moves nothing.
        self.placement: TerminalPlacement | None = None
        #: Set once this daemon's first gateway connection has succeeded or failed,
        #: or when there will be none: until then nobody can say which process serves
        #: a supervised session's terminal (`settle`).
        self.settled = asyncio.Event()
        #: `(pid, created_at)` of the gateway this daemon's sessions were handed to.
        self.holder: tuple[int, float | None] | None = None
        #: When this daemon lost its control connection to a gateway still alive.
        self.unreachable_since: float | None = None
        self.probe_misses = 0
        self.hung_ends = 0
        self.last_hung_reason = ""
        self.state = STATE_DISABLED if not self.enabled else STATE_STOPPED
        if not self.enabled:
            self.settled.set()
        self.unavailable_reason = ""
        #: This daemon's own build of the gateway, computed off the loop at start.
        self.build_id: str | None = None
        #: The gateway this daemon is driving, from its discovery record (no token).
        self.gateway: dict[str, Any] | None = None
        #: `(pid, created_at)` of the gateway this host itself started, if it did.
        self.spawned: tuple[int, float] | None = None
        self.spawns = 0
        self.adoptions = 0
        self.replacements = 0
        self.restarts = 0
        self.last_exit_code: int | None = None
        self.last_error = ""
        self.last_replacement = ""
        self.connected_at: float | None = None
        self.started = False
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._build_warning_logged = False

    # -- reporting -----------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self.state == STATE_RUNNING and self.hub.connected

    @property
    def pid(self) -> int | None:
        gateway = self.gateway
        return int(gateway["pid"]) if gateway is not None else None

    @property
    def carrying(self) -> bool:
        """Whether the gateway serves this daemon's supervised terminals now: while it
        is connected, and while it still holds sessions through a lost connection."""
        if self.gateway is None or self.state in {
            STATE_DISABLED,
            STATE_UNAVAILABLE,
            STATE_STOPPED,
        }:
            return False
        if self.hub.connected:
            return True
        placement = self.placement
        return placement is not None and placement.remote_count > 0

    def transport(self) -> dict[str, Any]:
        """Which process a browser should open terminals on, for `/api/health`.

        `gateway` while it carries terminals (`carrying`); every other state - off, no
        supervisor, starting, between restarts with nothing held - is the daemon, with
        the reason.
        """
        if self.carrying and self.gateway is not None:
            return {
                "transport": TRANSPORT_GATEWAY,
                "state": self.state,
                "port": int(self.gateway["port"]),
            }
        reason = self.unavailable_reason or self.last_error
        return {
            "transport": TRANSPORT_DAEMON,
            "state": self.state,
            "port": None,
            **({"reason": reason} if reason else {}),
        }

    def health(self) -> dict[str, Any]:
        """The `terminal_gateway` block on `/api/diagnostics/background`."""
        connected_at = self.connected_at
        return {
            "enabled": self.enabled,
            "state": self.state,
            "ready": self.ready,
            "reason": self.unavailable_reason or None,
            "gateway": self.gateway,
            "spawned_here": self.spawned is not None
            and self.gateway is not None
            and _identity(self.gateway) == self.spawned,
            "configured_port": self.port,
            "build_id": self.build_id,
            "spawns": self.spawns,
            "adoptions": self.adoptions,
            "replacements": self.replacements,
            "last_replacement": self.last_replacement or None,
            "restarts": self.restarts,
            "last_exit_code": self.last_exit_code,
            "last_error": self.last_error,
            "uptime_seconds": (
                round(time.monotonic() - connected_at, 3) if connected_at is not None else None
            ),
            "connection": self.hub.stats(),
            "carrying": self.carrying,
            "settled": self.settled.is_set(),
            "placement": self.placement.stats() if self.placement is not None else None,
            "unreachable_seconds": (
                round(time.monotonic() - self.unreachable_since, 3)
                if self.unreachable_since is not None
                else None
            ),
            "probe_misses": self.probe_misses,
            "hung_ends": self.hung_ends,
            "last_hung_reason": self.last_hung_reason or None,
        }

    async def settle(self, session: Any) -> None:
        """Wait, briefly, until it is known which process serves ``session``'s terminal.

        Only during this daemon's first gateway connection, and only for a session
        the gateway may hold. A daemon that has just restarted cannot yet tell whether
        the gateway it left running is still carrying that terminal for other
        browsers; serving it here in that window would put a second writer on the
        PTY. Bounded, because a page waiting on it is waiting on a handshake.
        """
        placement = self.placement
        if self.settled.is_set() or placement is None or not placement.eligible(session):
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.settled.wait(), SETTLE_WAIT_SECONDS)

    # -- lifecycle -----------------------------------------------------------------------

    def mark_unavailable(self, reason: str) -> None:
        """Enabled, but this daemon cannot run a gateway (no PTY supervisor, say)."""
        if not self.enabled:
            return
        self.state = STATE_UNAVAILABLE
        self.unavailable_reason = reason
        self.settled.set()
        log.warning("terminal gateway not started: %s; terminals stay on the daemon", reason)

    async def start(self) -> None:
        """Start finding or starting the gateway. Returns at once."""
        if not self.enabled:
            log.info("terminal gateway disabled (terminal_gateway_enabled = false)")
            return
        if self.started or self.state == STATE_UNAVAILABLE:
            # Unavailable is this daemon's verdict that it cannot run one (no PTY
            # supervisor); starting anyway would erase the reason and spawn a gateway
            # with nothing to carry.
            return
        self.started = True
        self._stopping = False
        self.unavailable_reason = ""
        # Starting from this instant, not from when the loop first runs: `/api/health`
        # can be asked in between, and `stopped` there reads as a gateway that will
        # never come (the redeploy's report took it for exactly that, 2026-10-18).
        self.state = STATE_STARTING
        self._task = background.start(GATEWAY_HOST_LOOP, self._supervise)

    async def stop(self, *, quit: bool) -> None:
        """Leave the gateway running for a successor, or - `quit` - end it too."""
        if not self.started:
            return
        self._stopping = True
        gateway = self.gateway
        process = self._process
        if quit:
            if not self.hub.quit() and gateway is not None:
                await _ask_to_quit(gateway, await asyncio.to_thread(read_discovery, self.data_dir))
        await background.stop(GATEWAY_HOST_LOOP)
        self._task = None
        self.settled.set()
        # No terminal is taken back: a quitting daemon takes the gateway and its
        # sessions down together, and a restarting one leaves both to its successor.
        await self.hub.close()
        if quit:
            identity = _identity(gateway) if gateway is not None else None
            if identity is not None:
                await self._end(identity, reason="the daemon is quitting")
            elif process is not None and process.poll() is None:
                # Started and cancelled before it announced itself: nothing else
                # knows it exists.
                await asyncio.to_thread(_kill_process, process)
            await asyncio.to_thread(
                ledger, self.data_dir, f"terminal_gateway quit pid={self.pid}"
            )
        else:
            log.info(
                "detached from terminal gateway pid %s; it keeps carrying terminals",
                self.pid,
            )
        self.started = False
        self.connected_at = None
        self.state = STATE_STOPPED

    async def _supervise(self) -> None:
        if self.build_id is None:
            self.build_id = await asyncio.to_thread(self._build_id)
        delay = self._restart_initial
        while not self._stopping:
            began = time.monotonic()
            try:
                await self._run_incarnation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop's whole job is to outlive this
                self.last_error = f"{type(exc).__name__}: {exc}"[:500]
                log.warning(
                    "terminal gateway incarnation failed: %s",
                    self.last_error,
                    extra={"gateway_pid": self.pid, "gateway_exit_code": self.last_exit_code},
                )
            # Whatever the first incarnation came to, it has come to it.
            self.settled.set()
            if self._stopping:
                return
            if time.monotonic() - began >= self._stable_seconds:
                delay = self._restart_initial
            self.restarts += 1
            self.state = STATE_BACKOFF
            log.warning(
                "terminal gateway: trying again in %.1fs (restart %s)",
                delay,
                self.restarts,
                extra={"gateway_restarts": self.restarts},
            )
            await asyncio.sleep(delay)
            delay = min(self._restart_max, delay * 2)

    async def _run_incarnation(self) -> None:
        self.state = STATE_STARTING
        holder = self.holder
        if holder is not None and await asyncio.to_thread(_holder_state, holder) == "gone":
            self._take_back_all(f"terminal gateway pid {holder[0]} exited")
        record, how = await self._find_or_start()
        if self.holder is not None and _holder(record) != self.holder:
            # The gateway this daemon's sessions were handed to is not the one found:
            # it exited, or was replaced, and nothing carries those terminals now.
            self._take_back_all(f"terminal gateway pid {self.holder[0]} is gone")
        self.gateway = {key: value for key, value in record.items() if key != "token"}
        try:
            await self.hub.connect(
                "127.0.0.1",
                int(record["control_port"]),
                token=str(record["token"]),
                before_sync=lambda: self._hand_over(record),
            )
        except Exception:
            await self._unreachable(record)
            raise
        peer = self.hub.peer
        assert peer is not None
        if how == HOW_ADOPTED:
            self.adoptions += 1
        self.connected_at = time.monotonic()
        self.unreachable_since = None
        self.state = STATE_RUNNING
        self.last_error = ""
        self.settled.set()
        identity = _identity(record)
        log.info(
            "terminal gateway pid %s %s: browsers on port %s, build %s",
            record["pid"],
            how,
            record["port"],
            record["build_id"],
            extra={"gateway_pid": record["pid"], "gateway_port": record["port"]},
        )
        await asyncio.to_thread(self._record_identity, identity, how)
        watch = asyncio.create_task(self._watch(peer, record), name="terminal-gateway-watch")
        try:
            await peer.wait_closed()
        finally:
            if not watch.done():
                watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        if not self._stopping:
            self.last_error = f"connection closed: {peer.close_reason}"
            process = self._process
            if process is not None:
                self.last_exit_code = process.poll()
            await self._connection_lost(record)

    # -- which process holds the terminals -----------------------------------------------

    def _hand_over(self, record: dict[str, Any]) -> None:
        """Every eligible session to the gateway just connected. Inside `connect`."""
        self.holder = _holder(record)
        if self.placement is not None:
            self.placement.adopt_all()

    def _take_back_all(self, reason: str) -> None:
        self.holder = None
        self.unreachable_since = None
        if self.placement is not None:
            self.placement.take_back_all(reason)

    async def _connection_lost(self, record: dict[str, Any]) -> None:
        """The control connection closed. A gateway that is gone gives its terminals
        back now; one still alive keeps them, because it is still serving them."""
        state = await asyncio.to_thread(
            process_state, {"pid": record["pid"], "created_at": record.get("created_at")}
        )
        if state == "gone":
            self._take_back_all(f"terminal gateway pid {record['pid']} exited")
            return
        if self.unreachable_since is None:
            self.unreachable_since = time.monotonic()
        log.warning(
            "lost the control connection to terminal gateway pid %s, which is still running "
            "and keeps its %d session(s); reconnecting",
            record["pid"],
            self.placement.remote_count if self.placement is not None else 0,
            extra={"gateway_pid": record["pid"]},
        )

    async def _unreachable(self, record: dict[str, Any]) -> None:
        """A connect that failed. Past `UNREACHABLE_HUNG_SECONDS` of a live gateway
        holding this daemon's sessions and refusing it, ask the gateway's own listener
        whether it is serving; silence there too means hung."""
        if self.holder is None or _holder(record) != self.holder:
            return
        now = time.monotonic()
        if self.unreachable_since is None:
            self.unreachable_since = now
            return
        if now - self.unreachable_since < self._unreachable_hung:
            return
        await self._suspect_hung(
            record, f"its control port refused this daemon for {now - self.unreachable_since:.0f}s"
        )

    async def _watch(self, peer: Any, record: dict[str, Any]) -> None:
        """One connection's liveness and housekeeping: reconcile, then ping.

        A miss counts only when it is the gateway's: a ping that the gateway answered
        late, or that this daemon's own stall made look late, is not one, and nothing
        is done on a suspicion until the gateway's `/health` has also gone unanswered.
        """
        misses = 0
        # unsupervised-loop-ok: one connection's watch, inside the supervised host loop.
        while not peer.closed and not self._stopping:
            await asyncio.sleep(self._probe_interval)
            if peer.closed or self._stopping:
                return
            if self.placement is not None:
                self.placement.reconcile()
            began = time.monotonic()
            heard = self.hub.last_heard
            try:
                answered = await self.hub.ping(self._probe_timeout)
            except PeerClosed:
                return
            except RemoteError:
                answered = True  # a refusal is an answer
            if answered is None:
                continue  # a gateway without `ping`: the connection is all there is
            if answered or self.hub.last_heard > heard:
                misses = 0
                continue
            if time.monotonic() - began > self._probe_timeout * LATE_TIMER_FACTOR:
                # The timer fired late: this daemon was not running, so the silence
                # it measured is its own.
                continue
            misses += 1
            self.probe_misses += 1
            log.warning(
                "terminal gateway pid %s did not answer a ping in %.0fs (%d of %d)",
                record["pid"],
                self._probe_timeout,
                misses,
                self._hung_probes,
                extra={"gateway_pid": record["pid"]},
            )
            if misses < self._hung_probes:
                continue
            await self._suspect_hung(record, f"{misses} pings unanswered")
            return

    async def _suspect_hung(self, record: dict[str, Any], why: str) -> None:
        """End a gateway that answers neither this daemon nor its own browsers, and
        take its terminals back; reconnect to one that still serves its browsers."""
        port = int(record["port"])
        if await asyncio.to_thread(_answers_health, port, HEALTH_CONFIRM_SECONDS):
            log.warning(
                "terminal gateway pid %s answers its browsers but not this daemon (%s); "
                "it keeps its sessions and the control connection is renewed",
                record["pid"],
                why,
                extra={"gateway_pid": record["pid"]},
            )
            peer = self.hub.peer
            if peer is not None:
                await peer.close("control connection unresponsive")
            return
        identity = _identity(record)
        self.hung_ends += 1
        self.last_hung_reason = why
        log.error(
            "terminal gateway pid %s is hung (%s, and its /health is silent): ending it and "
            "taking its terminals back",
            record["pid"],
            why,
            extra={"gateway_pid": record["pid"]},
        )
        await asyncio.to_thread(
            ledger, self.data_dir, f"terminal_gateway hung pid={record['pid']}: {why}"
        )
        if identity is None:
            log.error("terminal gateway pid %s has no process identity to end", record["pid"])
            return
        await asyncio.to_thread(_terminate, identity)
        if not await _wait_gone(identity, TERMINATE_GRACE_SECONDS):
            # A stopped process holds a termination request until it runs again.
            await asyncio.to_thread(_terminate, identity, kill=True)
        if not await _wait_gone(identity, TERMINATE_GRACE_SECONDS):
            # Still there: its terminals stay with it rather than gain a second writer.
            log.error("terminal gateway pid %s could not be ended", record["pid"])
            return
        self._take_back_all(f"terminal gateway pid {record['pid']} was hung ({why})")
        peer = self.hub.peer
        if peer is not None:
            await peer.close("gateway hung")

    def _record_identity(self, identity: tuple[int, float] | None, how: str) -> None:
        """The recovery record and the lifecycle ledger name the gateway. In a thread."""
        if identity is not None:
            update_daemon(
                self.data_dir,
                terminal_gateway={"pid": identity[0], "created_at": identity[1]},
            )
        ledger(
            self.data_dir,
            f"terminal_gateway {how} pid={identity[0] if identity else '?'} daemon={os.getpid()}",
        )

    # -- finding, replacing and starting -------------------------------------------------

    async def _find_or_start(self) -> tuple[dict[str, Any], str]:
        """A usable gateway's record, and how this daemon came by it."""
        record = await asyncio.to_thread(_live_record, self.data_dir)
        if record is not None:
            reason = self._replacement_reason(record)
            if reason is None:
                own = self.spawned is not None and _identity(record) == self.spawned
                return record, HOW_RECONNECTED if own else HOW_ADOPTED
            await self._replace(record, reason)
        record = await self._spawn_and_wait()
        own = self.spawned is not None and _identity(record) == self.spawned
        return record, HOW_STARTED if own else HOW_ADOPTED

    def _replacement_reason(self, record: dict[str, Any]) -> str | None:
        """Why a running gateway must not serve this daemon, or None when it may."""
        if self.spawned is not None and _identity(record) == self.spawned:
            if record["build_id"] != self.build_id and not self._build_warning_logged:
                self._build_warning_logged = True
                log.warning(
                    "terminal gateway pid %s, which this daemon started, reports build %s "
                    "where this daemon computes %s; kept, since it runs this daemon's code",
                    record["pid"],
                    record["build_id"],
                    self.build_id,
                )
            return None
        if record["protocol"] != GATEWAY_PROTOCOL_VERSION:
            return (
                f"it speaks protocol {record['protocol']}, this daemon "
                f"{GATEWAY_PROTOCOL_VERSION}"
            )
        if record["build_id"] != self.build_id:
            return f"it runs build {record['build_id']}, this daemon {self.build_id}"
        if record["daemon_port"] != self.daemon_port:
            return f"it serves the daemon on port {record['daemon_port']}, not {self.daemon_port}"
        if record["port"] != self.port:
            return f"it listens on port {record['port']}, and port {self.port} is configured"
        return None

    async def _replace(self, record: dict[str, Any], reason: str) -> None:
        """Ask a gateway running other code to quit, and end it if it stays."""
        self.replacements += 1
        self.last_replacement = reason
        log.warning(
            "replacing terminal gateway pid %s: %s",
            record["pid"],
            reason,
            extra={"gateway_pid": record["pid"]},
        )
        await asyncio.to_thread(
            ledger, self.data_dir, f"terminal_gateway replacing pid={record['pid']}: {reason}"
        )
        await _ask_to_quit(record, record)
        identity = _identity(record)
        if identity is None:
            raise RuntimeError(f"terminal gateway pid {record['pid']} has no process identity")
        await self._end(identity, reason=reason)
        if self.holder is not None and _holder(record) == self.holder:
            self._take_back_all(f"terminal gateway pid {record['pid']} was replaced: {reason}")

    async def _end(self, identity: tuple[int, float], *, reason: str) -> None:
        """Wait for a gateway to leave, terminate it if it does not, and confirm it went."""
        if await _wait_gone(identity, self._quit_grace):
            return
        log.warning(
            "terminal gateway pid %s did not exit within %.0fs (%s); terminating it",
            identity[0],
            self._quit_grace,
            reason,
            extra={"gateway_pid": identity[0]},
        )
        await asyncio.to_thread(_terminate, identity)
        if not await _wait_gone(identity, TERMINATE_GRACE_SECONDS):
            raise RuntimeError(f"terminal gateway pid {identity[0]} could not be stopped")

    async def _spawn_and_wait(self) -> dict[str, Any]:
        hosts = list(await self._hosts())
        process = await asyncio.to_thread(self._spawn, hosts)
        self._process = process
        self.spawns += 1
        log.info(
            "terminal gateway spawned: pid %s (spawn %s) for port %s",
            process.pid,
            self.spawns,
            self.port,
            extra={"gateway_pid": process.pid, "gateway_spawns": self.spawns},
        )
        deadline = time.monotonic() + self._ready_timeout
        # unsupervised-loop-ok: bounded by the ready deadline, inside the supervised loop.
        while True:
            record = await asyncio.to_thread(_spawned_record, self.data_dir, process.pid)
            if record is not None:
                self.spawned = _identity(record)
                return record
            code = process.poll()
            if code is not None:
                self.last_exit_code = code
                if code == EXIT_LOCKED:
                    # Another gateway holds this data directory. It writes its record
                    # once its listeners are bound, and that is the one to use.
                    found = await self._await_any_record(LOCKED_RECORD_SECONDS)
                    if found is not None:
                        reason = self._replacement_reason(found)
                        if reason is None:
                            return found
                        await self._replace(found, reason)
                    raise RuntimeError("another terminal gateway holds this data directory")
                if code == EXIT_BIND:
                    raise RuntimeError(
                        f"the terminal gateway could not listen on port {self.port} "
                        "(see terminal-gateway.log)"
                    )
                raise RuntimeError(
                    f"the terminal gateway exited with code {code} before announcing itself "
                    f"(see {CONSOLE_LOG_NAME})"
                )
            if time.monotonic() >= deadline:
                await asyncio.to_thread(_kill_process, process)
                raise TimeoutError(
                    f"the terminal gateway did not announce itself within {self._ready_timeout:g}s"
                )
            await asyncio.sleep(POLL_SECONDS)

    async def _await_any_record(self, seconds: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            record = await asyncio.to_thread(_live_record, self.data_dir)
            if record is not None:
                return record
            await asyncio.sleep(POLL_SECONDS)
        return None

    def _spawn(self, hosts: list[str]) -> subprocess.Popen[bytes]:
        """Start one gateway, outside every Job and in its own group. Runs in a thread."""
        command = self._command(
            data_dir=self.data_dir,
            daemon_port=self.daemon_port,
            port=self.port,
            hosts=hosts,
            log_level=self.log_level,
            switch_interval_ms=self.switch_interval_ms,
            subscribe=DEFAULT_SUBSCRIBE_POLICY,
        )
        environment = scrub_claude_session_markers(os.environ)
        for name in WITHHELD_ENVIRONMENT:
            environment.pop(name, None)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        console_path = self.data_dir / CONSOLE_LOG_NAME
        mode = "ab"
        with contextlib.suppress(OSError):
            if console_path.stat().st_size > CONSOLE_MAX_BYTES:
                mode = "wb"
        options: dict[str, Any] = {}
        creationflags = 0
        if os.name == "nt":
            # Its own process group, so a Ctrl-C at the daemon's console stays there.
            creationflags = background_creation_flags() | int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            # Its own session, so the daemon's terminal hanging up does not reach it.
            # There is no Job object on POSIX; this is what keeps it out of the
            # daemon's process group.
            options["start_new_session"] = True
        with console_path.open(mode) as console:
            # cwd in the data directory, so it never holds an app tree against a rebuild.
            return popen_outside_job(
                command,
                creationflags=creationflags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=console,
                cwd=str(self.data_dir),
                env=environment,
                **options,
            )


def terminal_transport(host: GatewayHost | None) -> dict[str, Any]:
    """`terminal_transport` for `/api/health` and the doctor report.

    No host is a daemon whose runtime never built one, which serves every terminal
    itself exactly as a disabled gateway does.
    """
    if host is None:
        return {"transport": TRANSPORT_DAEMON, "state": STATE_DISABLED, "port": None}
    return host.transport()


# --- discovery records -----------------------------------------------------------------


_usable = usable
_identity = identity


def _holder(record: dict[str, Any]) -> tuple[int, float | None]:
    """Which gateway process a record names, for "is it still the one holding them"."""
    created = record.get("created_at")
    if isinstance(created, (int, float)) and not isinstance(created, bool):
        return int(record["pid"]), float(created)
    return int(record["pid"]), None


def _holder_state(holder: tuple[int, float | None]) -> str:
    """`process_state` of a holder. In a thread."""
    return process_state({"pid": holder[0], "created_at": holder[1]})


def _answers_health(port: int, timeout: float) -> bool:
    """Whether the gateway's browser listener answers `/health` at all. In a thread.

    Any HTTP answer counts, a 503 included: the question is whether its loop is
    running, and only a silent one is hung.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False


def _live_record(data_dir: Path) -> dict[str, Any] | None:
    """The discovery record of a gateway that is running, or None. In a thread.

    A record whose process cannot be inspected is kept: its token is what proves
    it, and the connection that follows is what decides.
    """
    record = _usable(read_discovery(data_dir))
    if record is None:
        return None
    identity = _identity(record)
    if identity is None:
        return record
    state = process_state({"pid": identity[0], "created_at": identity[1]})
    return None if state == "gone" else record


def _spawned_record(data_dir: Path, spawned_pid: int) -> dict[str, Any] | None:
    """The record of the gateway `spawned_pid` is, or launched. In a thread.

    One level of parentage, for the reason the worker has (`worker_host._spawned_by`):
    a Windows virtual environment's `python.exe` is a launcher whose child is the
    real interpreter.
    """
    record = _usable(read_discovery(data_dir))
    if record is None:
        return None
    pid = int(record["pid"])
    if pid == spawned_pid:
        return record
    try:
        import psutil

        return record if psutil.Process(pid).ppid() == spawned_pid else None
    except Exception:  # noqa: BLE001 - an unreadable process is not one we started
        return None


async def _ask_to_quit(gateway: dict[str, Any], record: dict[str, Any] | None) -> bool:
    """Send a gateway `quit` over a connection of its own; whether it was sent.

    For a gateway this daemon is not connected to: one it is replacing, or one it
    lost the connection to. A protocol it does not speak cannot carry the request,
    and the caller terminates it instead. The sending is `gateway_discovery`'s, which
    the staged swap uses too, so there is one way to ask a gateway to go.
    """
    if record is None or record.get("pid") != gateway.get("pid"):
        return False
    return await send_quit(record)


async def _wait_gone(identity: tuple[int, float], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    # unsupervised-loop-ok: bounded by its deadline; a wait, not a service loop.
    while True:
        state = await asyncio.to_thread(
            process_state, {"pid": identity[0], "created_at": identity[1]}
        )
        if state == "gone":
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(POLL_SECONDS)


def _terminate(identity: tuple[int, float], *, kill: bool = False) -> None:
    """Terminate - or ``kill`` - exactly that process, never a recycled pid. In a thread."""
    try:
        import psutil

        process = psutil.Process(identity[0])
        if abs(process.create_time() - identity[1]) >= 0.001:
            return
        if kill:
            process.kill()
        else:
            process.terminate()
    except Exception:  # noqa: BLE001 - gone, or not ours to signal; the wait decides
        return


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(OSError):
        process.kill()
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=TERMINATE_GRACE_SECONDS)


async def _listener_hosts(config: Any) -> list[str]:
    """The daemon's own listener hosts, so a page that reaches the daemon reaches this."""
    from .tailscale import listener_hosts

    return await listener_hosts(
        config.host,
        bool(getattr(config, "tailnet_enabled", False)),
        bool(getattr(config, "wsl_bridge_enabled", False)),
    )


__all__ = [
    "GATEWAY_HOST_LOOP",
    "TRANSPORT_DAEMON",
    "TRANSPORT_GATEWAY",
    "GatewayHost",
    "terminal_transport",
]
