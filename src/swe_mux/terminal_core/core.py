"""One session's terminal: who owns its input, what size it is, and what its
browser connections have asked of it."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from ..composer_input import (
    ComposerScreenReading,
    ComposerState,
    PendingSubmit,
    merge_screen_readings,
    parse_screen_reading,
    write_may_add_text,
)
from ..terminal_arbitration import OwnerState, effective_geometry, release_owner
from .diagnostics import (
    CLIENT_DIAGNOSTIC_DETAIL_LIMIT,
    CLIENT_DIAGNOSTIC_MIN_INTERVAL_SECONDS,
    CLIENT_INPUT_DIAGNOSTIC_PHASES,
    CLIENT_PASTE_TRACE_DETAIL_LIMIT,
    CLIENT_PASTE_TRACE_PHASE,
    CLIENT_REPAIR_PHASES,
)
from .host import PtySink, TerminalDescriptor, TerminalHost, TerminalSocket
from .stream import TerminalStream

# Terminal-input claims, for diagnosing which device ended up owning a session and
# why. Small: only the last few decisions matter, and they are read by hand.
CLAIM_LOG_LIMIT = 24


# A `repaint` frame makes the child restate its whole transcript (~100 KB+ for a deep
# session), so back-to-back requests — several panes of one session finishing replay
# together, or a reconnect storm — collapse into one restatement they all receive. The
# same window bounds the attach pulse an alternate-screen session takes
# (`schedule_attach_repaint`), whose restatement is one screen rather than a transcript.
CLIENT_REPAINT_MIN_INTERVAL_SECONDS = 2.0


# How long the arbitrated geometry must hold still before an alternate-screen child is
# pulsed into restating its screen. Long enough that a drag (which emits changes at
# frame rate) settles first and costs one pulse rather than hundreds, short enough that
# a corrupt screen is not something the user has time to start working around.
RESIZE_REPAINT_SETTLE_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class WriteCondition:
    """What a delivery decided on, re-checked atomically with its write.

    Delivery readiness judges a session and the delivery's write follows. In
    process nothing can run between the two; once keystrokes arrive in another
    process, one can land in between, and pasting a delivery over a half-typed
    prompt is the failure readiness exists to prevent. So the write carries what
    the judgement relied on, and the core refuses it when that no longer holds:
    the input revision the judgement saw, how long the human has been quiet, and
    no earlier delivery still sitting unsubmitted in the composer.

    ``screen_position`` is set only when the judgement relied on a screen reading
    of an empty composer (`composer_screen`): the write then also needs that
    reading to still be current at that ring position, because anything drawn
    since may be the composer filling up again.

    Times are durations, never clock readings, so a condition means the same in
    whichever process evaluates it.
    """

    input_revision: int | None = None
    operator_quiet_seconds: float | None = None
    no_pending_submit: bool = False
    screen_position: int | None = None


class WriteRefused(Exception):
    """A conditional write found its condition no longer held, and wrote nothing.

    ``reason`` names which part failed. The caller re-evaluates rather than
    retrying the write: whatever moved is evidence the judgement has to see.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TerminalCore:
    """Everything about one session's terminal that its browser connections touch.

    ``pty`` is asked for each time it is needed rather than captured, because the
    hosting session's PTY can be replaced underneath it, and ``descriptor`` reads
    the session live (`TerminalDescriptor`); ``tasks`` is the hosting session's
    task set, so the work this schedules is cancelled with the session.
    """

    def __init__(
        self,
        stream: TerminalStream,
        *,
        pty: Callable[[], PtySink],
        descriptor: TerminalDescriptor,
        tasks: set[asyncio.Task[Any]],
    ) -> None:
        self.stream = stream
        self._pty = pty
        self.descriptor = descriptor
        self.tasks = tasks
        self.attachments_seen = 0
        # Exactly one browser connection may write terminal-generated responses
        # and user keystrokes. Without this, two attached xterms both answer
        # device-status queries and the duplicate response appears at the prompt.
        self.input_owner: str | None = None
        # The owner's socket, so a displaced owner can be told it lost the claim.
        # Silent displacement left the previous client typing into a void.
        self.input_owner_socket: Any = None
        # Transfer counter and the owning device's label, carried on every
        # `input_owner` frame so a client can discard an ownership notification that
        # lost a race with a newer one. See terminal_arbitration.
        self.input_owner_epoch = 0
        self.input_owner_device: str | None = None
        # Monotonic time of the last gesture-backed claim or keystroke by the owner:
        # what protects an actively typed-in device from a passive re-claim by a
        # background pane on another device.
        self.input_owner_gesture_ts: float | None = None
        # Non-owner input is refused rather than silently dropped; these make the
        # refusals visible in telemetry instead of leaving the user to notice
        # missing characters by feel.
        self.input_rejections = 0
        self.last_input_reject_report_ts = 0.0
        # Monotonic stamps bounding client-requested work: a `repaint` frame makes the
        # child restate its whole transcript, and `client_diagnostic` frames are
        # persisted to the durable event log, so neither may be driven at frame rate
        # by a misbehaving or malicious page. The repaint stamp also covers the pulse
        # an alternate-screen session takes after a windowed replay, so a reconnect
        # storm across devices costs the session one restatement rather than one each.
        self.last_client_repaint_ts = 0.0
        self.client_diagnostic_timestamps: dict[str, float] = {}
        # A drag emits geometry changes continuously, and an alternate-screen child
        # cannot repair its own screen after one (`needs_resize_repaint`). These carry
        # the trailing-edge debounce that pulses it once the size stops moving: the
        # deadline is pushed forward by every change, and one task waits it out, so a
        # gesture of any length costs exactly one pulse after the user lets go.
        self.resize_repaint_deadline = 0.0
        self.resize_repaint_task: asyncio.Task[None] | None = None
        # Passive claims refused because another device is actively being typed into:
        # the direct measure of how often a background pane tried to steal the keyboard.
        self.input_claim_denials = 0
        # Last refusal per connection, so a client that re-claims on its own refusal
        # stops being answered instead of looping. Cleared when the connection ends.
        self.claim_refusals: dict[str, float] = {}
        # Recent claim decisions, newest last. Ownership disputes are otherwise only
        # visible as a counter going up, which says a claim was refused but never
        # which device asked, what it reported about itself, or what the daemon
        # believed at the time — the three things any diagnosis actually needs.
        self.claim_log: deque[dict[str, Any]] = deque(maxlen=CLAIM_LOG_LIMIT)
        # Per-connection fitted terminal size for every attached client that reports
        # itself visible. Hidden panes deregister, so a minimized window can no longer
        # reshape ConPTY for the device a human is actually using.
        self.viewports: dict[str, tuple[int, int]] = {}
        self.geometry: tuple[int, int] | None = None
        self.last_input_event_ts = 0.0
        self.last_input_report_ts = 0.0
        self.input_revision = 0
        # Estimated unsent composer contents, kept from the bytes written to this
        # PTY (`composer_input.py`). Display evidence only: the delivery gate has
        # its own `input_revision` boundary and never reads this.
        self.composer = ComposerState()
        # A queue delivery whose bytes reached this composer and whose Enter the
        # CLI never took. Blocks the next delivery to this target so a second
        # body cannot be pasted on top of the first, and clears itself at the two
        # seams that prove the composer emptied (`composer_input.PendingSubmit`).
        # Run-scoped and process-scoped like the composer estimate beside it: a
        # daemon restart forgets it, which is the honest reading of "not
        # observed", and the block it carries is one a human can override.
        self.pending_submit: PendingSubmit | None = None
        # What a browser last read in the composer, and the ring position of the
        # last write that could have put text there (`write_may_add_text`). The
        # reading counts only while it is current (`composer_screen`): its screen
        # is where the ring ends, and the CLI drew it after that write. -1 is "no
        # such write through this core", which is every write a previous process
        # made - those are seconds old by the time any core exists to replace it.
        self.composer_reading: ComposerScreenReading | None = None
        self.additive_input_position = -1
        # xterm's active buffer, as the owning browser reports it: corroboration
        # for the daemon's own reading of the stream (`TerminalStream.screen`).
        self.terminal_mode: str | None = None
        self.terminal_mode_updated_at = 0.0
        # Told whenever something the daemon mirrors changes - the input accounting,
        # ownership, geometry, viewports, the terminal mode, claims and refusals - so
        # a gateway can send it (`gateway.GatewaySession.mark_dirty`). None in
        # process, where the daemon reads this object directly.
        self.on_change: Callable[[], None] | None = None
        # The browser sockets being served from this core, so a core that stops
        # serving its session can close every one of them (`retire`).
        self.sockets: set[TerminalSocket] = set()
        # Set when this core no longer serves its session - another process now holds
        # the terminal (`DAEMON_LOAD_ISOLATION.md` item 32) - to the close code and
        # reason its browsers are sent. A retired core touches the PTY no more: a
        # terminal written and resized from two processes has two owners.
        self.retired: tuple[int, bytes] | None = None

    @property
    def pty(self) -> PtySink:
        return self._pty()

    @property
    def attached_connections(self) -> int:
        """Browser connections attached to this terminal now."""
        return len(self.stream.subscribers)

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    # --- writes -------------------------------------------------------------------

    def last_input_age(self) -> float | None:
        """Seconds since the last human input, or None when there has been none.

        An age rather than the monotonic stamp it is computed from, because a stamp
        means nothing in another process (`WriteCondition`).
        """
        if self.last_input_event_ts <= 0:
            return None
        return max(0.0, time.monotonic() - self.last_input_event_ts)

    def refusal(self, condition: WriteCondition) -> str | None:
        """Which part of ``condition`` no longer holds, or None when all of it does."""
        if condition.input_revision is not None and self.input_revision != condition.input_revision:
            return "input_revision_moved"
        if condition.operator_quiet_seconds is not None:
            age = self.last_input_age()
            if age is not None and age < condition.operator_quiet_seconds:
                return "operator_recently_typed"
        if condition.no_pending_submit and self.pending_submit is not None:
            return "unsubmitted_delivery_in_composer"
        if condition.screen_position is not None:
            current = self.composer_screen()
            if current is None or current.position != condition.screen_position:
                return "composer_screen_moved"
        return None

    def write(self, data: str | bytes, *, condition: WriteCondition | None = None) -> None:
        """Write to the PTY: the only place in the daemon that does.

        One writer is what gives writes from several sources - a browser, the queue,
        voice, an approval - a single order, and what lets a condition be checked
        with nothing able to run between the check and the write. A guard test fails
        on any other `pty.write` or `pty.resize`.

        It is also why the composer's screen reading is retired here: any write that
        could put text in the composer makes the screen a browser read describe a
        composer that may no longer exist, until the CLI draws again.
        """
        if self.retired is not None:
            raise WriteRefused("moved")
        if condition is not None:
            reason = self.refusal(condition)
            if reason is not None:
                raise WriteRefused(reason)
        self.pty.write(data)
        if write_may_add_text(data):
            self.additive_input_position = self.stream.scrollback.position
            if self.composer_reading is not None:
                self.composer_reading = None
                self._changed()

    # --- the composer, as a browser read it ---------------------------------------

    def composer_screen(self) -> ComposerScreenReading | None:
        """The composer reading, while it still describes the composer.

        Current means three things at once: it was taken in this ring's stream, its
        screen is where the ring ends now (nothing has been drawn since), and that
        screen came after the last write that could have added text. A reading that
        fails any of them is not wrong about the past - it is simply no longer about
        the present, and nothing may act on it.
        """
        reading = self.composer_reading
        if reading is None:
            return None
        if reading.stream != self.stream.stream_id:
            return None
        if reading.position != self.stream.scrollback.position:
            return None
        if reading.position <= self.additive_input_position:
            return None
        return reading

    def note_composer_reading(self, frame: dict[str, Any]) -> ComposerScreenReading | None:
        """Take a browser's `composer_reading` frame; the reading kept, or None.

        Refused unless it is current at the moment it arrives (`composer_screen`),
        and unless it was read at the size the PTY is: a pane laid out at another
        width wraps the composer somewhere the CLI did not, and its rectangle is not
        the CLI's. A refusal is silent and costs nothing - the browser reads again
        after the next output it parses.
        """
        reading = parse_screen_reading(frame)
        if reading is None or self.retired is not None:
            return None
        if reading.stream != self.stream.stream_id:
            return None
        if reading.position != self.stream.scrollback.position:
            return None
        if reading.position <= self.additive_input_position:
            return None
        geometry = self.geometry
        cols, rows = frame.get("cols"), frame.get("rows")
        if geometry is not None and (cols, rows) != geometry:
            return None
        kept = merge_screen_readings(self.composer_screen(), reading)
        if kept != self.composer_reading:
            self.composer_reading = kept
            self._changed()
        return kept

    def note_stream_reseeded(self) -> None:
        """The ring was replaced: no reading taken against the old one is current,
        and a write in flight before it may not have been drawn into the new one."""
        self.composer_reading = None
        self.additive_input_position = self.stream.scrollback.position
        self._changed()

    def note_output(self) -> bool:
        """Output reached the ring. True when it retired the composer reading, which
        a host that mirrors the reading elsewhere has to say (`gateway`)."""
        reading = self.composer_reading
        if reading is None or reading.position == self.stream.scrollback.position:
            return False
        self.composer_reading = None
        self._changed()
        return True

    def write_operator_input(
        self, data: str, *, condition: WriteCondition | None = None
    ) -> None:
        """Operator-class input - everything that is not a browser keystroke - with
        the accounting delivery readiness reads.

        Every such path must advance `input_revision` and the last input's time, or
        readiness reports the composer untouched and the operator quiet for text the
        operator just sent. The browser path accounts for itself, per keystroke kind
        (`connection._handle_text_input`).
        """
        self.write(data, condition=condition)
        now = time.monotonic()
        self.input_revision += 1
        self.last_input_event_ts = now
        self.last_input_report_ts = now
        self._changed()

    async def write_when(
        self, data: str | bytes, *, condition: WriteCondition | None, accounted: bool
    ) -> None:
        """The daemon's conditional write, the same call wherever the core runs.

        In process the check and the write happen now; a remote terminal sends both
        to the gateway and waits for the answer (`remote.RemoteTerminal.write_when`).
        Raises `WriteRefused` when the condition no longer holds.
        """
        if accounted:
            if not isinstance(data, str):
                raise TypeError("accounted input is text")
            self.write_operator_input(data, condition=condition)
        else:
            self.write(data, condition=condition)

    # --- browser input accounting ---------------------------------------------

    def note_typed_input(self, now: float) -> None:
        """A keystroke that puts text in the composer: the revision, the quiet clock,
        and the owner's protection from passive claims all move."""
        self.input_revision += 1
        self.last_input_event_ts = now
        self.input_owner_gesture_ts = now
        self._changed()

    def note_pointer_input(self, now: float) -> None:
        """A click or a wheel notch: the human is here, but the composer is untouched,
        so the quiet clock moves and the revision does not."""
        self.last_input_event_ts = now
        self.input_owner_gesture_ts = now
        self._changed()

    def set_terminal_mode(self, mode: str) -> bool:
        """Record the owning browser's active buffer; True when it changed."""
        changed = self.terminal_mode != mode
        self.terminal_mode = mode
        self.terminal_mode_updated_at = time.monotonic()
        self._changed()
        return changed

    def record_claim(self, entry: dict[str, Any]) -> None:
        self.claim_log.append(entry)
        self._changed()

    # --- frames from the hosting process ----------------------------------------

    def publish_update(self, frame: dict[str, Any]) -> None:
        """A snapshot `update` for every attached browser."""
        self.stream.publish_update(frame)

    def publish_exit(self, frame: dict[str, Any]) -> None:
        """The `exit` frame for every attached browser."""
        self.stream.publish_exit(frame)

    # --- input ownership (see terminal_arbitration) ---------------------------

    def owner_state(self) -> OwnerState:
        return OwnerState(
            connection_id=self.input_owner,
            epoch=self.input_owner_epoch,
            device=self.input_owner_device,
            last_gesture_at=self.input_owner_gesture_ts,
        )

    def apply_owner_state(self, state: OwnerState) -> None:
        self.input_owner = state.connection_id
        self.input_owner_epoch = state.epoch
        self.input_owner_device = state.device
        self.input_owner_gesture_ts = state.last_gesture_at
        self._changed()

    def note_owner_input(self, now: float) -> None:
        """Human keystrokes renew the owner's protection from passive claims."""
        self.input_owner_gesture_ts = now

    def release_input_owner(self, connection_id: str) -> bool:
        """Drop ownership held by a detaching connection. True when it held it."""
        if self.input_owner != connection_id:
            return False
        self.apply_owner_state(release_owner(self.owner_state(), connection_id))
        self.input_owner_socket = None
        return True

    def note_input_rejected(self, host: TerminalHost, byte_count: int) -> None:
        """Count input refused from a non-owner. Silent drops left this failure invisible:
        the user saw missing characters and there was nothing in telemetry to correlate."""
        self.input_rejections += 1
        self._changed()
        now = time.monotonic()
        if now - self.last_input_reject_report_ts < 2:
            return
        self.last_input_reject_report_ts = now
        host.emit(
            "terminal_input_rejected",
            source="daemon",
            owner_device=self.input_owner_device,
            bytes=byte_count,
            rejections=self.input_rejections,
        )

    # --- shared geometry --------------------------------------------------------

    def set_viewport(self, connection_id: str, cols: int, rows: int, *, hidden: bool) -> None:
        """Record (or, for a hidden client, forget) one client's fitted size."""
        if hidden:
            self.viewports.pop(connection_id, None)
        else:
            self.viewports[connection_id] = (max(2, cols), max(1, rows))
        self._changed()

    def drop_viewport(self, connection_id: str) -> None:
        if self.viewports.pop(connection_id, None) is not None:
            self._changed()

    def apply_geometry(self) -> bool:
        """Resize the PTY to the arbitrated size. True when it actually changed.

        Called after every viewport update, ownership transfer and detach. Resizing is
        gated on a real change because each one is a SIGWINCH that makes an agent TUI
        repaint its whole screen.
        """
        if self.retired is not None:
            return False
        size = effective_geometry(self.viewports, self.input_owner)
        if size is None or size == self.geometry:
            return False
        self.geometry = size
        self.pty.resize(size[0], size[1])
        self.stream.publish_control(self.geometry_frame())
        self._changed()
        return True

    def geometry_frame(self) -> dict[str, Any]:
        # getattr-guarded for the lightweight PTY stubs used by protocol tests, which
        # implement resize/write but carry no dimensions of their own.
        pty = self.pty
        cols, rows = self.geometry or (
            int(getattr(pty, "cols", 80)),
            int(getattr(pty, "rows", 24)),
        )
        return {
            "type": "geometry",
            "cols": cols,
            "rows": rows,
            "owner_device": self.input_owner_device,
        }

    # --- repaint ------------------------------------------------------------------

    async def repaint_current_geometry(self) -> bool:
        """Make a full-screen child repaint without changing canonical geometry.

        A repaint-heavy TUI (OMP's spinner alone emits kilobytes per second) wraps the
        retained ring until a bounded replay holds only live-region repaint traffic,
        which parses to a single screen with no scrollback. The child holds the full
        transcript and restates it in response to a real width change (measured
        2026-08-07: one pulse elicited a ~460-line transcript re-render), so pulse one
        column and restore it after the child has had a scheduler turn. Triggered by a
        client `repaint` frame when its parsed replay produced no scrollback; the
        restated bytes also repopulate the ring for every later attach.

        Another client may resize during the yield. In that case its new canonical
        geometry has already won, so never restore the stale size over it.
        """
        size = self.geometry
        if size is None or self.descriptor.ended or self.retired is not None:
            return False
        cols, rows = size
        pulse_cols = cols - 1 if cols > 2 else cols + 1
        self.pty.resize(pulse_cols, rows)
        await asyncio.sleep(0.02)
        if self.geometry != size or self.retired is not None:
            return False
        self.pty.resize(cols, rows)
        return True

    # --- the browsers it serves ---------------------------------------------------

    async def close_sockets(self, code: int, message: bytes) -> None:
        """Close every browser socket this core serves, telling each where to go."""
        for socket in tuple(self.sockets):
            close = getattr(socket, "close", None)
            if close is not None:
                with suppress(Exception):
                    await close(code=code, message=message)

    def retire(self, code: int, message: bytes) -> None:
        """Stop serving this session: another process holds its terminal now.

        Synchronous, so the caller that moved the terminal can retire the old core
        in the same instant: from here on it writes and resizes nothing, a socket
        mid-handshake is refused, and every attached one is closed with ``code``.
        """
        if self.retired is not None:
            return
        self.retired = (code, message)
        if self.sockets:
            self._start(
                self.close_sockets(code, message),
                name=f"terminal-retire-{self.descriptor.session_id}",
            )

    def _start(self, coroutine: Any, name: str) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.create_task(coroutine, name=name)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _repaint_when_resize_settles(self, host: TerminalHost) -> None:
        """Wait out a resize gesture, then make the child restate its screen once.

        Trailing edge, not leading: pulsing while the pointer is still moving would repaint
        a size the user has already dragged past, and the screen that has to end up correct
        is the one they stop on. The deadline is re-read every wake rather than captured,
        so a gesture that outlives the first sleep extends this task instead of starting a
        second one.
        """
        # unsupervised-loop-ok: one settle window for one session's resize gesture, and it
        # can only extend while that gesture keeps pushing the deadline forward.
        while True:
            remaining = self.resize_repaint_deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)
        descriptor = self.descriptor
        if descriptor.stopping:
            return
        size = self.geometry
        repainted = await self.repaint_current_geometry()
        host.emit(
            "terminal_repaint_requested",
            source="daemon",
            reason="resize_settled",
            backend=descriptor.backend,
            cols=size[0] if size else None,
            rows=size[1] if size else None,
            applied=repainted,
        )

    def schedule_resize_repaint(self, host: TerminalHost) -> None:
        """Arm (or extend) the trailing repaint for a harness that cannot self-repair.

        Called on every arbitrated geometry change, so it must stay cheap and idempotent:
        the common case is pushing a float forward while a task already waits on it.
        """
        descriptor = self.descriptor
        if not descriptor.needs_resize_repaint:
            return
        self.resize_repaint_deadline = time.monotonic() + RESIZE_REPAINT_SETTLE_SECONDS
        pending = self.resize_repaint_task
        if pending is not None and not pending.done():
            return
        self.resize_repaint_task = self._start(
            self._repaint_when_resize_settles(host),
            name=f"resize-repaint-{descriptor.session_id}",
        )

    async def _repaint_after_truncated_replay(self, host: TerminalHost) -> None:
        repainted = await self.repaint_current_geometry()
        host.emit(
            "terminal_repaint_requested",
            source="daemon",
            reason="truncated_replay",
            backend=self.descriptor.backend,
            applied=repainted,
        )

    def schedule_attach_repaint(self, host: TerminalHost) -> None:
        """Restate an alternate-screen child's screen when its replay was only a window.

        Unconditional on truncation rather than on any judgement about what the bytes
        parsed to, because for this harness class there is nothing to judge: a slice of a
        differential frame stream is complete only if a full repaint happened to fall
        inside it (`replay_needs_repaint`), and neither end can see whether one did. The
        client cannot either — the signal that serves the normal-screen case, "the parse
        produced no scrollback", is meaningless against a buffer that has none by design,
        which is why `handle_client_repaint` refuses these sessions and why they were left
        with no attach-time repair at all. Their only repaint path was a geometry *change*,
        so the workaround was to resize the window by hand.

        The cost the retired OMP attach pulse was carrying does not transfer: that pulse
        bought a ~460-line transcript restatement on every healthy cold attach, while an
        alternate-screen child restates one screen. Deliberately not conditioned on
        `hidden` — a warm pane mass-mounted after a Reload UI parses into its buffer while
        its rendering is paused, so the reveal it is heading for finds a whole screen.

        Ordering is safe wherever this is called from: the pulse's bytes reach this client
        through the subscriber queue registered before the replay snapshot, so they are
        delivered after the replay rather than painted over by it.
        """
        descriptor = self.descriptor
        if not descriptor.replay_needs_repaint:
            return
        if not self.stream.replay_window_truncated():
            return
        now = time.monotonic()
        if now - self.last_client_repaint_ts < CLIENT_REPAINT_MIN_INTERVAL_SECONDS:
            return
        # Shares the stamp with `handle_client_repaint`: both are a browser causing one
        # restatement, and a reconnect storm (desktop and phone returning together) must
        # cost the session one pulse rather than one per socket.
        self.last_client_repaint_ts = now
        self._start(
            self._repaint_after_truncated_replay(host),
            name=f"attach-repaint-{descriptor.session_id}",
        )

    async def handle_client_repaint(self, host: TerminalHost) -> None:
        """A client whose parsed replay produced no scrollback asks the child to restate it.

        Only the client can make that judgement: the daemon sees bytes, not what they parse
        to. Gated on the harness trait rather than the session name — a transcript-in-
        scrollback TUI that repaints its live region is both the only case that can wrap
        the ring into an empty-looking replay and the only one that answers a width pulse
        by re-rendering its transcript. Alternate-screen TUIs (Claude) never qualify:
        their buffer has no scrollback by design, so a client-side request keyed on
        missing scrollback would fire forever. Their own version of this problem — a
        windowed replay of a differential frame stream — is repaired without asking the
        client anything, in `schedule_attach_repaint`.
        """
        if not self.descriptor.repaints_scrollback:
            return
        now = time.monotonic()
        if now - self.last_client_repaint_ts < CLIENT_REPAINT_MIN_INTERVAL_SECONDS:
            return
        # Stamped before the await so concurrent requests cannot interleave two pulses.
        self.last_client_repaint_ts = now
        repainted = await self.repaint_current_geometry()
        if repainted:
            host.emit(
                "terminal_repaint_requested",
                source="browser",
                reason="missing_scrollback",
            )

    # --- client diagnostics -----------------------------------------------------------

    def note_client_diagnostic(
        self, host: TerminalHost, connection_id: str, frame: dict[str, Any]
    ) -> None:
        """Persist a bounded client-side terminal diagnostic to the durable event log.

        The repair layers (terminalHealth.ts and TerminalPane's fit/surface debt) fire in
        production browsers, where the opt-in in-page ring buffer is invisible and lost on
        reload. Recording each firing durably is what makes the layers individually
        auditable — a layer that never fires in months of logs is a removal candidate,
        one that fires daily is load-bearing. Rate-limited per session because the log is
        SQLite-backed; the question this answers needs existence and rough frequency, not
        an exact count.
        """
        phase = frame.get("phase")
        if not isinstance(phase, str) or phase not in (
            CLIENT_REPAIR_PHASES | CLIENT_INPUT_DIAGNOSTIC_PHASES | {CLIENT_PASTE_TRACE_PHASE}
        ):
            return
        now = time.monotonic()
        last_report = self.client_diagnostic_timestamps.get(phase, 0.0)
        if now - last_report < CLIENT_DIAGNOSTIC_MIN_INTERVAL_SECONDS:
            return
        self.client_diagnostic_timestamps[phase] = now
        detail = frame.get("detail")
        payload = json.dumps(detail) if isinstance(detail, dict) else ""
        if phase == CLIENT_PASTE_TRACE_PHASE:
            event_type = CLIENT_PASTE_TRACE_PHASE
            detail_limit = CLIENT_PASTE_TRACE_DETAIL_LIMIT
        elif phase in CLIENT_REPAIR_PHASES:
            event_type = "terminal_client_repair"
            detail_limit = CLIENT_DIAGNOSTIC_DETAIL_LIMIT
        else:
            event_type = "terminal_input_diagnostic"
            detail_limit = CLIENT_DIAGNOSTIC_DETAIL_LIMIT
        host.emit(
            event_type,
            source="browser",
            phase=phase,
            detail=payload[:detail_limit],
            input_owner=self.input_owner == connection_id,
            owner_device=self.input_owner_device,
        )
