from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from types import MethodType, SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.adapters import ShellAdapter
from swe_mux.git_projects import ProjectIdentity
from swe_mux.models import SessionRecord
from swe_mux.runtime_cwd import Osc7Parser, Osc133Parser, OscSignalParser
from swe_mux.screen_mode import BracketedPasteParser, ScreenModeParser, StickyModeParser
from swe_mux.server import session_startup_metrics
from swe_mux.session import ScrollbackBuffer, Session, SessionManager


def _fake_session(max_bytes: int = 32) -> Any:
    fake = cast(Any, Session.__new__(Session))
    fake.scrollback = ScrollbackBuffer(max_bytes)
    fake.subscribers = set()
    fake.record = cast(
        Any,
        type(
            "Record",
            (),
            # `cold` is read by the delta-attach decision: a recovered session's
            # ring was rebuilt from disk, so its positions describe a different
            # stream and a delta across that boundary would corrupt the terminal.
            # These fixtures are all live sessions.
            {"snapshot": lambda self: {"state": "running"}, "cold": False},
        )(),
    )
    fake.revision = 0
    # Replay is exact here: these fixtures pin the *retention* contract, and a
    # replay budget would silently trim the very boundaries they assert on.
    fake.attach_replay_bytes = None
    fake.screen = ScreenModeParser()
    fake.bracketed_paste = BracketedPasteParser()
    fake.sticky_modes = StickyModeParser()
    return fake


def test_scrollback_retains_exact_tail_across_chunk_boundaries() -> None:
    buffer = ScrollbackBuffer(5)
    buffer.append(b"abc")
    buffer.append(b"defg")
    assert buffer.bytes() == b"cdefg"


def test_scrollback_retains_tail_of_oversized_chunk() -> None:
    buffer = ScrollbackBuffer(4)
    buffer.append(b"old")
    buffer.append(b"012345")
    assert buffer.bytes() == b"2345"


def test_scrollback_zero_capacity_retains_nothing() -> None:
    buffer = ScrollbackBuffer(0)
    buffer.append(b"ignored")
    assert buffer.bytes() == b""


def test_tail_bytes_reads_the_end_without_joining_the_buffer() -> None:
    # Every screen reader wants the last few KiB of a buffer that holds megabytes.
    # Routing that through bytes() joined the whole retention first, on a 5-second
    # watchdog loop, per agent session -- worst where buffers are fullest, which is
    # Codex (alternate_screen=never puts its transcript in scrollback).
    buffer = ScrollbackBuffer(1024)
    for index in range(20):
        buffer.append(f"chunk{index:02d}|".encode())
    whole = buffer.bytes()
    for count in (0, 1, 7, 8, 9, 50, len(whole) - 1, len(whole), len(whole) + 100):
        assert buffer.tail_bytes(count) == (whole[-count:] if count else b""), count
    assert buffer.tail_bytes(-5) == b""


def test_tail_bytes_touches_only_the_chunks_it_needs() -> None:
    # The whole point: it must not walk (or join) the chunks it is not returning.
    buffer = ScrollbackBuffer(1 << 20)
    reads: list[int] = []

    class CountingChunk(bytes):
        def __len__(self) -> int:  # noqa: D105 - counts access, not behavior
            reads.append(1)
            return super().__len__()

    for _index in range(500):
        buffer.append(CountingChunk(b"0123456789"))
    reads.clear()
    assert len(buffer.tail_bytes(25)) == 25
    assert len(reads) <= 8, f"walked {len(reads)} chunks to read 25 bytes of 5000"


def test_bytes_since_uses_the_same_right_anchored_walk() -> None:
    buffer = ScrollbackBuffer(1024)
    buffer.append(b"aaaa")
    buffer.append(b"bbbb")
    assert buffer.bytes_since(0) == b"aaaabbbb"
    assert buffer.bytes_since(4) == b"bbbb"
    assert buffer.bytes_since(8) == b""
    assert buffer.bytes_since(99) == b""
    # A cursor older than what is still retained returns everything retained.
    trimmed = ScrollbackBuffer(4)
    trimmed.append(b"aaaa")
    trimmed.append(b"bbbb")
    assert trimmed.bytes_since(0) == b"bbbb"


