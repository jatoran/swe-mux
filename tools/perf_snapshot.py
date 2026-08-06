"""One-command performance snapshot of a running swe-mux daemon.

Everything `development/PERFORMANCE_RUNBOOK.md` prescribes for steps 1-4, in the order
it prescribes them, so an investigation starts from evidence instead of from a guess.
The whole reason this exists: swe-mux was optimized once by inspection, carefully, and a
defect costing 45% of the daemon's CPU survived inside the file that audit had just
rewritten. It survived because looking is not measuring.

    uv run python tools/perf_snapshot.py                 # cheap: loops, lag, process cost
    uv run python tools/perf_snapshot.py --profile 45    # also sample the daemon with py-spy
    uv run python tools/perf_snapshot.py --json out.json # machine-readable, for before/after

Read-only. It never writes to the daemon and never restarts anything.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8765
# Long enough for a CPU rate to mean something rather than catching one tick.
DEFAULT_SAMPLE_SECONDS = 20.0
# Loops quieter than this are noise in a report meant to be read at a glance.
BUSY_SHARE_FLOOR = 0.001


def _api(port: int, path: str) -> Any:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as response:
        return json.load(response)


def daemon_pid(port: int) -> int | None:
    """The pid actually serving the port.

    By socket, never by name: the tray shell is also `swe-mux.exe`, and profiling it
    produces a confidently wrong answer rather than an obviously empty one.
    """
    try:
        import psutil
    except ImportError:
        return None
    for connection in psutil.net_connections(kind="tcp"):
        local = connection.laddr
        if local and local.port == port and connection.status == psutil.CONN_LISTEN:
            return connection.pid
    return None


def process_costs(seconds: float) -> dict[str, Any]:
    """CPU rate and memory for the swe-mux processes, sampled over a window."""
    try:
        import psutil
    except ImportError:
        return {"error": "psutil unavailable"}
    names = ("swe-mux", "swe-mux-supervisor", "msedgewebview2")
    watched = []
    for process in psutil.process_iter(["name"]):
        name = (process.info["name"] or "").lower().removesuffix(".exe")
        if name in names:
            try:
                process.cpu_percent(None)
                watched.append((name, process))
            except psutil.Error:
                continue
    time.sleep(seconds)
    groups: dict[str, dict[str, float]] = {}
    for name, process in watched:
        try:
            cpu = process.cpu_percent(None)
            rss = process.memory_info().rss / 1048576
        except psutil.Error:
            continue
        entry = groups.setdefault(name, {"cpu_percent": 0.0, "rss_mb": 0.0, "processes": 0})
        entry["cpu_percent"] += cpu
        entry["rss_mb"] += rss
        entry["processes"] += 1
    return {
        "window_seconds": seconds,
        "logical_cores": psutil.cpu_count(),
        "groups": {
            name: {key: round(value, 2) for key, value in entry.items()}
            for name, entry in groups.items()
        },
    }


def profile(pid: int, seconds: float, rate: int, out_dir: Path) -> dict[str, Any]:
    """Sample the daemon with py-spy and attribute the samples.

    `--nonblocking` is not optional against a live daemon: without it py-spy pauses the
    process to read its stacks, which is a stall inflicted on every attached terminal.
    It costs some dropped samples, which does not matter for proportions.
    """
    runner = (
        ["uvx", "py-spy"] if shutil.which("uvx") else ["py-spy"] if shutil.which("py-spy") else None
    )
    if runner is None:
        return {"error": "py-spy not available (install uv, or pip install py-spy)"}
    raw = out_dir / "perf-profile.txt"
    command = [
        *runner, "record", "--pid", str(pid), "--duration", str(int(seconds)),
        "--rate", str(rate), "--nonblocking", "--format", "raw", "--output", str(raw),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0 or not raw.is_file():
        return {"error": (completed.stderr or completed.stdout or "py-spy failed").strip()[:400]}
    return {**summarize_profile(raw), "raw": str(raw)}


def summarize_profile(raw: Path) -> dict[str, Any]:
    """Collapse py-spy raw stacks into leaf and subsystem attributions.

    A sampled profile shows where *threads* are, not only where CPU is: a thread parked
    in a blocking read attributes samples to that frame while burning nothing. Cross-check
    anything that looks expensive against the process CPU above before believing it.
    """
    leaves: Counter[str] = Counter()
    modules: Counter[str] = Counter()
    total = 0
    idle = 0
    for line in raw.read_text(encoding="utf-8", errors="replace").splitlines():
        stack, _, count = line.rpartition(" ")
        if not stack or not count.strip().isdigit():
            continue
        samples = int(count)
        total += samples
        frames = stack.split(";")
        leaves[frames[-1]] += samples
        if "_poll (windows_events" in stack or "select (windows_events" in stack:
            idle += samples
        for frame in frames:
            _name, _, rest = frame.partition(" (")
            module = rest.partition(".py:")[0]
            if module and module not in _STDLIB_FRAMES:
                modules[module] += samples
                break
    if not total:
        return {"error": "no samples parsed"}
    return {
        "samples": total,
        "idle_share": round(idle / total, 4),
        "top_leaves": [
            {"frame": name, "share": round(n / total, 4)} for name, n in leaves.most_common(12)
        ],
        "top_modules": [
            {"module": f"{name}.py", "share": round(n / total, 4)}
            for name, n in modules.most_common(10)
        ],
    }


_STDLIB_FRAMES = {
    "base_events", "windows_events", "runners", "events", "tasks", "threading", "thread",
    "asyncio", "selectors", "queues",
}


def render(report: dict[str, Any]) -> None:
    print("=== swe-mux performance snapshot ===\n")

    processes = report.get("processes", {})
    if groups := processes.get("groups"):
        cores = processes.get("logical_cores") or 1
        print(f"process cost (over {processes['window_seconds']:.0f}s, {cores} cores)")
        for name, entry in sorted(groups.items(), key=lambda item: -item[1]["cpu_percent"]):
            print(
                f"  {entry['cpu_percent']:7.2f}% of a core  {entry['rss_mb']:8.1f} MB  "
                f"{int(entry['processes'])} proc  {name}"
            )
        print()

    if lag := report.get("loop_lag"):
        if lag.get("samples"):
            print(
                f"event-loop lag   p50={lag['p50_seconds'] * 1000:6.2f}ms  "
                f"p95={lag['p95_seconds'] * 1000:6.2f}ms  p99={lag['p99_seconds'] * 1000:6.2f}ms  "
                f"max={lag['max_seconds'] * 1000:7.1f}ms"
            )
            print(
                f"                 worst since boot {lag['worst_seconds'] * 1000:.1f}ms   "
                f"stalls {lag['stalls']}/{lag['observed']} "
                f"(>= {lag['stall_threshold_seconds'] * 1000:.0f}ms)"
            )
            quantization = lag.get("timer_quantization_seconds")
            if quantization and lag.get("quantization_bound"):
                print(
                    f"                 every sample is under the {quantization * 1000:.1f}ms OS "
                    "timer tick: nothing is blocking the loop"
                )
            elif quantization:
                print(
                    f"                 percentiles below {quantization * 1000:.1f}ms are the OS "
                    "timer tick, not congestion; read max/worst/stalls"
                )
            print()

    if loops := report.get("costliest"):
        print("costliest loops (busy_share = wall time in iteration bodies, awaits included)")
        print(f"  {'share':>8} {'busy_s':>9} {'iters':>7} {'p95':>9} {'worst':>9}  loop")
        for row in loops:
            if (row.get("busy_share") or 0) < BUSY_SHARE_FLOOR:
                continue
            print(
                f"  {row['busy_share']:8.5f} {row['busy_seconds']:9.2f} {row['iterations']:7d} "
                f"{(row.get('p95_seconds') or 0) * 1000:8.1f}ms "
                f"{(row.get('slowest_seconds') or 0) * 1000:8.1f}ms  {row['name']}"
            )
        print()

    if degraded := report.get("degraded"):
        print(f"DEGRADED LOOPS: {', '.join(degraded)}\n")

    prof = report.get("profile")
    if prof and "error" not in prof:
        print(f"profile ({prof['samples']} samples, {prof['idle_share']:.1%} idle)")
        for row in prof["top_modules"]:
            print(f"  {row['share']:7.2%}  {row['module']}")
        print()
        print("  top frames:")
        for row in prof["top_leaves"][:8]:
            print(f"    {row['share']:7.2%}  {row['frame']}")
        print()
    elif prof:
        print(f"profile unavailable: {prof['error']}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--sample", type=float, default=DEFAULT_SAMPLE_SECONDS, help="CPU sampling window"
    )
    parser.add_argument(
        "--profile", type=float, default=0, help="also record a py-spy profile for N seconds"
    )
    parser.add_argument("--rate", type=int, default=120, help="py-spy sampling rate")
    parser.add_argument("--json", type=Path, help="write the full report here")
    args = parser.parse_args()

    try:
        diagnostics = _api(args.port, "/api/diagnostics/background")
    except (urllib.error.URLError, OSError) as exc:
        print(f"no daemon answering on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "captured_at": time.time(),
        "loop_lag": diagnostics.get("loop_lag"),
        "costliest": diagnostics.get("costliest"),
        "degraded": diagnostics.get("degraded"),
        "total_busy_seconds": diagnostics.get("total_busy_seconds"),
        "loops": diagnostics.get("loops"),
    }
    report["processes"] = process_costs(args.sample)

    if args.profile:
        pid = daemon_pid(args.port)
        if pid is None:
            report["profile"] = {"error": "could not resolve the daemon pid from its port"}
        else:
            report["profile"] = profile(
                pid, args.profile, args.rate, args.json.parent if args.json else Path.cwd()
            )

    render(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
