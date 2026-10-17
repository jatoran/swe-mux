"""The load one agent session puts on swe-mux, generated from inside a session.

Run by `tools/fleet_load_harness.py` in each scratch shell session it spawns. It
lives inside the session on purpose: the hook and telemetry traffic it posts uses
the session's own `MUX_HOOK_URL`/`MUX_HOOK_SECRET`, exactly as a real CLI's hook
client does, and the child processes it starts land in the session's process tree,
which is what the daemon's process inspector walks.

Four independent sources, each on its own thread so one stalling does not hide
another:

- terminal output at a set byte rate, in one of two shapes agents produce: an
  alternate-screen TUI repainting whole frames (`redraw`) or a CLI writing real
  lines into scrollback (`scroll`, how mux runs Codex);
- `PostToolUse` hook POSTs, with body sizes drawn around the 2026-10-16 fleet's
  measured 50 KiB mean;
- OTLP log batches carrying `tool_result` records, which the canonical telemetry
  ledger reduces;
- short-lived child processes.

Hook and OTLP round trips are timed from here, so the harness can report what a
CLI waiting on the daemon would have felt. The summary is written to `--stats` as
JSON when the run ends; nothing is printed to the terminal except the load itself.

Stdlib only, so it runs under any Python the harness points at.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Body sizes for hook POSTs: the fleet averaged 50.2 KiB, and the daemon refuses
# anything over 256 KiB. A lognormal around that mean gives the long tail the
# incident had without a hand-picked histogram.
HOOK_MEAN_BYTES = 50 * 1024
HOOK_CAP_BYTES = 250 * 1024
OUTPUT_TICK_SECONDS = 0.05


class Stats:
    """Round-trip samples per traffic kind, guarded for the worker threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.samples: dict[str, list[float]] = {"hook": [], "otlp": []}
        self.statuses: dict[str, dict[str, int]] = {"hook": {}, "otlp": {}}
        self.bytes_sent: dict[str, int] = {"hook": 0, "otlp": 0, "output": 0}
        self.children = 0

    def record(self, kind: str, seconds: float, status: str, size: int) -> None:
        with self._lock:
            self.samples[kind].append(seconds)
            counts = self.statuses[kind]
            counts[status] = counts.get(status, 0) + 1
            self.bytes_sent[kind] += size

    def add_output(self, size: int) -> None:
        with self._lock:
            self.bytes_sent["output"] += size

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "round_trips": {
                    kind: _percentiles(values) for kind, values in self.samples.items()
                },
                # Raw samples too, so the harness can pool every session's round
                # trips into one distribution instead of averaging percentiles.
                "samples": {
                    kind: [round(value, 4) for value in values]
                    for kind, values in self.samples.items()
                },
                "statuses": {kind: dict(counts) for kind, counts in self.statuses.items()},
                "bytes_sent": dict(self.bytes_sent),
                "children": self.children,
            }


def _percentiles(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 4)

    return {
        "count": len(ordered),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": round(ordered[-1], 4),
    }


def _post(url: str, secret: str, body: bytes, timeout: float) -> str:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Mux-Hook-Secret": secret},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            return str(response.status)
    except urllib.error.HTTPError as error:
        return str(error.code)
    except (OSError, TimeoutError):
        return "timeout"


def _redraw_frame(cols: int, rows: int, tick: int) -> bytes:
    """One full repaint of a TUI frame: home, then every row, coloured."""
    parts = ["\x1b[H"]
    for row in range(rows):
        colour = 31 + (row + tick) % 7
        text = f" row {row:02d} tick {tick:06d} ".ljust(cols - 1, ".")[: cols - 1]
        parts.append(f"\x1b[{colour}m{text}\x1b[0m\r\n" if row < rows - 1 else text)
    return "".join(parts).encode()


def _scroll_line(tick: int, cols: int) -> bytes:
    colour = 32 + tick % 5
    body = f"[{tick:06d}] working on the change, reading files and running tools ".ljust(
        cols - 1, "-"
    )
    return f"\x1b[{colour}m{body[: cols - 1]}\x1b[0m\r\n".encode()