def test_replay_tail_is_bounded_without_touching_retention() -> None:
    # Retention and replay are separate budgets: the daemon keeps history to scroll
    # back through, while an attaching client has to parse everything it is handed
    # before it can render anything.
    buffer = ScrollbackBuffer(1024)
    buffer.append(b"line one\nline two\nline three\n")
    assert buffer.tail(None) == buffer.bytes()
    assert buffer.tail(10_000) == buffer.bytes()
    assert buffer.tail(len(buffer.bytes())) == buffer.bytes()
    # Trimmed windows resume after the next newline so the client can never begin
    # parsing inside an escape sequence.
    assert buffer.tail(14) == b"line three\n"


def test_a_trimmed_replay_with_no_line_boundary_is_still_bounded() -> None:
    # One enormous line (a TUI repainting with cursor moves and no newline) has no
    # safe cut, and truncating the *bound* instead would defeat it.
    buffer = ScrollbackBuffer(1024)
    buffer.append(b"x" * 600)
    assert buffer.tail(100) == b"x" * 100


def test_a_bounded_replay_restates_the_alternate_screen_it_cut_off() -> None:
    # The switch is written once at startup and never repeated, so a window that
    # begins after it would leave the client painting a full-screen TUI into its
    # *normal* buffer — every repaint growing scrollback instead of overwriting one
    # screen, which is the exact cost the bound exists to remove.
    fake = _fake_session(4096)
    fake.attach_replay_bytes = 32
    fake.screen.feed(b"\x1b[?1049h")
    fake.scrollback.append(b"\x1b[?1049h" + b"repaint\n" * 40)
    replay = Session.replay_bytes(fake)
    assert replay.startswith(b"\x1b[?1049h")
    assert len(replay) < 64


def test_replay_window_truncation_is_reported_without_materializing_the_bytes() -> None:
    # What decides whether an alternate-screen child is pulsed into restating its screen
    # after an attach: a window over a differential frame stream is complete only if a
    # full repaint happened to fall inside it, while a replay of everything retained is
    # self-contained by construction.
    fake = _fake_session(4096)
    fake.scrollback.append(b"x" * 100)
    assert Session.replay_window_truncated(fake) is False

    fake.attach_replay_bytes = 32
    assert Session.replay_window_truncated(fake) is True
    fake.attach_replay_bytes = 100
    assert Session.replay_window_truncated(fake) is False
    # A non-positive budget means "replay everything", exactly as `ScrollbackBuffer.tail`
    # reads it — the two must not disagree about what a client was handed.
    fake.attach_replay_bytes = 0
    assert Session.replay_window_truncated(fake) is False


def test_a_bounded_replay_restates_the_bracketed_paste_mode_it_cut_off() -> None:
    # Agent CLIs enable bracketed paste once at startup and never restate it, so a
    # window over a long session never carries it. A reconnecting pane resets its
    # terminal, and without this it pastes unwrapped: xterm rewrites every newline
    # to a carriage return, so the CLI submits the paste one line at a time and
    # keeps only the text after the final newline.
    fake = _fake_session(4096)
    fake.attach_replay_bytes = 32
    fake.bracketed_paste.feed(b"\x1b[?2004h")
    fake.scrollback.append(b"\x1b[?2004h" + b"output\n" * 40)
    replay = Session.replay_bytes(fake)
    assert replay.startswith(b"\x1b[?2004h")


def test_a_deep_session_replay_preserves_the_mouse_modes_a_phone_drag_needs() -> None:
    """Measured from a real Claude start: `?1000h ?1002h ?1003h ?1006h`, once, at byte 101.

    Losing them is what made a mobile swipe do nothing. With no mouse modes the browser's
    terminal reports no mouse, so `mobileDragTarget` has nothing to forward and falls back
    to scrolling xterm's own buffer — which on the alternate screen has no scrollback, so
    the gesture moves nothing. Only sessions deep enough to outgrow the replay window were
    affected, which is why it presented as intermittent.
    """
    fake = _fake_session(8192)
    fake.attach_replay_bytes = 64
    startup = b"\x1b[?2004h\x1b[?1049h\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h"
    fake.bracketed_paste.feed(startup)
    fake.screen.feed(startup)
    fake.sticky_modes.feed(startup)
    fake.scrollback.append(startup + b"conversation\n" * 200)
    replay = Session.replay_bytes(fake)
    for mode in (b"\x1b[?1000h", b"\x1b[?1002h", b"\x1b[?1003h", b"\x1b[?1006h"):
        assert mode in replay, mode
    # The modes have to arrive before the output they apply to, not after it.
    assert replay.index(b"\x1b[?1006h") < replay.index(b"conversation")


