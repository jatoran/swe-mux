"""An isolated swe-mux daemon on an ephemeral port, for every live tier that needs one.

Two consumers, two shapes, one set of isolation rules.

``isolated_daemon`` runs the daemon **in process** over
``aiohttp.test_utils.TestServer`` + ``create_app``. That is what the ``live_mcp``
wire canary needs (a daemon that mints a real per-session MCP token, a real agent
carrying it, the real ``/mcp`` endpoint authenticating it) and it is also the only
shape that can observe the runtime it built: the startup timeline, the adapters,
the session manager, the pseudoterminal a spawn actually allocated. The
``live_daemon`` tier uses it for the startup and session halves of its smoke.

``daemon_process`` launches the **real ``muxd`` entry point** as a subprocess. It
can see none of the above, and in exchange it exercises the four things an
in-process app object cannot have: console-script resolution, argv parsing,
``asyncio.run(serve(...))`` binding a real socket, and a process that has to exit
cleanly and take its children with it. The ``live_daemon`` tier uses it for the
lifecycle half.

The isolation rules are the same for both, because the runtime-collision note in
``CLAUDE.md`` is about the host and not about the transport: an OS-allocated free
port, never 8765, and a ``data_dir`` under the test's ``tmp_path``, never the
operator's ``~/.mux``. The in-process daemon's ingress URL is derived from
``config.port`` (``session.py`` builds ``MUX_HOOK_URL``/``MUX_MCP_URL`` from it),
so the harness binds the test server to the same chosen port; that is what makes
a spawned agent's hook and MCP env point back at this daemon rather than the live
one. ``daemon_process`` goes further and re-homes ``HOME``/``USERPROFILE`` too,
because a separate process reads a real user profile through ``Path.home()`` and
nothing in the test would notice if it wrote there.

The spawned agent's token is recovered the way the manual method does it — via
``psutil.Process(pid).environ()`` — because it is env-expanded into the child and
never written to disk. That same read is what lets a test assert the env isolation
held (the child's ``MUX_HOOK_URL`` names this daemon's port, not the live fleet's).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from aiohttp import ClientSession, TCPConnector, WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import Config, LaunchProfile, default_shell_executable
from swe_mux.server import create_app, wait_runtime_ready
from swe_mux.startup_phases import UNNAMED_PHASE

# A registered Project that has granted agents direct interrupt/end and spawn, so
# the wire tests exercise the acting path rather than only the inert draft.
_GRANT_CONFIG = (
    # `version` is required by parse_project_config; without it the whole file is
    # rejected and both grant readers silently fall back to their draft default.
    "version = 1\n"
    'session_control_grant = "granted"\n'
    'spawn_grant = "granted"\n'
    "\n"
    'land_grant = "granted"\n'
    "\n"
    "[automations]\n"
    "session_control = true\n"
    # Phase 14: the land queue is its own capability with its own switch, so the
    # scratch Project has to opt into it separately from session control.
    "land_queue = true\n"
)

# Terminal output is styled, and a shell that colourizes its own echo can put an
# SGR sequence between two characters of a word. Assertions read the plain text.
_ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def strip_ansi(data: bytes) -> bytes:
    """Terminal bytes with CSI/OSC escape sequences removed."""
    return _ANSI.sub(b"", data)


_SERVER_SOURCE = Path(__file__).resolve().parents[2] / "src" / "swe_mux" / "server.py"


def declared_startup_phases() -> list[str]:
    """Every startup phase the composition root names unconditionally.

    Read out of `server.py` with an AST walk rather than copied into a test, so a
    phase added tomorrow is covered the day it lands. Only `timeline.mark(...)`
    calls that are *direct statements of a function body* count: a mark nested
    inside an `if` or a `try` is conditional by construction, and a start that
    legitimately skips one must not fail. Every mark is unconditional today,
    which is what makes the assertion over this list strict.
    """
    tree = ast.parse(_SERVER_SOURCE.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for statement in node.body:
            call = statement.value if isinstance(statement, ast.Expr) else None
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr != "mark" or not isinstance(call.func.value, ast.Name):
                continue
            if call.func.value.id != "timeline" or not call.args:
                continue
            argument = call.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                names.append(argument.value)
    return names


def assert_startup_is_complete(health: Mapping[str, Any]) -> None:
    """The daemon says it is ready, and every phase it declares actually ran.

    Asserted against the list the daemon reports plus the list its own source
    declares, never against a copy of either: a hardcoded set is a second
    registry and the copy is what drifts.
    """
    assert health["ok"] is True, health
    assert health["status"] == "ready", health
    # `phase` is absent once the build is over; a name left behind reads as a
    # phase that never ended, which is the state this would otherwise hide.
    assert health["phase"] is None, health
    records = health["phases"]
    assert records, "the daemon reported no startup phases at all"
    for record in records:
        assert isinstance(record["name"], str) and record["name"], record
        seconds = record["seconds"]
        assert isinstance(seconds, int | float) and not isinstance(seconds, bool), record
        assert math.isfinite(seconds) and seconds >= 0.0, record
    reported = [record["name"] for record in records]
    # An `(unnamed)` record means startup spent half a second or more in work no
    # `timeline.mark` covers, which is the exact failure `startup_phases.py` was
    # written to make impossible - a silent stretch nobody can attribute. The fix
    # is a `mark` around the new work, never a relaxation here.
    assert UNNAMED_PHASE not in reported, f"startup ran unnamed work: {records}"
    missing = [name for name in declared_startup_phases() if name not in reported]
    assert not missing, f"declared startup phases never completed: {missing}; ran {reported}"
    assert health["elapsed_seconds"] >= 0.0, health


def alive(pid: int) -> bool:
    """Whether `pid` is a live, non-zombie process.

    `psutil.pid_exists` is true for a zombie on POSIX, and a reaped child stays a
    zombie until its parent waits on it - so the plain existence check reports a
    correctly-dead process as alive on Linux and nowhere else.
    """
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        # Access denied to a pid that still resolves: it exists.
        return True


def free_port() -> int:
    """One OS-allocated loopback port, released immediately for the daemon to take.

    A brief race between release and re-bind is acceptable for a single-operator
    test box and far safer than a hardcoded port that could collide with the live
    daemon.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _scratch_shell_profile() -> LaunchProfile:
    """The default shell profile a real install would have written for this host.

    `load_config` synthesizes one when the config file declares none; a `Config`
    built by hand — which is what these harnesses do, deliberately, so no test
    depends on a file the operator owns — has an empty `shell_profiles`, and
    `resolve_profile` then refuses every shell spawn with "unknown shell
    profile: default". The id has to be `default` because that is what
    `Config.default_shell_profile` names.
    """
    return LaunchProfile("default", "Scratch shell", default_shell_executable())


