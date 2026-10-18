"""The terminal gateway: browser terminals served from a process of their own.

`development/DAEMON_LOAD_ISOLATION.md` item 28, and the point of the whole plan.
The daemon runs every terminal keystroke on the same event loop as everything else
it does, so a daemon stall - a slow scan, a GIL-heavy reconcile, a restart - froze
every terminal with it. Here a terminal's bytes go browser -> gateway -> PTY
supervisor and back, and the daemon is told about them afterwards: it can stall,
or restart, and typing keeps echoing.

**What runs here** is the terminal core the daemon runs in process
(`terminal_core`): the same stream, core and browser protocol, hosted by
`GatewayHub` for the daemon's sessions. Around it this module adds the three
things only a process has:

- a **supervisor link** (`SupervisorLink`) at the supervisor's `terminal` scope,
  which may subscribe, write, resize and list and nothing else, and never sees a
  session's secrets. It asks for `output_gap`, so the supervisor never waits on
  it, and for `subscribe_since`, so a resubscribe after a gap or a reconnect is a
  delta. Against a supervisor too old to offer them it still works, as the daemon
  used to: whole-buffer resubscribes, and a supervisor that may wait on it.
- a **browser listener** on the daemon's host set and `terminal_gateway_port`,
  serving `/pty/{sid}` and `/health` behind the daemon's own browser policy
  (`http_origin_policy.gateway_middleware`), re-bound by a `ListenerGuard` if
  asyncio closes it, and answering 503 until it has a supervisor and has heard
  from a daemon.
- a **lifecycle**: one instance per data directory on every platform (a locked
  file; the supervisor's named mutex has no POSIX twin to copy), a discovery file
  (`terminal-gateway.json`) naming its ports, token, build and protocol, its own
  rotating `terminal-gateway.log`, and an exit when the daemon says it is
  quitting or when there has been no daemon and nothing live for a while.

**It outlives the daemon on purpose.** A daemon restart is exactly when typing
must not stop, so the gateway keeps every session it was given, keeps carrying
output and input with no daemon attached, and holds what the daemon has not yet
heard until a daemon - the same one or its successor - attaches again.

Started frozen as `swe-mux.exe --gateway-child` and from source as
`python -m swe_mux.terminal_gateway`, never as `-m swe_mux`, which would load the
whole daemon into a process that needs none of it (`worker.py` has the same rule).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import json
import logging
import marshal
import os
import secrets
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from aiohttp import web

from . import __version__
from . import app_keys as keys
from .framed_ipc import FramedPeer, HandshakeRefused, connect, new_token
from .gateway_discovery import DISCOVERY_FILENAME, discovery_path, read_discovery
from .gil_switch import apply_gil_switch_interval
from .http_origin_policy import gateway_middleware
from .listener_guard import ListenerGuard
from .logsetup import enable_crash_tracebacks, setup_gateway_logging
from .network_usage import NetworkUsage, metered_websocket
from .proactor_accept import resilient_event_loop
from .process_priority import raise_self
from .terminal_core import GATEWAY_PROTOCOL_VERSION, GatewayHub
from .terminal_core.protocol import CLOSE_GATEWAY_RESTARTING, CLOSE_USE_DAEMON
from .timer_resolution import raise_timer_resolution

# Named, not `__name__`: from source this module runs as `-m swe_mux.terminal_gateway`,
# where `__name__` is `__main__`, and a log line should say which process wrote it.
log = logging.getLogger("swe_mux.terminal_gateway")

#: The supervisor's protocol (`supervisor.PROTOCOL_VERSION`), restated rather than
#: imported: `supervisor.py` drags in the PTY stack. A test holds the two equal.
SUPERVISOR_PROTOCOL = 1
SUPERVISOR_DISCOVERY = "supervisor.json"
SUPERVISOR_SCOPE = "terminal"
CAPABILITY_OUTPUT_GAP = "output_gap"
CAPABILITY_SUBSCRIBE_SINCE = "subscribe_since"
CAPABILITY_TERMINAL_SCOPE = "terminal_scope"
SUPERVISOR_CAPABILITIES = (CAPABILITY_OUTPUT_GAP, CAPABILITY_SUBSCRIBE_SINCE)

#: Where a reverse proxy in front of an `https:` page mounts the gateway.
GATEWAY_PROXY_PREFIX = "/gateway"
#: The gateway's whole browser surface, `(method, path)`: what `build_app` registers,
#: and what the frontend overlay's API digest covers beside the daemon's own routes
#: (`frontend_overlay.daemon_api_digest`), since a page talks to both.
BROWSER_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/pty/{sid}"),
    # The same socket under the prefix a reverse proxy mounts the gateway at
    # (Tailscale Serve for an `https:` page, Vite in development), so it is reached
    # whether or not the proxy strips the prefix (`frontend/src/terminalTransport.ts`).
    ("GET", f"{GATEWAY_PROXY_PREFIX}/pty/{{sid}}"),
    ("GET", "/health"),
)
LOCK_FILENAME = "terminal-gateway.lock"
CONSOLE_LOG_NAME = "terminal-gateway-console.log"

#: When a session's supervisor subscription starts. `eager`: as soon as the daemon
#: hands the session over, so a browser's first attach is a local read. `on_attach`:
#: at a session's first browser, ended - ring and all - a linger after its last, so
#: an unwatched session costs the gateway nothing.
#:
#: Eager is the default, settled by measurement (item 28's record): 36 sessions
#: holding 2 MiB each cost the eager gateway 114 MiB against 41 MiB, and on_attach
#: added 13 ms to each session's first attach (15.5 ms against 2.7 ms) and made
#: every first attach wait on the supervisor. Parsing the output of every session
#: cost neither policy a measurable share of a core. The memory is the bound the
#: daemon already pays for the same rings, and an attach that needs nothing but
#: this process is the property the gateway exists for.
SUBSCRIBE_EAGER = "eager"
SUBSCRIBE_ON_ATTACH = "on_attach"
SUBSCRIBE_POLICIES = (SUBSCRIBE_EAGER, SUBSCRIBE_ON_ATTACH)
DEFAULT_SUBSCRIBE_POLICY = SUBSCRIBE_EAGER
DEFAULT_SUBSCRIBE_LINGER_SECONDS = 600.0

#: With no daemon attached and nothing live under the supervisor for this long,
#: the gateway has nobody to serve and exits.
DEFAULT_IDLE_EXIT_SECONDS = 120.0
LIFECYCLE_CHECK_SECONDS = 5.0
METERING_INTERVAL_SECONDS = 5.0
#: How long a browser's attach waits for its session's subscription.
SUBSCRIBE_TIMEOUT_SECONDS = 15.0
SUPERVISOR_BACKOFF_SECONDS = (0.5, 1.0, 2.0, 5.0, 10.0)
#: A subscribe reply carries a whole retained buffer, whose size is the daemon's
#: `scrollback_bytes`; framed_ipc's own default would refuse a large one.
DEFAULT_MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
WS_MAX_MESSAGE_BYTES = 2 * 1024 * 1024

#: Every `swe_mux` module this process loads, in a fixed order, which is what its
#: build identity is a digest over (`gateway_build_id`). A test imports this module
#: in a fresh interpreter and fails when the two disagree.
GATEWAY_MODULES = (
    "swe_mux",
    "swe_mux.app_keys",
    "swe_mux.composer_input",
    "swe_mux.framed_ipc",
    "swe_mux.gateway_discovery",
    "swe_mux.gil_switch",
    "swe_mux.http_origin_policy",
    "swe_mux.listener_guard",
    "swe_mux.logsetup",
    "swe_mux.network_usage",
    "swe_mux.proactor_accept",
    "swe_mux.process_priority",
    "swe_mux.screen_mode",
    "swe_mux.scrollback",
    "swe_mux.terminal_arbitration",
    "swe_mux.terminal_core",
    "swe_mux.terminal_core.connection",
    "swe_mux.terminal_core.core",
    "swe_mux.terminal_core.diagnostics",
    "swe_mux.terminal_core.flow",
    "swe_mux.terminal_core.gateway",
    "swe_mux.terminal_core.host",
    "swe_mux.terminal_core.input",
    "swe_mux.terminal_core.protocol",
    "swe_mux.terminal_core.remote",
    "swe_mux.terminal_core.stream",
    "swe_mux.terminal_gateway",
    "swe_mux.timer_resolution",
)


def gateway_build_id() -> str:
    """A digest over the code this process runs: equal for the daemon that spawned
    it and for the gateway it spawned when both are the same build.

    `__version__` cannot say that - the frozen app is rebuilt per commit and the
    version moves per release - so the digest is over each module's compiled code,
    which the frozen archive and a source checkout both provide.
    """
    digest = hashlib.sha256()
    for name in GATEWAY_MODULES:
        digest.update(name.encode("utf-8"))
        spec = importlib.util.find_spec(name)
        loader = getattr(spec, "loader", None) if spec is not None else None
        get_code = getattr(loader, "get_code", None)
        code = get_code(name) if get_code is not None else None
        digest.update(marshal.dumps(code) if code is not None else b"-")
    return digest.hexdigest()[:24]


# --- the supervisor link --------------------------------------------------------


@dataclass(eq=False)
class _Subscription:
    sid: str
    stream: str = ""
    position: int = 0
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    pending: asyncio.Task[None] | None = None
    alive: bool = True
    browsers: int = 0
    linger: asyncio.TimerHandle | None = None


class SupervisorPty:
    """A hosted core's PTY sink: its writes and resizes go to the supervisor."""

    def __init__(self, link: SupervisorLink, sid: str) -> None:
        self._link = link
        self._sid = sid

    def write(self, data: str | bytes) -> None:
        self._link.write(self._sid, data)

    def resize(self, cols: int, rows: int) -> None:
        self._link.resize(self._sid, cols, rows)