def test_a_replay_that_carries_a_mode_itself_is_not_contradicted() -> None:
    # The window's own toggle is the child's most recent word on that mode. Restating a
    # stale value over it would be the daemon overruling the child.
    fake = _fake_session(8192)
    fake.attach_replay_bytes = 4096
    fake.sticky_modes.feed(b"\x1b[?1000h")
    fake.scrollback.append(b"\x1b[?1000h" + b"x" * 100 + b"\x1b[?1000l")
    replay = Session.replay_bytes(fake)
    assert not replay.startswith(b"\x1b[?1000h\x1b[?1000h")


def test_a_mode_the_child_never_enabled_is_not_invented() -> None:
    # Restating a mode the child did not ask for leaves it reporting events it has no
    # handler for. Codex enables no mouse modes at all.
    fake = _fake_session(8192)
    fake.attach_replay_bytes = 32
    fake.sticky_modes.feed(b"\x1b[?2004h")
    fake.scrollback.append(b"codex output\n" * 200)
    replay = Session.replay_bytes(fake)
    assert b"\x1b[?1000h" not in replay
    assert b"\x1b[?1006h" not in replay


def test_an_omp_deep_session_replay_preserves_its_startup_bracketed_paste() -> None:
    # OMP 17.2.10 emits the same DECSET 2004 startup toggle under ConPTY and
    # leaves its transcript in the normal buffer.  A deep reconnect must carry
    # that one-time mode declaration back into xterm before replayed output.
    fake = _fake_session(8192)
    fake.attach_replay_bytes = 48
    fake.bracketed_paste.feed(b"\x1b[?2004h")
    fake.scrollback.append(b"\x1b[?2004h" + b"omp transcript line\n" * 200)

    replay = Session.replay_bytes(fake)

    assert replay.startswith(b"\x1b[?2004h")


def test_a_replay_carrying_its_own_bracketed_paste_toggle_is_left_alone() -> None:
    fake = _fake_session(4096)
    fake.attach_replay_bytes = 64
    fake.bracketed_paste.feed(b"\x1b[?2004h")
    fake.scrollback.append(b"filler\n" * 40 + b"\x1b[?2004ldone\n")
    replay = Session.replay_bytes(fake)
    assert not replay.startswith(b"\x1b[?2004h")
    assert b"\x1b[?2004l" in replay


def test_a_child_that_never_enabled_bracketed_paste_is_not_given_it() -> None:
    # A plain shell has no bracketed paste; inventing it would make the shell
    # receive literal ESC[200~ wrapper bytes on every paste.
    fake = _fake_session(4096)
    fake.attach_replay_bytes = 32
    fake.scrollback.append(b"prompt$ " * 40)
    replay = Session.replay_bytes(fake)
    assert not replay.startswith(b"\x1b[?2004h")


def test_both_cut_off_modes_are_restated_together() -> None:
    fake = _fake_session(4096)
    fake.attach_replay_bytes = 32
    fake.screen.feed(b"\x1b[?1049h")
    fake.bracketed_paste.feed(b"\x1b[?2004h")
    fake.scrollback.append(b"\x1b[?1049h\x1b[?2004h" + b"repaint\n" * 40)
    replay = Session.replay_bytes(fake)
    assert replay.startswith(b"\x1b[?2004h\x1b[?1049h")


def test_a_replay_that_carries_its_own_toggle_is_left_alone() -> None:
    # The window contains its own answer; prefixing would override a child that
    # deliberately left the alternate screen inside it.
    fake = _fake_session(4096)
    fake.attach_replay_bytes = 64
    fake.screen.feed(b"\x1b[?1049h")
    fake.scrollback.append(b"filler\n" * 40 + b"\x1b[?1049lback to normal\n")
    replay = Session.replay_bytes(fake)
    assert not replay.startswith(b"\x1b[?1049h")
    assert b"\x1b[?1049l" in replay


