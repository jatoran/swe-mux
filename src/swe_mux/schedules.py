"""Trigger arithmetic and validation for scheduled agent runs.

A schedule is a *user-authored deferred spawn*: the same `SpawnRequest` the Run
menu builds, plus a trigger and an owner. That framing is what keeps this out of
the decision-gated "model-authored spawn" territory in `ROADMAP.md` — the human
who wrote the schedule is the authority, exactly as if they had pressed Run later
themselves.

This module is deliberately pure. It owns the answer to "when does this next
fire", nothing else: no storage, no spawning, no I/O. `schedule_store.py`
persists, `scheduler.py` acts.

Three trigger kinds:

- ``once``     - one absolute instant. Fires and disables itself.
- ``interval`` - every N seconds, anchored on the previous fire.
- ``cron``     - a five-field cron expression evaluated in a named timezone.

**Wall-clock arithmetic is the whole difficulty**, so it is concentrated here.
A cron schedule means a *local wall-clock* time, which is not a fixed number of
seconds apart from the last one: an hour vanishes and reappears twice a year.
Every candidate is therefore constructed as local wall time and converted to an
instant at the last possible moment, through `_Wall`, which has one
implementation for a named IANA zone and one for "whatever this host's local
time is" (the default, and the only option that is correct on a Windows host
with no IANA name available).

The two DST edges are decided, not accidental:

- **Spring forward** (a local time that does not exist). The candidate resolves
  to the equivalent instant just after the jump, so a 02:30 daily job fires once
  on that day rather than being silently skipped.
- **Fall back** (a local time that happens twice). ``fold=0`` — the *first*
  occurrence — is authoritative, so the job fires once rather than twice. The
  second pass is not a new occurrence because it is not a later instant than the
  one already recorded.

Missed windows are the other half. This daemon is a desktop app: it sleeps, it
restarts, it gets redeployed, and a 3 a.m. job on a laptop that was shut is the
normal case rather than an incident. `next_occurrence` never invents history —
the *policy* for a fire time already in the past belongs to the schedule
(``catch_up``) and is applied by `scheduler.py`, which either fires once for the
missed occurrence or records that it was skipped. Either way the window is
visible; nothing is silently dropped.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .harness import is_agent_harness

TriggerKind = Literal["once", "interval", "cron"]
OverlapPolicy = Literal["skip", "allow"]

TRIGGER_KINDS: tuple[str, ...] = ("once", "interval", "cron")
OVERLAP_POLICIES: tuple[str, ...] = ("skip", "allow")

# Bounds. Every one of these is a "a typo must not become a standing cost"
# limit rather than a capability statement.
MAX_LABEL_CHARS = 80
MAX_PROMPT_CHARS = 100_000
MAX_FOLLOW_UPS = 20
MAX_FOLLOW_UP_CHARS = 100_000
MIN_INTERVAL_SECONDS = 300.0
MAX_INTERVAL_SECONDS = 90 * 86_400.0
# How far ahead a `once` schedule may be parked, matching the prompt queue's own
# scheduling horizon so the two halves of "later" agree.
MAX_ONCE_HORIZON_SECONDS = 365 * 86_400.0
# How far the cron search walks before declaring an expression unsatisfiable.
# `30 2 30 2 *` (February 30th) is syntactically valid and never happens; a
# bounded walk answers "never" instead of spinning.
CRON_SEARCH_DAYS = 1_500
# A follow-up may be held back from its own session, but never past this.
MAX_FOLLOW_UP_DELAY_SECONDS = 7 * 86_400.0


class ScheduleError(ValueError):
    """A rejected schedule definition, carrying a machine code and a field map."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        fields: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.fields = fields or {}


# ---------------------------------------------------------------- wall clock


class _Wall(Protocol):
    """Conversion between an instant and the local wall time a schedule means."""

    def wall(self, epoch: float) -> datetime:
        """Naive local wall time for an instant."""

    def epoch(self, wall: datetime) -> float:
        """Instant for a naive local wall time, resolving DST edges."""


