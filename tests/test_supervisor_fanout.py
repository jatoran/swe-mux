"""The supervisor's fanout never waits on a client that asked not to be waited on.

`DAEMON_LOAD_ISOLATION.md` item 12. Before it, `_fanout` awaited `drain()` on any
subscriber whose buffer passed 2 MiB, serially and with no timeout, so one client
that stopped reading stalled that session's output to every other subscriber and,
once the 1,024-chunk reader queue filled, the agent itself. A client that
negotiates `output_gap` now has an outbox of its own; past a byte cap its output is
dropped per session with one `gap` frame, and it resubscribes to recover.

These run against a real loopback listener with the pseudoterminal replaced by a
queue, so they need no ConPTY and run on every host. A "stalled" client is a real
socket with a tiny receive buffer that the test simply stops reading.
"""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from swe_mux import supervisor as supervisor_module
from swe_mux.supervisor import CAPABILITY_OUTPUT_GAP, Connection, encode_frame, read_frame
from tests.support.settle import until
from tests.support.supervisor_wire import (
    CHUNK,
    Running,
    add_session,
    chunk,
    drained,
    open_supervisor,
    pour,
)


@pytest.fixture
async def supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Running]:
    async with open_supervisor(tmp_path, monkeypatch) as running:
        yield running


# --------------------------------------------------------------------------
# Negotiation
# --------------------------------------------------------------------------


async def test_hello_announces_capabilities_and_only_an_asking_client_is_lossy(
    supervisor: Running,
) -> None:
    _, asked = await supervisor.connect(capabilities=(CAPABILITY_OUTPUT_GAP,))
    _, plain = await supervisor.connect()
    # Announced to both, so a client can tell "declined" from "an older supervisor".
    assert CAPABILITY_OUTPUT_GAP in asked["capabilities"]
    assert CAPABILITY_OUTPUT_GAP in plain["capabilities"]
    await until(lambda: len(supervisor.server.connections) == 2)
    assert sorted(c.output_gap for c in supervisor.server.connections) == [False, True]


# --------------------------------------------------------------------------
# The two claims: the child is never back-pressured, and nobody else waits
# --------------------------------------------------------------------------


async def test_a_lossy_subscriber_that_stops_reading_never_back_pressures_the_child(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 256 * 1024)
    _, host = add_session(supervisor.server, "s1")
    stalled, _ = await supervisor.connect(capabilities=(CAPABILITY_OUTPUT_GAP,), stalls=True)
    await stalled.request({"t": "subscribe", "sid": "s1"})

    # 32 MiB, twice the reader queue's capacity in chunks and far past anything a
    # kernel buffers for a 4 KiB window; the client reads none of it.
    await pour(host, 2048)
    await drained(host)

    (connection,) = supervisor.lossy_connections()
    assert connection.gapped == {"s1"}
    assert connection.gaps == 1, "one gap per episode, not one per dropped chunk"
    assert connection.dropped_bytes > 0
    # What it holds for the client is bounded by the cap, not by the output.
    assert connection._queued_output_bytes <= 256 * 1024 + CHUNK + 64


async def test_a_lossy_subscriber_that_stops_reading_never_delays_another_subscriber(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 256 * 1024)
    entry, host = add_session(supervisor.server, "s1")
    stalled, _ = await supervisor.connect(capabilities=(CAPABILITY_OUTPUT_GAP,), stalls=True)
    await stalled.request({"t": "subscribe", "sid": "s1"})
    # A direct subscriber, so every byte it is owed must arrive and none may be
    # skipped: if the stalled client could hold the fanout, this one would starve.
    live, _ = await supervisor.connect()
    await live.request({"t": "subscribe", "sid": "s1"})

    count = 1024
    expected = b"".join(chunk(index) for index in range(count))
    received = bytearray()

    async def read_live() -> None:
        while len(received) < len(expected):
            frame, body = await read_frame(live.reader)
            if frame.get("t") == "output":
                received.extend(body)

    reader = asyncio.create_task(read_live())
    for index in range(count):
        # This subscriber's own backpressure is allowed to slow the producer; the
        # stalled one's is not, which the completion below is what proves.
        await host.output_queue.put(chunk(index))
    await asyncio.wait_for(reader, timeout=30)

    assert bytes(received) == expected
    assert entry.scrollback.position == len(expected)
    (connection,) = supervisor.lossy_connections()
    assert connection.gapped == {"s1"}