def test_an_untrimmed_replay_is_never_prefixed() -> None:
    # Nothing was cut, so the stream already contains whatever the child said.
    fake = _fake_session(4096)
    fake.attach_replay_bytes = 4096
    fake.screen.feed(b"\x1b[?1049h")
    fake.scrollback.append(b"\x1b[?1049hshort\n")
    assert Session.replay_bytes(fake) == b"\x1b[?1049hshort\n"


async def test_natural_exit_releases_conpty_but_retains_scrollback() -> None:
    class EndedPty:
        released = False

        def exit_status(self) -> int:
            return 9

        def release(self) -> None:
            self.released = True

    class History:
        ended: list[tuple[str, str]] = []

        async def session_ended(self, record: SessionRecord, reason: str) -> None:
            self.ended.append((record.id, reason))

    class Events:
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(self, event_type: str, **payload: Any) -> None:
            self.emitted.append((event_type, payload))

    adapter = ShellAdapter()
    history = History()
    events = Events()
    manager = SessionManager(
        {"shell": adapter},
        cast(Any, SimpleNamespace()),
        cast(Any, history),
        cast(Any, events),
        1024,
        "http://127.0.0.1:1",
    )
    record = SessionRecord(
        "ended-session",
        "ended",
        "project",
        "shell",
        "native",
        ".",
        "cmd.exe",
        [],
        state="running",
    )
    pty = EndedPty()
    session = Session(record, cast(Any, pty), adapter, 1024, "secret")
    session.scrollback.append(b"retained output")
    subscriber = session.subscribe()

    await manager._mark_ended(session, "process_exit")

    assert pty.released is True
    assert session.record.state == "crashed"
    assert session.record.exit_code == 9
    assert session.scrollback.bytes() == b"retained output"
    assert history.ended == [(record.id, "process_exit")]
    exit_frame = await subscriber.queue.get()
    assert exit_frame["type"] == "exit"
    assert exit_frame["snapshot"]["state"] == "crashed"


def test_scrollback_cursor_excludes_retained_output_and_tracks_new_tail() -> None:
    buffer = ScrollbackBuffer(8)
    buffer.append(b"old")
    position = buffer.position

    assert buffer.bytes_since(position) == b""

    buffer.append(b"-new-data")
    assert buffer.bytes() == b"new-data"
    assert buffer.bytes_since(position) == b"new-data"


def test_replay_subscription_boundary_is_atomic() -> None:
    # The method is intentionally synchronous: no fanout task can run between its
    # scrollback snapshot and subscriber registration on the event loop.
    fake = _fake_session(10)
    fake.scrollback.append(b"past")

    snapshot, revision, replay, queue = Session.replay_and_subscribe(fake)
    assert snapshot == {"state": "running"}
    assert revision == 0
    assert replay == b"past"
    assert queue in fake.subscribers


# --- delta attach: a reconnect that keeps the client's parsed buffer ------------
#
# The client offers the ring position it has parsed up to; when the ring provably
# holds everything after it, the attach is the missed bytes into an un-reset
# terminal. Every doubt falls back to the full bounded replay, because a wrong
# delta corrupts a terminal silently while a wasted replay only costs a parse.


def test_a_covered_gap_is_answered_with_exactly_the_missed_bytes() -> None:
    fake = _fake_session(64)
    fake.scrollback.append(b"earlier output ")
    since = fake.scrollback.position
    fake.scrollback.append(b"missed while away")

    snapshot, revision, kind, payload, position, queue = Session.attach_and_subscribe(fake, since)

    assert (kind, payload) == ("delta", b"missed while away")
    assert position == fake.scrollback.position
    assert snapshot == {"state": "running"}
    assert revision == 0
    assert queue in fake.subscribers