class _HostWall:
    """The host's own local time, including its DST rules.

    Used when a schedule names no timezone. It is the only correct default on a
    Windows host, where the platform has no IANA zone name to hand and any
    fixed-offset stand-in would be wrong for half the year.
    """

    def wall(self, epoch: float) -> datetime:
        return datetime.fromtimestamp(epoch)

    def epoch(self, wall: datetime) -> float:
        # A naive datetime's `.timestamp()` interprets it in local time through
        # the platform's DST rules and honours `fold`, which is exactly the
        # resolution documented at the top of this module.
        return wall.timestamp()


@dataclass(frozen=True, slots=True)
class _ZoneWall:
    zone: ZoneInfo

    def wall(self, epoch: float) -> datetime:
        return datetime.fromtimestamp(epoch, self.zone).replace(tzinfo=None)

    def epoch(self, wall: datetime) -> float:
        return wall.replace(tzinfo=self.zone).timestamp()


def resolve_wall(timezone: str) -> _Wall:
    if not timezone:
        return _HostWall()
    try:
        return _ZoneWall(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(
            "invalid_timezone",
            f"unknown timezone {timezone!r}",
            fields={"timezone": "must be an IANA timezone name, or empty for this host"},
        ) from exc


# --------------------------------------------------------------------- cron

_FIELD_RANGES: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day_of_month", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 7),
)
_NAMED_MONTHS = {
    name: index
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
_NAMED_DAYS = {
    name: index
    for index, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))
}
_TERM = re.compile(r"^(?:(\*)|(\d+|[a-z]{3})(?:-(\d+|[a-z]{3}))?)(?:/(\d+))?$")


@dataclass(frozen=True, slots=True)
class CronExpression:
    """A parsed five-field cron expression.

    `day_of_month` and `day_of_week` follow the Vixie rule: when *both* are
    restricted the day matches if *either* does. That is what makes
    ``0 9 1 * mon`` mean "the first of the month, and every Monday" rather than
    the almost-never intersection of the two.
    """

    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days_of_month: tuple[int, ...]
    months: tuple[int, ...]
    days_of_week: tuple[int, ...]
    dom_restricted: bool
    dow_restricted: bool
    source: str

    def matches_date(self, moment: datetime) -> bool:
        if moment.month not in self.months:
            return False
        # `datetime.weekday()` is Monday=0; cron is Sunday=0.
        weekday = (moment.weekday() + 1) % 7
        dom_hit = moment.day in self.days_of_month
        dow_hit = weekday in self.days_of_week
        if self.dom_restricted and self.dow_restricted:
            return dom_hit or dow_hit
        return dom_hit and dow_hit


def _parse_value(raw: str, name: str, low: int, high: int) -> int:
    token = raw.strip().lower()
    if name == "month" and token in _NAMED_MONTHS:
        return _NAMED_MONTHS[token]
    if name == "day_of_week" and token in _NAMED_DAYS:
        return _NAMED_DAYS[token]
    if not token.isdigit():
        raise ScheduleError("invalid_cron", f"{name} field has an unreadable value {raw!r}")
    value = int(token)
    if not low <= value <= high:
        raise ScheduleError(
            "invalid_cron", f"{name} must be between {low} and {high} (got {value})"
        )
    return value


def _parse_field(spec: str, name: str, low: int, high: int) -> tuple[int, ...]:
    values: set[int] = set()
    for term in spec.split(","):
        match = _TERM.match(term.strip().lower())
        if not match:
            raise ScheduleError("invalid_cron", f"{name} field has an unreadable term {term!r}")
        star, start_raw, end_raw, step_raw = match.groups()
        step = int(step_raw) if step_raw else 1
        if step < 1:
            raise ScheduleError("invalid_cron", f"{name} step must be 1 or more")
        if star:
            start, end = low, high
        else:
            start = _parse_value(start_raw or "", name, low, high)
            if end_raw:
                end = _parse_value(end_raw, name, low, high)
            else:
                # `5/15` means "from 5 to the end of the range, every 15"; a bare
                # `5` means only 5.
                end = high if step_raw else start
            if end < start:
                raise ScheduleError(
                    "invalid_cron", f"{name} range {term!r} ends before it starts"
                )
        values.update(range(start, end + 1, step))
    if not values:
        raise ScheduleError("invalid_cron", f"{name} field matches nothing")
    return tuple(sorted(values))


