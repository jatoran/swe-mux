"""Reproducible fleet load against an isolated swe-mux daemon, with the numbers a user feels.

The 2026-10-16 freezes (26 loop stalls of 3-9 s with about 36 agents) cannot be
replayed on the live fleet, and every fix in `development/DAEMON_LOAD_ISOLATION.md`
needs a before and an after. This harness is that yardstick.

It starts its own daemon from this checkout on an OS-allocated port with a data
directory and a re-homed `HOME` under a scratch directory, so it never touches
port 8765, `~/.mux`, or the operator's profile - the same isolation rules the
`live_daemon` test tier relies on (`tests/support/live_daemon.py`). It then:

- spawns N scratch shell sessions and runs `tools/load_session_generator.py` in
  each, which produces terminal output, hook and OTLP traffic through the
  session's own ingress credentials, and child-process churn;
- attaches one terminal client per session that acknowledges output the way
  xterm does, so the daemon's fanout and flow control carry real work;
- keeps one quiet probe session and times keystroke-to-echo on it continuously,
  the measurement `tools/pty_latency_bench.py` makes against an idle daemon;
- polls `/api/sessions` and `/api/health` the way open windows do, and times them;
- reads loop lag, stall records and per-loop cost before and after the window.

    uv run python tools/fleet_load_harness.py --sessions 36 --duration 120
    uv run python tools/fleet_load_harness.py --sessions 36 --profile 45 --json after.json
    uv run python tools/fleet_load_harness.py --set gil_switch_interval_ms=1

Every run ends by quitting its daemon through the authenticated shutdown route,
which reaps the scratch sessions and the scratch supervisor; a daemon that will
not quit is killed and its supervisor reaped by the identity-checked
`kill_server`, which refuses any process not keyed on this run's config file.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import shlex
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

TOOLS = Path(__file__).resolve().parent
GENERATOR = TOOLS / "load_session_generator.py"
CONTROL_TOKEN = "fleet-load-harness-control-token"
KEYSTROKE = "x"
CLEAR_LINE = "\x15"
LOOP_STALL = re.compile(r"^(\S+ \S+) WARNING swe_mux\.server: loop_stall duration_s=([\d.]+)")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def percentiles(values: list[float], scale: float = 1000.0) -> dict[str, Any]:
    """Distribution summary in milliseconds (scale 1000 from seconds)."""
    if not values:
        return {"count": 0}
    ordered = sorted(value * scale for value in values)

    def at(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 2)

    return {
        "count": len(ordered),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": round(ordered[-1], 2),
        "mean": round(statistics.fmean(ordered), 2),
    }


@dataclass
class Options:
    sessions: int
    duration: float
    warmup: float
    mode: str
    output_rate: float
    hook_rate: float
    otlp_rate: float
    spawn_interval: float
    probe_interval: float
    profile: float
    workdir: Path
    settings: dict[str, str]
    git: bool
    burn: int
    stall_profiler: str = ""
    stall_threshold: float = 0.0


@dataclass
class Client:
    """One attached terminal client acknowledging output like xterm."""

    sid: str
    ws: aiohttp.ClientWebSocketResponse
    received: int = 0
    last_output: float = field(default_factory=time.monotonic)
    output_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        async for message in self.ws:
            if message.type == aiohttp.WSMsgType.BINARY:
                size = len(message.data)
                self.received += size
                self.last_output = time.monotonic()
                self.output_event.set()
                with contextlib.suppress(ConnectionResetError, RuntimeError):
                    await self.ws.send_json({"type": "output_ack", "bytes": size})
            elif message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                return

    async def quiet(self, seconds: float, cap: float) -> None:
        deadline = time.monotonic() + cap
        while time.monotonic() < deadline:
            if time.monotonic() - self.last_output >= seconds:
                return
            await asyncio.sleep(0.1)

    async def send(self, data: str) -> None:
        await self.ws.send_json({"type": "input", "data": data, "kind": "user", "broadcast": False})


class Harness:
    def __init__(self, options: Options) -> None:
        self.options = options
        self.workdir = options.workdir
        self.data_dir = self.workdir / "data"
        self.home = self.workdir / "home"
        self.project_root = self.workdir / "project"
        self.stats_dir = self.workdir / "stats"
        self.config_path = self.data_dir / "config.toml"
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[bytes] | None = None
        self.http: aiohttp.ClientSession | None = None
        self.clients: list[Client] = []
        self.probe: Client | None = None

    # ---- daemon lifecycle ---------------------------------------------------

    def _write_config(self) -> None:
        for path in (self.data_dir, self.home, self.project_root, self.stats_dir):
            path.mkdir(parents=True, exist_ok=True)
        lines = [
            f"port = {self.port}",
            'host = "127.0.0.1"',
            "tailnet_enabled = false",
            "wsl_bridge_enabled = false",
            "reconcile_external_history = false",
        ]
        for key, value in self.options.settings.items():
            lines.append(f"{key} = {value}")
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def _init_project(self) -> None:
        (self.project_root / "README.md").write_text("load harness scratch project\n")
        if not self.options.git:
            return
        identity = ["-c", "user.name=load", "-c", "user.email=load@localhost"]
        subprocess.run(["git", "init", "-q", str(self.project_root)], check=True)
        subprocess.run(["git", "-C", str(self.project_root), *identity, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.project_root), *identity, "commit", "-qm", "seed"],
            check=True,
        )

    def start_daemon(self) -> None:
        self._write_config()
        self._init_project()
        env = {
            **os.environ,
            "SWE_MUX_DESKTOP_CONTROL_TOKEN": CONTROL_TOKEN,
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
        }
        # A launching agent session's own ingress variables must not leak into the
        # scratch daemon, or its sessions would report to the live fleet; and a
        # harness data-home override (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`) would point
        # the scratch daemon's observers at the operator's real conversations.
        for key in list(env):
            if key.startswith(("MUX_", "SWE_MUX_", "CLAUDE", "CODEX_")) and key != (
                "SWE_MUX_DESKTOP_CONTROL_TOKEN"
            ):
                env.pop(key)
        if self.options.stall_profiler:
            env["MUX_STALL_PROFILER"] = self.options.stall_profiler
        if self.options.stall_threshold:
            env["MUX_STALL_THRESHOLD_SECONDS"] = str(self.options.stall_threshold)
        log = (self.workdir / "daemon-console.log").open("wb")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "swe_mux",
                "--config",
                str(self.config_path),
                "--local-only",
                "--no-browser",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(self.data_dir),
        )

    async def wait_ready(self, seconds: float = 300.0) -> None:
        assert self.http is not None
        deadline = time.monotonic() + seconds
        last: Any = None
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise SystemExit(
                    f"daemon exited with {self.process.returncode}; see {self.workdir}"
                )
            try:
                async with self.http.get(f"{self.base}/api/health") as response:
                    last = await response.json()
                    if response.status == 200 and last.get("status") == "ready":
                        return
            except (aiohttp.ClientError, OSError, ValueError):
                pass
            await asyncio.sleep(0.25)
        raise SystemExit(f"daemon never became ready; last health {last}")

    def daemon_pid(self) -> int | None:
        """The interpreter serving the port, which is not the launcher on Windows venvs."""
        try:
            record = json.loads((self.data_dir / "daemon-heartbeat.json").read_text("utf-8"))
            return int(record["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def stop_daemon(self) -> None:
        if self.http is not None and self.process is not None and self.process.poll() is None:
            with contextlib.suppress(aiohttp.ClientError, OSError, TimeoutError):
                async with self.http.post(
                    f"{self.base}/api/desktop/shutdown",
                    json={"mode": "quit"},
                    headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    await response.read()
        if self.process is not None:
            try:
                await asyncio.to_thread(self.process.wait, 60)
            except subprocess.TimeoutExpired:
                self.process.kill()
                await asyncio.to_thread(self.process.wait, 30)
        # Whatever the daemon did, no scratch supervisor may outlive the run.
        from swe_mux.config import Config
        from swe_mux.supervisor_client import kill_server

        with contextlib.suppress(Exception):
            await kill_server(Config(data_dir=self.data_dir, config_path=self.config_path))

    # ---- sessions -----------------------------------------------------------

    async def _json(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        assert self.http is not None
        async with self.http.request(method, f"{self.base}{path}", json=body) as response:
            text = await response.text()
            return response.status, (json.loads(text) if text else None)

    async def register_project(self) -> str:
        status, payload = await self._json(
            "POST",
            "/api/projects",
            {"name": "load", "root": str(self.project_root), "create_missing": False},
        )
        if status not in (200, 201):
            raise SystemExit(f"project registration failed: {status} {payload}")
        return str(payload["id"])

    async def spawn_shell(self, project_id: str, name: str) -> str:
        status, payload = await self._json(
            "POST",
            "/api/sessions",
            {
                "project_id": project_id,
                "backend": "shell",
                "cwd": str(self.project_root),
                "name": name,
            },
        )
        if status != 201:
            raise SystemExit(f"spawn failed: {status} {payload}")
        return str(payload["id"])

    async def attach(self, sid: str, terminal_base: str) -> Client:
        assert self.http is not None
        ws = await self.http.ws_connect(f"{terminal_base}/pty/{sid}", max_msg_size=0)
        client = Client(sid, ws)
        client.task = asyncio.create_task(client.run(), name=f"client-{sid}")
        await ws.send_json(
            {
                "type": "attach_ready",
                "cols": 120,
                "rows": 30,
                "renderer": "dom",
                "hidden": False,
                "output_flow_control": True,
            }
        )
        await ws.send_json(
            {"type": "claim_input", "reason": "gesture", "device": "desktop", "focused": True}
        )
        return client

    def generator_command(self, index: int, stats: Path) -> str:
        options = self.options
        per_session = max(1, options.sessions)
        args = [
            "--duration",
            f"{options.warmup + options.duration:.1f}",
            "--stats",
            str(stats),
            "--mode",
            options.mode,
            "--output-rate",
            f"{options.output_rate:.0f}",
            "--hook-rate",
            f"{options.hook_rate / per_session:.5f}",
            "--otlp-rate",
            f"{options.otlp_rate / per_session:.5f}",
            "--spawn-interval",
            f"{options.spawn_interval:.2f}",
            "--seed",
            str(index),
        ]
        if sys.platform == "win32":
            quoted = " ".join(f'"{part}"' if " " in part or "\\" in part else part for part in args)
            return f'& "{sys.executable}" "{GENERATOR}" {quoted}\r'
        return shlex.join([sys.executable, str(GENERATOR), *args]) + "\n"

    # ---- measurement --------------------------------------------------------

    def start_burners(self) -> list[subprocess.Popen[bytes]]:
        """CPU-bound processes standing in for the builds and tools real agents run.

        The scratch sessions' own load is light on CPU next to a fleet compiling and
        testing, and a saturated host is part of what 2026-10-16 looked like. They
        run below normal priority, which is where the daemon puts session trees.
        """
        burners: list[subprocess.Popen[bytes]] = []
        flags = 0
        if sys.platform == "win32":
            flags = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
        for _ in range(self.options.burn):
            burners.append(
                subprocess.Popen(
                    [sys.executable, "-c", "while True: pass"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
            )
        return burners

    async def probe_loop(self, until: float, samples: list[float], misses: list[float]) -> None:
        probe = self.probe
        assert probe is not None
        while time.monotonic() < until:
            await asyncio.sleep(self.options.probe_interval)
            await probe.quiet(0.2, 2.0)
            probe.output_event.clear()
            started = time.perf_counter()
            await probe.send(KEYSTROKE)
            try:
                await asyncio.wait_for(probe.output_event.wait(), timeout=30)
                samples.append(time.perf_counter() - started)
            except TimeoutError:
                misses.append(time.perf_counter() - started)
            await probe.send(CLEAR_LINE)

    async def events_loop(self, until: float, counts: dict[str, int]) -> None:
        """One browser window's `/events` socket, reporting presence like the SPA.

        Without a browser subscriber the daemon's readiness watcher skips its pass
        entirely (`readiness_watch.py`), and the per-event fanout to browser sockets
        never runs - so a harness without this measures a daemon nobody has open.
        """
        assert self.http is not None
        ws = await self.http.ws_connect(f"{self.base}/events", max_msg_size=0)
        presence = {
            "type": "presence",
            "profile": "desktop",
            "visible": True,
            "focused": True,
            "interaction_age": 1.0,
        }
        await ws.send_json(presence)
        last_presence = time.monotonic()
        try:
            while time.monotonic() < until:
                if time.monotonic() - last_presence >= 30:
                    await ws.send_json(presence)
                    last_presence = time.monotonic()
                try:
                    message = await ws.receive(timeout=1.0)
                except TimeoutError:
                    continue
                if message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                }:
                    counts["closed"] = counts.get("closed", 0) + 1
                    return
                counts["frames"] = counts.get("frames", 0) + 1
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    async def rest_loop(self, until: float, path: str, interval: float, out: list[float]) -> None:
        assert self.http is not None
        while time.monotonic() < until:
            started = time.perf_counter()
            with contextlib.suppress(aiohttp.ClientError, OSError, TimeoutError):
                async with self.http.get(
                    f"{self.base}{path}", timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    await response.read()
            out.append(time.perf_counter() - started)
            await asyncio.sleep(interval)

    async def diagnostics(self) -> dict[str, Any]:
        status, payload = await self._json("GET", "/api/diagnostics/background")
        return payload if status == 200 and isinstance(payload, dict) else {}

    def stalls_between(self, started: float, ended: float) -> list[float]:
        log = self.data_dir / "daemon.log"
        durations: list[float] = []
        if not log.is_file():
            return durations
        start_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started))
        end_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended + 15))
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            match = LOOP_STALL.match(line)
            if match and start_text <= match.group(1)[:19] <= end_text:
                durations.append(float(match.group(2)))
        return durations

    async def profile(self) -> dict[str, Any]:
        pid = self.daemon_pid()
        if pid is None:
            return {"error": "daemon pid unknown"}
        sys.path.insert(0, str(TOOLS))
        import perf_snapshot

        return await asyncio.to_thread(
            perf_snapshot.profile,
            pid,
            self.options.profile,
            120,
            self.workdir,
            gil=True,
            threads=True,
        )

    # ---- the run ------------------------------------------------------------

    async def run(self) -> dict[str, Any]:
        options = self.options
        self.start_daemon()
        self.http = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0))
        try:
            await self.wait_ready()
            project_id = await self.register_project()
            terminal_base = self.base
            sids = [
                await self.spawn_shell(project_id, f"load-{i:02d}") for i in range(options.sessions)
            ]
            probe_sid = await self.spawn_shell(project_id, "probe")
            self.clients = [await self.attach(sid, terminal_base) for sid in sids]
            self.probe = await self.attach(probe_sid, terminal_base)
            await asyncio.gather(*(c.quiet(1.5, 60) for c in [*self.clients, self.probe]))
            for index, client in enumerate(self.clients):
                await client.send(
                    self.generator_command(index, self.stats_dir / f"{index:03d}.json")
                )
            # Warm-up: generators start, shells settle, the daemon reaches steady state.
            await asyncio.sleep(options.warmup)
            before = await self.diagnostics()
            window_start = time.time()
            until = time.monotonic() + options.duration
            echo: list[float] = []
            echo_misses: list[float] = []
            fleet_reads: list[float] = []
            health_reads: list[float] = []
            process_reads: list[float] = []
            event_counts: dict[str, int] = {}
            work = [
                self.probe_loop(until, echo, echo_misses),
                # The open window's cadences (`frontend/src/App.tsx`): the fleet
                # read, the health poll, and the process summary.
                self.rest_loop(until, "/api/sessions", 1.0, fleet_reads),
                self.rest_loop(until, "/api/health", 2.0, health_reads),
                self.rest_loop(until, "/api/processes?summary=1", 10.0, process_reads),
                self.events_loop(until, event_counts),
            ]
            profile_task = asyncio.create_task(self.profile()) if options.profile else None
            burners = self.start_burners()
            try:
                await asyncio.gather(*work)
            finally:
                for burner in burners:
                    burner.kill()
                for burner in burners:
                    await asyncio.to_thread(burner.wait)
            window_end = time.time()
            after = await self.diagnostics()
            profile_result = await profile_task if profile_task else None
            # Let every generator finish and write its stats.
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if len(list(self.stats_dir.glob("*.json"))) >= options.sessions:
                    break
                await asyncio.sleep(1)
            report = self.report(
                before,
                after,
                window_start,
                window_end,
                echo,
                echo_misses,
                fleet_reads,
                health_reads,
                profile_result,
            )
            report["process_read_ms"] = percentiles(process_reads)
            report["events_frames"] = event_counts
            return report
        finally:
            for client in [*self.clients, *([self.probe] if self.probe else [])]:
                with contextlib.suppress(Exception):
                    await client.ws.close()
                if client.task is not None:
                    client.task.cancel()
            await asyncio.gather(
                *(c.task for c in [*self.clients, *([self.probe] if self.probe else [])] if c.task),
                return_exceptions=True,
            )
            await self.stop_daemon()
            await self.http.close()

    def report(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        window_start: float,
        window_end: float,
        echo: list[float],
        echo_misses: list[float],
        fleet_reads: list[float],
        health_reads: list[float],
        profile_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        stalls = self.stalls_between(window_start, window_end)
        lag_before = before.get("loop_lag") or {}
        lag_after = after.get("loop_lag") or {}
        loops_before = {row["name"]: row for row in before.get("loops", [])}
        loop_cost = []
        for row in after.get("loops", []):
            prior = loops_before.get(row["name"], {})
            busy = float(row.get("busy_seconds") or 0) - float(prior.get("busy_seconds") or 0)
            iterations = int(row.get("iterations") or 0) - int(prior.get("iterations") or 0)
            if busy > 0:
                loop_cost.append(
                    {"name": row["name"], "busy_seconds": round(busy, 3), "iterations": iterations}
                )
        loop_cost.sort(key=lambda row: -row["busy_seconds"])
        hooks: list[float] = []
        otlp: list[float] = []
        statuses: dict[str, dict[str, int]] = {"hook": {}, "otlp": {}}
        output_bytes = 0
        generators = 0
        for path in sorted(self.stats_dir.glob("*.json")):
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(path.read_text("utf-8"))
                generators += 1
                hooks.extend(data.get("samples", {}).get("hook", []))
                otlp.extend(data.get("samples", {}).get("otlp", []))
                output_bytes += int(data.get("bytes_sent", {}).get("output", 0))
                for kind in ("hook", "otlp"):
                    for code, count in data.get("statuses", {}).get(kind, {}).items():
                        statuses[kind][code] = statuses[kind].get(code, 0) + count
        return {
            "options": {
                key: (str(value) if isinstance(value, Path) else value)
                for key, value in self.options.__dict__.items()
            },
            "window_seconds": round(window_end - window_start, 1),
            "generators_reported": generators,
            "output_bytes_per_second": round(
                output_bytes / max(1.0, self.options.duration + self.options.warmup)
            ),
            "echo_ms": percentiles(echo),
            "echo_timeouts": len(echo_misses),
            "fleet_read_ms": percentiles(fleet_reads),
            "health_read_ms": percentiles(health_reads),
            "hook_round_trip_ms": percentiles(hooks),
            "otlp_round_trip_ms": percentiles(otlp),
            "ingress_statuses": statuses,
            "loop_stalls": {
                "count": len(stalls),
                "total_seconds": round(sum(stalls), 2),
                "worst_seconds": round(max(stalls), 2) if stalls else 0.0,
            },
            "loop_lag": {
                "samples_over_100ms": int(lag_after.get("stalls") or 0)
                - int(lag_before.get("stalls") or 0),
                "observed": int(lag_after.get("observed") or 0)
                - int(lag_before.get("observed") or 0),
                "p99_seconds_at_end": lag_after.get("p99_seconds"),
                "max_seconds_at_end": lag_after.get("max_seconds"),
            },
            "loop_cost": loop_cost[:15],
            "stall_records": (after.get("stall_watchdog") or {}).get("recent", [])[:8],
            # Off-loop CPU by callable over the scratch daemon's life, which the window
            # dominates (`executor_accounting.py`).
            "executor_work": (after.get("executor_work") or {}).get("since_start", []),
            "screen_classification": _counter_delta(
                before.get("screen_classification") or {},
                after.get("screen_classification") or {},
                ("hits", "classifications", "classify_seconds"),
            ),
            "profile": profile_result,
            "workdir": str(self.workdir),
        }


def render(report: dict[str, Any]) -> None:
    print("=== fleet load harness ===")
    options = report["options"]
    print(
        f"  {options['sessions']} sessions, {options['mode']} @ "
        f"{options['output_rate']:.0f} B/s each, "
        f"hooks {options['hook_rate']}/s, otlp {options['otlp_rate']}/s, "
        f"window {report['window_seconds']}s, settings {options['settings'] or '{}'}"
    )
    print(f"  generators reported: {report['generators_reported']}/{options['sessions']}")

    def line(label: str, dist: dict[str, Any]) -> None:
        if not dist.get("count"):
            print(f"  {label:<18} no samples")
            return
        print(
            f"  {label:<18} n={dist['count']:<5} p50={dist['p50']:>9.1f}ms "
            f"p95={dist['p95']:>9.1f}ms p99={dist['p99']:>9.1f}ms max={dist['max']:>9.1f}ms"
        )

    line("keystroke echo", report["echo_ms"])
    print(f"  {'echo timeouts':<18} {report['echo_timeouts']}")
    line("GET /api/sessions", report["fleet_read_ms"])
    line("GET /api/health", report["health_read_ms"])
    line("GET /api/processes", report.get("process_read_ms") or {})
    print(f"  {'/events frames':<18} {report.get('events_frames')}")
    line("hook round trip", report["hook_round_trip_ms"])
    line("otlp round trip", report["otlp_round_trip_ms"])
    stalls = report["loop_stalls"]
    print(
        f"  loop stalls >={options.get('stall_threshold') or 3.0:g}s   "
        f"{stalls['count']} totalling {stalls['total_seconds']}s, "
        f"worst {stalls['worst_seconds']}s"
    )
    lag = report["loop_lag"]
    print(f"  lag samples >=100ms {lag['samples_over_100ms']}/{lag['observed']}")
    print(f"  ingress statuses   {report['ingress_statuses']}")
    print("  costliest loops in the window:")
    for row in report["loop_cost"][:8]:
        print(f"    {row['busy_seconds']:9.2f}s {row['iterations']:7d} it  {row['name']}")
    screens = report.get("screen_classification") or {}
    if screens:
        window = max(1.0, float(report["window_seconds"]))
        print(
            f"  screen verdicts    {screens.get('classifications', 0) / window:.1f} classified/s, "
            f"{screens.get('hits', 0) / window:.1f} cached/s, "
            f"{screens.get('classify_seconds', 0.0):.2f}s classifying"
        )
    if report.get("executor_work"):
        print("  costliest executor work (CPU since start):")
        for row in report["executor_work"][:10]:
            print(f"    {row['cpu_seconds']:9.2f}s {row['count']:7d} x  {row['label']}")
    for record in report.get("stall_records") or []:
        diagnosis = record.get("diagnosis") or {}
        holder = diagnosis.get("gil_holder") or {}
        top = ", ".join(
            f"{item['name']} {item['share']:.0%}"
            + (f" [{item['work']}]" if item.get("work") else "")
            for item in diagnosis.get("top_threads", [])[:3]
        )
        print(
            f"  stall {record.get('duration_seconds')}s {diagnosis.get('verdict')}: "
            f"loop {diagnosis.get('loop_cpu_share')}, others {diagnosis.get('others_cpu_cores')}, "
            f"host {diagnosis.get('host_cpu_share')}, ready {diagnosis.get('ready_queue_max')}, "
            f"gil {holder.get('name')} ({holder.get('source')}); top: {top or '-'}"
        )
    profile = report.get("profile")
    if profile and "error" not in profile:
        print(f"  profile: {profile.get('samples')} samples")
        for row in profile.get("top_threads", [])[:8]:
            print(f"    {row['share']:7.2%}  {row['thread']}")
        for row in profile.get("top_leaves", [])[:8]:
            print(f"    {row['share']:7.2%}  {row['frame']}")
    elif profile:
        print(f"  profile unavailable: {profile['error']}")
    print(f"  artifacts: {report['workdir']}")


def _counter_delta(
    before: dict[str, Any], after: dict[str, Any], keys: tuple[str, ...]
) -> dict[str, float]:
    """Monotonic counters over the measurement window; empty when the daemon lacks them."""
    if not after:
        return {}
    return {
        key: round(float(after.get(key) or 0) - float(before.get(key) or 0), 3) for key in keys
    }


def parse_settings(values: list[str]) -> dict[str, str]:
    settings: dict[str, str] = {}
    for value in values:
        key, separator, raw = value.partition("=")
        if not separator or not key.strip():
            raise SystemExit(f"--set expects key=value (TOML value), got {value!r}")
        settings[key.strip()] = raw.strip()
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sessions", type=int, default=36)
    parser.add_argument("--duration", type=float, default=120.0, help="measurement window seconds")
    parser.add_argument("--warmup", type=float, default=20.0)
    parser.add_argument("--mode", choices=("redraw", "scroll"), default="scroll")
    parser.add_argument("--output-rate", type=float, default=20_000, help="bytes/s per session")
    parser.add_argument(
        "--hook-rate", type=float, default=3.0, help="hook POSTs/s across the fleet"
    )
    parser.add_argument(
        "--otlp-rate", type=float, default=1.5, help="OTLP posts/s across the fleet"
    )
    parser.add_argument(
        "--spawn-interval", type=float, default=5.0, help="child process every N s per session"
    )
    parser.add_argument("--probe-interval", type=float, default=1.0)
    parser.add_argument(
        "--profile", type=float, default=0.0, help="py-spy --gil --threads for N seconds"
    )
    parser.add_argument(
        "--set", action="append", default=[], metavar="KEY=TOML", help="extra config.toml line"
    )
    parser.add_argument(
        "--no-git", action="store_true", help="do not make the scratch project a git repo"
    )
    parser.add_argument(
        "--burn", type=int, default=0, help="CPU-bound below-normal processes during the window"
    )
    parser.add_argument(
        "--stall-profiler",
        default="",
        metavar="PATH",
        help="py-spy executable the scratch daemon dumps itself with at a stall's onset",
    )
    parser.add_argument(
        "--stall-threshold",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="explain stalls from this long (MUX_STALL_THRESHOLD_SECONDS; default 3)",
    )
    parser.add_argument(
        "--workdir", type=Path, help="artifacts directory (default: a new temp dir, always kept)"
    )
    parser.add_argument("--json", type=Path, help="write the report here")
    args = parser.parse_args()

    # The harness times milliseconds; on Windows its own waits would otherwise
    # round to the 15.6 ms timer tick and every latency would read as that.
    from swe_mux.timer_resolution import raise_timer_resolution

    raise_timer_resolution()
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="swe-mux-load-"))
    workdir.mkdir(parents=True, exist_ok=True)
    options = Options(
        sessions=args.sessions,
        duration=args.duration,
        warmup=args.warmup,
        mode=args.mode,
        output_rate=args.output_rate,
        hook_rate=args.hook_rate,
        otlp_rate=args.otlp_rate,
        spawn_interval=args.spawn_interval,
        probe_interval=args.probe_interval,
        profile=args.profile,
        workdir=workdir,
        settings=parse_settings(args.set),
        git=not args.no_git,
        burn=args.burn,
        stall_profiler=args.stall_profiler,
        stall_threshold=args.stall_threshold,
    )
    report = asyncio.run(Harness(options).run())
    render(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