class SupervisorUnreachable(ConnectionError):
    """The supervisor link is down, so nothing was written."""


class SupervisorLink:
    """The gateway's connection to the PTY supervisor, at its `terminal` scope."""

    def __init__(
        self,
        data_dir: Path,
        hub: GatewayHub,
        *,
        policy: str = DEFAULT_SUBSCRIBE_POLICY,
        linger_seconds: float = DEFAULT_SUBSCRIBE_LINGER_SECONDS,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> None:
        if policy not in SUBSCRIBE_POLICIES:
            raise ValueError(f"unknown subscribe policy {policy!r}")
        self.data_dir = data_dir
        self.hub = hub
        self.policy = policy
        self.linger_seconds = linger_seconds
        self.max_payload_bytes = max_payload_bytes
        self.peer: FramedPeer | None = None
        self.capabilities: frozenset[str] = frozenset()
        self.supervisor_pid: int | None = None
        self.live: set[str] = set()
        self.subscriptions: dict[str, _Subscription] = {}
        self.connects = 0
        self.connect_failures = 0
        self.gaps = 0
        # Resubscribes the supervisor answered with only what was missed, against
        # ones that took its whole buffer: the first is what `subscribe_since` buys.
        self.delta_subscribes = 0
        self.full_subscribes = 0
        self._ptys: dict[str, SupervisorPty] = {}
        self._up = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def connected(self) -> bool:
        return self.peer is not None and not self.peer.closed

    # -- what a core asks of it -------------------------------------------------------

    def pty_for(self, sid: str) -> SupervisorPty:
        pty = self._ptys.get(sid)
        if pty is None:
            pty = self._ptys[sid] = SupervisorPty(self, sid)
        return pty

    def stream_name(self, sid: str) -> str:
        subscription = self.subscriptions.get(sid)
        return subscription.stream if subscription is not None else ""

    def write(self, sid: str, data: str | bytes) -> None:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        self._send({"t": "write", "sid": sid}, payload)

    def resize(self, sid: str, cols: int, rows: int) -> None:
        self._send({"t": "resize", "sid": sid, "cols": int(cols), "rows": int(rows)})

    def _send(self, header: dict[str, Any], payload: bytes = b"") -> None:
        peer = self.peer
        if peer is None or peer.closed:
            raise SupervisorUnreachable("the PTY supervisor is not connected")
        peer.notify(header, payload)

    # -- subscriptions ---------------------------------------------------------------

    def session_added(self, sid: str) -> None:
        # A session the daemon hands over is live, whether or not the supervisor
        # has said so to this connection (it reports existing sessions at hello).
        self.live.add(sid)
        if self.policy == SUBSCRIBE_EAGER:
            self._start_subscribe(self._subscription(sid))

    def session_removed(self, sid: str) -> None:
        subscription = self.subscriptions.pop(sid, None)
        self._ptys.pop(sid, None)
        if subscription is None:
            return
        if subscription.linger is not None:
            subscription.linger.cancel()
        if subscription.pending is not None:
            subscription.pending.cancel()
        self._unsubscribe(sid)

    async def browser_attached(self, sid: str) -> None:
        """Make sure a browser about to attach reads a subscribed ring."""
        subscription = self._subscription(sid)
        subscription.browsers += 1
        if subscription.linger is not None:
            subscription.linger.cancel()
            subscription.linger = None
        if subscription.ready.is_set():
            return
        self._start_subscribe(subscription)
        try:
            await asyncio.wait_for(subscription.ready.wait(), SUBSCRIBE_TIMEOUT_SECONDS)
        except TimeoutError:
            subscription.browsers -= 1
            raise SupervisorUnreachable(f"session {sid} could not be subscribed") from None

    def browser_detached(self, sid: str) -> None:
        subscription = self.subscriptions.get(sid)
        if subscription is None:
            return
        subscription.browsers = max(0, subscription.browsers - 1)
        if subscription.browsers or self.policy != SUBSCRIBE_ON_ATTACH:
            return
        subscription.linger = asyncio.get_running_loop().call_later(
            self.linger_seconds, self._linger_expired, sid
        )

    def _linger_expired(self, sid: str) -> None:
        subscription = self.subscriptions.get(sid)
        if subscription is None or subscription.browsers:
            return
        subscription.linger = None
        subscription.ready.clear()
        self._unsubscribe(sid)
        # The ring goes too - releasing its memory is the whole point of this
        # policy - so the next attach must take the supervisor's whole buffer: a
        # delta appended to an emptied ring would replay a fragment as the screen.
        session = self.hub.sessions.get(sid)
        if session is not None:
            session.core.stream.reseed(b"", 0)
        subscription.stream, subscription.position = "", 0
        log.debug("terminal gateway released idle session %s", sid)

    def _subscription(self, sid: str) -> _Subscription:
        subscription = self.subscriptions.get(sid)
        if subscription is None:
            subscription = self.subscriptions[sid] = _Subscription(sid)
        return subscription

    def _start_subscribe(self, subscription: _Subscription) -> None:
        if subscription.pending is not None and not subscription.pending.done():
            return
        if not self.connected:
            return  # the reconnect resubscribes everything that wants it
        task = asyncio.get_running_loop().create_task(self._subscribe(subscription))
        subscription.pending = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _unsubscribe(self, sid: str) -> None:
        with contextlib.suppress(SupervisorUnreachable, ConnectionError):
            self._send({"t": "unsubscribe", "sid": sid})

    async def _subscribe(self, subscription: _Subscription) -> None:
        peer = self.peer
        if peer is None or peer.closed:
            return
        sid = subscription.sid
        header: dict[str, Any] = {"t": "subscribe", "sid": sid}
        if subscription.stream and CAPABILITY_SUBSCRIBE_SINCE in self.capabilities:
            header["since"] = subscription.position
            header["stream"] = subscription.stream

        def apply(reply: dict[str, Any], payload: bytes) -> None:
            # Inside the read loop, before the next frame: output that follows the
            # reply must land after the bytes the reply carries.
            if not reply.get("ok"):
                return
            stream = str(reply.get("stream") or "")
            position = int(reply.get("position") or 0)
            session = self.hub.sessions.get(sid)
            delta = bool(reply.get("delta")) and stream == subscription.stream
            if delta:
                self.delta_subscribes += 1
            else:
                self.full_subscribes += 1
            if session is not None:
                ring = session.core.stream
                if delta:
                    ring.feed(payload)
                else:
                    ring.reseed(payload, position)
            subscription.stream, subscription.position = stream, position
            subscription.alive = bool(reply.get("alive", True))
            subscription.ready.set()

        try:
            await peer.request(header, timeout_seconds=SUBSCRIBE_TIMEOUT_SECONDS, on_reply=apply)
        except Exception as exc:  # noqa: BLE001 - one session's subscribe; the link carries on
            log.warning("terminal gateway could not subscribe to %s: %s", sid, exc)

    # -- what the supervisor says ------------------------------------------------------

    def _on_notice(self, peer: FramedPeer, header: dict[str, Any], payload: bytes) -> None:
        kind = header.get("t")
        sid = str(header.get("sid"))
        subscription = self.subscriptions.get(sid)
        if kind == "output":
            if subscription is None or not subscription.ready.is_set():
                return
            subscription.position += len(payload)
            self.hub.feed_output(sid, payload)
        elif kind == "gap":
            # The supervisor stopped sending this session to us at `position`; the
            # resubscribe asks for exactly what it dropped.
            self.gaps += 1
            if subscription is not None:
                subscription.ready.clear()
                self._start_subscribe(subscription)
        elif kind == "pty_exit":
            self.live.discard(sid)
            if subscription is not None:
                subscription.alive = False
        elif kind == "removed":
            self.live.discard(sid)
        elif kind == "resized":
            pass  # this gateway's cores own geometry for the sessions it hosts
        else:
            log.debug("terminal gateway ignored a supervisor notice %r", kind)

    def _on_close(self, peer: FramedPeer, reason: str) -> None:
        if peer is not self.peer:
            return
        self.peer = None
        self._up.clear()
        for subscription in self.subscriptions.values():
            subscription.ready.clear()
        log.warning("terminal gateway lost the PTY supervisor: %s", reason)

    # -- the connection ----------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        """Hold a connection to the supervisor until ``stop``, reconnecting after a loss."""
        attempt = 0
        while not stop.is_set():
            try:
                peer = await self._connect()
            except Exception as exc:  # noqa: BLE001 - reported, then retried with backoff
                self.connect_failures += 1
                if attempt == 0 or self.connect_failures % 30 == 0:
                    log.warning("terminal gateway cannot reach the PTY supervisor: %s", exc)
                last = len(SUPERVISOR_BACKOFF_SECONDS) - 1
                delay = SUPERVISOR_BACKOFF_SECONDS[min(attempt, last)]
                attempt += 1
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), delay)
                continue
            attempt = 0
            closed = asyncio.ensure_future(peer.wait_closed())
            stopping = asyncio.ensure_future(stop.wait())
            await asyncio.wait({closed, stopping}, return_when=asyncio.FIRST_COMPLETED)
            closed.cancel()
            stopping.cancel()
        await self.close()

    async def _connect(self) -> FramedPeer:
        info = json.loads((self.data_dir / SUPERVISOR_DISCOVERY).read_text(encoding="utf-8"))
        protocol = info.get("protocol")
        if protocol != SUPERVISOR_PROTOCOL:
            raise HandshakeRefused(
                f"the supervisor speaks protocol {protocol!r}, this gateway {SUPERVISOR_PROTOCOL}"
            )
        peer = await connect(
            "127.0.0.1",
            int(info["port"]),
            token=str(info["token"]),
            protocol=SUPERVISOR_PROTOCOL,
            capabilities=SUPERVISOR_CAPABILITIES,
            hello={"scope": SUPERVISOR_SCOPE},
            name="terminal-gateway-supervisor",
            on_notice=self._on_notice,
            on_close=self._on_close,
            max_payload_bytes=self.max_payload_bytes,
        )
        offered = peer.remote_hello.get("capabilities")
        offered_set = set(offered) if isinstance(offered, list) else set()
        missing = [
            capability
            for capability in (*SUPERVISOR_CAPABILITIES, CAPABILITY_TERMINAL_SCOPE)
            if capability not in offered_set
        ]
        if missing:
            # An older supervisor: it still serves us, as it served the daemon
            # before these existed - whole-buffer resubscribes, and no promise
            # that it never waits on a reader that falls behind.
            log.warning(
                "terminal gateway: the PTY supervisor does not offer %s; carrying on without",
                ", ".join(missing),
            )
        self.capabilities = peer.capabilities
        pid = peer.remote_hello.get("pid")
        self.supervisor_pid = int(pid) if isinstance(pid, int) else None
        sessions = peer.remote_hello.get("sessions")
        self.live = {
            str(item.get("sid"))
            for item in sessions or ()
            if isinstance(item, dict) and item.get("alive", True)
        }
        self.peer = peer
        self.connects += 1
        self._up.set()
        log.info(
            "terminal gateway connected to the PTY supervisor pid %s (%d live session(s))",
            self.supervisor_pid,
            len(self.live),
        )
        for subscription in self.subscriptions.values():
            if self.policy == SUBSCRIBE_EAGER or subscription.browsers:
                subscription.pending = None
                self._start_subscribe(subscription)
        if self.policy == SUBSCRIBE_EAGER:
            for sid in self.hub.sessions:
                if sid not in self.subscriptions:
                    self._start_subscribe(self._subscription(sid))
        return peer

    async def close(self) -> None:
        peer, self.peer = self.peer, None
        if peer is not None:
            await peer.close("gateway stopping")
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def stats(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "supervisor_pid": self.supervisor_pid,
            "capabilities": sorted(self.capabilities),
            "policy": self.policy,
            "live": len(self.live),
            "subscribed": sum(1 for item in self.subscriptions.values() if item.ready.is_set()),
            "connects": self.connects,
            "connect_failures": self.connect_failures,
            "gaps": self.gaps,
            "delta_subscribes": self.delta_subscribes,
            "full_subscribes": self.full_subscribes,
        }