def test_a_current_client_gets_an_empty_delta_not_a_replay() -> None:
    # The common tab switch: nothing happened while hidden. The reconnect costs
    # nothing and the client's scrollback survives untouched.
    fake = _fake_session(64)
    fake.scrollback.append(b"all parsed already")

    _, _, kind, payload, position, _ = Session.attach_and_subscribe(
        fake, fake.scrollback.position
    )

    assert (kind, payload) == ("delta", b"")
    assert position == fake.scrollback.position


def test_an_uncovered_or_nonsense_position_falls_back_to_the_full_replay() -> None:
    fake = _fake_session(8)
    fake.scrollback.append(b"0123456789abcdef")  # ring retains only the last 8

    # Trimmed away: the ring cannot prove coverage.
    assert Session.attach_and_subscribe(fake, 2)[2] == "attach"
    # Ahead of the stream: not a position of this stream at all.
    assert Session.attach_and_subscribe(fake, 999)[2] == "attach"
    # Negative: same.
    assert Session.attach_and_subscribe(fake, -1)[2] == "attach"
    # No offer: the plain cold attach.
    assert Session.attach_and_subscribe(fake, None)[2] == "attach"


def test_a_gap_at_the_exact_retention_boundary_is_still_covered() -> None:
    fake = _fake_session(8)
    fake.scrollback.append(b"0123456789abcdef")
    since = fake.scrollback.position - fake.scrollback.size

    _, _, kind, payload, _, _ = Session.attach_and_subscribe(fake, since)

    assert (kind, payload) == ("delta", b"89abcdef")


def test_a_gap_larger_than_the_attach_budget_falls_back() -> None:
    # Continuity that costs more parse than a fresh window is not worth keeping:
    # the bounded replay exists to cap attach latency, and a delta must not
    # reintroduce the unbounded parse through the back door.
    fake = _fake_session(64)
    fake.attach_replay_bytes = 4
    fake.scrollback.append(b"0123456789")

    assert Session.attach_and_subscribe(fake, 2)[2] == "attach"
    assert Session.attach_and_subscribe(fake, 8)[2] == "delta"


def test_subscriber_overflow_coalesces_into_one_resync() -> None:
    fake = _fake_session(6)
    subscriber = Session.subscribe(fake, maxsize=2)
    for chunk in (b"12", b"34", b"56"):
        fake.scrollback.append(chunk)
        Session.publish_output(fake, chunk)

    assert subscriber.queue.qsize() == 1
    assert subscriber.queue.get_nowait() == {"type": "resync"}
    assert subscriber.dropped_bytes == 6
    assert subscriber.dropped_chunks == 3

    fake.scrollback.append(b"78")
    Session.publish_output(fake, b"78")
    result = Session.take_resync(fake, subscriber)
    dropped_bytes, dropped_chunks, replay, snapshot, revision, position, exit_frame = result
    assert (dropped_bytes, dropped_chunks) == (8, 4)
    assert replay == b"345678"
    assert snapshot == {"state": "running"}
    assert revision == 0
    # The drop broke the client's byte count; this position is its new anchor.
    assert position == fake.scrollback.position
    assert exit_frame is None


def test_exit_survives_subscriber_overflow() -> None:
    fake = _fake_session()
    subscriber = Session.subscribe(fake, maxsize=1)
    fake.scrollback.append(b"one")
    Session.publish_output(fake, b"one")
    fake.scrollback.append(b"two")
    Session.publish_output(fake, b"two")
    Session.publish_exit(fake, "process_exit")

    assert subscriber.queue.get_nowait() == {"type": "resync"}
    *_, exit_frame = Session.take_resync(fake, subscriber)
    assert exit_frame is not None
    assert exit_frame["type"] == "exit"
    assert exit_frame["reason"] == "process_exit"


def test_hook_state_blocks_lower_priority_transcript_regression() -> None:
    fake = _fake_session()
    fake.record = SessionRecord(
        "mux", "agent", "default", "claude", "native", ".", "claude.exe", []
    )
    fake.state_source_priority = -1

    assert Session.transition(fake, "awaiting", "permission", source="hook")
    assert not Session.transition(fake, "working", None, source="transcript")
    assert fake.record.state == "awaiting"
    assert fake.record.state_detail == "permission"
    assert Session.transition(fake, "idle", None, source="hook")
    assert fake.record.state == "idle"


