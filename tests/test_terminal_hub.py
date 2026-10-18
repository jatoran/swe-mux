"""DAEMON_LOAD_ISOLATION item 27 - the terminal core against both hubs.

Every test in the first half runs twice: with the core in process, and with it in a
terminal gateway that the daemon reaches over a real `framed_ipc` connection and
mirrors on `Session.terminal`. The behaviour a browser and the daemon see must be
the same either way; the only allowed difference is *when* the daemon's mirror
catches up, which is why those assertions wait rather than read.

The second half is the gateway's own contract: acknowledged coalescing, a reconnect
that resyncs everything once, a restarted daemon that keeps what the browsers built,
the protocol version, and the daemon refusing to serve a terminal the gateway holds.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.composer_input import PendingSubmit
from swe_mux.framed_ipc import HandshakeRefused, connect
from swe_mux.models import SessionRecord
from swe_mux.routes.pty import pty_ws
from swe_mux.session import Session
from swe_mux.terminal_core import (
    GATEWAY_PROTOCOL_VERSION,
    RemoteHub,
    RemoteTerminal,
    TerminalStream,
    WriteCondition,
    WriteRefused,
)
from swe_mux.terminal_core.protocol import CLOSE_USE_DAEMON, CLOSE_USE_GATEWAY
from tests.support.settle import until
from tests.support.terminal_rig import (
    SESSION_ID,
    DaemonHost,
    FakeSocket,
    LocalRig,
    RemoteRig,
    Rig,
    descriptor,
)


@pytest.fixture(params=["local", "remote"])
async def rig(request: pytest.FixtureRequest) -> AsyncIterator[Rig]:
    built: Rig = LocalRig() if request.param == "local" else RemoteRig()
    await built.start()
    try:
        yield built
    finally:
        await built.stop()


# --- the same behaviour wherever the core runs ------------------------------------


async def test_a_keystroke_is_written_and_accounted_where_the_daemon_reads_it(
    rig: Rig,
) -> None:
    browser = rig.connect("c1")
    await browser.attach()
    await browser.claim()
    browser.type("a")

    await until(lambda: rig.pty.writes == ["a"], what="the keystroke reached the PTY")
    await until(lambda: rig.terminal.input_revision == 1, what="the daemon saw the revision")
    age = rig.terminal.last_input_age()
    assert age is not None and age < 5
    await until(lambda: rig.host.typed == ["a"], what="the side effects ran")
    assert rig.host.submits == ["a"]


async def test_each_keystroke_reaches_the_daemon_once_and_in_order(rig: Rig) -> None:
    """Interrupt intent matches a lone `\\x03`, so writes cannot be merged."""
    browser = rig.connect("c1")
    await browser.attach()
    await browser.claim()
    for key in ("\x03", "x", "y", "\r"):
        browser.type(key)

    await until(lambda: len(rig.host.typed) == 4, what="four side effects")
    assert rig.host.typed == ["\x03", "x", "y", "\r"]
    assert rig.host.submits == ["\x03", "x", "y", "\r"]
    await until(lambda: rig.terminal.input_revision == 4, what="four revisions")


async def test_a_pointer_click_moves_the_quiet_clock_and_not_the_revision(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach()
    await browser.claim()
    browser.type("\x1b[<0;10;5M")

    await until(lambda: rig.terminal.last_input_age() is not None, what="the click was seen")
    assert rig.terminal.input_revision == 0
    assert rig.host.typed == []


async def test_a_conditional_write_is_refused_after_a_keystroke_lands(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach()
    await browser.claim()
    judged = rig.terminal.input_revision
    browser.type("q")
    # In process the keystroke is applied before the next await returns; in the
    # gateway it lands there whether or not the daemon's mirror has heard of it,
    # which is exactly the race the condition exists for.
    await until(lambda: "q" in rig.pty.writes, what="the keystroke landed")

    with pytest.raises(WriteRefused) as refused:
        await rig.terminal.write_when(
            "paste", condition=WriteCondition(input_revision=judged), accounted=True
        )
    assert refused.value.reason == "input_revision_moved"
    assert "paste" not in rig.pty.writes


async def test_a_condition_that_still_holds_is_written_and_accounted(rig: Rig) -> None:
    judged = rig.terminal.input_revision
    await rig.terminal.write_when(
        "paste",
        condition=WriteCondition(input_revision=judged, operator_quiet_seconds=2.0),
        accounted=True,
    )
    assert rig.pty.writes == ["paste"]
    # The answer carries the gateway's accounting, so the daemon has it on return.
    assert rig.terminal.input_revision == judged + 1


async def test_operator_input_is_accounted_on_both_sides(rig: Rig) -> None:
    rig.terminal.write_operator_input("hi")
    assert rig.terminal.input_revision == 1  # at once, for readiness
    await until(lambda: rig.pty.writes == ["hi"], what="the write arrived")
    await until(lambda: rig.core.input_revision == 1, what="the core counted it")


async def test_an_unaccounted_write_moves_nothing(rig: Rig) -> None:
    rig.terminal.write(b"\x03")
    await until(lambda: rig.pty.writes == [b"\x03"], what="the write arrived")
    assert rig.terminal.input_revision == 0
    assert rig.core.input_revision == 0


async def test_a_pending_submit_set_by_the_daemon_blocks_a_condition(rig: Rig) -> None:
    rig.terminal.pending_submit = PendingSubmit(message_id="m1", at=time.time(), byte_count=4)
    await until(lambda: rig.core.pending_submit is not None, what="the core heard the mark")
    with pytest.raises(WriteRefused) as refused:
        await rig.terminal.write_when(
            "next", condition=WriteCondition(no_pending_submit=True), accounted=True
        )
    assert refused.value.reason == "unsubmitted_delivery_in_composer"

    rig.terminal.pending_submit = None
    await until(lambda: rig.core.pending_submit is None, what="the mark cleared")
    await rig.terminal.write_when(
        "next", condition=WriteCondition(no_pending_submit=True), accounted=True
    )
    assert rig.pty.writes == ["next"]


async def test_a_snapshot_update_reaches_attached_browsers(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach()
    rig.terminal.publish_update(
        {"type": "update", "snapshot": {"id": SESSION_ID, "state": "working"}, "revision": 5}
    )
    await until(lambda: bool(browser.frames("update")), what="the update arrived")
    update = browser.frames("update")[-1]
    assert update["revision"] == 5
    assert update["snapshot"]["state"] == "working"
    assert update["snapshot"]["_snapshot_generation"] == "gen-1"


async def test_output_reaches_attached_browsers_and_their_replay(rig: Rig) -> None:
    first = rig.connect("c1")
    await first.attach()
    rig.feed(b"hello")
    await until(lambda: b"hello" in b"".join(first.socket.binary), what="live output")
    second = rig.connect("c2")
    await second.attach()
    assert b"hello" in b"".join(second.socket.binary)


async def test_the_leading_device_decides_a_passive_claim(rig: Rig) -> None:
    phone = rig.connect("phone")
    await phone.attach(40, 20)
    granted = await phone.claim("phone", reason="passive")
    assert granted["active"] is True
    await rig.set_leader("phone")

    desktop = rig.connect("desktop")
    await desktop.attach(120, 40)
    refused = await desktop.claim("desktop", reason="passive")
    assert refused["active"] is False
    assert refused["reason"] == "denied_device_in_use"
    await until(lambda: rig.terminal.input_claim_denials == 1, what="the denial was mirrored")
    await until(
        lambda: [claim["verdict"] for claim in rig.terminal.claim_log]
        == ["granted_unowned", "denied_device_in_use"],
        what="the claim log was mirrored",
    )


async def test_ownership_geometry_mode_and_attachment_reach_the_daemon(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach(132, 41)
    await browser.claim("desktop")
    browser.send({"type": "terminal_state", "mode": "alternate"})

    await until(lambda: rig.terminal.terminal_mode == "alternate", what="the mode")
    assert rig.terminal.input_owner_device == "desktop"
    assert rig.terminal.input_owner == "c1"
    assert rig.terminal.geometry == (132, 41)
    assert rig.terminal.viewports == {"c1": (132, 41)}
    assert rig.terminal.attached_connections == 1
    assert rig.pty.resizes == [(132, 41)]

    await browser.close()
    await until(lambda: rig.terminal.attached_connections == 0, what="the detach")
    assert rig.terminal.input_owner is None


async def test_a_daemon_repaint_pulses_the_pty(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach(90, 30)
    await until(lambda: rig.pty.resizes == [(90, 30)], what="the geometry")
    assert await rig.terminal.repaint_current_geometry() is True
    assert rig.pty.resizes == [(90, 30), (89, 30), (90, 30)]


async def test_events_reach_the_daemon_event_log(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach()
    await until(lambda: "terminal_attached" in rig.host.event_kinds(), what="attached")
    attached = next(event for event in rig.host.events if event[0] == "terminal_attached")
    assert attached[1] == "daemon"
    assert attached[2] == {"connections": 1}


async def test_broadcast_typing_is_delivered_by_the_daemon(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach()
    await browser.claim()
    browser.type("ls\r", broadcast=True)
    await until(lambda: rig.host.broadcasts == ["ls\r"], what="the broadcast")
    assert rig.pty.writes == ["ls\r"]


async def test_a_session_reads_its_terminal_through_the_same_views(rig: Rig) -> None:
    """`Session` keeps its attribute names as views onto whichever terminal it holds."""
    record = SessionRecord(SESSION_ID, "claude-s1", "default", "claude", "s1", ".", "claude", [])
    session = Session(record, cast(Any, rig.pty), cast(Any, SimpleNamespace()), 1024, "secret")
    session.terminal = rig.terminal
    browser = rig.connect("c1")
    await browser.attach()
    await browser.claim()
    browser.type("z")

    await until(lambda: session.input_revision == 1, what="the view read the revision")
    assert session.attended is True
    assert session.input_owner_device == "desktop"
    session.pending_submit = PendingSubmit(message_id="m2", at=time.time(), byte_count=1)
    await until(lambda: rig.core.pending_submit is not None, what="a view wrote through")


# --- the gateway's own contract --------------------------------------------------


async def test_a_stalled_daemon_costs_one_summary_per_session() -> None:
    rig = RemoteRig()
    await rig.start()
    try:
        held: list[dict[str, Any]] = []
        apply = rig.hub._apply_state

        def withhold(peer: Any, header: dict[str, Any]) -> None:
            held.append(header)

        rig.hub._apply_state = withhold  # type: ignore[method-assign]
        browser = rig.connect("c1")
        await browser.attach()
        await browser.claim()
        await until(lambda: bool(held), what="the first summary")
        session = rig.gateway.sessions[SESSION_ID]
        assert session.in_flight
        before = len(held)
        for key in "abcdefghij":
            browser.type(key)
        await until(lambda: rig.pty.writes == list("abcdefghij"), what="all ten written")
        await asyncio.sleep(0.05)  # a quiet window: nothing more may be sent unacknowledged

        assert len(held) == before, "a summary went out while one was unacknowledged"
        assert [entry[1] for entry in session.writes] == list("abcdefghij")

        rig.hub._apply_state = apply  # type: ignore[method-assign]
        assert rig.hub.peer is not None
        apply(rig.hub.peer, held[-1])  # the daemon catches up and acknowledges
        await until(lambda: len(rig.host.typed) == 10, what="the folded writes applied")
        assert rig.host.typed == list("abcdefghij")
        assert rig.terminal.input_revision == 10
        await until(lambda: not session.writes, what="the gateway let them go")
    finally:
        await rig.stop()


async def test_a_reconnect_resyncs_the_full_input_state_once() -> None:
    rig = RemoteRig()
    await rig.start()
    try:
        browser = rig.connect("c1")
        await browser.attach()
        await browser.claim()
        browser.type("a")
        await until(lambda: rig.host.typed == ["a"], what="the first keystroke applied")

        await rig.hub.close()
        await until(lambda: rig.gateway.daemon is None, what="the gateway saw it go")
        browser.type("b")
        browser.type("c")
        await until(lambda: rig.pty.writes == ["a", "b", "c"], what="typing went on")

        await rig.hub.connect("127.0.0.1", rig.port, token=rig.token)
        await until(lambda: rig.host.typed == ["a", "b", "c"], what="the missed writes")
        assert rig.terminal.input_revision == 3
        await asyncio.sleep(0.05)
        assert rig.host.typed == ["a", "b", "c"], "a write was applied twice"
    finally:
        await rig.stop()


async def test_writes_resent_after_a_lost_acknowledgement_apply_once() -> None:
    """The same daemon applied a summary, then lost the connection before its ack
    got out: the gateway resends, and the sequence numbers stop a second apply."""
    rig = RemoteRig()
    await rig.start()
    try:
        apply = rig.hub._apply_state

        def apply_without_ack(peer: Any, header: dict[str, Any]) -> None:
            if not header["writes"]:
                apply(peer, header)  # summaries before the keystroke go through whole
                return
            terminal = rig.hub.terminals[str(header["sid"])]
            terminal.merge(header)
            terminal.apply_writes(header["first_write_seq"], header["writes"])

        rig.hub._apply_state = apply_without_ack  # type: ignore[method-assign]
        browser = rig.connect("c1")
        await browser.attach()
        await browser.claim()
        browser.type("a")
        await until(lambda: rig.host.typed == ["a"], what="applied, not acknowledged")
        session = rig.gateway.sessions[SESSION_ID]
        assert [entry[1] for entry in session.writes] == ["a"]

        await rig.hub.close()
        rig.hub._apply_state = apply  # type: ignore[method-assign]
        await rig.hub.connect("127.0.0.1", rig.port, token=rig.token)
        await until(lambda: not session.writes, what="the resend was acknowledged")
        assert rig.host.typed == ["a"], "the resent write was applied twice"
        browser.type("b")
        await until(lambda: rig.host.typed == ["a", "b"], what="typing carries on")
    finally:
        await rig.stop()


async def test_a_restarted_daemon_keeps_what_the_browsers_built() -> None:
    rig = RemoteRig()
    await rig.start()
    try:
        browser = rig.connect("c1")
        await browser.attach(100, 40)
        await browser.claim("desktop")
        browser.type("a")
        await until(lambda: rig.terminal.input_revision == 1, what="the first daemon saw it")
        await rig.hub.close()

        # A new daemon: a new hub and a new mirror that knows nothing yet.
        successor = DaemonHost()
        hub = RemoteHub(generation="gen-2")
        terminal = hub.adopt(stream=rig.daemon_stream, descriptor=descriptor(), host=successor)
        await hub.connect("127.0.0.1", rig.port, token=rig.token)
        try:
            await until(lambda: terminal.input_revision == 1, what="the revision resynced")
            assert terminal.input_owner_device == "desktop"
            assert terminal.geometry == (100, 40)
            assert terminal.attached_connections == 1
            assert rig.gateway.generation == "gen-2"
            browser.type("b")
            await until(lambda: successor.typed == ["b"], what="the new daemon gets typing")
        finally:
            await hub.close()
    finally:
        await rig.stop()


async def test_the_daemon_names_the_sessions_it_serves() -> None:
    rig = RemoteRig()
    await rig.start()
    try:
        browser = rig.connect("c1")
        await browser.attach()
        rig.hub.release(SESSION_ID)
        await until(lambda: SESSION_ID not in rig.gateway.sessions, what="the gateway let go")
        await until(lambda: browser.socket.closed is not None, what="the browser was told")
        assert browser.socket.closed is not None
        assert browser.socket.closed[0] == CLOSE_USE_DAEMON
    finally:
        await rig.stop()


async def test_a_session_the_new_daemon_does_not_serve_is_dropped() -> None:
    rig = RemoteRig()
    await rig.start()
    try:
        await rig.hub.close()
        hub = RemoteHub()
        await hub.connect("127.0.0.1", rig.port, token=rig.token)
        try:
            await until(lambda: not rig.gateway.sessions, what="the stale session dropped")
        finally:
            await hub.close()
    finally:
        await rig.stop()


async def test_diagnostics_answer_from_the_gateway() -> None:
    rig = RemoteRig()
    await rig.start()
    try:
        browser = rig.connect("c1")
        await browser.attach(80, 24)
        await browser.claim("phone")
        report = await rig.hub.diagnostics(SESSION_ID)
        assert report is not None
        one = report["sessions"][SESSION_ID]
        assert one["owner_device"] == "phone"
        assert one["geometry"] == [80, 24]
        assert one["attached"] == 1
    finally:
        await rig.stop()


async def test_a_protocol_mismatch_is_refused() -> None:
    rig = RemoteRig()
    await rig.start()
    try:
        with pytest.raises(HandshakeRefused, match="protocol mismatch"):
            await connect(
                "127.0.0.1", rig.port, token=rig.token, protocol=GATEWAY_PROTOCOL_VERSION + 1
            )
        with pytest.raises(HandshakeRefused, match="invalid token"):
            await connect("127.0.0.1", rig.port, token="wrong", protocol=GATEWAY_PROTOCOL_VERSION)
    finally:
        await rig.stop()


async def test_a_disconnected_gateway_refuses_rather_than_drops_a_conditional_write() -> None:
    from swe_mux.terminal_core import TerminalUnavailable

    rig = RemoteRig()
    await rig.start()
    try:
        await rig.hub.close()
        with pytest.raises(TerminalUnavailable):
            await rig.terminal.write_when("x", condition=WriteCondition(), accounted=True)
        with pytest.raises(TerminalUnavailable):
            rig.terminal.write_operator_input("x")
        assert rig.pty.writes == []
    finally:
        await rig.stop()


async def test_the_daemon_refuses_to_serve_a_terminal_the_gateway_holds() -> None:
    record = SessionRecord(SESSION_ID, "claude-s1", "default", "claude", "s1", ".", "claude", [])
    session = Session(record, cast(Any, SimpleNamespace()), cast(Any, None), 1024, "secret")
    stream = TerminalStream(
        1024,
        attach_replay_bytes=None,
        stream_id="s",
        remote_stream=lambda: "",
        rebuilt=lambda: False,
    )
    session.terminal = RemoteTerminal(
        RemoteHub(), stream=stream, descriptor=descriptor(), host=DaemonHost()
    )
    app = web.Application()
    app[keys.SESSIONS] = cast(Any, SimpleNamespace(resolve=lambda _sid: session))
    app.router.add_get("/pty/{sid}", pty_ws)
    async with TestClient(TestServer(app)) as client:
        try:
            ws = await client.ws_connect(f"/pty/{SESSION_ID}")
        except WSServerHandshakeError as refused:  # pragma: no cover - the upgrade is accepted
            raise AssertionError(f"the upgrade itself was refused: {refused.status}") from None
        message = await ws.receive()
        assert ws.close_code == CLOSE_USE_GATEWAY, message
        await ws.close()


def test_the_fake_socket_is_a_terminal_socket() -> None:
    """The rig's socket satisfies the protocol the core types against."""
    from swe_mux.terminal_core import TerminalSocket

    socket: TerminalSocket = FakeSocket()
    assert socket is not None
