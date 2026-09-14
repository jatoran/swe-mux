"""A listener asyncio closed after a failed accept is found and rebound.

The Windows proactor closes the *listening* socket on one `OSError` from an
accept completion and never listens again; the daemon stayed alive on its
tailnet address and unreachable on loopback for four and a half minutes on
2026-09-14. The guard is the only thing in the process that notices.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from swe_mux import listener_guard
from swe_mux.listener_guard import ListenerGuard


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def hello(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def answers(port: int) -> bool:
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as client,
            client.get(f"http://127.0.0.1:{port}/") as response,
        ):
            return response.status == 200
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


def close_listening_socket(site: web.TCPSite) -> None:
    """What `BaseProactorEventLoop._start_serving.loop` does on an accept error."""
    server: Any = site._server
    for sock in server._sockets:
        sock.close()


async def test_a_closed_listening_socket_is_detected_and_rebound(tmp_path: Path) -> None:
    app = web.Application()
    app.router.add_get("/", hello)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    site = web.TCPSite(runner, host="127.0.0.1", port=port)
    await site.start()
    ledger: list[str] = []
    guard = ListenerGuard(
        make_site=lambda host, p: web.TCPSite(runner, host=host, port=p),
        ledger=ledger.append,
    )
    guard.watch("127.0.0.1", port, site)
    try:
        assert await answers(port)
        assert not ListenerGuard.is_dead(site)
        assert await guard.check_once() == 0

        close_listening_socket(site)
        assert ListenerGuard.is_dead(site)
        assert not await answers(port)

        assert await guard.check_once() == 1
        assert await answers(port)
        snapshot = guard.snapshot()
        assert snapshot["dead_found"] == 1
        assert snapshot["listeners"][0]["rebinds"] == 1
        assert snapshot["listeners"][0]["alive"] is True
        assert any("died" in line for line in ledger)
        assert any("rebound" in line for line in ledger)
        # The next tick finds a healthy listener and does nothing.
        assert await guard.check_once() == 0
        assert guard.snapshot()["listeners"][0]["rebinds"] == 1
    finally:
        await runner.cleanup()


async def test_a_site_that_never_started_is_not_reported_dead() -> None:
    app = web.Application()
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        never_started = web.TCPSite(runner, host="127.0.0.1", port=free_port())
        assert not ListenerGuard.is_dead(never_started)
        guard = ListenerGuard(make_site=lambda host, p: web.TCPSite(runner, host=host, port=p))
        guard.watch("127.0.0.1", 1, never_started)
        assert await guard.check_once() == 0
    finally:
        await runner.cleanup()


class DeadSite:
    """A site whose socket reads closed, and whose replacement may refuse to bind."""

    def __init__(self, fail_start: BaseException | None = None) -> None:
        self._server = type("Server", (), {"sockets": [type("Sock", (), {"fileno": lambda self: -1})()]})()
        self.fail_start = fail_start
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        if self.fail_start is not None:
            raise self.fail_start
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


class LiveSite:
    def __init__(self) -> None:
        self._server = type("Server", (), {"sockets": [type("Sock", (), {"fileno": lambda self: 7})()]})()
        self.started = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        pass


async def test_a_rebind_that_fails_is_retried_on_the_next_tick_and_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(listener_guard, "REBIND_FAILURE_LOG_INTERVAL_SECONDS", 1000.0)
    replacements: list[Any] = [DeadSite(OSError("address in use")), LiveSite()]
    made: list[Any] = []

    def make_site(host: str, port: int) -> Any:
        site = replacements.pop(0)
        made.append(site)
        return site

    dead = DeadSite()
    guard = ListenerGuard(make_site=make_site, interval_seconds=0.01)
    guard.watch("127.0.0.1", 8765, dead)
    with caplog.at_level(logging.ERROR, logger="swe_mux.listener_guard"):
        assert await guard.check_once() == 1
        # The failed replacement is not adopted; the dead site stays watched.
        assert guard.listeners[0].site is dead
        assert guard.snapshot()["rebind_failures"] == 1
        assert await guard.check_once() == 1
        assert guard.listeners[0].site is made[1]
        assert made[1].started == 1
    failures = [r for r in caplog.records if "listener_rebind_failed" in r.message]
    assert len(failures) == 1
    assert guard.snapshot()["dead_found"] == 1
    assert guard.snapshot()["listeners"][0]["rebinds"] == 1


async def test_the_guard_loop_stops_with_the_daemon() -> None:
    guard = ListenerGuard(make_site=lambda host, port: LiveSite(), interval_seconds=0.01)
    guard.watch("127.0.0.1", 8765, LiveSite())
    stop = asyncio.Event()
    task = asyncio.create_task(guard.run(stop))
    await asyncio.sleep(0.05)
    assert guard.checks >= 2
    stop.set()
    await asyncio.wait_for(task, timeout=1)