def output_source(args: argparse.Namespace, stats: Stats, stop: threading.Event) -> None:
    if args.output_rate <= 0:
        return
    out = sys.stdout.buffer
    per_tick = max(1, int(args.output_rate * OUTPUT_TICK_SECONDS))
    tick = 0
    if args.mode == "redraw":
        out.write(b"\x1b[?1049h\x1b[2J")
    try:
        while not stop.is_set():
            started = time.monotonic()
            written = 0
            while written < per_tick:
                chunk = (
                    _redraw_frame(args.cols, args.rows, tick)
                    if args.mode == "redraw"
                    else _scroll_line(tick, args.cols)
                )
                out.write(chunk)
                written += len(chunk)
                tick += 1
            out.flush()
            stats.add_output(written)
            stop.wait(max(0.0, OUTPUT_TICK_SECONDS - (time.monotonic() - started)))
    finally:
        if args.mode == "redraw":
            out.write(b"\x1b[?1049l")
            out.flush()


def _hook_body(rng: random.Random, sequence: int) -> bytes:
    size = int(min(HOOK_CAP_BYTES, rng.lognormvariate(0, 0.9) * HOOK_MEAN_BYTES / 1.5))
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_use_id": f"toolu_load_{sequence:08d}",
        "tool_input": {"command": "rg --files src | head -n 200", "description": "list files"},
        "tool_response": {"stdout": "x" * max(0, size - 400), "stderr": "", "interrupted": False},
    }
    return json.dumps(
        {"event": "PostToolUse", "source": "load", "sequence": sequence, "payload": payload}
    ).encode()


