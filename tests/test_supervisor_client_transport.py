"""Control plane vs data plane on the supervisor connection (S2.4/S2.5, audit F3/F1).

The read loop carries three different things on one socket: RPC replies, output,
and `pty_exit`. It used to `await` the per-session output queue inline, so a
single session whose consumer had not started (or had stalled) stopped *all* of
them for *every* session - long enough to push another session's spawn RPC past
its 60s timeout, which is the trigger for the duplicate-agent finding.

The rule these pin: dispatching a frame never waits. Output waits per session,
in that session's own pump.
"""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path
from typing import Any

import pytest

from swe_mux.supervisor_client import RemotePtyHost, SupervisorClient, SupervisorUnavailable

from .support.fake_supervisor import FakeSupervisor, default_responder
from .support.settle import until

QUEUE_MAXSIZE = 1024


async def connected_client(
    tmp_path: Path, responder: Any = None
) -> tuple[FakeSupervisor, SupervisorClient]:
    supervisor = FakeSupervisor(tmp_path, responder=responder)
    await supervisor.start()
    client = await SupervisorClient.connect(tmp_path)
    return supervisor, client


async def stalled_host(client: SupervisorClient, sid: str) -> RemotePtyHost:
    """A registered host whose consumer never runs, so its queue is already full."""
    host = RemotePtyHost(client, sid, appname="cmd.exe", cwd=".")
    host.prepare()
    host._alive = True
    for _ in range(QUEUE_MAXSIZE):
        host.output_queue.put_nowait(b"backlog")
    assert host.output_queue.full()
    return host


async def test_a_full_session_queue_does_not_delay_another_session_rpc(
    tmp_path: Path,
) -> None:
    """The head-of-line test: bulk output for a stalled session, RPC for a healthy one."""

    def responder(header: dict[str, Any], payload: bytes) -> dict[str, Any] | None:
        if header.get("t") == "subscribe":
            # Everything the stalled session could possibly be sent goes onto the
            # wire *ahead* of this reply, so the reply can only arrive if
            # dispatching those frames did not block.
            for index in range(200):
                supervisor.send({"t": "output", "sid": "stalled"}, f"chunk-{index}".encode())
            return {"ok": True, "alive": True, "exit_code": None}
        return default_responder(header, payload)

    supervisor, client = await connected_client(tmp_path, responder)
    try:
        stalled = await stalled_host(client, "stalled")
        healthy = RemotePtyHost(client, "healthy", appname="cmd.exe", cwd=".")
        healthy.prepare()

        response, _ = await asyncio.wait_for(client.subscribe(healthy), timeout=5)

        assert response["ok"] is True
        await until(
            lambda: stalled._pending_bytes > 0,
            what="the stalled session's output was staged rather than dropped or awaited",
        )
        assert stalled.output_queue.full()
    finally:
        await client.close()
        await supervisor.stop()


