"""Cold session recovery: the registry, the checkpoint format, and the restore.

Two layers under test, and they fail differently. The registry (Layer A) is what
brings a session back at all, so its interesting cases are about the *open*
marker: who sets it, who clears it, and which rows a boot may treat as cold. The
checkpoints (Layer B) only decide what the recovered pane shows, so their
interesting cases are all crash shapes - a torn final append, a stale generation,
a ring that wrapped past the log - where the wrong answer is not "no content" but
a terminal reconstructed from bytes that never followed each other.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux import session_recovery as session_recovery_module
from swe_mux.models import SessionRecord
from swe_mux.screen_mode import BracketedPasteParser, ScreenModeParser, StickyModeParser
from swe_mux.scrollback import ScrollbackBuffer
from swe_mux.session import Session, SessionManager
from swe_mux.session_recovery import (
    CHECKPOINT_BASE_NAME,
    CHECKPOINT_LOG_NAME,
    CHECKPOINT_META_NAME,
    LOG_HEADER_BYTES,
    SessionRecoveryStore,
    checkpoint_skip_reason,
    decode_log,
    encode_log_header,
    encode_output_frame,
    encode_resize_frame,
    read_checkpoint,
    redact_meta,
)

# --- fakes -------------------------------------------------------------------


class FakeSession:
    """The narrow surface the recovery store reads off a live session."""

    def __init__(
        self,
        sid: str = "s1",
        *,
        backend: str = "shell",
        project_id: str = "p1",
        state: str = "running",
        max_bytes: int = 64 * 1024,
    ) -> None:
        self.record = SessionRecord(
            sid, sid, project_id, cast(Any, backend), "native", ".", "cmd.exe", []
        )
        self.record.state = cast(Any, state)
        self.scrollback = ScrollbackBuffer(max_bytes)
        self.screen = ScreenModeParser()
        self.geometry: tuple[int, int] | None = (80, 24)
        self.pty = SimpleNamespace(cols=80, rows=24)

    def write(self, data: bytes) -> None:
        self.scrollback.append(data)
        self.screen.feed(data)

    def recovery_meta(self) -> dict[str, Any]:
        return {
            "record": self.record.snapshot(),
            "hook_secret": "secret-value",
            "mcp_token": "token-value",
            "transcript_path": "/transcripts/x.jsonl",
        }


def make_store(tmp_path: Path, **kwargs: Any) -> SessionRecoveryStore:
    return SessionRecoveryStore(
        tmp_path / "mux.db", tmp_path / "recovery", **kwargs
    )


# --- framed log --------------------------------------------------------------


def test_log_round_trips_output_and_resize_frames() -> None:
    data = (
        encode_log_header(7)
        + encode_output_frame(b"hello ")
        + encode_resize_frame(100, 40)
        + encode_output_frame(b"world")
    )
    decoded = decode_log(data, 7)
    assert decoded is not None
    assert decoded.output == b"hello world"
    assert decoded.geometry == (100, 40)
    assert decoded.truncated_tail is False


def test_a_torn_final_append_keeps_the_complete_prefix() -> None:
    """The ordinary shape of a crash mid-write, and the reason for the framing.

    Everything before the tear is byte-exact; replaying the tear itself would put
    half an escape sequence into a terminal.
    """
    whole = (
        encode_log_header(1) + encode_output_frame(b"complete") + encode_output_frame(b"torn")
    )
    for cut in range(len(whole) - len(b"torn"), len(whole)):
        decoded = decode_log(whole[:cut], 1)
        assert decoded is not None
        assert decoded.output == b"complete"
        assert decoded.truncated_tail is True


def test_a_stale_generation_is_refused_rather_than_replayed() -> None:
    """A crash between rewriting the checkpoint and truncating the log.

    Those bytes are already inside the new base, so appending them again would
    duplicate a screen's worth of output onto the restored terminal.
    """
    data = encode_log_header(3) + encode_output_frame(b"already in the base")
    assert decode_log(data, 4) is None


def test_an_unreadable_header_is_refused() -> None:
    assert decode_log(b"", 0) is None
    assert decode_log(b"NOPE" + bytes(5), 0) is None
    # Right magic, wrong format version: detected rather than misparsed.
    assert decode_log(b"SMKL" + bytes([99, 0, 0, 0, 0]), 0) is None


def test_an_unknown_frame_kind_stops_the_replay_instead_of_guessing() -> None:
    data = (
        encode_log_header(1)
        + encode_output_frame(b"good")
        + bytes([0x7F, 2, 0, 0, 0])
        + b"??"
        + encode_output_frame(b"never reached")
    )
    decoded = decode_log(data, 1)
    assert decoded is not None
    assert decoded.output == b"good"
    assert decoded.truncated_tail is True


def test_a_resize_frame_with_the_wrong_length_stops_the_replay() -> None:
    data = encode_log_header(1) + encode_output_frame(b"good") + bytes([0x02, 3, 0, 0, 0]) + b"abc"
    decoded = decode_log(data, 1)
    assert decoded is not None
    assert decoded.output == b"good"
    assert decoded.truncated_tail is True


# --- checkpoint reading ------------------------------------------------------


def write_checkpoint(
    directory: Path,
    base: bytes,
    *,
    generation: int = 1,
    cols: int = 80,
    rows: int = 24,
    captured_at: float = 1000.0,
    log: bytes | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CHECKPOINT_BASE_NAME).write_bytes(base)
    (directory / CHECKPOINT_META_NAME).write_text(
        json.dumps(
            {
                "generation": generation,
                "position": len(base),
                "cols": cols,
                "rows": rows,
                "captured_at": captured_at,
            }
        ),
        encoding="utf-8",
    )
    (directory / CHECKPOINT_LOG_NAME).write_bytes(
        log if log is not None else encode_log_header(generation)
    )


def test_read_checkpoint_joins_the_base_and_its_appends(tmp_path: Path) -> None:
    write_checkpoint(
        tmp_path / "s",
        b"base ",
        log=encode_log_header(1) + encode_output_frame(b"appended"),
    )
    restored = read_checkpoint(tmp_path / "s", 0)
    assert restored is not None
    assert restored.data == b"base appended"
    assert restored.captured_at == 1000.0


def test_read_checkpoint_ignores_a_log_from_a_previous_generation(tmp_path: Path) -> None:
    write_checkpoint(
        tmp_path / "s",
        b"rebased base",
        generation=5,
        log=encode_log_header(4) + encode_output_frame(b"stale"),
    )
    restored = read_checkpoint(tmp_path / "s", 0)
    assert restored is not None
    assert restored.data == b"rebased base"


def test_the_logs_last_geometry_outranks_the_checkpoints(tmp_path: Path) -> None:
    """Two stores that can disagree after a crash; the newer one wins.

    The checkpoint records the size at the rebase, and everything appended since
    was written under whatever the log last recorded.
    """
    write_checkpoint(
        tmp_path / "s",
        b"x",
        cols=80,
        rows=24,
        log=encode_log_header(1) + encode_resize_frame(200, 60) + encode_output_frame(b"y"),
    )
    restored = read_checkpoint(tmp_path / "s", 0)
    assert restored is not None
    assert restored.geometry == (200, 60)


def test_read_checkpoint_bounds_what_it_returns(tmp_path: Path) -> None:
    write_checkpoint(tmp_path / "s", b"0123456789")
    restored = read_checkpoint(tmp_path / "s", 4)
    assert restored is not None
    assert restored.data == b"6789"


def test_read_checkpoint_survives_every_missing_file(tmp_path: Path) -> None:
    assert read_checkpoint(tmp_path / "absent", 0) is None
    lone = tmp_path / "lone"
    lone.mkdir()
    (lone / CHECKPOINT_META_NAME).write_text("not json", encoding="utf-8")
    assert read_checkpoint(lone, 0) is None
    # Metadata with no base and no log describes nothing to show.
    (lone / CHECKPOINT_META_NAME).write_text(
        json.dumps({"generation": 1, "captured_at": 1.0}), encoding="utf-8"
    )
    assert read_checkpoint(lone, 0) is None


# --- the registry ------------------------------------------------------------


async def test_an_open_row_survives_and_a_closed_one_does_not(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        alive, ended = FakeSession("alive"), FakeSession("ended")
        await store.open_session(cast(Any, alive))
        await store.open_session(cast(Any, ended))
        await store.close_session("ended", "exited")
        rows = await store.open_rows()
        assert [row.session_id for row in rows] == ["alive"]
        assert rows[0].project_id == "p1"
        assert rows[0].meta["record"]["name"] == "alive"
    finally:
        store.close()


async def test_credentials_never_reach_the_registry(tmp_path: Path) -> None:
    """A cold session's process is gone, so its secrets authenticate nothing.

    Persisting them would leave credentials at rest for no recoverable purpose,
    and restoring an empty hook secret would be worse than a random one:
    `compare_digest("", "")` is True, so a hook with no header would authenticate.
    """
    assert redact_meta({"record": {}, "hook_secret": "a", "mcp_token": "b"}) == {"record": {}}
    store = make_store(tmp_path)
    try:
        await store.open_session(cast(Any, FakeSession("s")))
        rows = await store.open_rows()
        assert "hook_secret" not in rows[0].meta
        assert "mcp_token" not in rows[0].meta
        assert "secret-value" not in (tmp_path / "mux.db").read_bytes().decode(
            "utf-8", "ignore"
        )
    finally:
        store.close()


async def test_discard_removes_the_row_and_its_bytes(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        session = FakeSession("s")
        session.write(b"printed output\n")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        assert (tmp_path / "recovery" / "s" / CHECKPOINT_BASE_NAME).exists()
        await store.discard("s")
        assert not (tmp_path / "recovery" / "s").exists()
        assert await store.open_rows() == []
    finally:
        store.close()


async def test_an_ordinary_end_keeps_the_bytes_and_only_closes_the_row(
    tmp_path: Path,
) -> None:
    """"This session finished" and "I am done reading it" are different statements."""
    store = make_store(tmp_path)
    try:
        session = FakeSession("s")
        session.write(b"printed output\n")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        await store.close_session("s", "exited")
        assert (tmp_path / "recovery" / "s" / CHECKPOINT_BASE_NAME).exists()
        assert await store.open_rows() == []
    finally:
        store.close()


async def test_prune_bounds_recovery_data_by_age_and_by_count(tmp_path: Path) -> None:
    store = make_store(tmp_path, retention_days=1, max_cold_sessions=2)
    try:
        for index in range(4):
            await store.open_session(cast(Any, FakeSession(f"open{index}")))
        await store.open_session(cast(Any, FakeSession("old")))
        await store.close_session("old", "exited")
        # An open row is bounded by count, a closed one by age.
        assert await store.prune(now=time.time() + 3 * 86400) == 3
        assert len(await store.open_rows()) == 2
    finally:
        store.close()


async def test_orphan_directories_are_swept(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        (tmp_path / "recovery" / "no-such-session").mkdir(parents=True)
        await store.open_session(cast(Any, FakeSession("kept")))
        (tmp_path / "recovery" / "kept").mkdir(parents=True, exist_ok=True)
        assert await store.sweep_orphan_directories(await store.known_ids()) == 1
        assert (tmp_path / "recovery" / "kept").exists()
        assert not (tmp_path / "recovery" / "no-such-session").exists()
    finally:
        store.close()


# --- checkpoint writing ------------------------------------------------------


async def test_the_first_flush_rebases_and_later_ones_append(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        session = FakeSession("s")
        session.write(b"first\n")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        assert (tmp_path / "recovery" / "s" / CHECKPOINT_BASE_NAME).read_bytes() == b"first\n"
        assert (
            len((tmp_path / "recovery" / "s" / CHECKPOINT_LOG_NAME).read_bytes())
            == LOG_HEADER_BYTES
        )
        session.write(b"second\n")
        await store.flush_dirty()
        # The base is untouched; only the delta was appended.
        assert (tmp_path / "recovery" / "s" / CHECKPOINT_BASE_NAME).read_bytes() == b"first\n"
        restored = read_checkpoint(tmp_path / "recovery" / "s", 0)
        assert restored is not None and restored.data == b"first\nsecond\n"
    finally:
        store.close()


async def test_an_idle_session_writes_no_new_frames(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        session = FakeSession("s")
        session.write(b"once\n")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        before = (tmp_path / "recovery" / "s" / CHECKPOINT_LOG_NAME).stat().st_size
        await store.flush_dirty()
        await store.flush_dirty()
        assert (tmp_path / "recovery" / "s" / CHECKPOINT_LOG_NAME).stat().st_size == before
    finally:
        store.close()


async def test_a_ring_that_wrapped_past_the_log_forces_a_rebase(tmp_path: Path) -> None:
    """Appending the tail across a wrap would splice a hole into the stream.

    The log holds bytes up to the last flush; if the bounded ring has since
    evicted everything after that point, what is left is not a delta.
    """
    store = make_store(tmp_path)
    try:
        session = FakeSession("s", max_bytes=64)
        session.write(b"a" * 32)
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        session.write(b"b" * 4096)
        await store.flush_dirty()
        assert store.stats()["gap_rebases"] == 1
        restored = read_checkpoint(tmp_path / "recovery" / "s", 0)
        assert restored is not None
        # Exactly the ring, once: no duplicated span and no hole.
        assert restored.data == session.scrollback.tail(store.checkpoint_bytes)
    finally:
        store.close()


async def test_the_append_log_is_rebased_before_it_can_grow_without_bound(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path, checkpoint_bytes=4096, log_max_bytes=2048)
    try:
        session = FakeSession("s")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        # Distinguishable chunks, so a rebase that kept only the newest delta is
        # visible as lost history rather than as a plausible-looking tail.
        for index in range(12):
            session.write(f"[{index:02d}]".encode() + b"x" * 508)
            await store.flush_dirty()
        assert (tmp_path / "recovery" / "s" / CHECKPOINT_LOG_NAME).stat().st_size <= 2048 + 512
        assert store.stats()["checkpoints_written"] >= 2
        restored = read_checkpoint(tmp_path / "recovery" / "s", 0)
        assert restored is not None
        # A rollover folds the log back into a *whole* base, so what survives is
        # never less than the checkpoint budget. Writing the delta as the new base
        # instead would leave a checkpoint holding the last few seconds of output
        # while reporting itself as healthy, and the newest bytes look identical
        # either way - which is why this asserts the size and the join, not the tail.
        assert len(restored.data) >= store.checkpoint_bytes
        whole = session.scrollback.bytes()
        assert restored.data == whole[-len(restored.data) :], "a suffix, with no hole or repeat"
        assert b"[11]" in restored.data
    finally:
        store.close()


async def test_a_geometry_change_is_framed_into_the_log(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        session = FakeSession("s")
        session.write(b"before\n")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        session.geometry = (200, 60)
        session.write(b"after\n")
        await store.flush_dirty()
        restored = read_checkpoint(tmp_path / "recovery" / "s", 0)
        assert restored is not None
        assert restored.geometry == (200, 60)
        assert restored.data == b"before\nafter\n"
    finally:
        store.close()


async def test_an_ended_session_never_overwrites_what_it_was_restored_from(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    try:
        session = FakeSession("s")
        session.write(b"kept\n")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        session.record.state = cast(Any, "crashed")
        session.scrollback = ScrollbackBuffer(64)
        await store.flush_dirty()
        restored = read_checkpoint(tmp_path / "recovery" / "s", 0)
        assert restored is not None and restored.data == b"kept\n"
    finally:
        store.close()


# --- what gets checkpointed --------------------------------------------------


def test_only_harnesses_whose_bytes_are_a_transcript_are_checkpointed() -> None:
    assert checkpoint_skip_reason(FakeSession("a", backend="shell")) is None
    # Alternate-screen: a bounded window is a slice of a differential frame
    # stream, and there is no live child to pulse for a repaint.
    assert checkpoint_skip_reason(FakeSession("b", backend="claude")) == "alternate_screen_harness"
    # Repaint-heavy: the transcript is redrawn rather than written as scrollback.
    assert checkpoint_skip_reason(FakeSession("c", backend="codex")) == "repaints_scrollback"


def test_a_shell_inside_a_full_screen_program_is_skipped_too() -> None:
    """The descriptor cannot see this: a plain shell running `vim` at the moment
    of the crash is in exactly the position an agent TUI is."""
    session = FakeSession("s", backend="shell")
    session.write(b"\x1b[?1049h drawing")
    assert checkpoint_skip_reason(session) == "alternate_screen"
    session.write(b"\x1b[?1049l back to normal")
    assert checkpoint_skip_reason(session) is None


async def test_a_skipped_session_keeps_its_registry_row(tmp_path: Path) -> None:
    """Layer B declining is not Layer A declining: the session still comes back."""
    store = make_store(tmp_path)
    try:
        session = FakeSession("agent", backend="claude")
        session.write(b"frames")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        assert not (tmp_path / "recovery" / "agent").exists()
        rows = await store.open_rows()
        assert [row.session_id for row in rows] == ["agent"]
        assert rows[0].checkpoint_skipped == "alternate_screen_harness"
        assert rows[0].terminal is None
    finally:
        store.close()


async def test_checkpoint_bytes_zero_keeps_the_registry_and_stores_nothing(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path, checkpoint_bytes=0)
    try:
        session = FakeSession("s")
        session.write(b"output")
        await store.open_session(cast(Any, session))
        store.attach(cast(Any, session))
        await store.flush_dirty()
        assert not (tmp_path / "recovery" / "s").exists()
        assert [row.session_id for row in await store.open_rows()] == ["s"]
    finally:
        store.close()


# --- restoring into the session manager --------------------------------------


def bare_manager(store: SessionRecoveryStore | None = None) -> SessionManager:
    adapter = SimpleNamespace(
        name="shell",
        transcript_path=lambda _native, _cwd: None,
        graceful_exit_keys=lambda: "exit\r",
        session_env=lambda _sid: {},
    )
    return SessionManager(
        {"shell": cast(Any, adapter)},
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace(emit=lambda *_a, **_k: _noop())),
        64 * 1024,
        "http://127.0.0.1:1",
        recovery=store,
    )


async def _noop() -> None:
    return None


async def seeded_store(tmp_path: Path, **kwargs: Any) -> SessionRecoveryStore:
    """A store holding one open row for a shell that printed something."""
    store = make_store(tmp_path, **kwargs)
    session = FakeSession("cold-1")
    # Bracketed paste and the mouse group are set once at startup and never
    # restated, exactly like a real CLI's - the restore has to recover them from
    # the bytes or the attach preamble has nothing to restate.
    session.write(b"\x1b[?2004h\x1b[?1000;1006h$ echo hi\r\nhi\r\n")
    await store.open_session(cast(Any, session))
    store.attach(cast(Any, session))
    await store.flush_dirty()
    return store


async def test_a_recovered_session_is_dead_visible_and_labelled(tmp_path: Path) -> None:
    store = await seeded_store(tmp_path)
    try:
        manager = bare_manager(store)
        assert await manager.restore_cold_sessions() == 1
        session = manager.sessions["cold-1"]
        assert session.record.state == "crashed"
        assert session.record.cold is True
        assert session.record.cold_reason == "daemon_lost"
        assert session.record.cold_terminal_at
        assert session.pty.isalive() is False
        assert session.pty.pid == -1
        assert b"echo hi" in session.scrollback.bytes()
    finally:
        store.close()


async def test_a_recovered_session_restores_the_modes_a_child_set_once(
    tmp_path: Path,
) -> None:
    """Without this the attach preamble restates nothing, and a deep pane loses
    bracketed paste and the mouse group for the rest of its life."""
    store = await seeded_store(tmp_path)
    try:
        manager = bare_manager(store)
        await manager.restore_cold_sessions()
        session = manager.sessions["cold-1"]
        assert session.bracketed_paste.enabled is True
        assert session.sticky_modes.enabled.get(1000) is True
        assert session.sticky_modes.enabled.get(1006) is True
    finally:
        store.close()


async def test_a_recovered_session_never_gets_a_delta_attach(tmp_path: Path) -> None:
    """Its ring was rebuilt from disk, so a pre-crash position describes a
    different stream; a delta across that boundary corrupts the terminal."""
    store = await seeded_store(tmp_path)
    try:
        manager = bare_manager(store)
        await manager.restore_cold_sessions()
        session = manager.sessions["cold-1"]
        _snapshot, _revision, kind, _payload, _position, _sub = session.attach_and_subscribe(0)
        assert kind == "attach"
    finally:
        store.close()


async def test_a_recovered_session_cannot_authenticate_anything(tmp_path: Path) -> None:
    store = await seeded_store(tmp_path)
    try:
        manager = bare_manager(store)
        await manager.restore_cold_sessions()
        session = manager.sessions["cold-1"]
        assert session.mcp_token == ""
        # Never empty: an empty secret would accept a hook that sends no header.
        assert len(session.hook_secret) >= 16
    finally:
        store.close()


async def test_a_session_the_supervisor_handed_back_is_not_restored_cold(
    tmp_path: Path,
) -> None:
    """The ordering rule this whole path rests on: an open row for a session
    adoption already claimed describes a *live* process."""
    store = await seeded_store(tmp_path)
    try:
        manager = bare_manager(store)
        manager.sessions["cold-1"] = cast(Any, SimpleNamespace(record=SimpleNamespace(id="cold-1")))
        assert await manager.restore_cold_sessions() == 0
    finally:
        store.close()


async def test_a_row_whose_project_is_gone_is_closed_rather_than_reconsidered(
    tmp_path: Path,
) -> None:
    store = await seeded_store(tmp_path)
    try:
        manager = bare_manager(store)
        assert await manager.restore_cold_sessions(project_exists=lambda _pid: False) == 0
        assert await store.open_rows() == []
    finally:
        store.close()


async def test_a_recovered_session_holds_no_standing_engagements(tmp_path: Path) -> None:
    """A dead process is not looping, scheduled, or running subagents, and its
    exit code is genuinely unknown rather than whatever was last observed."""
    store = make_store(tmp_path)
    try:
        session = FakeSession("busy")
        session.record.standing_activity = [
            cast(Any, SimpleNamespace(kind="loop", source="hook", evidence="x", since=1.0))
        ]
        session.record.turn_started_at = 100.0
        session.record.awaiting_reason = "approval"
        session.record.exit_code = 0
        await store.open_session(cast(Any, session))
        manager = bare_manager(store)
        await manager.restore_cold_sessions()
        record = manager.sessions["busy"].record
        assert record.standing_activity == []
        assert record.turn_started_at is None
        assert record.awaiting_reason is None
        assert record.exit_code is None
    finally:
        store.close()


async def test_a_recovered_agent_with_no_bytes_still_comes_back(tmp_path: Path) -> None:
    """The Layer A / Layer B split, end to end: an alternate-screen harness is
    excluded from checkpointing and must still be a row you can Resume."""
    store = make_store(tmp_path)
    try:
        agent = FakeSession("agent-1", backend="claude")
        agent.write(b"frames")
        await store.open_session(cast(Any, agent))
        store.attach(cast(Any, agent))
        await store.flush_dirty()
        manager = bare_manager(store)
        assert await manager.restore_cold_sessions() == 1
        record = manager.sessions["agent-1"].record
        assert record.cold is True
        assert record.cold_terminal_at is None
        assert record.cold_terminal_skipped == "alternate_screen_harness"
        assert manager.sessions["agent-1"].transcript_path == Path("/transcripts/x.jsonl")
    finally:
        store.close()


def test_a_prepared_but_unspawned_host_is_already_the_contract_a_cold_pane_needs(
    tmp_path: Path,
) -> None:
    """Rather than a second implementation of a host that does nothing."""
    manager = bare_manager(None)
    record = SessionRecord("s", "s", "p", cast(Any, "shell"), "n", ".", "cmd.exe", [])
    session = Session(
        record,
        cast(Any, __import__("swe_mux.pty_host", fromlist=["PtyHost"]).PtyHost("cmd.exe")),
        cast(Any, manager.adapters["shell"]),
        1024,
        "secret",
    )
    assert session.pty.isalive() is False
    assert session.pty.exit_status() is None
    session.pty.release()
    session.pty.stop()
    assert session.pty.pid == -1


def test_restored_bytes_reconstruct_the_same_parser_state_as_the_live_stream() -> None:
    """The restore feeds the recovered bytes through the same parsers the live
    session used, so the two must agree - this is what the attach preamble reads."""
    stream = b"\x1b[?2004h\x1b[?1049h\x1b[?1002;1006hpainting"
    live_screen, live_paste, live_sticky = (
        ScreenModeParser(),
        BracketedPasteParser(),
        StickyModeParser(),
    )
    for chunk in (stream[:5], stream[5:17], stream[17:]):
        live_screen.feed(chunk)
        live_paste.feed(chunk)
        live_sticky.feed(chunk)
    restored_screen, restored_paste, restored_sticky = (
        ScreenModeParser(),
        BracketedPasteParser(),
        StickyModeParser(),
    )
    restored_screen.feed(stream)
    restored_paste.feed(stream)
    restored_sticky.feed(stream)
    assert restored_screen.mode == live_screen.mode == "alternate"
    assert restored_paste.enabled == live_paste.enabled is True
    assert restored_sticky.enabled == live_sticky.enabled


# --- input into a pane with nothing behind it --------------------------------


def test_input_is_refused_for_a_session_with_no_process() -> None:
    """Ended and recovered panes stay open, so both are panes a person can click
    into and type at. Neither has a PTY, and `PtyHost.write` raises for a released
    or never-spawned pseudoterminal - a 500 on the HTTP paths and a dropped socket
    on the WebSocket one. Refusing here makes it explainable instead."""
    from swe_mux.server import session_accepts_input

    assert session_accepts_input(FakeSession("live", state="running")) is True
    assert session_accepts_input(FakeSession("live", state="awaiting")) is True
    assert session_accepts_input(FakeSession("dead", state="exited")) is False
    assert session_accepts_input(FakeSession("cold", state="crashed")) is False


async def test_a_session_that_survived_a_restart_keeps_being_checkpointed(
    tmp_path: Path,
) -> None:
    """Adoption rejoins the durable registry.

    Without it a session that survived one session-preserving restart would
    silently stop being checkpointed and its row would stop being updated, so the
    *next* crash - the one this exists for - would recover it from whatever the
    previous daemon last wrote.

    Driven through a real `Session` rather than the store fake, because the half
    that can rot is `_attach_recovery` handing over the same metadata blob the
    supervisor mirror uses.
    """
    store = await seeded_store(tmp_path)
    try:
        manager = bare_manager(store)
        await manager.restore_cold_sessions()
        adopted = manager.sessions["cold-1"]
        # A restored session stands in for an adopted one here: both are a live
        # `Session` the manager registers after rebuilding it.
        adopted.record.state = cast(Any, "idle")
        manager._attach_recovery(adopted)
        await store.open_session(adopted)
        adopted.scrollback.append(b"after the restart\r\n")
        await store.flush_dirty()
        restored = read_checkpoint(tmp_path / "recovery" / "cold-1", 0)
        assert restored is not None
        assert b"after the restart" in restored.data
        # Still open, so a later crash recovers it rather than losing it.
        assert [row.session_id for row in await store.open_rows()] == ["cold-1"]
    finally:
        store.close()


async def test_an_adopted_sessions_checkpoint_is_never_read_at_boot(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Reading a checkpoint is file I/O on the startup path.

    After a session-preserving restart - the common case, and the one nothing
    went wrong in - every open row belongs to a live adopted session, so
    filtering after the read would read every checkpoint to throw them all away.
    """
    store = await seeded_store(tmp_path)
    try:
        reads: list[Path] = []
        real = session_recovery_module.read_checkpoint

        def counting(directory: Path, budget: int) -> Any:
            reads.append(directory)
            return real(directory, budget)

        monkeypatch.setattr(session_recovery_module, "read_checkpoint", counting)
        manager = bare_manager(store)
        manager.sessions["cold-1"] = cast(
            Any, SimpleNamespace(record=SimpleNamespace(id="cold-1"))
        )
        assert await manager.restore_cold_sessions() == 0
        assert reads == []
    finally:
        store.close()