class PtyAttachment:
    """One live `/pty/{sid}` websocket, with its output collected in the background.

    The frames a real client exchanges are ordered (`state`, `replay_start`, the
    replay bytes, `replay_end`, geometry, then live output), but a test that
    asserts on *terminal output* should not also be asserting on that order — the
    ordering is `test_pty_ws.py`'s subject and pinning it twice makes one change
    red in two places. So this collects every binary frame into one buffer and
    lets the caller wait on the bytes.

    The reader is a task, and it is cancelled and awaited by `attach_pty`'s exit.
    A websocket reader left running past its test is the shape that reports its
    failure against whichever test the collector happens to interrupt.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self.output = bytearray()
        self.closed = asyncio.Event()

    async def read_forever(self) -> None:
        async for message in self._ws:
            if message.type == WSMsgType.BINARY:
                self.output.extend(message.data)
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                break
        self.closed.set()

    async def send_input(self, data: str) -> None:
        await self._ws.send_json({"type": "input", "data": data})

    @property
    def text(self) -> str:
        """Everything received so far, escape sequences removed."""
        return strip_ansi(bytes(self.output)).decode("utf-8", "replace")

    async def settle(self, *, quiet_seconds: float = 0.75, seconds: float = 60.0) -> None:
        """Wait until the terminal stops producing output.

        Input written into a shell that has not finished starting is dropped by
        the pseudoterminal, and "finished starting" has no portable marker - a
        PowerShell bootstrap, a bash rc file, and a zsh prompt theme all end at
        different bytes. Quiescence is the portable one.

        This is a sleep guarding the *absence* of output, which is the shape
        `tests/support/settle.py` explicitly keeps: load only makes a quiet
        window safer. The bet a fixed sleep before a positive assertion makes is
        the opposite one, and is not made anywhere here.
        """
        deadline = asyncio.get_running_loop().time() + seconds
        seen = -1
        while asyncio.get_running_loop().time() < deadline:
            if len(self.output) == seen and seen > 0:
                return
            seen = len(self.output)
            await asyncio.sleep(quiet_seconds)
        raise AssertionError(
            f"the terminal never went quiet for {quiet_seconds}s within {seconds}s; "
            f"received {len(self.output)} bytes"
        )


@dataclass
class IsolatedDaemon:
    """A thin HTTP driver over the isolated daemon and its `/mcp` endpoint."""

    client: TestClient
    root: Path
    port: int
    data_dir: Path

    # ---- app internals ------------------------------------------------------
    #
    # Only reachable because this daemon runs in this process. Everything the
    # subprocess shape can assert goes through HTTP; these are for the facts that
    # have no endpoint - which adapter materialized which file, which pid a
    # pseudoterminal actually allocated.

    @property
    def app(self) -> Any:
        return self.client.app

    # ---- HTTP ---------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        response = await self.client.get("/api/health")
        assert response.status == 200, await response.text()
        return dict(await response.json())

    async def register_project(self, name: str = "live") -> str:
        response = await self.client.post(
            "/api/projects",
            json={"name": name, "root": str(self.root), "create_missing": False},
        )
        assert response.status in (200, 201), await response.text()
        return str((await response.json())["id"])

    async def spawn(
        self,
        project_id: str,
        backend: str,
        seed_text: str | None = None,
        name: str = "live",
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Spawn one session. `seed_text` is agent-only and omitted when absent.

        A shell backend *rejects* a seed prompt (`seed prompts require an agent
        backend`), which is why this is optional rather than defaulted to an
        empty string: the daemon distinguishes "no seed" from "empty seed" only
        by the key being absent.
        """
        body: dict[str, Any] = {
            "project_id": project_id,
            "backend": backend,
            # An explicit cwd is how a caller spawns into a worktree of this
            # repository; containment admits it through `resolve_listed_cwd`,
            # which asks Git rather than trusting the path.
            "cwd": cwd or str(self.root),
            "name": name,
        }
        if seed_text is not None:
            body["seed_text"] = seed_text
        response = await self.client.post("/api/sessions", json=body)
        assert response.status == 201, await response.text()
        return dict(await response.json())

    async def session(self, sid: str) -> dict[str, Any] | None:
        response = await self.client.get(f"/api/sessions/{sid}")
        if response.status == 404:
            return None
        assert response.status == 200, await response.text()
        return dict(await response.json())

    async def end_session(self, sid: str) -> None:
        response = await self.client.delete(f"/api/sessions/{sid}")
        assert response.status == 200, await response.text()

    @asynccontextmanager
    async def attach_pty(
        self, sid: str, *, cols: int = 120, rows: int = 30
    ) -> AsyncIterator[PtyAttachment]:
        """Attach to a session's terminal the way the browser does, and read it.

        `claim_input` before `attach_ready` mirrors the real client: the daemon
        holds messages that race the handshake and replays them once the attach
        completes, so the claim is honoured either way and the ordering here is
        the one the browser actually produces.
        """
        ws = await self.client.ws_connect(f"/pty/{sid}")
        attachment = PtyAttachment(ws)
        reader = asyncio.create_task(attachment.read_forever(), name=f"pty-reader-{sid}")
        try:
            await ws.send_json({"type": "claim_input"})
            await ws.send_json({"type": "attach_ready", "cols": cols, "rows": rows})
            yield attachment
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await ws.close()

    @staticmethod
    def child_env(pid: int) -> dict[str, str]:
        """The live environment of a spawned agent process, read via psutil."""
        return psutil.Process(pid).environ()

    def token_for(self, pid: int) -> str:
        """Recover one session's MCP bearer token from its live process env."""
        return self.child_env(pid)["MUX_MCP_TOKEN"]

    async def rpc(
        self, token: str, method: str, params: dict[str, Any] | None = None, *, mid: int = 1
    ) -> dict[str, Any]:
        response = await self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status == 200, await response.text()
        return dict(await response.json())

    async def call_tool(
        self, token: str, name: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Drive one tool through the wire; return its parsed payload and error flag."""
        envelope = await self.rpc(token, "tools/call", {"name": name, "arguments": arguments})
        result = envelope["result"]
        payload = json.loads(result["content"][0]["text"])
        return payload, bool(result.get("isError"))


@asynccontextmanager
async def isolated_daemon(
    tmp_path: Path, **config_overrides: Any
) -> AsyncIterator[IsolatedDaemon]:
    """Bring up an isolated in-process daemon with a granted, git-scoped scratch Project."""
    port = free_port()
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(subprocess.run, ["git", "init", "-q"], cwd=root, check=True)
    swe_dir = root / ".swe-mux"
    swe_dir.mkdir(exist_ok=True)
    (swe_dir / "config.toml").write_text(_GRANT_CONFIG, encoding="utf-8")

    data_dir = tmp_path / ".mux"
    settings: dict[str, Any] = {
        "data_dir": data_dir,
        "port": port,
        "host": "127.0.0.1",
        "tailnet_enabled": False,
        "reconcile_external_history": False,
        # Pinned off, and now *against* the shipped default rather than with it.
        # Two reasons, neither of which is "the supervisor is risky". A `Config`
        # built in code has no `config_path`, which is what a supervisor is
        # keyed on, so this shape could not start one if it tried
        # (`SupervisorClient.connect_or_spawn` refuses); and the agent-bearing
        # live tiers share this harness, where a second process that outlives
        # the test is exactly what the isolation rules forbid. The supervised
        # path is exercised by `daemon_process`, which has a real config file
        # and a real entry point - see `test_live_daemon.py`.
        "pty_supervisor_enabled": False,
        "session_control_enabled": True,
        # Without this every shell spawn is refused before it reaches a
        # pseudoterminal; see `_scratch_shell_profile`.
        "shell_profiles": [_scratch_shell_profile()],
    }
    settings.update(config_overrides)
    config = Config(**settings)
    server = TestServer(create_app(config), host="127.0.0.1", port=port)
    client = TestClient(server)
    await client.start_server()
    # The daemon binds its listeners before it builds its runtime, so a started
    # server is a *reachable* daemon and not yet a usable one. Every route but
    # health and the static document answers 503 until this returns.
    await wait_runtime_ready(client.app)
    try:
        yield IsolatedDaemon(client, root, port, data_dir)
    finally:
        await client.close()


# ---------------------------------------------------------------- subprocess shape


#: How long the real entry point gets to bind, build its runtime, and answer
#: ready. Generous rather than tuned: a cold CI runner builds thirteen stores and
#: runs an integrity probe against each, and this bound exists to stop a hung
#: start hanging the suite, not to measure anything.
DAEMON_READY_TIMEOUT_SECONDS = 180.0

#: How long it gets to unwind after `/api/desktop/shutdown` is accepted. The
#: teardown closes the listener first and only then drains ~30 services and
#: thirteen store connections, which is the window `__main__` already sizes its
#: successor gate against.
DAEMON_EXIT_TIMEOUT_SECONDS = 120.0


def muxd_executable() -> Path | None:
    """The real `muxd` console script for the interpreter running the tests.

    The scripts directory beside `sys.executable` is checked before `PATH`, so a
    checkout's own virtualenv wins over an unrelated `muxd` an operator happens
    to have installed globally - running the wrong one would prove nothing about
    this tree.
    """
    scripts = Path(sys.executable).parent
    for candidate in (scripts / "muxd.exe", scripts / "muxd", scripts / "Scripts" / "muxd.exe"):
        if candidate.is_file():
            return candidate
    found = shutil.which("muxd")
    return Path(found) if found else None


async def _drain(stream: asyncio.StreamReader | None, sink: bytearray) -> None:
    """Read one child pipe to EOF into `sink`.

    Draining continuously rather than at the end is not tidiness. `muxd`'s root
    logger keeps a console handler, so a start writes a few KB to stderr before
    it is ready; an undrained pipe buffer fills and the daemon blocks inside a
    log call, which reads as a daemon that hung during startup. Reading to EOF is
    also what disconnects the pipe, and a `BaseSubprocessTransport` whose pipes
    never disconnected raises from its finalizer against some other test.
    """
    if stream is None:
        return
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        sink.extend(chunk)


@dataclass
class DaemonProcess:
    """A live `muxd` subprocess, its ephemeral base URL, and its data directory."""

    process: asyncio.subprocess.Process
    base_url: str
    port: int
    data_dir: Path
    control_token: str
    session: ClientSession
    stdout: bytearray
    stderr: bytearray
    drains: tuple[asyncio.Task[None], ...]

    @property
    def pid(self) -> int:
        return self.process.pid

    def diagnostics(self, *, lines: int = 40) -> str:
        """What the daemon said, for a failure message that can be acted on.

        A live tier that discards its subject's output can only ever report that
        something did not happen. The console stream and `daemon.log` answer
        different questions - the first catches a failure before logging is
        configured, the second carries the structured startup phases - so both
        are here.
        """
        log = self.data_dir / "daemon.log"
        tail = ""
        if log.is_file():
            logged = log.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(logged[-lines:])
        return (
            f"--- muxd stdout ---\n{self.stdout.decode('utf-8', 'replace')}\n"
            f"--- muxd stderr (tail) ---\n"
            + "\n".join(self.stderr.decode("utf-8", "replace").splitlines()[-lines:])
            + f"\n--- daemon.log (tail) ---\n{tail}"
        )

    def descendants(self) -> list[psutil.Process]:
        """Every process still alive under the daemon, ignoring races with exit."""
        try:
            return psutil.Process(self.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            return []

    async def health(self) -> tuple[int, dict[str, Any]]:
        async with self.session.get(f"{self.base_url}/api/health") as response:
            return response.status, dict(await response.json())

    async def wait_ready(self, seconds: float = DAEMON_READY_TIMEOUT_SECONDS) -> dict[str, Any]:
        """Poll health until the runtime is built, or say which phase it died in.

        Polls rather than sleeping a fixed window for the reason `tests/support/
        settle.py` gives, and reports the *last phase seen* on timeout: "the
        daemon never became ready" is not an answer anyone can act on, and the
        phase in flight is the whole reason `/api/health` answers during startup.
        """
        last: dict[str, Any] = {}
        try:
            async with asyncio.timeout(seconds):
                while True:
                    if self.process.returncode is not None:
                        raise AssertionError(
                            f"muxd exited with {self.process.returncode} before becoming ready"
                        )
                    try:
                        status, payload = await self.health()
                    except OSError:
                        # The listener is not up yet. A refused connection during
                        # the bind window is expected, not a failure.
                        await asyncio.sleep(0.05)
                        continue
                    last = payload
                    if status == 200 and payload.get("status") == "ready":
                        return payload
                    if payload.get("status") == "failed":
                        raise AssertionError(f"muxd runtime build failed: {payload}")
                    await asyncio.sleep(0.05)
        except TimeoutError:
            raise AssertionError(
                f"muxd never became ready in {seconds:.0f}s; last health was {last}\n"
                f"{self.diagnostics()}"
            ) from None

    async def wait_for_exit(self, seconds: float = DAEMON_EXIT_TIMEOUT_SECONDS) -> int:
        """Wait for the process to end and both its pipes to reach EOF."""
        async with asyncio.timeout(seconds):
            returncode = await self.process.wait()
            await asyncio.gather(*self.drains)
        return int(returncode)

    async def request_shutdown(self, mode: str = "quit") -> int:
        """Ask the daemon to stop through the one route that has that authority.

        `mode` is the shutdown *intent*: "quit" reaps everything the supervisor
        holds and stops it too, "restart" detaches and leaves supervised sessions
        running for the next daemon. The two are the whole difference between
        "swe-mux is closed" and "swe-mux is reloading", and nothing else in the
        suite exercises the second over a real process.
        """
        async with self.session.post(
            f"{self.base_url}/api/desktop/shutdown",
            json={"mode": mode},
            headers={"Authorization": f"Bearer {self.control_token}"},
        ) as response:
            await response.read()
            return response.status

    # ---- HTTP, for the session half of the supervised smoke ------------------
    #
    # The in-process harness reaches into the app object for these; a subprocess
    # daemon has no app object, so everything goes through the routes a browser
    # uses. That is a feature here: it is the same wire a real client is on.

    async def _json(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        async with self.session.request(
            method, f"{self.base_url}{path}", json=body
        ) as response:
            text = await response.text()
            if not text:
                return response.status, None
            return response.status, json.loads(text)

    async def register_project(self, root: Path, name: str = "live") -> str:
        status, payload = await self._json(
            "POST", "/api/projects", {"name": name, "root": str(root), "create_missing": False}
        )
        assert status in (200, 201), f"{status}: {payload}\n{self.diagnostics()}"
        return str(payload["id"])

    async def spawn_shell(self, project_id: str, root: Path, name: str = "live") -> str:
        status, payload = await self._json(
            "POST",
            "/api/sessions",
            {"project_id": project_id, "backend": "shell", "cwd": str(root), "name": name},
        )
        assert status == 201, f"{status}: {payload}\n{self.diagnostics()}"
        return str(payload["id"])

    async def session_ids(self) -> list[str]:
        status, payload = await self._json("GET", "/api/sessions")
        assert status == 200, f"{status}: {payload}\n{self.diagnostics()}"
        return [str(row["id"]) for row in payload]

    @asynccontextmanager
    async def attach_pty(
        self, sid: str, *, cols: int = 120, rows: int = 30
    ) -> AsyncIterator[PtyAttachment]:
        """Attach to a session's terminal over the real websocket route.

        The close comes *before* the reader is cancelled, which is the opposite
        of the in-process shape and is deliberate: this websocket is a real
        connection on a real `TCPConnector`, and `close()` waits for the peer's
        close frame - which only the reader can consume. Cancel first and the
        close times out, leaving the connection in the connector for a finalizer
        to complain about against some other test.
        """
        ws = await self.session.ws_connect(f"{self.base_url}/pty/{sid}")
        attachment = PtyAttachment(ws)
        reader = asyncio.create_task(attachment.read_forever(), name=f"pty-reader-{sid}")
        try:
            await ws.send_json({"type": "claim_input"})
            await ws.send_json({"type": "attach_ready", "cols": cols, "rows": rows})
            yield attachment
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


def _daemon_config_toml(port: int, *, supervisor: bool | None) -> str:
    """The smallest config that keeps this daemon off every shared resource.

    `tailnet_enabled` and `wsl_bridge_enabled` off so it binds exactly one
    loopback listener; `reconcile_external_history` off so it does not walk the
    operator's transcripts. `data_dir` is not a key - it is the config file's own
    parent, which is what `--config` therefore selects.

    `pty_supervisor_enabled` is **not written at all** when `supervisor` is None,
    and that omission is the point. It used to be pinned `false` here, which was
    correct while the shipped default was off and became a lie the day it moved:
    a tier whose whole subject is "does the real entry point work on this host"
    was silently exercising a configuration no install has. A test that wants the
    unsupervised fallback asks for it explicitly.
    """
    lines = [
        f"port = {port}",
        'host = "127.0.0.1"',
        "tailnet_enabled = false",
        "wsl_bridge_enabled = false",
        "reconcile_external_history = false",
    ]
    if supervisor is not None:
        lines.append(f"pty_supervisor_enabled = {str(supervisor).lower()}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class DaemonSpec:
    """Everything two successive daemons must agree on to be the same install.

    A supervisor is keyed on its config **path** (its single-instance mutex, its
    discovery record, and the identity check that guards terminating it), and it
    hands its sessions back through the data directory beside that file. So a
    successor daemon is not "another daemon" - it is one launched from this exact
    spec, on the port the config names, which is free again because its
    predecessor exited.
    """

    data_dir: Path
    config_path: Path
    port: int
    home: Path


def daemon_spec(tmp_path: Path, *, supervisor: bool | None = None) -> DaemonSpec:
    """Prepare an isolated data directory and config file for `daemon_process`.

    `supervisor=None` writes no `pty_supervisor_enabled` key, so the daemon runs
    the shipped default; `True`/`False` pin it.
    """
    data_dir = tmp_path / "muxd-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    port = free_port()
    config_path = data_dir / "config.toml"
    config_path.write_text(
        _daemon_config_toml(port, supervisor=supervisor), encoding="utf-8", newline="\n"
    )
    # A separate process resolves `Path.home()` for itself, and several subsystems
    # read a real user profile through it. Re-homing makes "never touches the
    # operator's files" a property of the harness rather than of the code under
    # test - and it has to be part of the spec rather than of one launch, because
    # a successor that re-homed somewhere else would not be the same install.
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return DaemonSpec(data_dir, config_path, port, home)


def supervisor_discovery(data_dir: Path) -> dict[str, Any] | None:
    """The supervisor's own record of itself, or None when it wrote none."""
    path = data_dir / "supervisor.json"
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


async def reap_any_supervisor(data_dir: Path) -> bool:
    """Stop the supervisor keyed on `data_dir`, reaping every session it holds.

    This is `muxd --shutdown` for one test: the same `kill_server` the CLI calls,
    which reaps through the protocol when it can and falls back to an
    identity-checked terminate when it cannot. The identity check is what makes
    this safe to call from a test at all - it refuses any pid whose command line
    does not name *this* config path, so it can never reach the operator's own
    supervisor on `~/.mux`.
    """
    from swe_mux.supervisor_client import kill_server

    config = Config(data_dir=data_dir, config_path=data_dir / "config.toml")
    return await kill_server(config)


@asynccontextmanager
async def supervisor_reaped(data_dir: Path) -> AsyncIterator[None]:
    """Guarantee no supervisor keyed on `data_dir` outlives this block.

    A supervisor is *designed* to outlive the daemon that spawned it, so the
    usual "reap every subprocess in the test that started it" rule cannot be
    discharged by the daemon's own context manager - a test that deliberately
    hands sessions to a successor needs the supervisor alive in between. This
    wraps every such test instead, so the guarantee holds on the failure path
    too, which is the path that would otherwise leave a supervisor and a shell
    running on a CI runner for `LINGER_IDLE_EXIT_SECONDS`.
    """
    try:
        yield
    finally:
        await reap_any_supervisor(data_dir)


async def _port_is_free(port: int, *, seconds: float = 30.0) -> None:
    """Wait until nothing answers on `port`, so a successor can bind it.

    The predecessor's listener is closed before its teardown finishes, but the
    two are not the same instant, and a successor that loses that race exits
    during bind rather than reporting anything a reader can act on.
    """
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1.0
            )
        except (OSError, TimeoutError):
            return
        writer.close()
        with contextlib.suppress(OSError, ConnectionError):
            await writer.wait_closed()
        await asyncio.sleep(0.1)
    raise AssertionError(f"port {port} never stopped answering within {seconds:.0f}s")