# --- one instance per data directory ---------------------------------------------


class InstanceLock:
    """An exclusive lock on a file, held for this process's life.

    The OS drops it when the process dies, however it dies, so a crashed gateway
    never leaves a successor locked out - the property a pid file alone lacks.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - held open for the process's life
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        handle, self._file = self._file, None
        if handle is None:
            return
        with contextlib.suppress(OSError):
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _write_discovery(data_dir: Path, record: dict[str, Any]) -> None:
    path = discovery_path(data_dir)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _remove_discovery(data_dir: Path) -> None:
    record = read_discovery(data_dir)
    if record is not None and record.get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            discovery_path(data_dir).unlink()


def _created_at() -> float | None:
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except Exception:  # noqa: BLE001 - identity is best effort; the pid still names it
        return None


# --- the process -----------------------------------------------------------------


@dataclass
class GatewayOptions:
    data_dir: Path
    daemon_port: int
    port: int
    hosts: tuple[str, ...] = ("127.0.0.1",)
    control_port: int = 0
    policy: str = DEFAULT_SUBSCRIBE_POLICY
    linger_seconds: float = DEFAULT_SUBSCRIBE_LINGER_SECONDS
    idle_exit_seconds: float = DEFAULT_IDLE_EXIT_SECONDS
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES


class TerminalGateway:
    """The gateway process: the hub, its supervisor link, its browser listener."""

    def __init__(self, options: GatewayOptions) -> None:
        self.options = options
        self.token = new_token()
        self.hub = GatewayHub(
            token=self.token,
            pty_for=lambda sid: self.link.pty_for(sid),
            stream_name=lambda sid: self.link.stream_name(sid),
        )
        self.link = SupervisorLink(
            options.data_dir,
            self.hub,
            policy=options.policy,
            linger_seconds=options.linger_seconds,
            max_payload_bytes=options.max_payload_bytes,
        )
        self.hub.on_session_added = self.link.session_added
        self.hub.on_session_removed = self.link.session_removed
        self.hub.on_quit = self._quit
        self.usage = NetworkUsage()
        self.stop = asyncio.Event()
        self.started_at = time.time()
        self.build_id = gateway_build_id()
        self.port = options.port
        self.control_port = 0
        self.exit_reason = ""
        self._runner: web.AppRunner | None = None
        self._sent_metering: str | None = None

    @property
    def ready(self) -> bool:
        return self.link.connected and self.hub.daemon_seen

    def _quit(self) -> None:
        self.exit_reason = "the daemon quit"
        self.stop.set()

    # -- the browser side ----------------------------------------------------------------

    def build_app(self) -> web.Application:
        app = web.Application(
            middlewares=[
                gateway_middleware(
                    daemon_port=self.options.daemon_port, gateway_port=lambda: self.port
                )
            ]
        )
        app[keys.NETWORK_USAGE] = self.usage
        for method, path in BROWSER_ROUTES:
            handler = self._health if path == "/health" else self._pty
            if method == "GET":
                app.router.add_get(path, handler)
            else:
                app.router.add_route(method, path, handler)
        return app

    async def _pty(self, request: web.Request) -> web.WebSocketResponse:
        sid = request.match_info["sid"]
        ws = metered_websocket(request, "pty", heartbeat=20, max_msg_size=WS_MAX_MESSAGE_BYTES)
        await ws.prepare(request)
        if sid not in self.hub.sessions:
            # Not handed to this gateway (an in-process or cold session, or one the
            # daemon has not upserted yet): the daemon serves it.
            await ws.close(code=CLOSE_USE_DAEMON, message=b"not served by the gateway")
            return ws
        try:
            await self.link.browser_attached(sid)
        except SupervisorUnreachable:
            await ws.close(code=CLOSE_GATEWAY_RESTARTING, message=b"supervisor unreachable")
            return ws
        try:
            await self.hub.serve(ws, sid, connection_id=secrets.token_urlsafe(12))
        except KeyError:
            await ws.close(code=CLOSE_USE_DAEMON, message=b"moved")
        finally:
            self.link.browser_detached(sid)
        return ws

    async def _health(self, _request: web.Request) -> web.Response:
        body = {
            "status": "ok" if self.ready else "starting",
            "pid": os.getpid(),
            "build": __version__,
            "build_id": self.build_id,
            "protocol": GATEWAY_PROTOCOL_VERSION,
            "supervisor": self.link.stats(),
            "daemon_attached": self.hub.daemon is not None,
            "daemon_seen": self.hub.daemon_seen,
            "sessions": len(self.hub.sessions),
            "uptime_seconds": round(time.time() - self.started_at, 3),
        }
        return web.json_response(body, status=200 if self.ready else 503)

    async def _bind(self, app: web.Application) -> ListenerGuard:
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self._runner = runner
        guard = ListenerGuard(
            make_site=lambda host, port: web.TCPSite(runner, host=host, port=port)
        )
        for index, host in enumerate(self.options.hosts):
            site = web.TCPSite(runner, host=host, port=self.port)
            try:
                await site.start()
            except OSError as exc:
                if index == 0:
                    raise SystemExit(f"cannot bind {host}:{self.port} ({exc})") from exc
                log.warning(
                    "terminal gateway could not bind %s:%s (%s); continuing on the rest",
                    host,
                    self.port,
                    exc,
                )
                continue
            if self.port == 0:
                # An OS-allocated port (tests): every later host takes the same one.
                sockets = ListenerGuard.sockets_of(site)
                self.port = int(sockets[0].getsockname()[1])
            guard.watch(host, self.port, site)
            log.info("terminal gateway listening on %s:%s", host, self.port)
        return guard

    # -- the lifecycle ---------------------------------------------------------------------

    async def run(self) -> int:
        options = self.options
        self.control_port = await self.hub.start("127.0.0.1", options.control_port)
        guard = await self._bind(self.build_app())
        _write_discovery(options.data_dir, self.discovery_record())
        log.info(
            "terminal gateway pid %s ready: browsers on %s, control on %s, build %s",
            os.getpid(),
            self.port,
            self.control_port,
            self.build_id,
        )
        tasks = [
            asyncio.create_task(self.link.run(self.stop), name="gateway-supervisor-link"),
            asyncio.create_task(guard.run(self.stop), name="gateway-listener-guard"),
            asyncio.create_task(self._watch_idle(), name="gateway-idle-watch"),
            asyncio.create_task(self._report_metering(), name="gateway-metering"),
        ]
        try:
            await self.stop.wait()
        finally:
            log.info("terminal gateway stopping: %s", self.exit_reason or "asked to stop")
            _remove_discovery(options.data_dir)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.link.close()
            await self.hub.close()
            if self._runner is not None:
                await self._runner.cleanup()
        return 0

    def discovery_record(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "created_at": _created_at(),
            "started_at": self.started_at,
            "port": self.port,
            "hosts": list(self.options.hosts),
            "control_port": self.control_port,
            "token": self.token,
            "build": __version__,
            "build_id": self.build_id,
            "protocol": GATEWAY_PROTOCOL_VERSION,
            "daemon_port": self.options.daemon_port,
        }

    async def _watch_idle(self) -> None:
        idle_since: float | None = None
        while not self.stop.is_set():
            await asyncio.sleep(LIFECYCLE_CHECK_SECONDS)
            nobody = self.hub.daemon is None and not (self.link.connected and self.link.live)
            if not nobody:
                idle_since = None
                continue
            now = time.monotonic()
            idle_since = idle_since if idle_since is not None else now
            if now - idle_since >= self.options.idle_exit_seconds:
                self.exit_reason = (
                    f"no daemon and nothing live for {self.options.idle_exit_seconds:g}s"
                )
                self.stop.set()

    async def _report_metering(self) -> None:
        """Send the browser traffic this process carried to the daemon's counters.

        Cumulative since this process started, so a report the daemon missed costs
        nothing: the next one carries it.
        """
        while not self.stop.is_set():
            await asyncio.sleep(METERING_INTERVAL_SECONDS)
            snapshot = self.usage.snapshot()
            rendered = json.dumps(snapshot, sort_keys=True, default=str)
            if rendered == self._sent_metering:
                continue
            if self.hub.send_metering({"gateway_id": self.hub.gateway_id, "usage": snapshot}):
                self._sent_metering = rendered


# --- entry point --------------------------------------------------------------------


def gateway_command(
    *,
    data_dir: Path,
    daemon_port: int,
    port: int,
    hosts: Sequence[str] = ("127.0.0.1",),
    log_level: str = "INFO",
    switch_interval_ms: float = 5.0,
    subscribe: str = DEFAULT_SUBSCRIBE_POLICY,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    """How to start the gateway: the frozen app's entry flag, or the module from source.

    Frozen, never `-m swe_mux.terminal_gateway`, for the worker's reason: on the
    app's own image that shape reads as a session helper to the redeploy and the
    process inspector (`worker.py`).
    """
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    entry = (
        [executable, "--gateway-child"]
        if frozen
        else [executable, "-m", "swe_mux.terminal_gateway"]
    )
    command = [
        *entry,
        "--data-dir",
        str(data_dir),
        "--daemon-port",
        str(daemon_port),
        "--port",
        str(port),
        "--log-level",
        log_level,
        "--switch-interval-ms",
        f"{switch_interval_ms:g}",
        "--subscribe",
        subscribe,
    ]
    for host in hosts:
        command += ["--host", host]
    return command


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="swe-mux --gateway-child")
    result.add_argument("--data-dir", type=Path, required=True)
    result.add_argument("--daemon-port", type=int, required=True)
    result.add_argument("--port", type=int, required=True, help="0 picks a free port (tests)")
    result.add_argument(
        "--host",
        action="append",
        dest="hosts",
        default=None,
        help="a listener host; repeat for each (default 127.0.0.1)",
    )
    result.add_argument("--control-port", type=int, default=0)
    result.add_argument("--subscribe", choices=SUBSCRIBE_POLICIES, default=DEFAULT_SUBSCRIBE_POLICY)
    result.add_argument(
        "--subscribe-linger-seconds", type=float, default=DEFAULT_SUBSCRIBE_LINGER_SECONDS
    )
    result.add_argument("--idle-exit-seconds", type=float, default=DEFAULT_IDLE_EXIT_SECONDS)
    result.add_argument("--log-level", default="INFO")
    result.add_argument("--switch-interval-ms", type=float, default=5.0)
    result.add_argument("--priority", default="above_normal")
    return result


def options_from(args: argparse.Namespace) -> GatewayOptions:
    return GatewayOptions(
        data_dir=args.data_dir,
        daemon_port=args.daemon_port,
        port=args.port,
        hosts=tuple(args.hosts or ("127.0.0.1",)),
        control_port=args.control_port,
        policy=args.subscribe,
        linger_seconds=args.subscribe_linger_seconds,
        idle_exit_seconds=args.idle_exit_seconds,
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = parser().parse_args(argv)
    data_dir: Path = args.data_dir
    setup_gateway_logging(data_dir, args.log_level)
    enable_crash_tracebacks(data_dir)
    lock = InstanceLock(data_dir / LOCK_FILENAME)
    code = 1
    try:
        if not lock.acquire():
            log.warning("terminal gateway pid %s: another gateway holds %s", os.getpid(), data_dir)
            code = 3
            return
        with contextlib.suppress(OSError):
            os.chdir(data_dir)
        raise_timer_resolution()
        apply_gil_switch_interval(args.switch_interval_ms)
        log.info(
            "terminal gateway pid %s starting for daemon port %s (priority %s)",
            os.getpid(),
            args.daemon_port,
            raise_self(args.priority),
        )
        gateway = TerminalGateway(options_from(args))
        code = asyncio.run(gateway.run(), loop_factory=resilient_event_loop)
    except SystemExit as exc:
        log.error("terminal gateway pid %s could not start: %s", os.getpid(), exc)
        code = 4
    except Exception:
        log.exception("terminal gateway pid %s failed", os.getpid())
        code = 1
    finally:
        lock.release()
        logging.shutdown()
        # Exit codes the spawner records: 0 a stop, 3 another gateway already runs,
        # 4 a listener that would not bind, 1 a failure.
        sys.exit(code)


if __name__ == "__main__":
    main()


__all__ = [
    "DISCOVERY_FILENAME",
    "GATEWAY_MODULES",
    "SUPERVISOR_PROTOCOL",
    "GatewayOptions",
    "InstanceLock",
    "SupervisorLink",
    "SupervisorPty",
    "SupervisorUnreachable",
    "TerminalGateway",
    "discovery_path",
    "gateway_build_id",
    "main",
    "read_discovery",
]