async def test_a_subscriber_that_did_not_ask_for_gaps_is_still_waited_on(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capability gates the change: an older daemon keeps lossless delivery.

    It also keeps the coupling, which is the behaviour this plan exists to remove
    and the reason the new mode is opt-in rather than imposed on a client that
    cannot recover from a gap.
    """
    monkeypatch.setattr(supervisor_module, "WRITE_BUFFER_HIGH_WATER", 64 * 1024)
    _, host = add_session(supervisor.server, "s1", queue_size=64)
    stalled, _ = await supervisor.connect(stalls=True)
    await stalled.request({"t": "subscribe", "sid": "s1"})

    with pytest.raises(asyncio.QueueFull):
        await pour(host, 4096)

    # Closing the stalled client releases the fanout, and the queue drains.
    await stalled.close()
    await drained(host)
    assert supervisor.lossy_connections() == []


# --------------------------------------------------------------------------
# The gap protocol
# --------------------------------------------------------------------------


async def test_a_gap_names_where_the_clients_copy_stops_and_a_resubscribe_resumes_it(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 256 * 1024)
    entry, host = add_session(supervisor.server, "s1")
    stalled, _ = await supervisor.connect(capabilities=(CAPABILITY_OUTPUT_GAP,), stalls=True)
    subscribed, _ = await stalled.request({"t": "subscribe", "sid": "s1"})
    assert subscribed["position"] == 0

    await pour(host, 1024)
    await drained(host)

    # Reading again: everything queued arrives, in order, ahead of this reply.
    await stalled.request({"t": "ping"})
    gaps = [frame for frame, _ in stalled.frames if frame.get("t") == "gap"]
    assert len(gaps) == 1
    position = gaps[0]["position"]
    delivered = stalled.output("s1")
    # The copy is contiguous up to the gap, and the gap is exactly its end.
    assert position == len(delivered)
    assert delivered == entry.scrollback.bytes()[:position]
    assert stalled.kinds("s1")[-1] == "gap", "no output for the session after its gap"

    # Still gapped: further output is not delivered, and replies still are.
    stalled.frames.clear()
    await pour(host, 4, first=1024)
    await drained(host)
    await stalled.request({"t": "ping"})
    assert stalled.output("s1") == b""

    # A resubscribe ends the gap: its reply carries everything, and output flows.
    reply, replay = await stalled.request({"t": "subscribe", "sid": "s1"})
    assert replay == entry.scrollback.bytes()
    assert reply["position"] == entry.scrollback.position
    (connection,) = supervisor.lossy_connections()
    assert connection.gapped == set()
    stalled.frames.clear()
    await pour(host, 1, first=1028)
    # Drained means the fanout has taken the chunk, and it queues a lossy
    # connection's frame without awaiting, so the ping's reply comes after it.
    await drained(host)
    await stalled.request({"t": "ping"})
    assert stalled.output("s1") == chunk(1028)


async def test_an_exit_still_reaches_a_gapped_client(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 64 * 1024)
    entry, host = add_session(supervisor.server, "s1")
    stalled, _ = await supervisor.connect(capabilities=(CAPABILITY_OUTPUT_GAP,), stalls=True)
    await stalled.request({"t": "subscribe", "sid": "s1"})
    await pour(host, 512)
    await drained(host)

    host.alive = False
    host.output_queue.put_nowait(b"")
    assert entry.fanout_task is not None
    await asyncio.wait_for(entry.fanout_task, timeout=10)

    await stalled.request({"t": "ping"})
    kinds = stalled.kinds("s1")
    assert kinds[-2:] == ["gap", "pty_exit"]


async def test_a_lossy_client_that_stopped_reading_still_has_its_input_written(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keystrokes are requests; a backlog of replies must not hold them back.

    `_serve_connection` used to drain after every request, so a connection that
    stopped reading stopped having its own writes processed as well.
    """
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 64 * 1024)
    _, host = add_session(supervisor.server, "s1")
    stalled, _ = await supervisor.connect(capabilities=(CAPABILITY_OUTPUT_GAP,), stalls=True)
    await stalled.request({"t": "subscribe", "sid": "s1"})
    await pour(host, 1024)
    await drained(host)

    for key in (b"l", b"s", b"\r"):
        await stalled.notify({"t": "write", "sid": "s1"}, key)
    await until(lambda: host.writes == [b"l", b"s", b"\r"], what="the input reached the host")