@asynccontextmanager
async def daemon_process(
    where: Path | DaemonSpec, *, env_overrides: Mapping[str, str] | None = None
) -> AsyncIterator[DaemonProcess]:
    """Run the real `muxd` entry point as a subprocess, and reap it here.

    Every exit path ends with the process reaped *and* both pipes read to EOF. An
    asyncio subprocess whose pipes have not all disconnected when the loop closes
    leaves its `BaseSubprocessTransport` unclosed, and `__del__` then calls into
    the dead loop and raises `RuntimeError: Event loop is closed` from a
    finalizer - attributed to whatever test was running when the collector got to
    it, which is never this one. Killing without draining has the same effect, so
    the kill path drains too.
    """
    executable = muxd_executable()
    assert executable is not None, (
        "the `muxd` console script is not installed for this interpreter; "
        "run `uv sync` in this checkout"
    )
    spec = daemon_spec(where) if isinstance(where, Path) else where
    data_dir, config_path, port, home = spec.data_dir, spec.config_path, spec.port, spec.home
    # A successor launched from a spec its predecessor already used binds the
    # port that predecessor just released.
    await _port_is_free(port)
    control_token = "live-daemon-control-token"
    env = {
        **os.environ,
        "SWE_MUX_DESKTOP_CONTROL_TOKEN": control_token,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        **dict(env_overrides or {}),
    }
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "--config",
        str(config_path),
        "--local-only",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = bytearray(), bytearray()
    # `force_close` so nothing is pooled. The health poller reconnects many times
    # against a daemon that is starting and then, at the end, against one that is
    # shutting its listener down; a keep-alive connection left in the pool when
    # that happens is what produced an intermittent "Unclosed connector" from
    # `TCPConnector.__del__` - a finalizer complaint, and therefore one that
    # surfaces against whichever test the collector happened to interrupt.
    drains = (
        asyncio.create_task(_drain(process.stdout, stdout), name="muxd-stdout"),
        asyncio.create_task(_drain(process.stderr, stderr), name="muxd-stderr"),
    )
    session = ClientSession(connector=TCPConnector(force_close=True))
    handle = DaemonProcess(
        process,
        f"http://127.0.0.1:{port}",
        port,
        data_dir,
        control_token,
        session,
        stdout,
        stderr,
        drains,
    )
    try:
        yield handle
    finally:
        await session.close()
        # aiohttp closes its transports on the next loop turn; yielding once here
        # is what keeps that from happening after the loop is gone.
        await asyncio.sleep(0)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        with contextlib.suppress(TimeoutError):
            await handle.wait_for_exit()
        for drain in drains:
            drain.cancel()
        await asyncio.gather(*drains, return_exceptions=True)