async def test_fanout_records_first_output_and_prompt_startup_milestones() -> None:
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    await queue.put(b"profile output\r\n\x1b]7;file:///D:/PROJECTS/swe-mux\x07")
    await queue.put(b"")
    record = SessionRecord("mux", "shell", "default", "shell", "native", ".", "powershell.exe", [])
    updates: list[dict[str, float]] = []
    output: list[bytes] = []
    session = SimpleNamespace(
        pty=SimpleNamespace(output_queue=queue, first_output_at=time.perf_counter()),
        record=record,
        startup_started_at=time.perf_counter() - 0.05,
        stopping=True,
        output_window=deque(),
        osc7=Osc7Parser(),
        osc_signals=OscSignalParser(),
        osc133=Osc133Parser(),
        screen=ScreenModeParser(),
        bracketed_paste=BracketedPasteParser(),
        sticky_modes=StickyModeParser(),
        scrollback=ScrollbackBuffer(1024),
        publish_output=output.append,
        publish_update=lambda: updates.append(dict(record.startup_timing_ms)),
        startup_measurement_task=None,
        tasks=set(),
    )
    manager = cast(Any, SessionManager.__new__(SessionManager))
    prompt_uris: list[str] = []
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, **payload: Any) -> None:
        events.append((event_type, payload))

    manager.events = SimpleNamespace(
        emit=emit,
        emit_background=lambda event_type, **payload: events.append((event_type, payload)),
    )
    manager._queue_runtime_cwd = lambda _session, uri: prompt_uris.append(uri)

    await SessionManager._fanout(manager, session)
    await session.startup_measurement_task

    assert record.startup_timing_ms["first_output"] >= 40
    assert record.startup_timing_ms["first_prompt"] >= record.startup_timing_ms["first_output"]
    assert record.snapshot()["startup_timing_ms"] == record.startup_timing_ms
    assert prompt_uris == ["file:///D:/PROJECTS/swe-mux"]
    assert output == [b"profile output\r\n\x1b]7;file:///D:/PROJECTS/swe-mux\x07"]
    assert len(updates) == 1
    assert events[0][0] == "session_startup_measured"
    assert events[0][1]["milestone"] == "first_prompt"
    assert events[0][1]["timing_ms"] == record.startup_timing_ms


async def test_agent_startup_settles_to_ready_without_a_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("swe_mux.session.AGENT_STARTUP_QUIET_SECONDS", 0.01)
    record = SessionRecord(
        "mux",
        "codex",
        "default",
        "codex",
        "native",
        ".",
        "codex.exe",
        [],
        state="starting",
    )
    record.last_activity_ts = time.time()
    updates: list[str] = []
    session = SimpleNamespace(
        record=record,
        state_source_priority=-1,
        pty=SimpleNamespace(isalive=lambda: True),
        stopping=False,
        agent_ready_task=None,
        tasks=set(),
        revision=0,
        subscribers=set(),
        publish_update=lambda: updates.append(record.state),
    )
    session.transition = MethodType(Session.transition, session)
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, **payload: Any) -> None:
        emitted.append((event_type, payload))

    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.events = SimpleNamespace(emit=emit)
    SessionManager._queue_agent_ready_check(manager, session)
    assert session.agent_ready_task is not None
    await asyncio.wait_for(session.agent_ready_task, timeout=1)

    assert record.state == "idle"
    assert updates == ["idle"]
    assert emitted == [
        (
            "state_changed",
            {
                "session_id": "mux",
                "source": "pty",
                "previous": "starting",
                "state": "idle",
                "detail": None,
                "idle_reason": None,
                "standing": [],
                # Stamped `inferred` like every other PTY-sourced transition: this
                # idle is read off screen quiet, not observed, and a consumer that
                # cannot tell it from a hook-proven turn end will act on it.
                "proof": "inferred",
                "capability": "startup_quiet_fallback",
            },
        )
    ]
    # `previous: starting` is what keeps this out of the notification path — a
    # session that just booted is not waiting on anything the human asked for.
    from swe_mux.models import MuxEvent
    from swe_mux.push import classify_notification

    startup_idle = MuxEvent(
        ts=0.0, session_id="mux", source="pty", type="state_changed", payload=emitted[0][1]
    )
    assert classify_notification(startup_idle) is None