async def test_teardown_reaps_while_a_client_is_still_connected(
    supervisor: Running,
) -> None:
    """Since Python 3.12.1 `Server.wait_closed` waits for every connected client.

    Teardown awaited it before closing connections, so one client that stayed
    connected held the reap open for as long as it stayed.
    """
    await supervisor.connect(capabilities=(CAPABILITY_OUTPUT_GAP,))
    await supervisor.connect()
    supervisor.server.exit_event.set()
    await asyncio.wait_for(supervisor.server._teardown(), timeout=10)
    assert supervisor.server.reaper.closed is True  # type: ignore[attr-defined]
    await until(lambda: not supervisor.server.connections, what="every handler finished")


# --------------------------------------------------------------------------
# The outbox itself
# --------------------------------------------------------------------------


class HeldWriter:
    """A StreamWriter whose drain waits until the test releases it."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.release = asyncio.Event()
        self.transport = None
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        await self.release.wait()

    def close(self) -> None:
        self.closed = True


def lossy(writer: HeldWriter) -> Connection:
    connection = Connection(None, writer, peer="test")  # type: ignore[arg-type]
    connection.start_lossy()
    return connection


async def settle_writer() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def parse(frame: bytes) -> dict[str, Any]:
    (size,) = struct.unpack(">I", frame[:4])
    header: dict[str, Any] = json.loads(frame[4 : 4 + size])
    return header


async def test_the_outbox_keeps_order_drops_only_output_and_never_a_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 4096)
    writer = HeldWriter()
    connection = lossy(writer)
    output = encode_frame({"t": "output", "sid": "s1"}, b"x" * 2048)

    connection.send({"id": 1, "ok": True})
    await settle_writer()  # the writer takes frame one and waits in drain
    position = 0
    for _ in range(4):
        connection.offer_output("s1", output, position, 2048)
        position += 2048
    connection.send({"id": 2, "ok": True})
    connection.offer_output("s2", output, 0, 2048)
    connection.offer_output("s1", output, position, 2048)

    assert connection.gapped == {"s1", "s2"}
    assert connection.gaps == 2

    writer.release.set()
    await connection.flush()
    headers = [parse(frame) for frame in writer.written]
    kinds = [header.get("t") or f"reply{header['id']}" for header in headers]
    # Two output frames fit under the cap; the third finds it full and becomes the
    # gap, the reply that followed is kept, and each session gaps exactly once.
    assert kinds == ["reply1", "output", "output", "gap", "reply2", "gap"]
    assert headers[3] == {"t": "gap", "sid": "s1", "position": 4096}
    assert headers[5] == {"t": "gap", "sid": "s2", "position": 0}

    connection.resume("s1")
    connection.offer_output("s1", output, position + 2048, 2048)
    await connection.flush()
    assert parse(writer.written[-1])["t"] == "output"
    task = connection.close()
    assert task is not None
    await asyncio.gather(task, return_exceptions=True)
    assert writer.closed


async def test_a_client_that_asks_without_reading_is_held_until_it_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 1024)
    writer = HeldWriter()
    connection = lossy(writer)
    reply = {"id": 1, "ok": True, "padding": "p" * 512}
    connection.send(reply)
    await settle_writer()
    for _ in range(8):
        connection.send(reply)
    assert connection.queued_bytes > 1024

    waiting = asyncio.create_task(connection.wait_for_room())
    await settle_writer()
    assert not waiting.done()
    writer.release.set()
    await asyncio.wait_for(waiting, timeout=5)
    assert connection.queued_bytes <= 1024


async def test_closing_releases_anyone_waiting_on_the_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 16)
    writer = HeldWriter()
    connection = lossy(writer)
    for _ in range(4):
        connection.send({"id": 1, "ok": True})
    await settle_writer()
    room = asyncio.create_task(connection.wait_for_room())
    flushed = asyncio.create_task(connection.flush())
    await settle_writer()
    assert not room.done() and not flushed.done()

    task = connection.close()
    await asyncio.wait_for(room, timeout=5)
    await asyncio.wait_for(flushed, timeout=5)
    assert task is not None
    await asyncio.gather(task, return_exceptions=True)
    # Nothing further is queued once closed.
    connection.send({"id": 2, "ok": True})
    assert connection.queued_bytes == 0
