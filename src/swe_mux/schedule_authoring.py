"""Writing schedules: the one place a schedule is created, approved, or withdrawn.

There are two authors and one write path. A **person** writes a schedule from the
Schedule tab, and it carries their authority exactly as before. An **agent** writes one
through `mux.request_schedule`, and it carries the authority its *target* Project
granted agents - `schedule_grant`, resolved through the same four layers every other
agent authority field uses (`agent_authority.py`) - decided by
`schedules.agent_schedule_verdict`.

What an agent's schedule adds over an agent's immediate spawn is persistence: it keeps
firing after its author has gone, so the per-origin spawn budget, which is counted
while the author is alive, cannot bound it. The bounds here are the ones persistence
needs, and each one is checked at write time *and* again where it can drift:

- **Frequency.** At the default `limited` level a one-off is armed directly, and so is a
  recurring schedule that fires at most `agent_schedule_daily_fire_limit` times in any
  24 hours, measured over the real occurrences (`schedules.fires_per_day`). Anything
  busier is written as a pending request a person approves.
- **Lifetime.** Nothing an agent schedules lives longer than `agent_schedule_max_days`:
  a recurring one carries an end date, a one-off may be parked at most that far ahead.
- **Accumulation.** At most `agent_schedules_per_project` agent schedules stand in one
  Project, armed or pending.
- **Rate.** At most `agent_schedule_hourly_budget` requests per agent session per hour,
  counted from the table so the budget survives a daemon restart.
- **Revocation.** `scheduler.py` re-judges an agent-armed schedule at every fire, so
  lowering the Project's level converts it back into a pending request instead of
  letting it keep the authority it was armed under.

A pending request is the schedule row itself (`schedule_store.py`), never a second copy
of the definition: the Fleet Queue and the Schedule tab both read the row, and
approving it is one conditional update that two devices cannot both win.

**MCP is transport, not authority.** `mcp.py` translates arguments and nothing else;
every bound lives here, so the HTTP route a person uses and the tool an agent uses
cannot drift into two definitions of a legal schedule.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from .harness import is_agent_harness
from .project_scope import resolve_project_scope
from .prompt_queue import QueueError
from .schedule_store import (
    APPROVAL_APPROVED,
    APPROVAL_DISMISSED,
    APPROVAL_GRANTED,
    APPROVAL_PENDING,
    AUTHOR_AGENT,
    DISABLED_AWAITING_APPROVAL,
    DISABLED_CANCELLED,
    DISABLED_DISMISSED,
    ScheduleAuthor,
    ScheduleStore,
)
from .scheduler import spec_from_row
from .schedules import (
    FREQUENCY_COUNT_CEILING,
    ScheduleError,
    ScheduleSpec,
    agent_schedule_verdict,
    fires_per_day,
    first_occurrence,
    next_occurrence,
    parse_spec,
    resolve_wall,
)
from .session_resume import resumable_refusal
from .spawn_contract import field_refusal, requested_spawn_model

log = logging.getLogger(__name__)

# The four ways an agent names *when*. Exactly one per request, so a body can never
# carry both a cron expression and an interval the way a partial-update surface does.
TRIGGER_ARGS = ("at", "in_minutes", "cron", "every_minutes")
# A one-off must be at least this far ahead. A "schedule" for the current minute is a
# spawn the agent should have made directly, and it races the sweep that would fire it.
MIN_ONCE_LEAD_SECONDS = 60.0
# How recent a decided request still shows in the Fleet Queue.
DECIDED_VISIBLE_SECONDS = 7 * 86_400.0
# How many upcoming fire times a result spells out.
PREVIEW_FIRES = 3
# Prompt excerpt length in listings: enough to recognise a schedule, small enough that
# a list of twenty-five does not cost a context window.
LISTING_PROMPT_CHARS = 240
# Listing bounds.
DEFAULT_LIST_LIMIT = 25
MAX_LIST_LIMIT = 100


def _instant(epoch: float | None) -> dict[str, Any] | None:
    """An instant as both a number and the host's own wall clock, for an agent to read."""
    if epoch is None:
        return None
    local = datetime.fromtimestamp(float(epoch)).astimezone()
    return {"epoch": round(float(epoch), 3), "local": local.isoformat(timespec="minutes")}


def upcoming_fires(spec: ScheduleSpec, *, now: float, count: int = PREVIEW_FIRES) -> list[float]:
    """The next few instants this definition would fire, from the code that fires it."""
    fires: list[float] = []
    following = first_occurrence(spec, now=now)
    while following is not None and len(fires) < count:
        if spec.expires_at is not None and following >= spec.expires_at:
            break
        fires.append(following)
        following = next_occurrence(spec, following)
    return fires


