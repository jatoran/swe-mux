"""Daemon-native session-settle watches: being *told* a worker finished.

An orchestrator running many workers has no way to be told when one of them
stops working. It polls `/api/sessions` (or `list_sessions`) with ad-hoc
scripts, burning a turn per poll, and the only observable it gets is `idle` -
which also means "stalled at a question nobody answered". A watch moves that
loop into the daemon, where the state already lives and costs nothing to read.

**A watch is a read that matures into exactly one bounded message.** It grants
no authority the caller did not already have: the target is only ever read (the
same `SessionRecord.state` `get_session` returns), and the one write it produces
is a fixed daemon-authored template into the *caller's own* prompt queue, as a
`rule` sender - the deterministic-observer path the land queue's handback
already uses (`design/features/land-queue.md`, `prompt-queue.md`). Nothing here
writes a PTY, addresses a third session, spends a budget, or triggers a scan.

**And it is staged armed, because the watch is the consent.** A notice nobody
delivers is a notice that did not happen, and the whole point of a watch is to
replace polling with being told: a watcher that has to be hand-fed its own
answer by an operator is back to waiting on a human. The Phase 5 floor - a
non-human sender's write ends at a human - is about an *unsolicited* write
appearing in somebody's terminal, and this is the opposite of that. So the floor
is narrowed by exactly the width of the request and no further, the same four
bounds the land handback carries, each of which is the watch's own shape rather
than a new permission (`_notice_arming`):

- **Only the watcher.** The target is `watcher_session_id` and no argument could
  make it another session, the same way `watch_session` has no recipient.
- **Only this service's templates.** No model writes any part of `_notice_body`.
- **Only the run that armed it.** A conversation that rolled over never asked,
  which is the run binding every auto-delivery grant carries. The sweep already
  drops a rolled watch outright; this repeats the check at write time because
  the `stop()` flush does not go through the sweep.
- **Once.** `_resolve` pops the watch before staging, so one watch produces one
  notice by construction - the cap the land queue has to claim atomically is 1
  here for structural reasons, and there is nothing to count.

Read at write time rather than trusted from arming: `session_watch_enabled`,
which is this feature's own switch and has no per-Project half (`config.py`), so
an operator who turns watches off mid-flight turns off the unattended half too.
Refusing to arm is never refusing the notice - it is enqueued as the draft it
always used to be, and a person can still send it.

Three rules decide when it fires, and the third is the one the operator asked
for by name:

1. **Ended fires unconditionally.** A session that exited or crashed will never
   work again, so waiting for a working edge would guarantee a timeout.
2. **Settled fires on a working -> idle/awaiting edge that holds.** It must have
   been *observed working* first, because an orchestrator arms a watch in the
   seconds before status detection catches up with the prompt it just sent, and
   the pre-existing `idle` is not the answer to "did my worker finish".
3. **The timeout always fires if nothing else did.** A hung worker can never
   mean silence: either the watch resolves or the watch says it did not, and
   both notices name the case and the target's state at that moment.

Watches are ephemeral by construction - they live in this process, die with the
watcher session, are bounded per watcher, and are one-shot. A daemon that stops
flushes every open watch as a notice rather than dropping them silently, because
a restart is a routine act in this product (`CLAUDE.md`) and silence is the one
outcome this feature exists to remove.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .background_tasks import background
from .harness import is_agent_harness
from .project_scope import (
    ProjectScope,
    record_scope,
    resolve_project_scope,
    split_qualified_target,
)
from .prompt_queue import QueueError

log = logging.getLogger(__name__)

WATCH_LOOP = "session_watch"

#: How often the sweep re-reads the fleet, matching the land queue's cadence.
#: A watch is measured in minutes and the 120-second settle hold below dominates
#: its latency, so a tighter interval would buy nothing but wakeups: five-second
#: granularity on a multi-minute timeout is not a difference anyone can act on.
#: The pass itself is near-free - it walks at most
#: `watchers x session_watch_max_per_session` entries and reads fields already in
#: memory - and returns on the first line when nothing is armed.
SWEEP_INTERVAL_SECONDS = 5.0

#: How long a settled state must hold before the watch calls it settled.
#:
#: Not taste - the measurement is push.py's, from a 10-hour 17-session day:
#: **89 of 211 idle transitions were back to `working` inside 120 s** with no
#: human input in between (an agent-turn-complete landing mid-turn, the PTY
#: watchdog reading an idle prompt during a pause, startup settling). Firing on
#: the first idle edge would therefore tell an orchestrator "your worker
#: finished" and be wrong about two times in five. `push.WAITING_SETTLE_SECONDS`
#: holds the same number for the same reason; a contract test pins them equal.
#:
#: Applied to `awaiting` as well as `idle`, and for the same reason: an
#: auto-answered approval flickers through `awaiting` in well under a second
#: (`memory: codex-approval-signal-gap`).
SETTLE_HOLD_SECONDS = 120.0

#: Annotation kinds that mean work is running right now. Duplicated from
#: `session.RUNNING_ACTIVITY_KINDS` for the same reason `push.py` duplicates it:
#: this module must not drag the session machinery in behind it. `session.py`
#: owns the definition and a contract test pins them equal.
RUNNING_ACTIVITY_KINDS = frozenset({"subagents", "background_tasks"})

#: The settled half of the status contract. `awaiting` is settled *and* is the
#: case the operator most needs told apart from a finish, which is why the
#: notice always names the state and its sub-reason rather than saying "done".
SETTLED_STATES = frozenset({"idle", "awaiting"})
ENDED_STATES = frozenset({"exited", "crashed"})

#: What counts as working for the "left working" edge. Deliberately *not*
#: `starting`: a session that just booted reaches `idle` through
#: `startup_quiet_fallback`, which is inferred from PTY quiet and is not even
#: input-ready. Counting it would fire a "your worker finished" notice before
#: the seed prompt had run a single turn.
WORKING_STATES = frozenset({"working"})

DEFAULT_TIMEOUT_MINUTES = 30
MIN_TIMEOUT_MINUTES = 1

#: Bounds for the synchronous `await_session` read. The default sits under the
#: MCP tool-call timeout every targeted harness ships (60 s is the tightest
#: measured), because a wait that outlives the caller's own tool timeout
#: answers nobody: the harness kills the call and the agent reads an error
#: where a timeout *result* would have told it the state and invited another
#: call. The ceiling exists for a caller that raised its own tool timeout and
#: knows it; anything longer belongs to `watch_session`, whose notice does not
#: hold an HTTP request open. Constants rather than config keys: they bound a
#: read, and a read's bounds are the service's to state.
AWAIT_DEFAULT_TIMEOUT_SECONDS = 50.0
AWAIT_MIN_TIMEOUT_SECONDS = 5.0
AWAIT_MAX_TIMEOUT_SECONDS = 600.0
#: Concurrent awaiters one session may hold. More than a couple means the
#: caller is fanning out blocking reads it should be arming watches for.
AWAIT_MAX_PER_SESSION = 4

#: The cases an `await_session` call can resolve with. `already_settled` is the
#: immediate answer for a target that is settled *and has held it* at call
#: time - returning it beats holding the caller's turn open to re-learn a fact
#: the transition ledger already holds.
AWAIT_CASES = ("settled", "already_settled", "ended", "timeout", "daemon_stopped")

#: The case that resolved a watch, on the notice and on the event.
CASES = ("settled", "ended", "timeout", "daemon_stopped")

#: What a dropped watch is dropped for. These never produce a notice: there is
#: nobody left to read one.
DROP_REASONS = ("watcher_ended", "watcher_rolled")

_SENDER_ID = "session_watch"
_SENDER_LABEL = "Session watch"

#: Stated on every result rather than derived, because "queued" alone is not
#: something a caller can act on. A `rule` sender is not self-arming in general,
#: but this notice is the bounded deterministic answer to a request this very
#: session made, so it is staged armed by naming the watch in `solicited_by` -
#: exactly the narrowing the land-queue handback established
#: (`agent-messaging.md`, `land-queue.md`). Armed is still not delivered: every
#: auto-delivery gate the receiver has can refuse it.
NOTICE_DELIVERY_NOTE = (
    "the notice is the bounded answer to the watch you armed, so it is staged "
    "armed rather than as an inert draft, exactly like a land-queue handback. "
    "Armed is not delivered: your own auto-delivery grant, head-of-line order, "
    "delivery readiness, quiet hours, and the caps each still decide the send, "
    "and if any of them refuses it the notice waits in your queue for a person"
)

#: Why a notice was staged as a draft rather than armed, recorded on the
#: resolution alongside `armed` itself. A draft nobody delivered otherwise reads,
#: from the log and the event stream alone, exactly like a notice that arrived -
#: which is the failure mode that produced the land handback's audit fields.
ARMING_REASON_OK = "answering this session's own watch request"
ARMING_REASON_DISABLED = "session watches are not enabled on this install"
ARMING_REASON_UNKNOWN_RUN = "the watching conversation could not be identified"
ARMING_REASON_ROLLED = "the watching conversation was replaced"
ARMING_REASON_NOT_ARMED = "the queue staged the notice as a draft"


class WatchRefusal(QueueError):
    """A watch the service will not arm, with a code a caller can act on.

    A `QueueError` so the MCP layer's existing typed-refusal path carries it
    back as a result rather than a JSON-RPC fault, which an agent would retry
    blindly.
    """


@dataclass(slots=True)
class Watch:
    """One armed, one-shot watch. Lives in memory and nowhere else."""

    id: str
    watcher_session_id: str
    #: The conversation that asked. A watch is a promise to *this* run: after an
    #: in-CLI `/clear` the agent that armed it retains nothing, and a notice
    #: about a watch it never made would read as its own recollection
    #: (`backends.md`, `mux-mcp.md`).
    watcher_run_id: str
    target_session_id: str
    target_name: str
    target_project_id: str
    timeout_minutes: int
    created_at: float
    deadline: float
    #: Whether the target has been seen `working` since the watch was armed.
    #: Rule 2 above; `ended` bypasses it and the timeout backstops it.
    observed_working: bool = False
    #: When the current settled spell began, or None while it is not settled.
    #: Reset by anything that leaves the settled set, so a flap restarts the
    #: hold rather than accumulating across it.
    settled_since: float | None = None
    armed_state: str = ""
    reason: str = ""


@dataclass(slots=True)
class _Awaiter:
    """One in-flight synchronous wait. The answer travels in-band on `future`.

    The structural difference from a `Watch` is the sink: a watch matures into
    a queue message, an awaiter fulfils the caller's own blocked tool call, so
    nothing here touches the prompt queue, arming, or delivery at all - the
    caller is mid-turn holding the HTTP request open, which is exactly the one
    moment the delivery machinery does not apply to it.
    """

    id: str
    caller_session_id: str
    target_session_id: str
    target_name: str
    #: Which positive outcomes resolve it ("settled" and/or "ended"). An ended
    #: target always resolves whatever this says, because nothing further can
    #: ever happen to it and holding the caller for the timeout would be a
    #: promise about a corpse.
    until: frozenset[str]
    created_at: float
    deadline: float
    #: Created with `asyncio.get_running_loop().create_future()` by
    #: `await_settle`, which is the only constructor - so the future always
    #: belongs to the loop the sweep resolves it on.
    future: asyncio.Future[dict[str, Any]]
    observed_working: bool = False
    settled_since: float | None = None


@dataclass(slots=True)
class _Counters:
    armed: int = 0
    resolved: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    notice_failures: int = 0
    #: Notices that reached the queue armed. Reported next to `resolved` because
    #: the two diverging is the quiet failure: watches maturing while nothing is
    #: delivered looks, from every other counter, like a working service.
    armed_notices: int = 0
    awaits: int = 0
    await_resolved: dict[str, int] = field(default_factory=dict)


def running_work(record: Any) -> bool:
    """Whether the target has work in flight that will resume it unattended.

    The same two sources `push.py` reads, and for the same reason: an `idle`
    session with live subagents has *not* finished, it has handed off and will
    come back on its own. Reporting that as settled is the false "done" this
    feature would otherwise ship - and the timeout is what stops the suppression
    from becoming silence.
    """
    if str(getattr(record, "idle_reason", "") or "") == "waiting_on_background":
        return True
    standing = getattr(record, "standing_activity", ()) or ()
    return any(
        str(getattr(activity, "kind", "")) in RUNNING_ACTIVITY_KINDS
        for activity in standing
    )


def describe_state(record: Any) -> str:
    """The target's status as one line: state, sub-reason, standing work.

    Every notice and every result carries this rather than the bare state,
    because `idle` alone is the ambiguity the whole feature exists to remove -
    "finished", "waiting on you", and "waiting on its own subagents" are three
    different answers that all render as `idle` (`status-detection.md`).
    """
    state = str(getattr(record, "state", "") or "unknown")
    parts = [state]
    if state == "awaiting":
        reason = str(getattr(record, "awaiting_reason", "") or "")
        if reason:
            parts.append(reason)
    if state == "idle":
        reason = str(getattr(record, "idle_reason", "") or "")
        if reason:
            parts.append(reason)
    kinds = sorted(
        {
            str(getattr(activity, "kind", ""))
            for activity in (getattr(record, "standing_activity", ()) or ())
            if str(getattr(activity, "kind", "")) in RUNNING_ACTIVITY_KINDS
        }
    )
    if kinds:
        parts.append("background work running: " + ", ".join(kinds))
    return " · ".join(part for part in parts if part)


def _humanize(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{int(round(seconds))}s"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{int(round(minutes))}m"
    return f"{minutes / 60.0:.1f}h"


class SessionWatchService:
    """Arming, sweeping, and the one bounded notice a watch produces.

    Every collaborator is injected and none of them is HTTP or MCP, which is
    what lets the whole lifecycle be driven in a test with plain objects. The
    service owns the bounds; the MCP tool is a caller (CP §7.1).
    """

    def __init__(
        self,
        *,
        sessions: Any,
        projects: Any,
        config: Any,
        events: Any = None,
        queue_message: Callable[..., Awaitable[Any]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessions = sessions
        self._projects = projects
        self._config = config
        self._events = events
        self._queue_message = queue_message
        self._clock = clock
        self._watches: dict[str, Watch] = {}
        self._awaiters: dict[str, _Awaiter] = {}
        self._counters = _Counters()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        background.start(WATCH_LOOP, self._run)

    async def stop(self) -> None:
        """Stop sweeping, then flush every open watch as a notice.

        The flush is the point. Watches are in-memory and a daemon restart under
        live sessions is a routine act here, so without it a restart would drop
        an orchestrator's watches and hand it exactly the silence the timeout
        exists to prevent. The prompt queue is durable and is stopped *after*
        this service in `server.py`, so the notice survives the restart that
        caused it.
        """
        await background.stop(WATCH_LOOP)
        for watch in tuple(self._watches.values()):
            await self._resolve(watch, "daemon_stopped")
        # In-flight synchronous waits get a result, not an abandoned future: the
        # HTTP connection is dying with the daemon anyway, but a pending future
        # nobody resolves raises "exception was never retrieved" from a
        # finalizer at some later test's expense.
        for awaiter in tuple(self._awaiters.values()):
            self._finish_await(awaiter, "daemon_stopped", record=None)

    async def _run(self) -> None:
        while True:
            # Outside the guard: time spent sleeping is not this loop's cost,
            # and counting it as such is how a cheap loop ends up at the top of
            # the `costliest` list (`background_tasks.py`).
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            with background.iteration(WATCH_LOOP):
                await self.tick()

    # -- bounds ---------------------------------------------------------------

    def _enabled(self) -> bool:
        return bool(getattr(self._config, "session_watch_enabled", True))

    def _max_per_session(self) -> int:
        return max(0, int(getattr(self._config, "session_watch_max_per_session", 8)))

    def _max_minutes(self) -> int:
        return max(
            MIN_TIMEOUT_MINUTES,
            int(getattr(self._config, "session_watch_max_minutes", 240)),
        )

    def _resolve_timeout(self, requested: Any) -> int:
        ceiling = self._max_minutes()
        if requested is None or requested == "":
            return min(DEFAULT_TIMEOUT_MINUTES, ceiling)
        # `0` is deliberately not the omitted case. An agent that passes it means
        # "no timeout", which is the one shape this service refuses to promise -
        # and silently substituting the default would grant a bound it did not
        # ask for while looking like it had been honoured.
        try:
            minutes = int(requested)
        except (TypeError, ValueError) as exc:
            raise WatchRefusal(
                "invalid_timeout",
                "timeout_minutes must be a whole number of minutes",
                status=400,
            ) from exc
        if minutes < MIN_TIMEOUT_MINUTES or minutes > ceiling:
            raise WatchRefusal(
                "invalid_timeout",
                f"timeout_minutes must be between {MIN_TIMEOUT_MINUTES} and "
                f"{ceiling} minutes",
                status=400,
            )
        return minutes

    # -- arming ---------------------------------------------------------------

    async def watch(
        self,
        caller: Any,
        *,
        target: str,
        timeout_minutes: Any = None,
        project: str = "",
    ) -> dict[str, Any]:
        """Arm a one-shot watch, or return the one already watching this target."""
        if not self._enabled():
            raise WatchRefusal(
                "session_watch_disabled",
                "session watches are disabled on this mux install.",
                status=403,
            )
        if not is_agent_harness(getattr(caller.record, "backend", "")):
            # The notice is a queue item, and the queue targets live agent
            # sessions only. Refusing here rather than at fire time is the
            # difference between an answer now and a silence in half an hour.
            raise WatchRefusal(
                "not_agent_watcher",
                "only an agent session can be watched *for*: the notice is a "
                "prompt-queue item, and the queue never targets a shell.",
                status=400,
            )
        minutes = self._resolve_timeout(timeout_minutes)
        watched, scope = self._resolve_target(caller, target, project)
        record = watched.record
        existing = self._existing(str(caller.record.id), str(record.id))
        if existing is not None:
            # One-shot and idempotent: an orchestrator re-arming in a loop gets
            # the watch it already has rather than N copies of one notice.
            return {
                **self._view(existing, record, scope),
                "status": "already_watching",
                "note": (
                    "You are already watching this session; the existing watch "
                    "is unchanged and still one-shot. Nothing was armed twice."
                ),
            }
        open_watches = self._count_for(str(caller.record.id))
        ceiling = self._max_per_session()
        if open_watches >= ceiling:
            raise WatchRefusal(
                "watch_limit_reached",
                f"this session already holds {open_watches} open watches "
                f"(limit {ceiling}). Wait for one to resolve, or watch fewer "
                "sessions at a time.",
                status=429,
            )
        now = self._clock()
        watch = Watch(
            id=f"watch_{uuid.uuid4().hex[:12]}",
            watcher_session_id=str(caller.record.id),
            watcher_run_id=str(getattr(caller.record, "agent_run_id", "") or ""),
            target_session_id=str(record.id),
            target_name=str(getattr(record, "name", "") or record.id),
            target_project_id=str(getattr(record, "project_id", "") or ""),
            timeout_minutes=minutes,
            created_at=now,
            deadline=now + minutes * 60.0,
            observed_working=str(record.state) in WORKING_STATES,
            armed_state=str(record.state),
        )
        self._watches[watch.id] = watch
        self._counters.armed += 1
        log.info(
            "session_watch_armed watch_id=%s watcher=%s target=%s timeout_min=%s state=%s",
            watch.id,
            watch.watcher_session_id,
            watch.target_session_id,
            minutes,
            watch.armed_state,
        )
        await self._emit(
            "session_watch_armed",
            watch,
            state=watch.armed_state,
            timeout_minutes=minutes,
        )
        return {**self._view(watch, record, scope), "status": "watching"}

    # -- synchronous waits ----------------------------------------------------

    def _resolve_await_timeout(self, requested: Any) -> float:
        if requested is None or requested == "":
            return AWAIT_DEFAULT_TIMEOUT_SECONDS
        try:
            seconds = float(requested)
        except (TypeError, ValueError) as exc:
            raise WatchRefusal(
                "invalid_timeout",
                "timeout_seconds must be a number of seconds",
                status=400,
            ) from exc
        if (
            seconds < AWAIT_MIN_TIMEOUT_SECONDS
            or seconds > AWAIT_MAX_TIMEOUT_SECONDS
        ):
            raise WatchRefusal(
                "invalid_timeout",
                f"timeout_seconds must be between {AWAIT_MIN_TIMEOUT_SECONDS:g} "
                f"and {AWAIT_MAX_TIMEOUT_SECONDS:g} seconds; for anything "
                "longer, arm watch_session instead - its notice does not hold "
                "your tool call open.",
                status=400,
            )
        return seconds

    @staticmethod
    def _resolve_until(requested: Any) -> frozenset[str]:
        if requested is None or requested == "" or requested == []:
            return frozenset({"settled", "ended"})
        values = requested if isinstance(requested, (list, tuple)) else [requested]
        cleaned = {str(value).strip() for value in values}
        unknown = cleaned - {"settled", "ended"}
        if unknown or not cleaned:
            raise WatchRefusal(
                "invalid_until",
                'until entries must be "settled" or "ended"',
                status=400,
            )
        return frozenset(cleaned)

    async def await_settle(
        self,
        caller: Any,
        *,
        target: str,
        until: Any = None,
        timeout_seconds: Any = None,
        project: str = "",
    ) -> dict[str, Any]:
        """Block until the target settles, ends, or the bounded timeout elapses.

        The synchronous sibling of `watch()`, sharing its settle rules and its
        sweep: same working-edge requirement, same `SETTLE_HOLD_SECONDS`, same
        "an ended target answers unconditionally". What differs is the sink -
        the answer fulfils this call instead of maturing into a queue message -
        so nothing here stages, arms, or delivers anything, and no grant or
        readiness machinery applies. A timeout is a *result* carrying the
        target's state, not an error: the caller re-calls to keep waiting,
        which is what keeps each call inside the harness's own tool timeout.

        A target that is already settled and has *held* it for the settle
        window answers immediately (`already_settled`), read from the record's
        own `state_since` - that is what lets the re-call loop converge on a
        worker that finished between two polls, where the edge rule alone
        would wait forever for a working edge that already passed.
        """
        if not self._enabled():
            raise WatchRefusal(
                "session_watch_disabled",
                "session watches are disabled on this mux install.",
                status=403,
            )
        wanted = self._resolve_until(until)
        seconds = self._resolve_await_timeout(timeout_seconds)
        watched, scope = self._resolve_target(caller, target, project, allow_ended=True)
        record = watched.record
        now = self._clock()
        base = {
            "target_session_id": str(record.id),
            "target_name": str(getattr(record, "name", "") or record.id),
            "project_scope": scope.requested,
            "settle_hold_seconds": int(SETTLE_HOLD_SECONDS),
        }
        if str(record.state) in ENDED_STATES:
            self._counters.awaits += 1
            self._count_await("ended")
            return {
                **base,
                "case": "ended",
                "target_state": describe_state(record),
                "waited_seconds": 0,
                "note": (
                    "The session had already ended when you asked. Its record "
                    "stays readable through get_session, "
                    "list_sessions(include_ended), and history."
                ),
            }
        if "settled" in wanted:
            state_since = float(getattr(record, "state_since", 0.0) or 0.0)
            held = now - state_since if state_since else 0.0
            if (
                str(record.state) in SETTLED_STATES
                and not running_work(record)
                and held >= SETTLE_HOLD_SECONDS
            ):
                self._counters.awaits += 1
                self._count_await("already_settled")
                return {
                    **base,
                    "case": "already_settled",
                    "target_state": describe_state(record),
                    "waited_seconds": 0,
                    "note": (
                        "The session was already settled and had held it for "
                        f"{_humanize(held)} when you asked, so this answered "
                        "immediately. Settled means it stopped working, not "
                        "that it succeeded - read the session before acting "
                        "on it."
                    ),
                }
        open_awaits = sum(
            1
            for awaiter in self._awaiters.values()
            if awaiter.caller_session_id == str(caller.record.id)
        )
        if open_awaits >= AWAIT_MAX_PER_SESSION:
            raise WatchRefusal(
                "await_limit_reached",
                f"this session already holds {open_awaits} open waits "
                f"(limit {AWAIT_MAX_PER_SESSION}). Use watch_session to be "
                "told about many sessions at once.",
                status=429,
            )
        awaiter = _Awaiter(
            id=f"await_{uuid.uuid4().hex[:12]}",
            caller_session_id=str(caller.record.id),
            target_session_id=str(record.id),
            target_name=str(getattr(record, "name", "") or record.id),
            until=wanted,
            created_at=now,
            deadline=now + seconds,
            future=asyncio.get_running_loop().create_future(),
            observed_working=str(record.state) in WORKING_STATES,
        )
        self._awaiters[awaiter.id] = awaiter
        self._counters.awaits += 1
        log.info(
            "session_await_started await_id=%s caller=%s target=%s timeout_s=%s state=%s",
            awaiter.id,
            awaiter.caller_session_id,
            awaiter.target_session_id,
            seconds,
            record.state,
        )
        try:
            result = await awaiter.future
        finally:
            self._awaiters.pop(awaiter.id, None)
        return {**base, **result}

    def _advance_awaiter(self, awaiter: _Awaiter, now: float) -> None:
        if awaiter.future.done():
            self._awaiters.pop(awaiter.id, None)
            return
        target = self._sessions.sessions.get(awaiter.target_session_id)
        if target is None:
            self._finish_await(awaiter, "ended", record=None)
            return
        record = target.record
        state = str(record.state)
        if state in ENDED_STATES:
            # Unconditional whatever `until` says: nothing further can happen.
            self._finish_await(awaiter, "ended", record=record)
            return
        if state in WORKING_STATES:
            awaiter.observed_working = True
            awaiter.settled_since = None
        elif (
            "settled" in awaiter.until
            and state in SETTLED_STATES
            and awaiter.observed_working
            and not running_work(record)
        ):
            if awaiter.settled_since is None:
                awaiter.settled_since = now
            elif now - awaiter.settled_since >= SETTLE_HOLD_SECONDS:
                self._finish_await(awaiter, "settled", record=record)
                return
        else:
            awaiter.settled_since = None
        if now >= awaiter.deadline:
            self._finish_await(awaiter, "timeout", record=record)

    def _finish_await(self, awaiter: _Awaiter, case: str, *, record: Any) -> None:
        self._awaiters.pop(awaiter.id, None)
        self._count_await(case)
        state = describe_state(record) if record is not None else "ended (session gone)"
        waited = max(0, int(self._clock() - awaiter.created_at))
        notes = {
            "settled": (
                "Settled means it stopped working, not that it succeeded - "
                "read the session before acting on it. `awaiting` in the "
                "state above means it is blocked on a person, not finished."
            ),
            "ended": (
                "The session is gone. Its record stays readable through "
                "get_session, list_sessions(include_ended), and history."
            ),
            "timeout": (
                "The wait's own bound elapsed, which is a normal result, not "
                "an error: the state above is current, and calling "
                "await_session again continues waiting from here. For waits "
                "measured in many minutes, arm watch_session instead."
            ),
            "daemon_stopped": (
                "The daemon stopped before this wait resolved. Re-call "
                "await_session once it is back."
            ),
        }
        result = {
            "case": case,
            "target_state": state,
            "waited_seconds": waited,
            "note": notes.get(case, ""),
        }
        if not awaiter.future.done():
            awaiter.future.set_result(result)
        log.info(
            "session_await_resolved await_id=%s caller=%s target=%s case=%s state=%r waited_s=%s",
            awaiter.id,
            awaiter.caller_session_id,
            awaiter.target_session_id,
            case,
            state,
            waited,
        )

    def _count_await(self, case: str) -> None:
        self._counters.await_resolved[case] = (
            self._counters.await_resolved.get(case, 0) + 1
        )

    # -- the open-request evidence auto-delivery reads ------------------------

    #: How long past a watch's own deadline the watcher's grant stays held: the
    #: notice is staged at resolution and still has to travel the queue's
    #: stability window, so cutting the hold exactly at the deadline would lapse
    #: the grant in the seconds between staging and delivery.
    WINDOW_GRACE_SECONDS = 300.0

    async def origin_windows(
        self, session_ids: Any, since: float
    ) -> dict[str, dict[str, Any]]:
        """Open watches per watcher session, as reply-window evidence.

        The second half of the consent `watch_session` records (the first is
        the armed notice): a session holding an open watch is the waiting half
        of an exchange it opened - it asked to be told, and the answer arrives
        through its own queue - so its auto-delivery grant must not lapse for
        idleness while it waits, or the notice arms with nothing to deliver it
        (`auto-delivery.md` tracks this as the watch half of the land queue's
        same gap). Bounded by the watch's own deadline plus a delivery grace,
        never by the reply-window span, because the watch's timeout is the
        request's own clock. `since` is deliberately unused: an open watch is
        live evidence however long ago it was armed, and every watch already
        carries a hard deadline.
        """
        del since
        wanted = {str(session_id) for session_id in session_ids}
        windows: dict[str, dict[str, Any]] = {}
        for watch in self._watches.values():
            watcher = watch.watcher_session_id
            if watcher not in wanted:
                continue
            expires = watch.deadline + self.WINDOW_GRACE_SECONDS
            current = windows.get(watcher)
            if current is not None and float(current["expires_at"]) >= expires:
                continue
            windows[watcher] = {
                "kind": "watch",
                "updated_at": watch.created_at,
                "expires_at": expires,
                "watch_id": watch.id,
                "target_session_id": watch.target_session_id,
                "target_name": watch.target_name,
                "timeout_minutes": watch.timeout_minutes,
            }
        return windows

    def _existing(self, watcher_id: str, target_id: str) -> Watch | None:
        for watch in self._watches.values():
            if (
                watch.watcher_session_id == watcher_id
                and watch.target_session_id == target_id
            ):
                return watch
        return None

    def _count_for(self, watcher_id: str) -> int:
        return sum(
            1
            for watch in self._watches.values()
            if watch.watcher_session_id == watcher_id
        )

    def _resolve_target(
        self, caller: Any, identity: str, project: str, *, allow_ended: bool = False
    ) -> tuple[Any, ProjectScope]:
        text = str(identity or "").strip()
        if not text:
            raise WatchRefusal(
                "unknown_target", "a target session is required", status=400
            )
        scope = resolve_project_scope(project, caller.record, self._projects)
        found = self._find_in_scope(text, scope)
        if found is None and not str(project or "").strip():
            qualifier, name = split_qualified_target(text)
            if qualifier:
                try:
                    qualified = resolve_project_scope(
                        qualifier, caller.record, self._projects
                    )
                except ValueError:
                    qualified = None
                if qualified is not None:
                    candidate = self._find_in_scope(name, qualified)
                    if candidate is not None:
                        found, scope = candidate, qualified
        if found is None:
            # A scope miss and a true miss answer identically: confirming that a
            # session exists outside the requested scope is itself a leak.
            raise WatchRefusal(
                "unknown_target",
                "no such session in scope. To reach another Project, pass "
                'project:"fleet" or the Project name.',
                status=404,
            )
        if str(found.record.id) == str(caller.record.id):
            raise WatchRefusal(
                "self_watch",
                "a session cannot watch itself: you are working whenever you "
                "are able to ask, so the watch could only ever time out.",
                status=400,
            )
        if not is_agent_harness(found.record.backend):
            # The settled/working vocabulary a watch is written against is the
            # agent status contract. A shell's `running` is a different axis and
            # would resolve every watch the moment it was armed.
            raise WatchRefusal(
                "not_agent_target",
                "a watch targets agent sessions only; a shell has no working or "
                "settled state to leave.",
                status=400,
            )
        if str(found.record.state) in ENDED_STATES and not allow_ended:
            # Answered here rather than as a notice half a second later: there is
            # nothing left to watch, and the caller can act on the final state now.
            # `await_settle` passes allow_ended because for it an ended target IS
            # the immediate answer rather than a refusal.
            raise WatchRefusal(
                "target_ended",
                f"{found.record.name} has already ended "
                f"({describe_state(found.record)}); there is nothing to watch.",
                status=409,
                target_state=str(found.record.state),
            )
        return found, scope

    def _find_in_scope(self, text: str, scope: ProjectScope) -> Any:
        matches = [
            session
            for session in self._sessions.sessions.values()
            if scope.admits(*record_scope(session.record))
            and (
                str(session.record.id) == text
                or str(getattr(session.record, "agent_run_id", "") or "") == text
                or str(session.record.name) == text
            )
        ]
        if len(matches) > 1:
            candidates = sorted(str(session.record.id) for session in matches)
            raise WatchRefusal(
                "ambiguous_target",
                f'"{text}" matches {len(matches)} sessions in scope; repeat with '
                f"one of these session ids: {', '.join(candidates)}",
                status=409,
                candidates=candidates,
            )
        return matches[0] if matches else None

    # -- the sweep ------------------------------------------------------------

    async def tick(self) -> list[dict[str, Any]]:
        """Advance every open watch once. Returns whatever resolved this pass."""
        if not self._watches and not self._awaiters:
            return []
        now = self._clock()
        resolved: list[dict[str, Any]] = []
        for watch in tuple(self._watches.values()):
            outcome = await self._advance(watch, now)
            if outcome is not None:
                resolved.append(outcome)
        for awaiter in tuple(self._awaiters.values()):
            self._advance_awaiter(awaiter, now)
        return resolved

    async def _advance(self, watch: Watch, now: float) -> dict[str, Any] | None:
        watcher = self._sessions.sessions.get(watch.watcher_session_id)
        if watcher is None or str(watcher.record.state) in ENDED_STATES:
            self._drop(watch, "watcher_ended")
            return None
        live_run = str(getattr(watcher.record, "agent_run_id", "") or "")
        if watch.watcher_run_id and live_run and live_run != watch.watcher_run_id:
            # The conversation that asked is gone (an in-CLI `/clear` or `/new`).
            # Its successor never armed this watch and retains nothing about it,
            # so a notice would read as a memory it does not have.
            self._drop(watch, "watcher_rolled")
            return None

        target = self._sessions.sessions.get(watch.target_session_id)
        if target is None:
            # Gone from the registry entirely: ended and already reaped.
            return await self._resolve(watch, "ended")
        record = target.record
        state = str(record.state)
        if state in ENDED_STATES:
            return await self._resolve(watch, "ended", record=record)
        if state in WORKING_STATES:
            watch.observed_working = True
            watch.settled_since = None
        elif (
            state in SETTLED_STATES
            and watch.observed_working
            and not running_work(record)
        ):
            if watch.settled_since is None:
                watch.settled_since = now
            elif now - watch.settled_since >= SETTLE_HOLD_SECONDS:
                return await self._resolve(watch, "settled", record=record)
        else:
            # `starting`, or a settled state the rules above exclude (never seen
            # working, or holding running background work). Either way the
            # current spell is not a settle, so the hold restarts if one begins.
            watch.settled_since = None
        if now >= watch.deadline:
            # Last, so a settle that matured on this same pass reports the case
            # that actually happened. The timeout is the failsafe, not a race.
            return await self._resolve(watch, "timeout", record=record)
        return None

    def _drop(self, watch: Watch, reason: str) -> None:
        """Forget a watch nobody is left to read. Never produces a notice."""
        self._watches.pop(watch.id, None)
        self._counters.dropped[reason] = self._counters.dropped.get(reason, 0) + 1
        log.info(
            "session_watch_dropped watch_id=%s watcher=%s target=%s reason=%s",
            watch.id,
            watch.watcher_session_id,
            watch.target_session_id,
            reason,
        )

    async def _resolve(
        self, watch: Watch, case: str, *, record: Any = None
    ) -> dict[str, Any]:
        """Close a watch and stage its one notice."""
        self._watches.pop(watch.id, None)
        self._counters.resolved[case] = self._counters.resolved.get(case, 0) + 1
        if record is None:
            target = self._sessions.sessions.get(watch.target_session_id)
            record = target.record if target is not None else None
        state = describe_state(record) if record is not None else "ended (session gone)"
        body = self._notice_body(watch, case, state)
        message_id = ""
        armed, arming_reason = self._notice_arming(watch)
        if self._queue_message is not None:
            try:
                message = await self._queue_message(
                    target_session_id=watch.watcher_session_id,
                    body=body,
                    armed=armed,
                    solicited_by=watch.id if armed else None,
                    sender_kind="rule",
                    sender_id=_SENDER_ID,
                    sender_label=_SENDER_LABEL,
                    correlation_id=watch.id,
                )
                message_id = str((message or {}).get("id") or "")
                # Read the arming back off the row rather than reporting what was
                # asked for. A retry dedupes into the message it already created,
                # and the queue applies its own floor on top of this one; either
                # way what the audit must record is the state the row is in.
                staged = str((message or {}).get("state") or "") == "armed"
                if armed and not staged:
                    arming_reason = (
                        ARMING_REASON_NOT_ARMED
                        if message_id
                        else "the watcher was gone before the notice was staged"
                    )
                armed = staged
            except Exception:  # noqa: BLE001 - a failed notice must not kill the loop
                armed = False
                arming_reason = "the notice could not be enqueued"
                self._counters.notice_failures += 1
                log.warning(
                    "session_watch_notice_failed watch_id=%s watcher=%s case=%s",
                    watch.id,
                    watch.watcher_session_id,
                    case,
                )
        else:
            armed = False
            arming_reason = "this service has no queue to stage a notice in"
        if armed:
            self._counters.armed_notices += 1
        log.info(
            "session_watch_resolved watch_id=%s watcher=%s target=%s case=%s "
            "state=%r message_id=%s armed=%s arming_reason=%r",
            watch.id,
            watch.watcher_session_id,
            watch.target_session_id,
            case,
            state,
            message_id or "-",
            armed,
            arming_reason,
        )
        await self._emit(
            "session_watch_resolved",
            watch,
            case=case,
            state=state,
            armed=armed,
            arming_reason=arming_reason,
        )
        return {
            "watch_id": watch.id,
            "case": case,
            "watcher_session_id": watch.watcher_session_id,
            "target_session_id": watch.target_session_id,
            "target_state": state,
            "message_id": message_id,
            # Whether the answer will reach the session that asked without a human
            # press, and why not when it will not. From the resolution alone a
            # draft nobody delivered otherwise reads exactly like one that arrived.
            "armed": armed,
            "arming_reason": arming_reason,
        }

    def _notice_arming(self, watch: Watch) -> tuple[bool, str]:
        """Whether this watch's notice may reach its watcher without a human press.

        The four bounds in the module docstring, of which only two are decisions
        made here - the other two hold by construction. Refusing arming is never
        refusing the notice; it is staged as a draft and a person can send it.
        """
        if not self._enabled():
            return False, ARMING_REASON_DISABLED
        watcher = self._sessions.sessions.get(watch.watcher_session_id)
        if watcher is None:
            return False, ARMING_REASON_UNKNOWN_RUN
        live_run = str(getattr(watcher.record, "agent_run_id", "") or "")
        if not watch.watcher_run_id or not live_run:
            # A check that could not be made is not a check that passed. Without a
            # run on both sides the consent cannot be shown to still belong to the
            # conversation that gave it, and the sweep's drop guard - which needs
            # both too - will not have caught the case either.
            return False, ARMING_REASON_UNKNOWN_RUN
        if live_run != watch.watcher_run_id:
            return False, ARMING_REASON_ROLLED
        return True, ARMING_REASON_OK

    def _notice_body(self, watch: Watch, case: str, state: str) -> str:
        """A fixed template. No model writes any part of this message."""
        name = watch.target_name
        elapsed = _humanize(self._clock() - watch.created_at)
        headline = {
            "settled": (
                f"Watch on `{name}` resolved: it left working and has held a "
                f"settled state for {_humanize(SETTLE_HOLD_SECONDS)}."
            ),
            "ended": f"Watch on `{name}` resolved: the session ended.",
            "timeout": (
                f"Watch on `{name}` timed out: the {watch.timeout_minutes}-minute "
                "timeout elapsed before it settled."
            ),
            "daemon_stopped": (
                f"Watch on `{name}` was dropped: the daemon stopped before the "
                "watch resolved."
            ),
        }[case]
        lines = [
            headline,
            "",
            f"Session: `{watch.target_session_id}`",
            f"State now: {state}",
            f"Watch armed {elapsed} ago with a {watch.timeout_minutes}-minute timeout.",
            "",
        ]
        if case == "settled":
            lines.append(
                "Settled means it stopped working, not that it succeeded. Read "
                "the session before acting on it - `awaiting` in the state above "
                "means it is blocked on a person, not finished."
            )
        elif case == "ended":
            lines.append(
                "The session is gone. Its record stays readable through "
                "get_session, list_sessions(include_ended), and history."
            )
        elif case == "timeout":
            lines.append(
                "Nothing settled inside the window, so treat this session as "
                "unresolved rather than done. This notice is the failsafe: a "
                "watch that expires says so instead of going quiet."
            )
        else:
            lines.append(
                "Watches live in daemon memory and do not survive a restart, so "
                "this one was closed rather than left silently unarmed."
            )
        lines.extend(
            [
                "",
                "This watch was one-shot and is now closed. Call watch_session "
                "again if you still need to be told about this session.",
            ]
        )
        return "\n".join(lines)

    # -- views ----------------------------------------------------------------

    def _view(
        self, watch: Watch, record: Any, scope: ProjectScope
    ) -> dict[str, Any]:
        now = self._clock()
        state = describe_state(record)
        already_settled = (
            str(getattr(record, "state", "")) not in WORKING_STATES
            and not watch.observed_working
        )
        view: dict[str, Any] = {
            "watch_id": watch.id,
            "target_session_id": watch.target_session_id,
            "target_name": watch.target_name,
            "target_project_id": watch.target_project_id,
            "target_state": state,
            "timeout_minutes": watch.timeout_minutes,
            "expires_in_seconds": max(0, int(watch.deadline - now)),
            "settle_hold_seconds": int(SETTLE_HOLD_SECONDS),
            "watches_open": self._count_for(watch.watcher_session_id),
            "watches_limit": self._max_per_session(),
            # `auto_delivery` states eligibility, not a promise: the notice is
            # staged armed, and every receiver-side gate still decides the send.
            # Stated at arming time because "queued" alone is unactionable, which
            # is the same lesson `notify`'s `target_delivery` records.
            "notice_delivery": {"auto_delivery": True, "waits_for": NOTICE_DELIVERY_NOTE},
            "project_scope": scope.requested,
            "note": (
                "Nothing is delivered now. Exactly one message will enter your "
                "prompt queue - when this session leaves working for a settled "
                "state (idle or awaiting) and holds it, when it ends, or when "
                f"the {watch.timeout_minutes}-minute timeout elapses, whichever "
                f"happens first. It arrives as a queue item, and {NOTICE_DELIVERY_NOTE}. "
                "The watch is one-shot and dies with your session or a daemon "
                "restart, and a conversation that rolls over before it matures "
                "gets no notice at all."
            ),
        }
        if already_settled:
            view["already_settled"] = True
            view["note"] += (
                " This session is not working right now, and a settle fires on a "
                "working -> settled edge, so if it is already finished the "
                "timeout is what will answer you."
            )
        return view

    def status(self) -> dict[str, Any]:
        """Counters for `GET /api/diagnostics/background`.

        A watch service that stopped resolving looks exactly like a quiet fleet
        from the outside, which is the failure this reports.
        """
        return {
            "open": len(self._watches),
            "armed": self._counters.armed,
            "resolved": dict(self._counters.resolved),
            "dropped": dict(self._counters.dropped),
            "notice_failures": self._counters.notice_failures,
            "armed_notices": self._counters.armed_notices,
            "awaits_open": len(self._awaiters),
            "awaits": self._counters.awaits,
            "await_resolved": dict(self._counters.await_resolved),
            "settle_hold_seconds": int(SETTLE_HOLD_SECONDS),
            "enabled": self._enabled(),
        }

    def watches_for(self, watcher_id: str) -> list[dict[str, Any]]:
        """Open watches held by one session, newest last. Diagnostics only."""
        return [
            {
                "watch_id": watch.id,
                "target_session_id": watch.target_session_id,
                "target_name": watch.target_name,
                "timeout_minutes": watch.timeout_minutes,
                "expires_in_seconds": max(0, int(watch.deadline - self._clock())),
                "observed_working": watch.observed_working,
            }
            for watch in sorted(
                (
                    watch
                    for watch in self._watches.values()
                    if watch.watcher_session_id == watcher_id
                ),
                key=lambda item: item.created_at,
            )
        ]

    async def _emit(self, event_type: str, watch: Watch, **payload: Any) -> None:
        if self._events is None:
            return
        try:
            await self._events.emit(
                event_type,
                session_id=watch.watcher_session_id or None,
                source="daemon",
                watch_id=watch.id,
                target_session_id=watch.target_session_id,
                **payload,
            )
        except Exception:  # noqa: BLE001 - a dropped event is not a failed watch
            log.debug("session_watch_event_failed type=%s", event_type)