async def test_browser_startup_metrics_are_validated_and_persisted_once() -> None:
    record = SessionRecord("mux", "shell", "default", "shell", "native", ".", "powershell.exe", [])
    updates: list[dict[str, float]] = []
    emitted: list[tuple[str, dict[str, Any]]] = []
    session = SimpleNamespace(
        record=record,
        publish_update=lambda: updates.append(dict(record.client_startup_timing_ms)),
    )

    async def emit(event_type: str, **payload: Any) -> None:
        emitted.append((event_type, payload))

    class Request:
        app = {
            "sessions": SimpleNamespace(resolve=lambda _sid: session),
            "events": SimpleNamespace(emit=emit),
        }
        match_info = {"sid": "mux"}

        async def json(self) -> dict[str, Any]:
            return {
                "timing_ms": {
                    "api_response": 40.04,
                    "pane_mounted": 51.15,
                    "socket_open": 61.26,
                    "replay_ready": 72.37,
                }
            }

    response = await session_startup_metrics(cast(Any, Request()))
    await session_startup_metrics(cast(Any, Request()))
    payload = json.loads(response.text)

    assert payload["timing_ms"]["replay_ready"] == 72.4
    assert record.client_startup_timing_ms["api_response"] == 40.0
    assert len(updates) == 1
    assert len(emitted) == 1
    assert emitted[0][0] == "session_startup_client_measured"


async def test_spawn_returns_live_session_before_durable_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration_started = asyncio.Event()
    release_registration = asyncio.Event()
    persisted: list[str] = []

    class BlockingHistory:
        async def register_project_scope(self, _project: ProjectIdentity) -> None:
            registration_started.set()
            await release_registration.wait()
            persisted.append("scope")

        async def session_started(self, record: SessionRecord, _transcript: str | None) -> None:
            persisted.append(record.id)

    class FakePty:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.pid = 123
            self.reaper_assignment = "fixture"
            self.output_queue: asyncio.Queue[bytes] = asyncio.Queue()
            self.first_output_at = None

        def prepare(self) -> None:
            return None

        def spawn(self) -> None:
            return None

        def isalive(self) -> bool:
            return True

    adapter = SimpleNamespace(
        name="shell",
        spawn_spec=lambda _native, opts: SimpleNamespace(
            executable=opts.exe or "pwsh.exe", argv=tuple(opts.args), env={}
        ),
        resume_spec=lambda _native, opts: SimpleNamespace(
            executable=opts.exe or "pwsh.exe", argv=tuple(opts.args), env={}
        ),
        graceful_exit_keys=lambda: "exit\r",
        session_env=lambda _sid: {},
        transcript_path=lambda _native, _cwd: None,
    )
    monkeypatch.setattr("swe_mux.session.PtyHost", FakePty)
    manager = SessionManager(
        {"shell": cast(Any, adapter)},
        cast(Any, SimpleNamespace()),
        cast(Any, BlockingHistory()),
        cast(Any, SimpleNamespace(emit=lambda *_args, **_kwargs: asyncio.sleep(0))),
        1024,
        "http://127.0.0.1:1",
    )
    project = ProjectIdentity("scope", "Project", ".", "cwd")

    session = await asyncio.wait_for(
        manager.spawn(
            backend="shell",
            name=None,
            cwd=".",
            project_id="project",
            project=project,
            initial_output=b"setup scrollback\r\n",
        ),
        timeout=0.25,
    )
    assert manager.sessions[session.record.id] is session
    assert session.record.pid == 123
    assert "server_ready" in session.record.startup_timing_ms
    assert session.scrollback.bytes().startswith(b"setup scrollback\r\n")
    await asyncio.wait_for(registration_started.wait(), timeout=0.25)
    assert persisted == []

    release_registration.set()
    assert session.registration_task is not None
    await asyncio.wait_for(session.registration_task, timeout=0.25)
    assert persisted == ["scope", session.record.id]
    assert "durable_registration" in session.record.startup_timing_ms

    for task in tuple(session.tasks):
        task.cancel()
    await asyncio.gather(*session.tasks, return_exceptions=True)