def encode_cwd(cwd: str) -> str:
    """Claude Code's project-directory name for a cwd (`adapters/claude.py`)."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd).resolve()))


def promote(base: str, session_id: str, secret: str, backend: str, native_id: str) -> str:
    """Make this shell pane an agent session, as the agent launcher shim does.

    Hook ingress serves agent sessions only, and an agent session is also what
    the daemon observes, classifies and offers for delivery - so a pane that
    stayed a shell would put a fraction of an agent's load on the daemon.
    """
    body = json.dumps({"backend": backend, "native_id": native_id, "cwd": os.getcwd()}).encode()
    return _post(f"{base}/api/sessions/{session_id}/promote", secret, body, timeout=30)


def _transcript_record(native_id: str, parent: str | None, tick: int, size: int) -> dict[str, Any]:
    role = "assistant" if tick % 2 else "user"
    text = f"step {tick}: " + "z" * max(0, size - 200)
    return {
        "type": role,
        "uuid": str(uuid.uuid4()),
        "parentUuid": parent,
        "sessionId": native_id,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "cwd": os.getcwd(),
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    }


def transcript_source(
    args: argparse.Namespace, stats: Stats, stop: threading.Event, path: Path, native_id: str
) -> None:
    """Append conversation records the way a working CLI does, for the observer to tail."""
    if args.transcript_rate <= 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed + 3)
    parent: str | None = None
    tick = 0
    with path.open("a", encoding="utf-8") as handle:
        while not stop.wait(1.0):
            budget = int(args.transcript_rate)
            while budget > 0:
                size = min(budget, rng.randint(400, 6000))
                record = _transcript_record(native_id, parent, tick, size)
                parent = record["uuid"]
                handle.write(json.dumps(record) + "\n")
                budget -= size
                tick += 1
            handle.flush()


def hook_source(
    args: argparse.Namespace, stats: Stats, stop: threading.Event, url: str, secret: str
) -> None:
    if args.hook_rate <= 0:
        return
    rng = random.Random(args.seed)
    sequence = 0
    while not stop.wait(rng.expovariate(args.hook_rate)):
        sequence += 1
        body = _hook_body(rng, sequence)
        started = time.perf_counter()
        status = _post(url, secret, body, timeout=args.post_timeout)
        stats.record("hook", time.perf_counter() - started, status, len(body))


def _otlp_body(rng: random.Random, records: int, sequence: int) -> bytes:
    log_records = []
    now_ns = time.time_ns()
    for index in range(records):
        output = "y" * rng.randint(200, 4000)
        log_records.append(
            {
                "timeUnixNano": str(now_ns + index),
                "body": {"stringValue": "codex.tool_result"},
                "attributes": [
                    {"key": "event.name", "value": {"stringValue": "codex.tool_result"}},
                    {"key": "tool_name", "value": {"stringValue": "shell"}},
                    {"key": "call_id", "value": {"stringValue": f"call_{sequence}_{index}"}},
                    {"key": "success", "value": {"stringValue": "true"}},
                    {"key": "duration_ms", "value": {"intValue": str(rng.randint(5, 4000))}},
                    {"key": "output", "value": {"stringValue": output}},
                ],
            }
        )
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "codex_cli_rs"}},
                        {"key": "service.version", "value": {"stringValue": "0.0.0-load"}},
                    ]
                },
                "scopeLogs": [{"logRecords": log_records}],
            }
        ]
    }
    return json.dumps(payload).encode()


def otlp_source(
    args: argparse.Namespace, stats: Stats, stop: threading.Event, url: str, secret: str
) -> None:
    if args.otlp_rate <= 0:
        return
    rng = random.Random(args.seed + 1)
    sequence = 0
    while not stop.wait(rng.expovariate(args.otlp_rate)):
        sequence += 1
        body = _otlp_body(rng, rng.randint(4, 24), sequence)
        started = time.perf_counter()
        status = _post(url, secret, body, timeout=args.post_timeout)
        stats.record("otlp", time.perf_counter() - started, status, len(body))


def churn_source(args: argparse.Namespace, stats: Stats, stop: threading.Event) -> None:
    if args.spawn_interval <= 0:
        return
    children: list[subprocess.Popen[bytes]] = []
    rng = random.Random(args.seed + 2)
    while not stop.wait(args.spawn_interval * rng.uniform(0.5, 1.5)):
        children = [child for child in children if child.poll() is None]
        children.append(
            subprocess.Popen(
                [sys.executable, "-c", f"import time; time.sleep({args.child_seconds})"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        )
        stats.children += 1
    for child in children:
        try:
            child.wait(timeout=args.child_seconds + 5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--mode", choices=("redraw", "scroll"), default="scroll")
    parser.add_argument("--output-rate", type=float, default=20_000, help="bytes per second")
    parser.add_argument("--hook-rate", type=float, default=0.1, help="hook POSTs per second")
    parser.add_argument("--otlp-rate", type=float, default=0.05, help="OTLP posts per second")
    parser.add_argument("--spawn-interval", type=float, default=5.0)
    parser.add_argument("--child-seconds", type=float, default=3.0)
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--post-timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--promote",
        default="claude",
        help="agent backend to promote this pane to, or 'none' to stay a shell",
    )
    parser.add_argument(
        "--transcript-rate", type=float, default=3000, help="transcript bytes per second"
    )
    args = parser.parse_args()

    hook_url = os.environ.get("MUX_HOOK_URL", "")
    secret = os.environ.get("MUX_HOOK_SECRET", "")
    session_id = os.environ.get("MUX_SESSION_ID", "")
    base = hook_url.rsplit("/api/hooks/", 1)[0] if "/api/hooks/" in hook_url else ""
    otlp_url = f"{base}/api/telemetry/otlp/{session_id}/v1/logs" if base and session_id else ""

    stats = Stats()
    stop = threading.Event()
    promoted = "skipped"
    native_id = str(uuid.uuid4())
    if args.promote != "none" and base and session_id and secret:
        # The transcript exists before the promotion names it, as it does for a
        # real CLI, so the observer finds a file rather than polling for one.
        transcript = (
            Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
            / "projects"
            / encode_cwd(os.getcwd())
            / f"{native_id}.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps(_transcript_record(native_id, None, 0, 300)) + "\n", encoding="utf-8"
        )
        promoted = promote(base, session_id, secret, args.promote, native_id)
    else:
        transcript = None
    threads = [
        threading.Thread(target=output_source, args=(args, stats, stop), name="output"),
        threading.Thread(target=churn_source, args=(args, stats, stop), name="churn"),
    ]
    if transcript is not None:
        threads.append(
            threading.Thread(
                target=transcript_source,
                args=(args, stats, stop, transcript, native_id),
                name="transcript",
            )
        )
    if hook_url and secret:
        threads.append(
            threading.Thread(
                target=hook_source, args=(args, stats, stop, hook_url, secret), name="hooks"
            )
        )
    if otlp_url and secret:
        threads.append(
            threading.Thread(
                target=otlp_source, args=(args, stats, stop, otlp_url, secret), name="otlp"
            )
        )
    started = time.time()
    for thread in threads:
        thread.start()
    try:
        stop.wait(args.duration)
    except KeyboardInterrupt:
        pass
    stop.set()
    for thread in threads:
        thread.join(timeout=60)
    report = {
        "session_id": session_id,
        "started_at": started,
        "ended_at": time.time(),
        "hook_wired": bool(hook_url and secret),
        "promoted": promoted,
        **stats.summary(),
    }
    args.stats.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