def cadence_text(row: dict[str, Any]) -> str:
    """The trigger in words, for an agent. The browser renders its own, localized."""
    kind = str(row.get("trigger_kind") or "")
    if kind == "once":
        moment = _instant(row.get("run_at"))
        return f"once at {moment['local']}" if moment else "once (time not set)"
    if kind == "interval":
        seconds = float(row.get("interval_seconds") or 0)
        return f"every {round(seconds / 60)} minutes"
    zone = str(row.get("timezone") or "") or "this host's local time"
    return f"cron '{row.get('cron')}' ({zone})"


def schedule_state(row: dict[str, Any], *, now: float | None = None) -> str:
    """One word for where a schedule stands, derived from the row."""
    moment = time.time() if now is None else now
    approval = str(row.get("approval_state") or "")
    reason = str(row.get("disabled_reason") or "")
    if approval == APPROVAL_PENDING:
        return "awaiting_approval"
    if approval == APPROVAL_DISMISSED:
        return "dismissed"
    if row.get("enabled"):
        expires = row.get("expires_at")
        if isinstance(expires, int | float) and expires <= moment:
            return "expired"
        return "armed"
    if reason in {"expired", "cancelled", "completed"}:
        return reason
    return "paused"


class ScheduleAuthoringService:
    """Creates, approves, dismisses and withdraws schedules for both kinds of author."""

    def __init__(
        self,
        *,
        store: ScheduleStore,
        projects: Any,
        sessions: Any,
        config: Any,
        events: Any,
        automation_gate: Callable[[str], Awaitable[frozenset[str]]],
        schedule_grant: Callable[[str], str],
        history: Any = None,
        enqueue: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        delivery_brake: Callable[[], Awaitable[str | None]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.projects = projects
        self.sessions = sessions
        self.config = config
        self.events = events
        self._gate = automation_gate
        self._grant = schedule_grant
        self._history = history
        self._enqueue = enqueue
        self._delivery_brake = delivery_brake
        self._clock = clock

    # ------------------------------------------------------------- people

    async def create_for_person(
        self, project: Any, body: dict[str, Any]
    ) -> dict[str, Any]:
        """A person's schedule. Their authority, armed on save, as it always was."""
        now = self._clock()
        spec = parse_spec(body, now=now)
        row = await self.store.create(
            project_id=str(project.id),
            project_root=str(project.root),
            spec=spec,
            next_fire_at=first_occurrence(spec, now=now),
            now=now,
            fires_per_day=fires_per_day(spec, now=now),
        )
        log.info(
            "schedule_created schedule_id=%s project_id=%s author=user trigger=%s action=%s",
            row["id"],
            project.id,
            spec.trigger_kind,
            spec.action,
        )
        await self._changed(row, source="user", action="created")
        return row

    async def decide(
        self, schedule_id: str, decision: str, *, decided_by: str
    ) -> dict[str, Any]:
        """Approve or dismiss an agent's pending schedule request.

        Approval is the human act that arms it, so it is armed from *now*: a one-off
        whose moment passed while it waited fires on the next sweep, because the
        person approving it has just read the time on the card and said yes.
        """
        if decision not in {"approve", "dismiss"}:
            raise QueueError("invalid_decision", "decision must be approve or dismiss", status=400)
        row = await self.store.get(schedule_id)
        if row is None or str(row.get("author_kind")) != AUTHOR_AGENT:
            raise QueueError("not_found", "no such agent schedule request", status=404)
        if str(row.get("approval_state")) != APPROVAL_PENDING:
            raise QueueError(
                "already_decided",
                f"this request is already {row.get('approval_state') or 'decided'}",
                status=409,
            )
        now = self._clock()
        if decision == "dismiss":
            updated = await self.store.set_approval(
                schedule_id,
                expected=(APPROVAL_PENDING,),
                state=APPROVAL_DISMISSED,
                enabled=False,
                next_fire_at=None,
                disabled_reason=DISABLED_DISMISSED,
                decided_by=decided_by,
                now=now,
            )
        else:
            spec = spec_from_row(row)
            if spec.expires_at is not None and spec.expires_at <= now:
                raise QueueError(
                    "expired",
                    "this request's end date passed before it was approved; dismiss it "
                    "and ask the agent for a new one",
                    status=409,
                )
            following = first_occurrence(spec, now=now)
            if following is None and spec.trigger_kind == "once":
                following = now
            updated = await self.store.set_approval(
                schedule_id,
                expected=(APPROVAL_PENDING,),
                state=APPROVAL_APPROVED,
                enabled=True,
                next_fire_at=following,
                disabled_reason="",
                decided_by=decided_by,
                now=now,
            )
        if updated is None:
            raise QueueError("already_decided", "this request was decided elsewhere", status=409)
        verb = "approved" if decision == "approve" else "dismissed"
        log.info(
            "agent_schedule_decided schedule_id=%s project_id=%s decision=%s decided_by=%s",
            schedule_id,
            updated["project_id"],
            verb,
            decided_by,
        )
        await self._changed(updated, source="user", action=verb)
        await self._tell_author(updated, verb)
        return updated

    async def approval_cards(self, *, project_id: str | None = None) -> list[dict[str, Any]]:
        """Agent schedule requests for the Fleet Queue: pending, plus recent decisions.

        Each card carries what a person needs to decide without opening anything
        else - the measured frequency, the next fire times from the code that will
        fire them, the end date, and why it needed them - because a cron expression is
        the one field here whose meaning cannot be read off its own text.
        """
        now = self._clock()
        rows = await self.store.approval_requests(
            project_id=project_id, decided_since=now - DECIDED_VISIBLE_SECONDS
        )
        cards: list[dict[str, Any]] = []
        for row in rows:
            project = self.projects.projects.get(str(row.get("project_id") or ""))
            spec = spec_from_row(row)
            cards.append(
                {
                    **row,
                    "project_name": project.name if project is not None else "",
                    "status": str(row.get("approval_state") or APPROVAL_PENDING),
                    "preview_fires": upcoming_fires(spec, now=now),
                    "scheduled_runs_opted_in": (
                        "scheduled_runs" in await self._gate(str(project.root))
                        if project is not None
                        else False
                    ),
                }
            )
        return cards

    # -------------------------------------------------------------- agents

    async def request(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.request_schedule`: arm a schedule, or file it for approval.

        Every refusal is a typed `QueueError` whose message names the next call that
        would work, because the caller is an agent that will act on the text.
        """
        dry_run = bool(args.get("dry_run"))
        now = self._clock()
        if not bool(getattr(self.config, "scheduled_runs_enabled", True)):
            raise QueueError(
                "scheduled_runs_disabled",
                "scheduled runs are switched off for this whole install, so nothing "
                "would fire; tell the operator if a schedule is needed",
                status=403,
            )
        record = caller.record
        action = str(args.get("action") or ("resume" if args.get("target") else "spawn"))
        if action not in {"spawn", "resume"}:
            raise QueueError("invalid_action", "action must be spawn or resume", status=400)
        resume_row: dict[str, Any] | None = None
        if action == "resume":
            resume_row, project = await self._resume_target(caller, args)
        else:
            project = self._spawn_project(caller, args)
        trigger = self._trigger(args, now=now)
        recurring = trigger["trigger_kind"] != "once"
        max_days = int(getattr(self.config, "agent_schedule_max_days", 30))
        body = self._body(args, action=action, trigger=trigger, resume_row=resume_row)
        if recurring:
            body["expires_at"] = now + self._lifetime_days(args, max_days) * 86_400.0
        elif float(trigger["run_at"]) > now + max_days * 86_400.0:
            raise QueueError(
                "beyond_horizon",
                f"an agent may schedule at most {max_days} days ahead on this install; "
                "pick an earlier time",
                status=400,
            )
        try:
            spec = parse_spec(body, now=now)
        except ScheduleError as exc:
            raise QueueError(exc.code, str(exc), status=400, fields=exc.fields) from exc
        if action == "spawn":
            spec = await self._resolve_model(spec, caller)
        frequency = fires_per_day(spec, now=now)
        level = self._grant(str(project.root))
        opted_in = "scheduled_runs" in await self._gate(str(project.root))
        daily_limit = int(getattr(self.config, "agent_schedule_daily_fire_limit", 2))
        verdict = agent_schedule_verdict(
            level, spec, frequency=frequency, daily_limit=daily_limit, opted_in=opted_in
        )
        existing = await self._duplicate(caller, project, spec)
        if existing is not None:
            return await self._agent_view(
                existing, caller, status="already_scheduled", now=now, extra={
                    "note": "An identical schedule you wrote is already in place; nothing new "
                    "was created. Cancel it with cancel_schedule if it is no longer wanted.",
                },
            )
        slots = await self._check_capacity(caller, project, dry_run=dry_run, now=now)
        fires = [_instant(moment) for moment in upcoming_fires(spec, now=now)]
        if dry_run:
            return {
                "status": "dry_run",
                "would": "arm" if verdict.direct else "file for approval",
                "approval_reason": verdict.reason or None,
                "project_id": str(project.id),
                "project_name": str(project.name),
                "schedule_grant": level,
                "fires_per_day": frequency,
                "next_fires": fires,
                "expires_at": _instant(spec.expires_at),
                "slots_left": slots,
                "note": "Nothing was written. Repeat the call without dry_run to create it.",
            }
        author = ScheduleAuthor(
            kind=AUTHOR_AGENT,
            session_id=str(record.id),
            run_id=str(getattr(record, "agent_run_id", "") or ""),
            name=str(getattr(record, "name", "") or record.id),
            project_id=str(getattr(record, "project_id", "") or ""),
            reason=str(args.get("reason") or "")[:500],
        )
        row = await self.store.create(
            project_id=str(project.id),
            project_root=str(project.root),
            spec=spec,
            next_fire_at=first_occurrence(spec, now=now) if verdict.direct else None,
            now=now,
            author=author,
            enabled=verdict.direct,
            disabled_reason="" if verdict.direct else DISABLED_AWAITING_APPROVAL,
            approval_state=APPROVAL_GRANTED if verdict.direct else APPROVAL_PENDING,
            approval_reason=verdict.reason,
            fires_per_day=frequency,
        )
        log.info(
            "agent_schedule_created schedule_id=%s project_id=%s author_session=%s "
            "author_run=%s action=%s trigger=%s fires_per_day=%s level=%s verdict=%s",
            row["id"],
            project.id,
            author.session_id,
            author.run_id,
            spec.action,
            spec.trigger_kind,
            frequency,
            level,
            "armed" if verdict.direct else "pending",
        )
        await self._changed(
            row, source="agent", action="created" if verdict.direct else "requested"
        )
        extra: dict[str, Any] = {"note": await self._created_note(row, spec, verdict.direct)}
        return await self._agent_view(
            row,
            caller,
            status="scheduled" if verdict.direct else "awaiting_approval",
            now=now,
            extra=extra,
        )

    async def cancel(self, caller: Any, schedule_id: str, reason: str = "") -> dict[str, Any]:
        """`mux.cancel_schedule`: withdraw a schedule this agent wrote.

        Disarmed rather than deleted, so its run history and the record of what was
        asked for stay readable. A schedule a person wrote, or another agent wrote, is
        refused: withdrawing someone else's standing decision is not this tool's to do.
        """
        row = await self.store.get(str(schedule_id or "").strip())
        if row is None:
            raise QueueError(
                "not_found",
                "no schedule with that id; list_schedules(mine=true) shows yours",
                status=404,
            )
        if not self._owns(caller, row):
            raise QueueError(
                "not_yours",
                "that schedule was written by a person or another session; ask the "
                "operator to change it",
                status=403,
            )
        if not row.get("enabled") and str(row.get("approval_state")) != APPROVAL_PENDING:
            return await self._agent_view(
                row, caller, status="already_inactive", now=self._clock(), extra={
                    "note": "It was not armed, so there was nothing to cancel.",
                },
            )
        now = self._clock()
        expected = (str(row.get("approval_state") or ""),)
        state = (
            APPROVAL_DISMISSED
            if str(row.get("approval_state")) == APPROVAL_PENDING
            else str(row.get("approval_state") or "")
        )
        updated = await self.store.set_approval(
            str(row["id"]),
            expected=expected,
            state=state,
            enabled=False,
            next_fire_at=None,
            disabled_reason=DISABLED_CANCELLED,
            now=now,
        )
        if updated is None:
            raise QueueError(
                "changed",
                "the schedule changed while it was being cancelled; read it again",
                status=409,
            )
        log.info(
            "agent_schedule_cancelled schedule_id=%s project_id=%s by_session=%s reason=%r",
            updated["id"],
            updated["project_id"],
            caller.record.id,
            str(reason or "")[:200],
        )
        await self._changed(updated, source="agent", action="cancelled")
        return await self._agent_view(updated, caller, status="cancelled", now=now)

    async def list_for_agent(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.list_schedules`: what is scheduled in a scope, and what the rules are."""
        scope = resolve_project_scope(args.get("project"), caller.record, self.projects)
        mine = bool(args.get("mine"))
        include_inactive = bool(args.get("include_inactive"))
        limit = max(1, min(int(args.get("limit") or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
        now = self._clock()
        rows = await self.store.list_schedules(None if scope.fleet else scope.project_id or None)
        if not scope.fleet and not scope.project_id:
            rows = []
        selected: list[dict[str, Any]] = []
        for row in rows:
            if mine and not self._owns(caller, row):
                continue
            state = schedule_state(row, now=now)
            if not include_inactive and state not in {"armed", "awaiting_approval"}:
                continue
            selected.append(row)
        selected.sort(
            key=lambda item: (
                0 if item.get("enabled") else 1,
                float(item.get("next_fire_at") or float("inf")),
            )
        )
        result: dict[str, Any] = {
            "project_scope": scope.requested,
            "schedules": [
                self._compact(row, caller, now=now) for row in selected[:limit]
            ],
            "truncated": len(selected) > limit,
        }
        if not scope.fleet and scope.project_id:
            project = self.projects.projects.get(scope.project_id)
            if project is not None:
                result["policy"] = await self._policy(caller, project, now=now)
        return result

    # ------------------------------------------------------------ helpers

    def _spawn_project(self, caller: Any, args: dict[str, Any]) -> Any:
        scope = resolve_project_scope(args.get("project"), caller.record, self.projects)
        if scope.fleet:
            raise QueueError(
                "invalid_project",
                'a schedule belongs to one Project; name it rather than passing "fleet"',
                status=400,
            )
        project = self.projects.projects.get(scope.project_id) if scope.project_id else None
        if project is None:
            raise QueueError(
                "no_project",
                "your session is not registered to a Project; pass project to name one",
                status=409,
            )
        return project

    async def _resume_target(
        self, caller: Any, args: dict[str, Any]
    ) -> tuple[dict[str, Any], Any]:
        """The history row a resume reopens, and the Project it reopens in.

        ``self`` (the default) is the caller's own conversation. Anything else is a
        session id or name, live or ended, or a history run id - resolved within the
        `project` scope like every other target. The Project is the conversation's
        own, never the argument's: a CLI resolves a conversation from the directory it
        ran in, so reopening it under another Project's root opens nothing.
        """
        if self._history is None:
            raise QueueError(
                "history_unavailable", "history is not available on this daemon", status=503
            )
        target = str(args.get("target") or "self").strip() or "self"
        record = caller.record
        run_id = ""
        if target.casefold() == "self":
            run_id = str(getattr(record, "agent_run_id", "") or "")
            if not run_id:
                raise QueueError(
                    "no_conversation",
                    "your session has no agent conversation recorded yet, so there is "
                    "nothing to resume; try again after your first reply",
                    status=409,
                )
        else:
            scope = resolve_project_scope(args.get("project"), record, self.projects)
            for session in self.sessions.sessions.values():
                other = session.record
                if not scope.admits(
                    str(other.project_id or ""), str(getattr(other, "project_scope_id", "") or "")
                ):
                    continue
                if target in {str(other.id), str(other.name or "")}:
                    run_id = str(getattr(other, "agent_run_id", "") or "")
                    break
            if not run_id:
                run_id = target
        row = await self._history.history_entry(run_id)
        if row is None:
            raise QueueError(
                "target_not_found",
                f"no conversation found for {target!r}; pass a session id or name, a "
                "history run id, or 'self'",
                status=404,
            )
        project_id = str(row.get("project_id") or "")
        project = self.projects.projects.get(project_id) if project_id else None
        if project is None:
            raise QueueError(
                "no_project",
                "that conversation does not belong to a registered Project, so it has "
                "nowhere to reopen",
                status=409,
            )
        requested = str(args.get("project") or "").strip()
        if requested and requested.casefold() not in {"self", "fleet"}:
            named = resolve_project_scope(requested, record, self.projects)
            if named.project_id and named.project_id != project_id:
                raise QueueError(
                    "project_mismatch",
                    f"that conversation belongs to Project {project.name!r}; a resume "
                    "reopens a conversation where it ran, so omit project or name that one",
                    status=400,
                )
        refusal = resumable_refusal(
            row,
            projects=self.projects,
            adapters=getattr(self.sessions, "adapters", {}),
            target_project_id=project_id,
        )
        # A missing transcript can be a moved one the resume path repairs on its own
        # (`transcript_repair`), so only the refusals no repair can undo stop the write.
        if refusal is not None and refusal.code != "transcript_unavailable":
            raise QueueError(refusal.code, str(refusal), status=409)
        return row, project

    def _trigger(self, args: dict[str, Any], *, now: float) -> dict[str, Any]:
        given = [name for name in TRIGGER_ARGS if args.get(name) not in (None, "")]
        if len(given) != 1:
            raise QueueError(
                "invalid_trigger",
                "name exactly one of: at (an ISO-8601 time), in_minutes (one-off, from "
                "now), cron (five fields, recurring), every_minutes (recurring)",
                status=400,
            )
        kind = given[0]
        timezone = str(args.get("timezone") or "").strip()
        value = args.get(kind)
        if kind == "in_minutes":
            minutes = self._number(value, "in_minutes")
            if minutes * 60.0 < MIN_ONCE_LEAD_SECONDS:
                raise QueueError(
                    "too_soon",
                    "in_minutes must be at least 1; to start work now, use request_spawn",
                    status=400,
                )
            return {"trigger_kind": "once", "run_at": now + minutes * 60.0}
        if kind == "at":
            run_at = self._parse_at(str(value), timezone)
            if run_at < now + MIN_ONCE_LEAD_SECONDS:
                raise QueueError(
                    "too_soon",
                    "that time is in the past or under a minute away; to start work now, "
                    "use request_spawn",
                    status=400,
                )
            return {"trigger_kind": "once", "run_at": run_at, "timezone": timezone}
        if kind == "every_minutes":
            minutes = self._number(value, "every_minutes")
            return {"trigger_kind": "interval", "interval_seconds": minutes * 60.0}
        return {"trigger_kind": "cron", "cron": str(value).strip(), "timezone": timezone}

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, bool):
            raise QueueError("invalid_trigger", f"{name} must be a number", status=400)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise QueueError("invalid_trigger", f"{name} must be a number", status=400) from exc
        if number <= 0:
            raise QueueError("invalid_trigger", f"{name} must be positive", status=400)
        return number

    @staticmethod
    def _parse_at(text: str, timezone: str) -> float:
        """An ISO-8601 instant. A time with no offset means `timezone`, else this host."""
        try:
            moment = datetime.fromisoformat(text.strip())
        except ValueError as exc:
            raise QueueError(
                "invalid_trigger",
                f"at {text!r} is not an ISO-8601 time; use e.g. 2026-11-08T09:00 or "
                "2026-11-08T09:00:00-06:00",
                status=400,
            ) from exc
        if moment.tzinfo is not None:
            return moment.timestamp()
        try:
            return resolve_wall(timezone).epoch(moment.replace(second=0, microsecond=0))
        except ScheduleError as exc:
            raise QueueError(exc.code, str(exc), status=400) from exc

    @staticmethod
    def _lifetime_days(args: dict[str, Any], max_days: int) -> float:
        raw = args.get("expires_in_days")
        if raw in (None, ""):
            return float(max_days)
        if isinstance(raw, bool) or not isinstance(raw, int | float) or raw <= 0:
            raise QueueError(
                "invalid_expiry", "expires_in_days must be a positive number", status=400
            )
        if float(raw) > max_days:
            raise QueueError(
                "invalid_expiry",
                f"a recurring agent schedule may run for at most {max_days} days on this "
                "install; ask for fewer, and schedule again if it is still needed",
                status=400,
            )
        return float(raw)

    def _body(
        self,
        args: dict[str, Any],
        *,
        action: str,
        trigger: dict[str, Any],
        resume_row: dict[str, Any] | None,
    ) -> dict[str, Any]:
        prompt = str(args.get("prompt") or "").strip()
        label = str(args.get("label") or "").strip() or " ".join(prompt.split())[:60]
        recurring = trigger["trigger_kind"] != "once"
        body: dict[str, Any] = {
            **trigger,
            "label": label[:80],
            "prompt": prompt,
            "action": action,
            # A one-off an agent parks is something it is waiting on, so a laptop that
            # was asleep at the time should still run it when it wakes. A recurring one
            # simply waits for its next window, the way a person's does by default.
            "catch_up": bool(args.get("catch_up", not recurring)),
            "session_name": str(args.get("name") or "").strip()[:80],
            "daily_run_cap": args.get("daily_run_cap") or 0,
        }
        if action == "resume" and resume_row is not None:
            body["target_run_id"] = str(resume_row["id"])
            body["target_kind"] = str(
                args.get("target_kind") or ("latest_of_session" if recurring else "run")
            )
            if body["target_kind"] == "fork_point":
                raise QueueError(
                    "invalid_target",
                    "agents may resume a conversation as it stands (run) or wherever it "
                    "has got to (latest_of_session); a fixed fork point is set from the UI",
                    status=400,
                )
            body["when_live"] = str(args.get("when_live") or "deliver")
            if args.get("context_ceiling_pct") is not None:
                body["context_ceiling_pct"] = args.get("context_ceiling_pct")
        else:
            body["backend"] = str(args.get("backend") or "")
            body["model"] = str(args.get("model") or "")
        return body

    async def _resolve_model(self, spec: ScheduleSpec, caller: Any) -> ScheduleSpec:
        """Validate a spawn's model against the harness it will run, at write time.

        Refused here rather than at 3 a.m., in front of the agent that can pick
        another. The backend is fixed onto the row too, so the harness the model was
        checked against is the one that runs it.
        """
        backend = spec.backend
        if not backend:
            # The caller's own harness, as `request_spawn` defaults - unless the caller
            # is not an agent (a shim inside a shell pane), in which case the spawn path
            # picks the target Project's default harness when it fires.
            own = str(getattr(caller.record, "backend", "") or "")
            backend = own if is_agent_harness(own) else ""
        try:
            model = await requested_spawn_model(backend, spec.model)
        except ValueError as exc:
            raise QueueError("invalid_model", field_refusal(exc, "model"), status=400) from exc
        return replace(spec, backend=backend, model=model or "")

    async def _duplicate(
        self, caller: Any, project: Any, spec: ScheduleSpec
    ) -> dict[str, Any] | None:
        """An active schedule this author already wrote with the same meaning.

        An agent that retries a call it could not see the answer to must not arm a
        second copy. The label is left out of the comparison - it is a name, not a
        behaviour - and so are the end date and the moment of writing, which differ on
        every retry by construction.
        """
        record = caller.record
        mine = await self.store.authored_by(
            str(record.id), str(getattr(record, "agent_run_id", "") or "")
        )
        wanted = self._fingerprint(spec.as_dict())
        for row in mine:
            if str(row.get("project_id")) != str(project.id):
                continue
            if not row.get("enabled") and str(row.get("approval_state")) != APPROVAL_PENDING:
                continue
            if self._fingerprint(row) == wanted:
                return row
        return None

    @staticmethod
    def _fingerprint(values: dict[str, Any]) -> tuple[Any, ...]:
        run_at = values.get("run_at")
        return (
            values.get("action"),
            values.get("trigger_kind"),
            values.get("cron") or "",
            values.get("interval_seconds") or 0,
            # A one-off asked for "in 30 minutes" twice, seconds apart, is one request.
            int(float(run_at) // 60) if isinstance(run_at, int | float) else None,
            values.get("timezone") or "",
            str(values.get("prompt") or "").strip(),
            values.get("target_run_id") or "",
            values.get("target_kind") or "",
            values.get("backend") or "",
            values.get("model") or "",
        )

    async def _check_capacity(
        self, caller: Any, project: Any, *, dry_run: bool, now: float
    ) -> int:
        """Enforce the standing cap and the hourly budget. Returns the slots left.

        A dry run reports the same refusal a real call would meet, so an agent can ask
        "would this work" and get the honest answer.
        """
        cap = int(getattr(self.config, "agent_schedules_per_project", 10))
        if cap <= 0:
            raise QueueError(
                "agent_schedules_disabled",
                "this install does not let agents write schedules; ask the operator",
                status=403,
            )
        standing = await self.store.active_agent_schedules(str(project.id))
        if len(standing) >= cap:
            raise QueueError(
                "schedule_cap_reached",
                f"Project {project.name!r} already holds {len(standing)} agent schedules "
                f"(the most this install allows); cancel one you no longer need with "
                "cancel_schedule, or ask the operator",
                status=409,
                standing=[
                    {
                        "schedule_id": row["id"],
                        "label": row["label"],
                        "yours": self._owns(caller, row),
                    }
                    for row in standing
                ],
            )
        budget = int(getattr(self.config, "agent_schedule_hourly_budget", 6))
        used = await self.store.agent_created_since(str(caller.record.id), now - 3600.0)
        if budget <= 0 or used >= budget:
            raise QueueError(
                "origin_budget_exhausted",
                f"this session has written {used} schedule requests in the last hour, the "
                "most this install allows; wait, or ask the operator",
                status=429,
            )
        return cap - len(standing)

    def _owns(self, caller: Any, row: dict[str, Any]) -> bool:
        if str(row.get("author_kind")) != AUTHOR_AGENT:
            return False
        record = caller.record
        run_id = str(getattr(record, "agent_run_id", "") or "")
        return str(row.get("author_session_id")) == str(record.id) or bool(
            run_id and str(row.get("author_run_id")) == run_id
        )

    async def _policy(self, caller: Any, project: Any, *, now: float) -> dict[str, Any]:
        """The rules an agent schedule meets in one Project, as numbers it can plan with."""
        cap = int(getattr(self.config, "agent_schedules_per_project", 10))
        standing = await self.store.active_agent_schedules(str(project.id))
        budget = int(getattr(self.config, "agent_schedule_hourly_budget", 6))
        used = await self.store.agent_created_since(str(caller.record.id), now - 3600.0)
        return {
            "project_name": str(project.name),
            "schedule_grant": self._grant(str(project.root)),
            "scheduled_runs_opted_in": "scheduled_runs" in await self._gate(str(project.root)),
            "install_enabled": bool(getattr(self.config, "scheduled_runs_enabled", True)),
            "daily_fire_limit": int(getattr(self.config, "agent_schedule_daily_fire_limit", 2)),
            "max_days": int(getattr(self.config, "agent_schedule_max_days", 30)),
            "slots_left": max(0, cap - len(standing)),
            "requests_left_this_hour": max(0, budget - used),
        }

    async def _created_note(self, row: dict[str, Any], spec: ScheduleSpec, armed: bool) -> str:
        parts: list[str] = []
        if armed:
            parts.append("Armed. It fires on its own; nothing more is needed from you.")
        else:
            parts.append(
                "Filed for approval: a person decides in the Fleet Queue or the Schedule "
                f"tab, because {row.get('approval_reason')}. You will get a message in "
                "this conversation when they decide, if it is still open."
            )
        if spec.action == "spawn":
            parts.append(
                "When it fires, a new session starts with your prompt as its first "
                "message, headed by a line saying which schedule started it."
            )
        else:
            own = str(row.get("target_run_id")) == str(row.get("author_run_id"))
            if spec.when_live == "deliver":
                parts.append(
                    "When it fires, the conversation is reopened in a new pane - or, if "
                    "it is already open, your prompt is queued into that pane instead."
                )
            else:
                parts.append(
                    "When it fires, the conversation is reopened in a new pane; if it is "
                    "already open at that moment, the fire is skipped."
                )
            if own:
                parts.append(
                    "The prompt arrives as a message you asked for, so it is sent without "
                    "waiting for a person wherever auto-delivery is on."
                )
                if self._delivery_brake is not None:
                    brake = await self._delivery_brake()
                    if brake:
                        parts.append(
                            f"Right now {brake}, so the prompt would wait in the Queue tab "
                            "for a person to send it."
                        )
            else:
                parts.append(
                    "The prompt is attributed to you, and is sent automatically only if "
                    "that conversation accepts agent-authored messages."
                )
        if spec.recurring:
            parts.append("It expires on its own at the end date shown.")
        parts.append("Cancel it with cancel_schedule; see it with list_schedules.")
        return " ".join(parts)

    async def _agent_view(
        self,
        row: dict[str, Any],
        caller: Any,
        *,
        status: str,
        now: float,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec = spec_from_row(row)
        view = {
            "status": status,
            **self._compact(row, caller, now=now),
            "next_fires": [_instant(moment) for moment in upcoming_fires(spec, now=now)]
            if row.get("enabled")
            else [],
        }
        if extra:
            view.update(extra)
        return view

    def _compact(self, row: dict[str, Any], caller: Any, *, now: float) -> dict[str, Any]:
        project = self.projects.projects.get(str(row.get("project_id") or ""))
        frequency = int(row.get("fires_per_day") or 0)
        compact: dict[str, Any] = {
            "schedule_id": row["id"],
            "label": row["label"],
            "state": schedule_state(row, now=now),
            "action": row.get("action") or "spawn",
            "project_id": row["project_id"],
            "project_name": project.name if project is not None else None,
            "cadence": cadence_text(row),
            "fires_per_day": (
                f"more than {FREQUENCY_COUNT_CEILING}"
                if frequency > FREQUENCY_COUNT_CEILING
                else frequency
            ),
            "next_fire": _instant(row.get("next_fire_at")) if row.get("enabled") else None,
            "expires_at": _instant(row.get("expires_at")),
            "author": (
                {
                    "kind": "agent",
                    "name": row.get("author_name") or None,
                    "session_id": row.get("author_session_id") or None,
                }
                if row.get("author_kind") == AUTHOR_AGENT
                else {"kind": "user"}
            ),
            "yours": self._owns(caller, row),
            "prompt": str(row.get("prompt") or "")[:LISTING_PROMPT_CHARS],
        }
        if row.get("action") == "resume":
            compact["target"] = {
                "run_id": row.get("target_run_id"),
                "kind": row.get("target_kind"),
                "when_live": row.get("when_live") or "skip",
                "is_you": bool(row.get("author_run_id"))
                and str(row.get("target_run_id")) == str(row.get("author_run_id")),
            }
        elif row.get("backend") or row.get("model"):
            compact["backend"] = row.get("backend") or None
            compact["model"] = row.get("model") or None
        if row.get("approval_state") == APPROVAL_PENDING:
            compact["approval_reason"] = row.get("approval_reason") or None
        if row.get("last_outcome"):
            compact["last_run"] = {
                "outcome": row.get("last_outcome"),
                "reason": row.get("last_reason") or None,
                "at": _instant(row.get("last_fire_at")),
                "session_id": row.get("last_session_id"),
            }
        if not row.get("enabled") and row.get("disabled_reason"):
            compact["disabled_reason"] = row.get("disabled_reason")
        return compact

    async def _changed(self, row: dict[str, Any], *, source: str, action: str) -> None:
        await self.events.emit(
            "schedule_changed",
            source=source,
            action=action,
            schedule_id=row["id"],
            project_id=str(row.get("project_id") or ""),
            author_kind=str(row.get("author_kind") or "user"),
            approval_state=str(row.get("approval_state") or ""),
        )

    async def _tell_author(self, row: dict[str, Any], verb: str) -> None:
        """Tell the agent that asked what a person decided, if it is still there.

        Through the ordinary queue, armed by naming the schedule in `solicited_by`:
        the author asked a question and this is the bounded, daemon-worded answer to
        it, addressed to nobody else. Only the conversation that asked is told - a
        pane that rolled its conversation since is a different conversation, and
        best effort throughout, because the decision has already happened.
        """
        if self._enqueue is None:
            return
        session_id = str(row.get("author_session_id") or "")
        session = self.sessions.sessions.get(session_id) if session_id else None
        if session is None or str(session.record.state) in {"exited", "crashed"}:
            return
        asked_run = str(row.get("author_run_id") or "")
        live_run = str(getattr(session.record, "agent_run_id", "") or "")
        if asked_run and live_run and asked_run != live_run:
            return
        if verb == "approved":
            text = (
                f'[swe-mux] Your schedule "{row["label"]}" ({row["id"]}) was approved and '
                "is armed. Nothing more is needed from you."
            )
        else:
            text = (
                f'[swe-mux] Your schedule request "{row["label"]}" ({row["id"]}) was '
                "dismissed by the operator and will not run. Do not re-request it unless "
                "they ask you to."
            )
        try:
            await self._enqueue(
                target_session_id=session_id,
                body=text,
                armed=True,
                sender_kind="rule",
                sender_id=f"schedule:{row['id']}",
                sender_label="Schedule decision",
                solicited_by=str(row["id"]),
                origin={"kind": "schedule_decision", "schedule_id": row["id"], "decision": verb},
            )
        except Exception:  # noqa: BLE001 - the decision stands whether or not this lands
            log.warning(
                "schedule decision notice could not be queued schedule_id=%s session=%s",
                row["id"],
                session_id,
                exc_info=True,
            )
