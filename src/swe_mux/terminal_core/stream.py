"""One session's output stream: the retained ring, what an attach replays, and the
subscribers every byte fans out to."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..screen_mode import (
    BRACKETED_PASTE_TOGGLE,
    SCREEN_TOGGLE,
    BracketedPasteParser,
    ScreenModeParser,
    StickyModeParser,
)
from ..scrollback import ScrollbackBuffer

#: What `attach` answers: the snapshot and revision it was taken at, the replay kind
#: (`"delta"` or `"attach"`), the payload, the ring position it ends at, and the
#: subscription registered in the same instant.
AttachResult = tuple[dict[str, Any], int, str, bytes, int, "PtySubscriber"]

#: What `take_resync` answers: dropped bytes and chunks, the replay, the current
#: snapshot and revision, the ring position, and an exit frame held back by the gap.
ResyncResult = tuple[int, int, bytes, dict[str, Any], int, int, dict[str, Any] | None]


@dataclass(eq=False, slots=True)
class PtySubscriber:
    queue: asyncio.Queue[bytes | dict[str, Any]]
    resync_pending: bool = False
    dropped_bytes: int = 0
    dropped_chunks: int = 0
    exit_pending: dict[str, Any] | None = None


class TerminalStream:
    """The ring and its readers.

    ``remote_stream`` names the byte stream the PTY's owner counts positions in,
    or answers empty when there is none (`stream_id`); ``rebuilt`` answers whether
    the ring was rebuilt from disk rather than received, which no client cursor
    can be a position in (`delta_covers`).
    """

    def __init__(
        self,
        max_scrollback: int,
        *,
        attach_replay_bytes: int | None,
        stream_id: str,
        remote_stream: Callable[[], str],
        rebuilt: Callable[[], bool],
    ) -> None:
        self.scrollback = ScrollbackBuffer(max_scrollback)
        # What a fresh attach or a resync replays, as distinct from what is
        # retained above. Carried on the stream rather than read per-request so
        # every attach path (browser, adopted supervisor session, tests) is bound
        # by the same policy. ``None`` replays everything retained.
        self.attach_replay_bytes = attach_replay_bytes
        # The stream this ring's positions count when no supervisor names one
        # (`stream_id`). Minted per Session object, so a respawned id, a cold or
        # inactive session rebuilt from disk, and an in-process pane after a
        # restart can never share it.
        self.local_stream_id = stream_id
        self._remote_stream = remote_stream
        self._rebuilt = rebuilt
        self.subscribers: set[PtySubscriber] = set()
        # Two sources for the same fact, deliberately kept apart. The daemon reads
        # the child's own screen switch off the PTY and so has it for every
        # session; the browser reports xterm's active buffer only while a pane is
        # attached and owns input, which makes it corroboration rather than the
        # source of record (`delivery-readiness.md`).
        self.screen = ScreenModeParser()
        self.bracketed_paste = BracketedPasteParser()
        # The reporting modes an attach has to hand back, for the same reason bracketed
        # paste is handed back: set once at startup, never restated, and cleared by the
        # terminal reset a reconnecting pane performs. See `STICKY_PRIVATE_MODES`.
        self.sticky_modes = StickyModeParser()

    @property
    def stream_id(self) -> str:
        """Which byte stream this ring's positions count.

        A client's ``since`` means something only in the stream it was taken from,
        and the cold check alone could not tell streams apart. A supervisor-backed
        session uses the supervisor's ``stream``, which survives a daemon restart
        together with the positions (an adopted ring is seeded at the supervisor's
        position), so a browser's cursor stays good across a reload. Anything
        else - an in-process pane, a session rebuilt from disk, or one under a
        supervisor too old to name its stream - uses the id the Session minted.
        """
        remote = self._remote_stream()
        if isinstance(remote, str) and remote:
            return remote
        return self.local_stream_id

    def subscribe(self, maxsize: int = 1024) -> PtySubscriber:
        subscriber = PtySubscriber(asyncio.Queue(maxsize=maxsize))
        self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: PtySubscriber) -> None:
        self.subscribers.discard(subscriber)

    def attach(
        self,
        since: int | None,
        stream: str | None,
        snapshot: Callable[[], tuple[dict[str, Any], int]],
    ) -> AttachResult:
        """One atomic attach decision: what this client gets, and from where.

        ``since`` is the ring position the client has already parsed up to — the
        position a previous attach's ``replay_end`` reported plus every live byte
        it received since. When the ring still holds everything after it, the
        attach is a **delta** (kind ``"delta"``): exactly the missed bytes, into a
        terminal the client did *not* reset. That is what makes a reconnect
        lossless — the scrollback the client spent a session parsing survives a
        tab switch, a minimize, or a phone freeze, instead of being discarded and
        rebuilt from a bounded window that reconstructs roughly one screen.

        Everything else — no ``since``, a ``stream`` other than this ring's
        (`stream_id`) or none at all, a gap the ring no longer covers, a gap
        larger than the attach budget (continuity would cost more parse than a
        fresh window) — falls back to the full bounded replay (kind
        ``"attach"``), which is exactly the previous behaviour. The validation is
        what makes offering ``since`` always safe for the client: a wrong answer
        here corrupts a terminal silently, so nothing but a provably-covered gap
        in the same stream is served as a delta. A position check alone could not
        promise that: a respawn restarts positions under the same session id, and
        a cursor from the old stream can land inside the new range by chance.

        This has no await points, so the payload, the returned ring ``position``,
        the ``snapshot`` it reads and the subscription describe one instant; a
        caller reads `stream_id` before its next await to describe it too.
        """
        subscriber = self.subscribe()
        position = self.scrollback.position
        current, revision = snapshot()
        if (
            since is not None
            and stream is not None
            and stream == self.stream_id
            and self.delta_covers(since, position)
        ):
            payload = self.scrollback.bytes_since(since)
            return current, revision, "delta", payload, position, subscriber
        return current, revision, "attach", self.replay_bytes(), position, subscriber

    def delta_covers(self, since: int | None, position: int) -> bool:
        """Whether the ring provably holds every byte after ``since``.

        The strict-retention check matters because ``bytes_since`` answers an
        uncovered position with the whole buffer — correct for detection cursors,
        catastrophic for a client that would append it after a hole.
        """
        if self._rebuilt():
            # A cold session's ring was rebuilt from disk, so its positions
            # describe a *different* stream from the one any client parsed before
            # the crash. A `since` from that stream can still land inside this
            # range by coincidence, and a delta served across the boundary is
            # appended to a terminal that was never reset - silent corruption, in
            # the one case where the operator is reading the pane for evidence.
            return False
        if since is None or not 0 <= since <= position:
            return False
        gap = position - since
        if gap > self.scrollback.size:
            return False
        budget = self.attach_replay_bytes
        return budget is None or budget <= 0 or gap <= budget

    def replay_bytes(self) -> bytes:
        """Retained output for a fresh attach, bounded and self-contained.

        A bounded window can begin after the child selected the alternate screen,
        which would leave the client painting a full-screen TUI into its *normal*
        buffer - every repaint growing scrollback instead of overwriting one
        screen, which is precisely the cost the bound exists to remove. The ring
        tracks the mode from the stream itself (`screen_mode.py`), so it can
        restate it.

        Only restated when the window carries no toggle of its own. A window that
        contains its own answer, and prefixing would override a child that
        deliberately left the alternate screen inside it.
        """
        replay = self.scrollback.tail(self.attach_replay_bytes)
        suffix = b""
        # Boundary guard, not a diagnosis: the 2026-08-06 black OMP panes were an
        # xterm bundling defect crashing on DECRQM (see
        # frontend/scripts/patch-xterm-requestmode.mjs), not an unmatched paint
        # bracket. The guard stays because the hazard it names is real for any
        # harness that brackets paints: the replay snapshot may land between a
        # DEC 2026 begin/end or an autowrap off/on pair, and a window ending on
        # the open half leaves a fresh client withholding (2026) or mis-wrapping
        # (autowrap) everything after it until the live stream happens to close
        # the bracket - forever, if the child stops painting. Close only modes
        # whose complete opening sequence is newer than their complete closing
        # sequence; this does not splice bytes into a partial CSI at the
        # snapshot boundary.
        if replay.rfind(b"\x1b[?2026h") > replay.rfind(b"\x1b[?2026l"):
            suffix += b"\x1b[?2026l"
        if replay.rfind(b"\x1b[?7l") > replay.rfind(b"\x1b[?7h"):
            suffix += b"\x1b[?7h"
        if len(replay) == self.scrollback.size:
            return replay + suffix
        preamble = b""
        # Agent CLIs enable bracketed paste once at startup and never restate it, so
        # a bounded window over a long session never carries it. A reconnecting pane
        # resets its terminal, and without this it pastes unwrapped: xterm turns each
        # newline into Enter, and the CLI submits the paste a line at a time.
        if self.bracketed_paste.enabled and not BRACKETED_PASTE_TOGGLE.search(replay):
            preamble += b"\x1b[?2004h"
        if self.screen.mode == "alternate" and not SCREEN_TOGGLE.search(replay):
            preamble += b"\x1b[?1049h"
        # The rest of the same family, and for the same reason. Losing the mouse group
        # here is what made a phone swipe do nothing on any session deep enough to
        # outgrow this window: with no mouse modes the browser reports no mouse, so the
        # drag has nothing to forward and falls back to scrolling an alternate screen
        # that has no scrollback to scroll.
        preamble += self.sticky_modes.preamble(replay)
        return preamble + replay + suffix

    def replay_window_truncated(self) -> bool:
        """Whether retention outgrew the replay budget, so an attach gets a window.

        The mirror of the `len(replay) == self.scrollback.size` branch above, asked
        without materializing the bytes. `False` means an attach replays everything the
        ring holds, which is self-contained by construction; `True` means the client
        is handed a slice, which for an alternate-screen child is a slice of a
        differential frame stream (`harness.replay_needs_repaint`).
        """
        limit = self.attach_replay_bytes
        return limit is not None and limit > 0 and self.scrollback.size > limit

    def schedule_resync(self, subscriber: PtySubscriber, rejected: bytes | None = None) -> None:
        if rejected is not None:
            subscriber.dropped_bytes += len(rejected)
            subscriber.dropped_chunks += 1
        while not subscriber.queue.empty():
            queued = subscriber.queue.get_nowait()
            if isinstance(queued, bytes):
                subscriber.dropped_bytes += len(queued)
                subscriber.dropped_chunks += 1
        if not subscriber.resync_pending:
            subscriber.resync_pending = True
            subscriber.queue.put_nowait({"type": "resync"})

    def publish_control(self, frame: dict[str, Any]) -> None:
        """Fan out a small control frame. Unlike output it is never worth a resync:
        a client mid-resync gets the current geometry with its resync `update`."""
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self.schedule_resync(subscriber)

    def feed(self, data: bytes) -> None:
        """Take one chunk of PTY output: the standing-state parsers, the ring, and
        every attached reader, in the order the daemon's own fanout uses.

        For a host that reads output for the terminal alone (the gateway); the
        daemon's fanout does the same three steps with its own parsers in between.
        """
        self.screen.feed(data)
        self.bracketed_paste.feed(data)
        self.sticky_modes.feed(data)
        self.scrollback.append(data)
        self.publish_output(data)

    def publish_output(self, data: bytes) -> None:
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                subscriber.dropped_bytes += len(data)
                subscriber.dropped_chunks += 1
                continue
            try:
                subscriber.queue.put_nowait(data)
            except asyncio.QueueFull:
                self.schedule_resync(subscriber, data)

    def publish_update(self, frame: dict[str, Any]) -> None:
        """Fan out a snapshot `update`; a subscriber mid-resync gets the current one
        with its resync instead."""
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self.schedule_resync(subscriber)

    def publish_exit(self, frame: dict[str, Any]) -> None:
        """Fan out the `exit` frame, which a subscriber mid-resync must still get."""
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                subscriber.exit_pending = frame
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self.schedule_resync(subscriber)
                subscriber.exit_pending = frame

    def take_resync(
        self,
        subscriber: PtySubscriber,
        snapshot: Callable[[], tuple[dict[str, Any], int]],
    ) -> ResyncResult:
        """Capture a deterministic recovery boundary without yielding the event loop.

        Bounded by the same budget as a fresh attach, and for the same reason: the
        client resets its terminal on a resync, so what it receives is a complete
        replay into an empty buffer rather than a patch. A resync is also triggered
        by a client that could not keep up, which is the worst moment to hand one
        the largest possible payload. Deliberately never a delta even though the
        ring may still cover the drop, for that same reason.

        The returned ring position re-anchors the client's byte cursor: the drop
        broke its count, and without a fresh anchor every later reconnect would
        offer a stale ``since`` and pay a full replay it did not need.
        """
        dropped_bytes = subscriber.dropped_bytes
        dropped_chunks = subscriber.dropped_chunks
        replay = self.replay_bytes()
        position = self.scrollback.position
        current, revision = snapshot()
        exit_frame = subscriber.exit_pending
        subscriber.resync_pending = False
        subscriber.dropped_bytes = 0
        subscriber.dropped_chunks = 0
        subscriber.exit_pending = None
        return dropped_bytes, dropped_chunks, replay, current, revision, position, exit_frame

    def reseed(self, data: bytes, position: int) -> None:
        """Replace the ring with bytes received after a hole, and resync every reader.

        The parsers that derive standing state from the stream are rebuilt from
        those bytes, because whatever they had read before the hole may have
        ended mid-sequence; every attached terminal is resynced, since its cursor
        is in the old ring.
        """
        self.scrollback.seed(data, position)
        self.screen = ScreenModeParser()
        self.bracketed_paste = BracketedPasteParser()
        self.sticky_modes = StickyModeParser()
        self.screen.feed(data)
        self.bracketed_paste.feed(data)
        self.sticky_modes.feed(data)
        for subscriber in tuple(self.subscribers):
            self.schedule_resync(subscriber)
