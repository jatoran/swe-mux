"""Subscribe-since, stream identity, terminal scope, and the supervisor's notices.

`DAEMON_LOAD_ISOLATION.md` item 13, the supervisor half of what the terminal
gateway needs: resubscribing with a delta instead of a whole scrollback, cursors
that cannot be answered from a different incarnation of a session id, a
connection that can drive terminals without ever seeing a session's secrets, and
true geometry and removal when another connection is the one that acted.

Every message here is additive. A client that sends none of the new fields gets
the replies it always got plus fields it ignores, which is what lets a daemon that
predates them drive this supervisor (the real-ConPTY suite in
`test_pty_supervisor.py` runs the unmodified client end to end).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from swe_mux.supervisor import (
    CAPABILITY_NOTICES,
    CAPABILITY_SUBSCRIBE_SINCE,
    CAPABILITY_TERMINAL_SCOPE,
    SCOPE_TERMINAL,
)
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


# --------------------------------------------------------------------------
# Stream identity
# --------------------------------------------------------------------------


async def test_every_session_info_and_subscribe_reply_names_its_stream(
    supervisor: Running,
) -> None:
    entry, _ = add_session(supervisor.server, "s1")
    client, hello = await supervisor.connect()
    (info,) = hello["sessions"]
    assert info["stream"] == entry.stream
    assert entry.stream.startswith("s1@")

    reply, _ = await client.request({"t": "subscribe", "sid": "s1"})
    assert reply["stream"] == entry.stream
    listed, _ = await client.request({"t": "list"})
    assert listed["sessions"][0]["stream"] == entry.stream


async def test_a_new_incarnation_of_a_session_id_is_a_new_stream(supervisor: Running) -> None:
    """A respawn reuses the id and restarts the byte positions; the stream must differ."""
    first, _ = add_session(supervisor.server, "s1")
    client, _ = await supervisor.connect()
    await client.request({"t": "remove", "sid": "s1"})
    await until(lambda: "s1" not in supervisor.server.sessions)
    second, _ = add_session(supervisor.server, "s1")
    assert second.stream != first.stream


# --------------------------------------------------------------------------
# Subscribe since
# --------------------------------------------------------------------------


async def test_subscribe_since_answers_with_the_delta_while_it_is_retained(
    supervisor: Running,
) -> None:
    entry, host = add_session(supervisor.server, "s1", scrollback_bytes=1024 * 1024)
    client, _ = await supervisor.connect()
    first, _ = await client.request({"t": "subscribe", "sid": "s1"})
    await pour(host, 8)
    await drained(host)

    since = first["position"] + 3 * CHUNK + 7
    reply, delta = await client.request(
        {"t": "subscribe", "sid": "s1", "since": since, "stream": entry.stream}
    )
    assert reply["delta"] is True
    assert delta == entry.scrollback.bytes()[since:]
    assert reply["position"] == entry.scrollback.position == 8 * CHUNK

    # A cursor at the end is an empty delta, not a reason to resend everything.
    reply, delta = await client.request(
        {"t": "subscribe", "sid": "s1", "since": reply["position"], "stream": entry.stream}
    )
    assert reply["delta"] is True
    assert delta == b""


@pytest.mark.parametrize(
    "case",
    ["evicted", "from_the_future", "other_stream", "no_stream", "not_a_number", "negative"],
)
async def test_subscribe_since_falls_back_to_the_whole_buffer_when_it_cannot_be_honoured(
    supervisor: Running, case: str
) -> None:
    # Retention of 4 chunks behind 16 written: positions below 12 chunks are gone.
    entry, host = add_session(supervisor.server, "s1", scrollback_bytes=4 * CHUNK)
    client, _ = await supervisor.connect()
    await pour(host, 16)
    await drained(host)

    since: object = {
        "evicted": 4 * CHUNK,
        "from_the_future": 17 * CHUNK,
        "other_stream": 14 * CHUNK,
        "no_stream": 14 * CHUNK,
        "not_a_number": "14",
        "negative": -1,
    }[case]
    request: dict[str, object] = {"t": "subscribe", "sid": "s1", "since": since}
    if case == "other_stream":
        request["stream"] = "s1@0.deadbeef"
    elif case != "no_stream":
        request["stream"] = entry.stream
    reply, payload = await client.request(request)
    assert reply["delta"] is False
    assert payload == entry.scrollback.bytes()
    assert reply["position"] == entry.scrollback.position


# --------------------------------------------------------------------------
# Terminal scope
# --------------------------------------------------------------------------


async def test_a_terminal_scope_connection_never_sees_session_secrets(
    supervisor: Running,
) -> None:
    entry, _ = add_session(supervisor.server, "s1")
    entry.meta = {"hook_secret": "s3cret", "mcp_token": "t0ken", "record": {"id": "s1"}}

    terminal, hello = await supervisor.connect(scope=SCOPE_TERMINAL)
    assert hello["ok"] is True
    assert hello["scope"] == SCOPE_TERMINAL
    assert "meta" not in hello["sessions"][0]
    listed, _ = await terminal.request({"t": "list"})
    assert "meta" not in listed["sessions"][0]
    subscribed, _ = await terminal.request({"t": "subscribe", "sid": "s1"})
    assert "meta" not in subscribed
    assert "s3cret" not in repr((hello, listed, subscribed))

    # The daemon's own connection still gets the mirror it rebuilds state from.
    daemon, daemon_hello = await supervisor.connect()
    assert daemon_hello["scope"] == "daemon"
    assert daemon_hello["sessions"][0]["meta"]["hook_secret"] == "s3cret"


@pytest.mark.parametrize(
    "kind",
    [
        "spawn",
        "spawn_status",
        "set_meta",
        "set_graceful_exit",
        "job_pids",
        "stop",
        "release",
        "remove",
        "reap_all_and_exit",
    ],
)
async def test_a_terminal_scope_connection_may_only_drive_terminals(
    supervisor: Running, kind: str
) -> None:
    entry, host = add_session(supervisor.server, "s1")
    entry.meta = {"record": {"id": "s1"}}
    terminal, _ = await supervisor.connect(scope=SCOPE_TERMINAL)

    refused, _ = await terminal.request({"t": kind, "sid": "s1", "meta": {}, "keys": "x"})
    assert refused["ok"] is False
    assert "terminal" in refused["error"]

    # Refused, not disconnected, and nothing it asked for happened.
    pong, _ = await terminal.request({"t": "ping"})
    assert pong["ok"] is True
    assert "s1" in supervisor.server.sessions
    assert entry.meta == {"record": {"id": "s1"}}
    assert host.alive and not host.stopped
    assert not supervisor.server.exit_event.is_set()


async def test_a_terminal_scope_connection_drives_the_terminal(supervisor: Running) -> None:
    entry, host = add_session(supervisor.server, "s1")
    terminal, _ = await supervisor.connect(scope=SCOPE_TERMINAL)
    await terminal.request({"t": "subscribe", "sid": "s1"})
    await terminal.notify({"t": "write", "sid": "s1"}, b"echo hi\r")
    await terminal.notify({"t": "resize", "sid": "s1", "cols": 100, "rows": 40})
    await until(lambda: host.writes == [b"echo hi\r"] and (host.cols, host.rows) == (100, 40))
    await pour(host, 1)
    await drained(host)
    # `unsubscribe` is fire-and-forget; the ping's reply orders everything before it.
    await terminal.notify({"t": "unsubscribe", "sid": "s1"})
    await terminal.request({"t": "ping"})
    assert terminal.output("s1") == entry.scrollback.bytes()


async def test_an_unknown_scope_is_refused_and_disconnected(supervisor: Running) -> None:
    client, reply = await supervisor.connect(scope="admin")
    assert reply["ok"] is False
    assert await client.reader.read() == b""


async def test_the_capabilities_name_what_this_supervisor_understands(
    supervisor: Running,
) -> None:
    _, hello = await supervisor.connect()
    assert {CAPABILITY_SUBSCRIBE_SINCE, CAPABILITY_TERMINAL_SCOPE, CAPABILITY_NOTICES} <= set(
        hello["capabilities"]
    )


# --------------------------------------------------------------------------
# Notices
# --------------------------------------------------------------------------


async def test_a_resize_is_announced_to_every_other_subscriber(supervisor: Running) -> None:
    _, host = add_session(supervisor.server, "s1")
    resizer, _ = await supervisor.connect()
    watcher, _ = await supervisor.connect(scope=SCOPE_TERMINAL)
    bystander, _ = await supervisor.connect()
    await resizer.request({"t": "subscribe", "sid": "s1"})
    await watcher.request({"t": "subscribe", "sid": "s1"})

    # Out of range on purpose: the notice carries what the host applied.
    await resizer.notify({"t": "resize", "sid": "s1", "cols": 1, "rows": 0})
    await until(lambda: (host.cols, host.rows) == (2, 1))
    for client in (resizer, watcher, bystander):
        await client.request({"t": "ping"})

    assert watcher.unsolicited("resized") == [{"t": "resized", "sid": "s1", "cols": 2, "rows": 1}]
    # The resizer knows what it asked for, and an echo would race its next resize.
    assert resizer.unsolicited("resized") == []
    assert bystander.unsolicited("resized") == []


async def test_a_removal_is_announced_before_the_fanout_stops(supervisor: Running) -> None:
    entry, host = add_session(supervisor.server, "s1")
    remover, _ = await supervisor.connect()
    watcher, _ = await supervisor.connect(scope=SCOPE_TERMINAL)
    await remover.request({"t": "subscribe", "sid": "s1"})
    await watcher.request({"t": "subscribe", "sid": "s1"})

    reply, _ = await remover.request({"t": "remove", "sid": "s1"})
    assert reply["ok"] is True
    await watcher.request({"t": "ping"})

    assert watcher.unsolicited("removed") == [{"t": "removed", "sid": "s1"}]
    assert remover.unsolicited("removed") == []
    assert "s1" not in supervisor.server.sessions
    assert entry.fanout_task is not None and entry.fanout_task.done()
    assert host.stopped, "a live session is stopped, not released, by a removal"


async def test_a_client_sending_none_of_the_new_fields_gets_the_old_replies(
    supervisor: Running,
) -> None:
    """Additive only: every key an older daemon reads is still there, meaning the same."""
    entry, host = add_session(supervisor.server, "s1")
    entry.meta = {"record": {"id": "s1"}}
    client, hello = await supervisor.connect()
    assert {"id", "ok", "protocol", "pid", "sessions"} <= set(hello)
    info = hello["sessions"][0]
    assert {
        "sid",
        "alive",
        "exit_code",
        "pid",
        "cols",
        "rows",
        "reaper_assignment",
        "meta",
        "started_at",
    } <= set(info)
    await pour(host, 2)
    await drained(host)
    reply, replay = await client.request({"t": "subscribe", "sid": "s1"})
    assert {"alive", "exit_code", "position", "meta"} <= set(reply)
    assert replay == entry.scrollback.bytes()
    assert reply["position"] == entry.scrollback.position
