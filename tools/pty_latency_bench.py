"""End-to-end PTY round-trip latency: the number a user feels as "snappy".

Measures the full path a keystroke takes and comes back on:

    client -> websocket -> daemon -> supervisor -> ConPTY -> child
    child -> ConPTY -> supervisor reader -> daemon -> websocket -> client

That path crosses every component this repo tunes, and no single component's metrics
report it. `loop_lag` shows whether the daemon is blocking; per-loop cost shows which
subsystem is expensive; only this shows what the two of them add up to at the keyboard.

It is also the direct check on the PTY reader's poll ladder (`pty_host.read_poll_interval`),
whose whole purpose is that an echo must not queue behind an interval tuned for an idle
pseudoconsole. Before that ladder the reader polled every 10 ms flat, so this benchmark's
floor could not go below it.

    uv run python tools/pty_latency_bench.py
    uv run python tools/pty_latency_bench.py --samples 60 --idle-gap 6 --json after.json

Spawns one throwaway shell session and removes it afterwards, including on failure.
Run it against a quiet daemon; a working agent's output makes every reading noise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import aiohttp

DEFAULT_PORT = 8765
# One printable character per sample, not a word. An interactive shell echoes each
# keystroke as it arrives (PSReadLine repaints the line per character), so waiting for a
# multi-character marker measures the time to type a string, not the latency of a
# keystroke. First byte back after a single character is the quantity a human feels.
KEYSTROKE = "x"
# Sent after each sample to clear the composed line, so every sample starts identically.
CLEAR_LINE = "\x15"


def _get(port: int, path: str) -> Any:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as response:
        return json.load(response)


async def _spawn(session: aiohttp.ClientSession, port: int, project_id: str) -> str:
    async with session.post(
        f"http://127.0.0.1:{port}/api/sessions",
        json={"project_id": project_id, "backend": "shell", "name": "perf-latency-bench"},
    ) as response:
        response.raise_for_status()
        return (await response.json())["id"]


async def _remove(session: aiohttp.ClientSession, port: int, sid: str) -> None:
    try:
        async with session.delete(f"http://127.0.0.1:{port}/api/sessions/{sid}") as response:
            await response.read()
    except aiohttp.ClientError:
        pass


async def _drain_until_quiet(ws: aiohttp.ClientWebSocketResponse, quiet: float, cap: float) -> None:
    """Read until the child has said nothing for `quiet` seconds."""
    deadline = time.monotonic() + cap
    while time.monotonic() < deadline:
        try:
            await asyncio.wait_for(ws.receive(), timeout=quiet)
        except TimeoutError:
            return


async def run(port: int, samples: int, idle_gap: float, warmup: int) -> dict[str, Any]:
    projects = _get(port, "/api/projects")
    items = projects if isinstance(projects, list) else projects.get("projects", [])
    if not items:
        raise SystemExit("no projects registered; cannot spawn a bench session")
    project_id = items[0]["id"]

    latencies: list[float] = []
    async with aiohttp.ClientSession() as http:
        sid = await _spawn(http, port, project_id)
        try:
            async with http.ws_connect(f"http://127.0.0.1:{port}/pty/{sid}") as ws:
                await ws.send_json(
                    {"type": "attach_ready", "cols": 100, "rows": 30,
                     "renderer": "dom", "hidden": False}
                )
                # The daemon refuses input from a connection that does not own it, and a
                # passive claim from a client reporting itself unfocused is denied
                # outright (`terminal_arbitration.evaluate_claim`). A gesture is what a
                # real keystroke carries, and it is what this is standing in for.
                await ws.send_json(
                    {"type": "claim_input", "reason": "gesture",
                     "device": "desktop", "focused": True}
                )
                # Let the shell finish starting and replaying before anything is timed.
                await _drain_until_quiet(ws, quiet=1.5, cap=30)

                for index in range(samples + warmup):
                    # Idle first, so most samples measure the case that actually matters:
                    # a human typing into a session that has been sitting at its prompt.
                    # That is the exact state the reader's deep-idle rung applies to.
                    if idle_gap:
                        await asyncio.sleep(idle_gap)
                        await _drain_until_quiet(ws, quiet=0.2, cap=2)
                    started = time.perf_counter()
                    await ws.send_json(
                        {"type": "input", "data": KEYSTROKE, "kind": "user", "broadcast": False}
                    )
                    elapsed = None
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        try:
                            message = await asyncio.wait_for(ws.receive(), timeout=10)
                        except TimeoutError:
                            break
                        # Any output at all is the echo: the line was quiesced before the
                        # keystroke, so nothing else can be talking.
                        if message.type == aiohttp.WSMsgType.BINARY:
                            elapsed = time.perf_counter() - started
                            break
                    await ws.send_json(
                        {"type": "input", "data": CLEAR_LINE, "kind": "user", "broadcast": False}
                    )
                    await _drain_until_quiet(ws, quiet=0.2, cap=2)
                    if elapsed is not None and index >= warmup:
                        latencies.append(elapsed * 1000)
        finally:
            await _remove(http, port, sid)

    if not latencies:
        raise SystemExit("no echo was observed; is the daemon healthy and quiet?")
    ordered = sorted(latencies)
    return {
        "samples": len(ordered),
        "idle_gap_seconds": idle_gap,
        "min_ms": round(ordered[0], 2),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 2),
        "max_ms": round(ordered[-1], 2),
        "mean_ms": round(statistics.fmean(ordered), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--samples", type=int, default=25)
    parser.add_argument(
        "--idle-gap",
        type=float,
        default=6.0,
        help="seconds to leave the session idle before each keystroke; above the reader's "
        "deep-idle threshold by default, which is the case worth measuring",
    )
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    result = asyncio.run(run(args.port, args.samples, args.idle_gap, args.warmup))
    print("=== PTY round-trip latency (keystroke to echo) ===")
    print(f"  samples   {result['samples']} (idle gap {result['idle_gap_seconds']:g}s)")
    print(f"  min       {result['min_ms']:8.2f} ms")
    print(f"  p50       {result['p50_ms']:8.2f} ms")
    print(f"  p95       {result['p95_ms']:8.2f} ms")
    print(f"  max       {result['max_ms']:8.2f} ms")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
