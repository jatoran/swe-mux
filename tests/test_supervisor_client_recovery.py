"""The daemon fills every hole in a session's output instead of skipping it.

`DAEMON_LOAD_ISOLATION.md` item 14. A hole opens two ways: the supervisor stops
sending a session's output because this daemon stopped reading (`gap`, item 12),
or this daemon's own staging for a session outgrows its budget because the
session's consumer stopped taking output. Before this, the second silently
dropped the oldest staged bytes, after which the daemon's ring disagreed with the
supervisor's positions for the rest of the session's life, and the first could
not happen at all because the supervisor waited instead.

Both now end in one resubscribe from where the copy stops: a delta while the
supervisor retains it, and its whole buffer otherwise, which the session adopts
in place of its ring. The client also reattaches after a dropped socket instead
of waiting for a daemon restart, and keeps working against a supervisor that
predates all of it.

The new supervisor here is the real `SupervisorServer` with queue-backed sessions
(`tests/support/supervisor_wire.py`); the old one is `FakeSupervisor`, which
announces no capabilities.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import supervisor as supervisor_module
from swe_mux import supervisor_client
from swe_mux.adapters import ShellAdapter
from swe_mux.models import SessionRecord
from swe_mux.screen_mode import BracketedPasteParser, ScreenModeParser, StickyModeParser
from swe_mux.scrollback import ScrollbackBuffer
from swe_mux.session import Session, SessionManager
from swe_mux.supervisor_client import (
    OutputReseed,
    RemotePtyHost,
    SupervisorClient,
    host_for_adoption,
)
from tests.support.fake_supervisor import FakeSupervisor
from tests.support.settle import until
from tests.support.supervisor_wire import (
    CHUNK,
    Running,
    add_session,
    drained,
    open_supervisor,
    pour,
)


@pytest.fixture
async def supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Running]:
    async with open_supervisor(tmp_path, monkeypatch) as running:
        yield running


class Consumer:
    """Stands in for `Session._fanout`: takes a host's output and keeps its ring."""

    def __init__(self, host: RemotePtyHost) -> None:
        self.host = host
        self.ring = bytearray()
        self.reseeds: list[OutputReseed] = []
        self.ended = False
        self.task: asyncio.Task[None] | None = None

    def start(self) -> Consumer:
        self.task = asyncio.create_task(self._run())
        return self

    async def _run(self) -> None:
        while True:
            item = await self.host.output_queue.get()
            if isinstance(item, OutputReseed):
                self.reseeds.append(item)
                self.ring[:] = item.data
            elif item == b"":
                self.ended = True
                return
            else:
                self.ring.extend(item)

    async def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def attach(client: SupervisorClient, sid: str) -> RemotePtyHost:
    info = next(item for item in (await client.request({"t": "list"}))[0]["sessions"]
                if item["sid"] == sid)
    host = host_for_adoption(client, info)
    host.prepare()
    await client.subscribe(host)
    return host


async def daemon_client(running: Running) -> SupervisorClient:
    client = await SupervisorClient.connect(running.server.data_dir)
    return client


# --------------------------------------------------------------------------
# Against the new supervisor
# --------------------------------------------------------------------------


async def test_the_daemon_asks_for_output_gap_and_records_what_was_announced(
    supervisor: Running,
) -> None:
    client = await daemon_client(supervisor)
    try:
        assert supervisor_module.CAPABILITY_OUTPUT_GAP in client.capabilities
        assert supervisor_module.CAPABILITY_SUBSCRIBE_SINCE in client.capabilities
        await until(lambda: len(supervisor.lossy_connections()) == 1)
    finally:
        await client.close()


async def test_a_daemon_that_stopped_reading_recovers_every_byte_after_a_gap(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 256 * 1024)
    entry, source = add_session(supervisor.server, "s1")
    client = await daemon_client(supervisor)
    try:
        host = await attach(client, "s1")
        consumer = Consumer(host).start()
        transport = client._writer.transport
        transport.pause_reading()  # the daemon's loop has stopped reading
        await pour(source, 2048)
        await drained(source)
        (connection,) = supervisor.lossy_connections()
        assert connection.gapped == {"s1"}, "the supervisor dropped rather than waited"

        transport.resume_reading()
        await until(
            lambda: bytes(consumer.ring) == entry.scrollback.bytes(),
            seconds=30,
            what="the consumer holds exactly the supervisor's bytes",
        )
        assert client.gaps_received == 1
        assert client.recoveries["delta"] == 1
        assert client.recoveries["full"] == 0
        assert host.stream_position == entry.scrollback.position
        assert consumer.reseeds == []
        await consumer.stop()
    finally:
        await client.close()