async def test_pty_exit_is_delivered_while_the_session_queue_is_full(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    try:
        host = await stalled_host(client, "stalled")

        supervisor.send({"t": "output", "sid": "stalled"}, b"more")
        supervisor.send({"t": "pty_exit", "sid": "stalled", "exit_code": 7})
        await until(lambda: not host._alive, what="pty_exit was applied past a full queue")

        assert host.exit_status() == 7
    finally:
        await client.close()
        await supervisor.stop()


async def test_the_exit_sentinel_survives_a_full_queue(tmp_path: Path) -> None:
    """Audit F14, client half: a full queue used to silently drop the sentinel."""
    supervisor, client = await connected_client(tmp_path)
    try:
        host = await stalled_host(client, "stalled")

        supervisor.send({"t": "output", "sid": "stalled"}, b"tail-output")
        supervisor.send({"t": "pty_exit", "sid": "stalled", "exit_code": 0})
        await until(lambda: not host._alive, what="pty_exit was applied")

        drained: list[bytes] = []
        while len(drained) < QUEUE_MAXSIZE + 2:
            drained.append(await asyncio.wait_for(host.output_queue.get(), timeout=5))
            if drained[-1] == b"":
                break

        assert drained[-1] == b"", "the end-of-output sentinel must always arrive"
        assert b"tail-output" in drained, "output before the exit must arrive before it"
    finally:
        await client.close()
        await supervisor.stop()


async def test_staged_output_is_bounded_and_the_drop_is_reported(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    try:
        host = await stalled_host(client, "stalled")
        host.max_scrollback = 1  # the floor (1 MiB) applies; keep the test honest about it
        budget = host._overflow_budget
        chunk = b"x" * 64 * 1024
        for _ in range((budget // len(chunk)) + 4):
            host._offer(chunk)

        assert host._pending_bytes <= budget
        assert host.output_dropped_bytes > 0
        assert host._pending[-1] is chunk, "the newest chunk is never the one dropped"
    finally:
        await client.close()
        await supervisor.stop()


async def test_staged_output_reaches_the_consumer_in_order(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    try:
        host = RemotePtyHost(client, "s1", appname="cmd.exe", cwd=".")
        host.prepare()
        host._alive = True
        # One more chunk than the queue holds, so the tail can only be delivered
        # through the staging pump.
        expected = [f"chunk-{index}".encode() for index in range(QUEUE_MAXSIZE + 50)]
        for chunk in expected:
            host._offer(chunk)

        received: list[bytes] = []
        for _ in expected:
            received.append(await asyncio.wait_for(host.output_queue.get(), timeout=5))

        assert received == expected
        assert host.output_dropped_bytes == 0
    finally:
        await client.close()
        await supervisor.stop()


async def test_a_malformed_frame_is_classified_as_desync_not_as_a_dead_supervisor(
    tmp_path: Path,
) -> None:
    """A ValueError from one frame took the whole connection-lost path unlogged."""
    supervisor, client = await connected_client(tmp_path)
    try:
        host = RemotePtyHost(client, "s1", appname="cmd.exe", cwd=".")
        host.prepare()
        host._alive = True

        supervisor.send_raw(struct.pack(">I", 5) + b"{not!")
        await until(lambda: client.desync_count > 0, what="the desync was classified")

        assert client.connected is False
        assert "JSONDecodeError" in client.desync_reason or "Expecting" in client.desync_reason
        assert "desync" in client.lost_reason
        # The supervisor process is still this test process, so the sessions are
        # unreachable, not dead: a protocol bug must not fabricate exits either.
        assert client.lost is True
        assert host.isalive() is True
        assert host.output_queue.empty()
    finally:
        await client.close()
        await supervisor.stop()


async def test_an_oversized_frame_header_is_desync_too(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    try:
        supervisor.send_raw(struct.pack(">I", 1 << 30))
        await until(lambda: client.desync_count > 0, what="the oversized header was classified")

        assert "oversized frame header" in client.desync_reason
    finally:
        await client.close()
        await supervisor.stop()


async def test_a_clean_socket_close_is_not_counted_as_desync(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    try:
        await supervisor.drop_connection()
        await until(lambda: not client.connected, what="the client noticed the drop")

        assert client.desync_count == 0
        assert client.desync_reason == ""
        assert "connection lost" in client.lost_reason
    finally:
        await client.close()
        await supervisor.stop()


async def test_pending_rpcs_fail_fast_when_the_connection_drops(tmp_path: Path) -> None:
    def responder(header: dict[str, Any], _payload: bytes) -> dict[str, Any] | None:
        return None if header.get("t") == "subscribe" else default_responder(header, _payload)

    supervisor, client = await connected_client(tmp_path, responder)
    try:
        host = RemotePtyHost(client, "s1", appname="cmd.exe", cwd=".")
        host.prepare()
        pending = asyncio.create_task(client.subscribe(host))
        await until(lambda: bool(supervisor.requests_of("subscribe")), what="the RPC was sent")

        await supervisor.drop_connection()

        with pytest.raises(SupervisorUnavailable):
            await asyncio.wait_for(pending, timeout=5)
    finally:
        await client.close()
        await supervisor.stop()