def parse_cron(expression: str) -> CronExpression:
    """Parse ``minute hour day-of-month month day-of-week``.

    Supports ``*``, ``a``, ``a-b``, ``*/n``, ``a-b/n``, comma lists, and the
    three-letter month/weekday names. Deliberately no ``@daily``-style macros and
    no seconds field: the UI writes these expressions and a second axis buys a
    surface where a typo costs an unattended agent run every second.
    """
    fields = expression.strip().split()
    if len(fields) != 5:
        raise ScheduleError(
            "invalid_cron",
            "a cron expression has five fields: minute hour day-of-month month day-of-week",
            fields={"cron": "five fields required"},
        )
    parsed: list[tuple[int, ...]] = []
    for value, (name, low, high) in zip(fields, _FIELD_RANGES, strict=True):
        parsed.append(_parse_field(value, name, low, high))
    minutes, hours, days_of_month, months, days_of_week = parsed
    # Cron accepts 7 as Sunday; normalize so matching never has to know.
    days_of_week = tuple(sorted({0 if day == 7 else day for day in days_of_week}))
    return CronExpression(
        minutes=minutes,
        hours=hours,
        days_of_month=days_of_month,
        months=months,
        days_of_week=days_of_week,
        dom_restricted=fields[2].strip() != "*",
        dow_restricted=fields[4].strip() != "*",
        source=" ".join(fields),
    )


def next_cron_fire(expression: CronExpression, after: float, wall: _Wall) -> float | None:
    """The first instant strictly after `after` that satisfies `expression`.

    Walks candidate *days* rather than minutes, so a yearly schedule costs a few
    hundred cheap date comparisons instead of half a million.
    """
    cursor = wall.wall(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for day in range(CRON_SEARCH_DAYS):
        if expression.matches_date(cursor):
            floor_hour = cursor.hour if day == 0 else 0
            floor_minute = cursor.minute if day == 0 else 0
            for hour in expression.hours:
                if hour < floor_hour:
                    continue
                for minute in expression.minutes:
                    if hour == floor_hour and minute < floor_minute:
                        continue
                    candidate = wall.epoch(cursor.replace(hour=hour, minute=minute))
                    # A spring-forward candidate resolves past its own wall time,
                    # and a fall-back one can resolve to an instant already
                    # behind us. Both are handled by requiring forward progress
                    # rather than by special-casing the transition.
                    if candidate > after:
                        return candidate
        cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0)
    return None


# ----------------------------------------------------------------- the spec


@dataclass(frozen=True, slots=True)
class FollowUp:
    """One message pre-queued behind the seed prompt.

    `delay_seconds` becomes a `not_before` constraint on the queue item at fire
    time, so the wait is enforced by the queue's own delivery contract rather
    than by anything this feature owns. A browser timer would die with the tab;
    a scheduler-owned timer would be a second delivery path.
    """

    body: str
    delay_seconds: float = 0.0
    armed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"body": self.body, "delay_seconds": self.delay_seconds, "armed": self.armed}


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    """The validated definition of one schedule, without its runtime state."""

    label: str
    prompt: str
    trigger_kind: TriggerKind = "cron"
    cron: str = ""
    interval_seconds: float | None = None
    run_at: float | None = None
    timezone: str = ""
    catch_up: bool = False
    overlap: OverlapPolicy = "skip"
    backend: str = ""
    profile_id: str = ""
    cwd: str = ""
    session_name: str = ""
    daily_run_cap: int = 0
    follow_ups: tuple[FollowUp, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "prompt": self.prompt,
            "trigger_kind": self.trigger_kind,
            "cron": self.cron,
            "interval_seconds": self.interval_seconds,
            "run_at": self.run_at,
            "timezone": self.timezone,
            "catch_up": self.catch_up,
            "overlap": self.overlap,
            "backend": self.backend,
            "profile_id": self.profile_id,
            "cwd": self.cwd,
            "session_name": self.session_name,
            "daily_run_cap": self.daily_run_cap,
            "follow_ups": [item.as_dict() for item in self.follow_ups],
        }