async def test_output_that_follows_a_recovery_reply_is_kept(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reply is applied as it is read, not when its caller next runs.

    Output frames for the session follow the reply on the wire at once, and the
    read loop dispatches buffered frames back to back; a reply applied a loop turn
    later would leave the host still recovering for those frames, and drop them.
    """
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 256 * 1024)
    entry, source = add_session(supervisor.server, "s1")
    client = await daemon_client(supervisor)
    try:
        host = await attach(client, "s1")
        consumer = Consumer(host).start()
        transport = client._writer.transport
        transport.pause_reading()
        await pour(source, 2048)
        await drained(source)

        async def keep_producing() -> None:
            for index in range(2048, 2048 + 512):
                await source.output_queue.put(bytes([97 + index % 26]) * CHUNK)
                await asyncio.sleep(0.0005)

        producer = asyncio.create_task(keep_producing())
        transport.resume_reading()
        await asyncio.wait_for(producer, timeout=30)
        await drained(source)
        await until(
            lambda: bytes(consumer.ring) == entry.scrollback.bytes(),
            seconds=30,
            what="every byte, including those right behind the reply",
        )
        assert client.recoveries["delta"] >= 1
        await consumer.stop()
    finally:
        await client.close()


async def test_a_consumer_that_falls_behind_is_refilled_rather_than_skipped(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staging overflow used to drop the oldest bytes and carry on with a hole."""
    monkeypatch.setattr(supervisor_client, "MIN_OUTPUT_OVERFLOW_BYTES", 64 * 1024)
    entry, source = add_session(supervisor.server, "s1")
    client = await daemon_client(supervisor)
    try:
        host = await attach(client, "s1")
        host.max_scrollback = 64 * 1024
        # Nobody consumes yet: 1,024 queued chunks, then 64 KiB of staging.
        await pour(source, 1100)
        await drained(source)
        await until(lambda: host.consumer_overflows >= 1, what="staging overflowed")

        consumer = Consumer(host).start()
        await until(
            lambda: bytes(consumer.ring) == entry.scrollback.bytes() and not host.recovering,
            seconds=60,
            what="the consumer caught up with every byte",
        )
        assert client.recoveries["delta"] >= 1
        assert client.recoveries["full"] == 0
        assert host.stream_position == entry.scrollback.position
        await consumer.stop()
    finally:
        await client.close()


async def test_a_hole_the_supervisor_no_longer_retains_is_filled_by_reseeding(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_module, "OUTPUT_GAP_QUEUE_BYTES", 128 * 1024)
    entry, source = add_session(supervisor.server, "s1", scrollback_bytes=64 * 1024)
    client = await daemon_client(supervisor)
    try:
        host = await attach(client, "s1")
        consumer = Consumer(host).start()
        transport = client._writer.transport
        transport.pause_reading()
        await pour(source, 2048)
        await drained(source)
        transport.resume_reading()

        await until(lambda: client.recoveries["full"] == 1, seconds=30, what="a full refill")
        await until(lambda: bytes(consumer.ring) == entry.scrollback.bytes(), seconds=30)
        (reseed,) = consumer.reseeds
        assert reseed.position == entry.scrollback.position
        assert len(reseed.data) == 64 * 1024
        assert client.recoveries["delta"] == 0
        await consumer.stop()
    finally:
        await client.close()


async def test_another_connections_resize_reaches_the_daemons_mirror(
    supervisor: Running,
) -> None:
    add_session(supervisor.server, "s1")
    client = await daemon_client(supervisor)
    try:
        host = await attach(client, "s1")
        gateway, _ = await supervisor.connect(scope="terminal")
        await gateway.request({"t": "subscribe", "sid": "s1"})
        await gateway.notify({"t": "resize", "sid": "s1", "cols": 91, "rows": 33})
        await until(lambda: (host.cols, host.rows) == (91, 33), what="the mirror followed")
    finally:
        await client.close()


async def test_a_removal_by_another_connection_ends_the_daemons_session(
    supervisor: Running,
) -> None:
    _, source = add_session(supervisor.server, "s1")
    client = await daemon_client(supervisor)
    try:
        host = await attach(client, "s1")
        other, _ = await supervisor.connect()
        await other.request({"t": "remove", "sid": "s1"})
        assert await asyncio.wait_for(host.output_queue.get(), timeout=10) == b""
        assert host.isalive() is False
        assert source.stopped
    finally:
        await client.close()


async def test_a_dropped_socket_is_reattached_and_the_output_meanwhile_recovered(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_client, "RECONNECT_INITIAL_SECONDS", 0.05)
    entry, source = add_session(supervisor.server, "s1")
    client = await daemon_client(supervisor)
    try:
        host = await attach(client, "s1")
        consumer = Consumer(host).start()
        await pour(source, 8)
        await until(lambda: len(consumer.ring) == 8 * CHUNK)

        # Drop the socket from the supervisor's side; its process (this one) lives.
        for connection in tuple(supervisor.server.connections):
            connection.close()
        await until(lambda: not client.connected, what="the drop was noticed")
        assert client.lost is True
        await pour(source, 8, first=8)  # produced while nobody was attached
        await drained(source)

        await until(lambda: client.connected, seconds=10, what="reattached")
        await until(
            lambda: bytes(consumer.ring) == entry.scrollback.bytes(),
            seconds=10,
            what="the output produced while detached was recovered",
        )
        assert client.reconnects == 1
        assert client.recoveries["delta"] == 1
        assert host.isalive() is True
        await consumer.stop()
    finally:
        await client.close()


async def test_a_stop_asked_for_while_unreachable_is_sent_on_reattach(
    supervisor: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to be honoured locally only, leaving the child running until a restart."""
    monkeypatch.setattr(supervisor_client, "RECONNECT_INITIAL_SECONDS", 1.0)
    _, source = add_session(supervisor.server, "s1")
    client = await daemon_client(supervisor)
    try:
        host = await attach(client, "s1")
        for connection in tuple(supervisor.server.connections):
            connection.close()
        await until(lambda: not client.connected, what="the drop was noticed")

        await asyncio.to_thread(host.stop, graceful=False)
        host.release()  # the session ends here, so its `remove` is dropped too
        assert not source.stopped, "nothing reached the supervisor while unreachable"

        await until(lambda: client.connected, seconds=10, what="reattached")
        await until(lambda: source.stopped, what="the stop was sent for real")
        await until(lambda: "s1" not in supervisor.server.sessions, what="and the entry removed")
    finally:
        await client.close()


# --------------------------------------------------------------------------
# Against a supervisor that predates all of it
# --------------------------------------------------------------------------


async def test_an_older_supervisor_is_driven_in_a_logged_compatibility_mode(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_client, "_DEGRADED_LOGGED", set())
    old = FakeSupervisor(tmp_path)
    await old.start()
    try:
        with caplog.at_level(logging.WARNING, logger="swe_mux.supervisor_client"):
            client = await SupervisorClient.connect(tmp_path)
            second = await SupervisorClient.connect(tmp_path)
        try:
            assert client.capabilities == frozenset()
            notices = [r for r in caplog.records if "predates output_gap" in r.getMessage()]
            assert len(notices) == 1, "said once per supervisor, not once per connection"
        finally:
            await second.close()
            await client.close()
    finally:
        await old.stop()


async def test_an_overflow_against_an_older_supervisor_resends_the_whole_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `since` to ask with, so the hole is filled by reseeding, never skipped."""
    monkeypatch.setattr(supervisor_client, "MIN_OUTPUT_OVERFLOW_BYTES", 1024)
    old = FakeSupervisor(tmp_path)
    await old.start()
    client = await SupervisorClient.connect(tmp_path)
    try:
        host = RemotePtyHost(client, "s1", appname="cmd.exe", cwd=".", max_scrollback=1024)
        host.prepare()
        await asyncio.to_thread(host.spawn)
        host._queue = asyncio.Queue(maxsize=1)
        for index in range(4):
            old.send({"t": "output", "sid": "s1"}, bytes([65 + index]) * 600)
        await until(lambda: bool(old.requests_of("subscribe")), what="it resubscribed")
        request = old.requests_of("subscribe")[-1]
        assert "since" not in request and "stream" not in request
        await until(lambda: client.recoveries["full"] == 1, what="the refill was applied")
        assert host.output_queue.get_nowait() == b"A" * 600
        await until(lambda: host.output_queue.qsize() == 1)
        assert isinstance(host.output_queue.get_nowait(), OutputReseed)
    finally:
        await client.close()
        await old.stop()


# --------------------------------------------------------------------------
# The host's own ordering, and the session's reseed
# --------------------------------------------------------------------------


def detached_host() -> RemotePtyHost:
    client = cast(Any, SimpleNamespace(recover=lambda _host: None, loop=None))
    host = RemotePtyHost(client, "s1")
    host._queue = asyncio.Queue()
    host._alive = True
    return host


def drain(host: RemotePtyHost) -> list[Any]:
    items: list[Any] = []
    while not host.output_queue.empty():
        items.append(host.output_queue.get_nowait())
    return items


async def test_an_exit_reported_during_a_recovery_waits_for_the_recovered_bytes() -> None:
    host = detached_host()
    host._begin_recovery()
    host._receive_wire_output(b"covered by the reply")
    host._mark_dead(3)
    assert drain(host) == [], "neither the dropped frame nor the exit may jump the reply"

    kind = host._apply_recovery(
        {"ok": True, "delta": True, "position": 40, "alive": False, "exit_code": 3}, b"tail"
    )
    assert kind == "delta"
    assert drain(host) == [b"tail", b""]
    assert host.exit_status() == 3
    assert host.stream_position == 40


async def test_a_full_refill_is_queued_in_order_as_a_reseed() -> None:
    host = detached_host()
    host._offer(b"before")
    host._begin_recovery()
    kind = host._apply_recovery({"ok": True, "position": 900, "alive": True}, b"whole buffer")
    assert kind == "full"
    assert drain(host) == [b"before", OutputReseed(b"whole buffer", 900)]
    assert host.stream_position == 900


async def test_an_exit_waiting_on_a_recovery_is_delivered_when_none_is_coming() -> None:
    host = detached_host()
    host._begin_recovery()
    host._mark_dead(None)
    host._abandon_recovery()
    assert drain(host) == [b""]


async def test_the_session_adopts_a_reseed_and_resyncs_its_terminals() -> None:
    record = SessionRecord("s1", "s1", "project", "shell", "native", ".", "cmd.exe", [])
    session = Session(record, cast(Any, SimpleNamespace()), ShellAdapter(), 1024, "secret")
    session.scrollback.append(b"old bytes \x1b[?1049h")
    session.screen.feed(b"\x1b[?1049h")
    subscriber = session.subscribe()

    session.reseed_from_supervisor(OutputReseed(b"\x1b[?2004hnew ring", 5000))

    assert session.scrollback.bytes() == b"\x1b[?2004hnew ring"
    assert session.scrollback.position == 5000
    # Derived state is the new stream's, not a leftover from before the hole.
    assert session.screen.mode is None
    assert session.bracketed_paste.enabled is True
    assert subscriber.resync_pending is True
    assert subscriber.queue.get_nowait() == {"type": "resync"}


async def test_the_fanout_applies_a_reseed_between_the_chunks_around_it() -> None:
    queue: asyncio.Queue[Any] = asyncio.Queue()
    for item in (b"one", OutputReseed(b"seed", 10), b"two", b""):
        queue.put_nowait(item)
    order: list[Any] = []
    record = SessionRecord("mux", "shell", "default", "shell", "native", ".", "cmd.exe", [])
    record.startup_timing_ms["first_output"] = 1.0
    session = SimpleNamespace(
        pty=SimpleNamespace(output_queue=queue, first_output_at=None),
        record=record,
        startup_started_at=0.0,
        stopping=True,
        output_window=deque(),
        osc7=SimpleNamespace(feed=lambda _chunk: []),
        osc_signals=SimpleNamespace(feed=lambda _chunk: None),
        osc133=SimpleNamespace(feed=lambda _chunk: []),
        # The fanout feeds the terminal core's stream directly.
        terminal=SimpleNamespace(
            stream=SimpleNamespace(
                screen=ScreenModeParser(),
                bracketed_paste=BracketedPasteParser(),
                sticky_modes=StickyModeParser(),
                scrollback=ScrollbackBuffer(1024),
                publish_output=order.append,
            )
        ),
        reseed_from_supervisor=lambda reseed: order.append(reseed),
        publish_update=lambda: None,
        tasks=set(),
    )
    manager = cast(Any, SessionManager.__new__(SessionManager))
    await SessionManager._fanout(manager, session)
    assert order == [b"one", OutputReseed(b"seed", 10), b"two"]
