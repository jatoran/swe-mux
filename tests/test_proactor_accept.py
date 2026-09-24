"""A client that vanishes mid-accept must not close the daemon's listener.

On Windows, `BaseProactorEventLoop._start_serving` closes the listening socket on any
failed accept. `WinError 64` - a client that gave up before its connection was
accepted, which every client does while the loop is blocked past its timeout - closed
the daemon's loopback listener 36 times in one morning of 2026-09-24, each one a short
outage for every local client until the listener guard re-bound it.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from swe_mux import proactor_accept
from swe_mux.proactor_accept import is_transient_accept_error

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="the proactor accept loop exists only on Windows"
)


def winerror(code: int) -> OSError:
    error = OSError(f"winerror {code}")
    error.winerror = code  # type: ignore[attr-defined]
    return error


def test_connection_level_failures_are_transient_and_listener_failures_are_not() -> None:
    assert is_transient_accept_error(winerror(64))
    assert is_transient_accept_error(winerror(1236))
    assert is_transient_accept_error(ConnectionResetError())
    assert is_transient_accept_error(ConnectionAbortedError())
    # ERROR_OPERATION_ABORTED is what a *closed listener's* pending accept reports.
    assert not is_transient_accept_error(winerror(995))
    assert not is_transient_accept_error(OSError("anything else"))
    assert not is_transient_accept_error(ValueError("not an OSError"))


def serve_one_request(loop_factory: Any, fail_first: int, monkeypatch: Any) -> tuple[bool, bool]:
    """Fail the first `fail_first` accepts with WinError 64; report (served, listening)."""
    from asyncio import windows_events

    original = windows_events.IocpProactor.accept
    calls = {"count": 0}

    def flaky(self: Any, listener: Any) -> Any:
        calls["count"] += 1
        if calls["count"] <= fail_first:
            future = self._loop.create_future()
            future.set_exception(winerror(64))
            return future
        return original(self, listener)

    monkeypatch.setattr(windows_events.IocpProactor, "accept", flaky)

    async def scenario() -> tuple[bool, bool]:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readexactly(4)
            writer.write(b"pong")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        served = False
        try:
            await asyncio.sleep(0.05)
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=2
                )
                writer.write(b"ping")
                await writer.drain()
                served = await asyncio.wait_for(reader.readexactly(4), timeout=2) == b"pong"
                writer.close()
                await writer.wait_closed()
            except (OSError, TimeoutError, asyncio.IncompleteReadError):
                served = False
            listening = all(sock.fileno() != -1 for sock in server.sockets)
        finally:
            server.close()
            await server.wait_closed()
        return served, listening

    loop = loop_factory()
    loop.set_exception_handler(lambda _loop, _context: None)
    try:
        return loop.run_until_complete(scenario())
    finally:
        loop.close()


@WINDOWS_ONLY
def test_the_stock_proactor_closes_its_listener_after_one_failed_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure mode itself, so a Python upgrade that fixes it upstream is noticed."""
    served, listening = serve_one_request(asyncio.ProactorEventLoop, 1, monkeypatch)
    assert not listening
    assert not served


@WINDOWS_ONLY
def test_the_resilient_proactor_keeps_accepting_through_failed_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = proactor_accept.STATS.snapshot()["absorbed"]

    served, listening = serve_one_request(proactor_accept.resilient_event_loop, 3, monkeypatch)

    assert served
    assert listening
    assert proactor_accept.STATS.snapshot()["absorbed"] - before == 3