def _text(body: Any, name: str, limit: int, *, required: bool = True) -> str:
    if body is None:
        value = ""
    elif isinstance(body, str):
        value = body.strip()
    else:
        raise ScheduleError("invalid_field", f"{name} must be text", fields={name: "must be text"})
    if required and not value:
        raise ScheduleError("invalid_field", f"{name} is required", fields={name: "is required"})
    if len(value) > limit:
        raise ScheduleError(
            "invalid_field",
            f"{name} must be at most {limit} characters",
            fields={name: f"at most {limit} characters"},
        )
    return value


def parse_follow_ups(raw: Any) -> tuple[FollowUp, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ScheduleError(
            "invalid_follow_ups",
            "follow_ups must be a list",
            fields={"follow_ups": "must be a list"},
        )
    if len(raw) > MAX_FOLLOW_UPS:
        raise ScheduleError(
            "invalid_follow_ups",
            f"a schedule may pre-queue at most {MAX_FOLLOW_UPS} messages",
            fields={"follow_ups": f"at most {MAX_FOLLOW_UPS} messages"},
        )
    items: list[FollowUp] = []
    for index, entry in enumerate(raw):
        if isinstance(entry, str):
            entry = {"body": entry}
        if not isinstance(entry, dict):
            raise ScheduleError(
                "invalid_follow_ups", f"follow-up {index + 1} must be text or an object"
            )
        body = _text(entry.get("body"), f"follow_ups[{index}].body", MAX_FOLLOW_UP_CHARS)
        delay = entry.get("delay_seconds") or 0
        if not isinstance(delay, int | float) or isinstance(delay, bool):
            raise ScheduleError(
                "invalid_follow_ups", f"follow-up {index + 1} delay must be a number"
            )
        if not 0 <= float(delay) <= MAX_FOLLOW_UP_DELAY_SECONDS:
            raise ScheduleError(
                "invalid_follow_ups",
                f"follow-up {index + 1} delay must be between 0 and "
                f"{int(MAX_FOLLOW_UP_DELAY_SECONDS // 86_400)} days",
            )
        items.append(
            FollowUp(body=body, delay_seconds=float(delay), armed=bool(entry.get("armed")))
        )
    return tuple(items)


def parse_spec(body: dict[str, Any], *, now: float | None = None) -> ScheduleSpec:
    """Validate a request body into a `ScheduleSpec`.

    Every bound is enforced here rather than at the store, so the HTTP surface,
    the run-now path, and any future CLI share one definition of a legal
    schedule.
    """
    moment = time.time() if now is None else now
    kind = str(body.get("trigger_kind") or "cron").strip()
    if kind not in TRIGGER_KINDS:
        raise ScheduleError(
            "invalid_trigger",
            f"trigger_kind must be one of {', '.join(TRIGGER_KINDS)}",
            fields={"trigger_kind": "unknown trigger"},
        )
    overlap = str(body.get("overlap") or "skip").strip()
    if overlap not in OVERLAP_POLICIES:
        raise ScheduleError(
            "invalid_overlap",
            "overlap must be skip or allow",
            fields={"overlap": "must be skip or allow"},
        )
    timezone = _text(body.get("timezone"), "timezone", 64, required=False)
    resolve_wall(timezone)
    backend = _text(body.get("backend"), "backend", 40, required=False)
    if backend and not is_agent_harness(backend):
        raise ScheduleError(
            "invalid_backend",
            "a scheduled run starts an agent; backend must be a registered harness",
            fields={"backend": "must be a registered agent harness"},
        )
    cron = ""
    interval: float | None = None
    run_at: float | None = None
    if kind == "cron":
        cron = parse_cron(_text(body.get("cron"), "cron", 200)).source
    elif kind == "interval":
        raw_interval = body.get("interval_seconds")
        if not isinstance(raw_interval, int | float) or isinstance(raw_interval, bool):
            raise ScheduleError(
                "invalid_interval",
                "interval_seconds must be a number",
                fields={"interval_seconds": "must be a number"},
            )
        interval = float(raw_interval)
        if not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
            raise ScheduleError(
                "invalid_interval",
                f"interval must be between {int(MIN_INTERVAL_SECONDS // 60)} minutes and "
                f"{int(MAX_INTERVAL_SECONDS // 86_400)} days",
                fields={"interval_seconds": "outside the allowed range"},
            )
    else:
        raw_at = body.get("run_at")
        if not isinstance(raw_at, int | float) or isinstance(raw_at, bool):
            raise ScheduleError(
                "invalid_run_at",
                "run_at must be epoch seconds",
                fields={"run_at": "must be epoch seconds"},
            )
        run_at = float(raw_at)
        if run_at > moment + MAX_ONCE_HORIZON_SECONDS:
            raise ScheduleError(
                "invalid_run_at",
                "run_at is beyond the one-year scheduling horizon",
                fields={"run_at": "too far in the future"},
            )
    cap = body.get("daily_run_cap", 0) or 0
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0 or cap > 500:
        raise ScheduleError(
            "invalid_field",
            "daily_run_cap must be between 0 (the global cap only) and 500",
            fields={"daily_run_cap": "must be between 0 and 500"},
        )
    return ScheduleSpec(
        label=_text(body.get("label"), "label", MAX_LABEL_CHARS),
        prompt=_text(body.get("prompt"), "prompt", MAX_PROMPT_CHARS),
        trigger_kind=kind,  # type: ignore[arg-type]
        cron=cron,
        interval_seconds=interval,
        run_at=run_at,
        timezone=timezone,
        catch_up=bool(body.get("catch_up")),
        overlap=overlap,  # type: ignore[arg-type]
        backend=backend,
        profile_id=_text(body.get("profile_id"), "profile_id", 120, required=False),
        cwd=_text(body.get("cwd"), "cwd", 400, required=False),
        session_name=_text(
            body.get("session_name"), "session_name", MAX_LABEL_CHARS, required=False
        ),
        daily_run_cap=int(cap),
        follow_ups=parse_follow_ups(body.get("follow_ups")),
    )


def next_occurrence(spec: ScheduleSpec, after: float) -> float | None:
    """The next instant this schedule should fire, strictly after `after`.

    Returns ``None`` when there is no next instant: a ``once`` schedule whose
    moment has passed, or a cron expression that can never match.
    """
    if spec.trigger_kind == "once":
        if spec.run_at is None or spec.run_at <= after:
            return None
        return spec.run_at
    if spec.trigger_kind == "interval":
        if not spec.interval_seconds:
            return None
        return after + float(spec.interval_seconds)
    return next_cron_fire(parse_cron(spec.cron), after, resolve_wall(spec.timezone))


def first_occurrence(spec: ScheduleSpec, *, now: float) -> float | None:
    """The first fire for a newly written schedule.

    An ``interval`` schedule waits a full interval rather than firing on save:
    pressing Save is not a request to start an agent this second, and a surface
    that starts one anyway teaches people not to press Save.
    """
    if spec.trigger_kind == "once":
        return spec.run_at if spec.run_at and spec.run_at > now else None
    return next_occurrence(spec, now)


def occurrence_key(fire_at: float) -> str:
    """Stable identity for one occurrence, to the minute.

    The uniqueness guard behind it is what makes a fire idempotent across a
    daemon restart: the same occurrence cannot spawn twice, however many times
    the sweep sees it.
    """
    return str(int(fire_at // 60) * 60)
