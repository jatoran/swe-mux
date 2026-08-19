"""The scheduled-run service: the only thing that turns a due schedule into a session.

A scheduled run is the human's own deferred press of a button they could have pressed
themselves, which is what keeps this out of decision-gated territory. There are two
buttons, and each goes through the code that already owns it:

- **Run** (`action: spawn`) starts a new session through `_spawn_from_body`, the exact
  path the Run menu and the Fleet Queue approval use.
- **Resume** (`action: resume`) reopens an existing conversation through
  `session_resume.resume_run`, the exact path the History browser's Resume button uses,
  with the same five refusals worded the same way.

Follow-up messages go through the ordinary prompt queue with its readiness,
constraint, and audit contract. None of the three is re-implemented here: a second
spawn path, a second resume path, or a second delivery path would each be a second
authority, and the one that drifted would do so silently at 3 a.m.

What this module owns is the decision to fire, and every guard around it:

- **Idempotency.** The occurrence is claimed by inserting a run row under a
  unique `(schedule_id, fire_key)` index *before* anything is spawned. A daemon
  that dies mid-fire cannot double-spawn on restart, and two sweeps cannot race.
- **Missed windows.** This daemon lives in a desktop app that sleeps and gets
  redeployed, so "the machine was off at 03:00" is the normal case. A fire that
  is late by more than `MISSED_GRACE_SECONDS` is either replayed once
  (`catch_up`) or recorded as `missed` - never silently dropped, and never
  replayed once per missed occurrence.
- **Overlap.** `skip` (the default) refuses to start a second run while the
  previous one's session is still alive. Unattended agents that pile up are how
  a nightly job becomes a fleet of forgotten panes burning subscription quota.
  A resume has no other option: a CLI opens a conversation once, so `allow` is
  refused at write time rather than discovered as a nightly skip.
- **Caps.** A per-schedule daily cap and an install-wide concurrency cap, both
  checked at fire time rather than trusted from the definition.
- **Enablement.** The Project must have opted into the `scheduled_runs`
  automation, and the install-wide switch must be on. Permission is checked at
  every fire, not at write time, so revoking it takes effect immediately.

Nothing here can raise into the daemon: the loop runs under the background-task
supervisor, and a fault costs one occurrence rather than the feature.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .background_tasks import background
from .schedule_store import ScheduleConflict, ScheduleStore
from .schedules import (
    FollowUp,
    ScheduleSpec,
    first_occurrence,
    next_occurrence,
    occurrence_key,
)
from .session_resume import ResumeRefused, fork_run, resolve_latest_run, resume_run

log = logging.getLogger(__name__)

SCHEDULE_LOOP = "schedules"

# How late a fire may be before it counts as a missed window rather than an
# ordinary late tick. Generous relative to the sweep cadence and far shorter than
# any legal interval, so an ordinary busy daemon never trips it and a laptop that
# was shut always does.
MISSED_GRACE_SECONDS = 900.0

# Reasons a fire did not start a session. These are user-facing strings in the
# Schedule tab's `last outcome`, so they say what happened rather than which
# branch was taken.
REASON_LABELS = {
    "install_disabled": "scheduled runs are switched off for this install",
    "project_missing": "the Project this schedule belongs to is no longer registered",
    "automation_disabled": "this Project has not opted into scheduled runs",
    "overlap": "the previous run's session is still live",
    "daily_cap": "this schedule reached its daily run cap",
    "concurrency_cap": "too many scheduled sessions are already running",
    "missed_window": "the daemon was not running when this was due",
    "target_missing": "the conversation this schedule resumes has no History entry any more",
    "history_unavailable": "history is not available to this daemon",
}

# Refusals a resume can meet that say "not now" rather than "never". They are recorded
# as `skipped` and raise no alert, because a schedule armed against a conversation the
# operator also uses by hand will meet them routinely - somebody was simply in it - and
# a nightly alert for that would teach its reader to ignore the alerts that matter.
TRANSIENT_RESUME_CODES = frozenset({"conversation_live", "conversation_held"})

# The reason a fire was refused, in the words of the cause rather than of the branch.
RESUME_REASON_LABELS = {
    "conversation_live": "the conversation is open in another session",
    "conversation_held": "another CLI process is holding this conversation",
    "context_ceiling": "the conversation is already too full to continue usefully",
}


def spec_from_row(row: dict[str, Any]) -> ScheduleSpec:
    """Rebuild the validated spec from a stored definition."""
    follow_ups = tuple(
        FollowUp(
            body=str(item.get("body") or ""),
            delay_seconds=float(item.get("delay_seconds") or 0.0),
            armed=bool(item.get("armed")),
        )
        for item in (row.get("follow_ups") or [])
        if str(item.get("body") or "").strip()
    )
    return ScheduleSpec(
        label=str(row["label"]),
        prompt=str(row["prompt"]),
        trigger_kind=str(row["trigger_kind"]),  # type: ignore[arg-type]
        cron=str(row.get("cron") or ""),
        interval_seconds=row.get("interval_seconds"),
        run_at=row.get("run_at"),
        timezone=str(row.get("timezone") or ""),
        catch_up=bool(row.get("catch_up")),
        overlap=str(row.get("overlap") or "skip"),  # type: ignore[arg-type]
        backend=str(row.get("backend") or ""),
        profile_id=str(row.get("profile_id") or ""),
        cwd=str(row.get("cwd") or ""),
        session_name=str(row.get("session_name") or ""),
        daily_run_cap=int(row.get("daily_run_cap") or 0),
        follow_ups=follow_ups,
        action=str(row.get("action") or "spawn"),  # type: ignore[arg-type]
        target_run_id=str(row.get("target_run_id") or ""),
        target_kind=str(row.get("target_kind") or "run"),  # type: ignore[arg-type]
        target_cut_message_id=str(row.get("target_cut_message_id") or ""),
        target_cut_mode=str(row.get("target_cut_mode") or ""),
        context_ceiling_pct=float(row.get("context_ceiling_pct") or 0.0),
    )


class ScheduleService:
    """Sweeps due schedules and fires them under every guard above."""

    def __init__(
        self,
        *,
        store: ScheduleStore,
        projects: Any,
        sessions: Any,
        config: Any,
        events: Any,
        automation_gate: Callable[[str], Awaitable[frozenset[str]]],
        spawn_op: Callable[[dict[str, Any]], Awaitable[Any]],
        enqueue: Callable[..., Awaitable[dict[str, Any]]],
        notify: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        history: Any = None,
        automation_store: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.projects = projects
        self.sessions = sessions
        self.config = config
        self.events = events
        self._gate = automation_gate
        self._spawn = spawn_op
        self._enqueue = enqueue
        self._notify = notify
        # Read-only, and only for `action: resume`: the conversation to reopen is named
        # by a history run id, and following one to where it has got to walks lineage.
        # Optional so the existing spawn-only tests construct the service unchanged.
        self._history = history
        self._automation_store = automation_store
        self._clock = clock
        self._started = False

    # ------------------------------------------------------------------ loop

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        background.start(SCHEDULE_LOOP, self._run)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        await background.stop(SCHEDULE_LOOP)

    async def _run(self) -> None:
        cadence = max(1.0, float(getattr(self.config, "scheduled_runs_poll_seconds", 5.0)))
        while True:
            # The wait is outside the guard on purpose: time spent sleeping is not
            # this loop's cost, and counting it as such is how a cheap loop ends up
            # at the top of the `costliest` list (`background_tasks.py`).
            await asyncio.sleep(cadence)
            with background.iteration(SCHEDULE_LOOP):
                await self.tick()

    async def tick(self, now: float | None = None) -> list[dict[str, Any]]:
        """Fire every schedule that is due. Returns the run rows it settled."""
        moment = self._clock() if now is None else now
        due = await self.store.due(moment)
        settled: list[dict[str, Any]] = []
        for schedule in due:
            try:
                run = await self.fire(schedule, due_at=float(schedule["next_fire_at"]), now=moment)
            except Exception:  # noqa: BLE001 - one schedule must not cost the sweep
                log.exception("scheduled run failed schedule_id=%s", schedule.get("id"))
                continue
            if run is not None:
                settled.append(run)
        return settled

    # ------------------------------------------------------------------ fire

    async def run_now(self, schedule_id: str) -> dict[str, Any] | None:
        """Fire one schedule immediately, on a human's explicit request.

        A manual run gets its own occurrence key, so it never consumes or
        collides with the timer's claim on the next scheduled window, and it does
        not move `next_fire_at`.
        """
        schedule = await self.store.get(schedule_id)
        if schedule is None:
            return None
        return await self.fire(
            schedule,
            due_at=self._clock(),
            origin="manual",
            fire_key=f"manual:{uuid.uuid4().hex[:12]}",
            advance=False,
        )

    async def fire(
        self,
        schedule: dict[str, Any],
        *,
        due_at: float,
        now: float | None = None,
        origin: str = "timer",
        fire_key: str | None = None,
        advance: bool = True,
    ) -> dict[str, Any] | None:
        moment = self._clock() if now is None else now
        schedule_id = str(schedule["id"])
        spec = spec_from_row(schedule)

        # Advance the trigger *first*. A fire that is refused for any reason
        # still has to move the window forward, or the sweep spins on the same
        # row every tick for as long as the refusal lasts.
        if advance:
            following = next_occurrence(spec, max(due_at, moment))
            await self.store.set_next_fire(schedule_id, following, now=moment)
            if following is None and spec.trigger_kind == "once":
                await self.store.set_enabled(
                    schedule_id, False, next_fire_at=None, reason="completed", now=moment
                )

        try:
            run = await self.store.claim_run(
                schedule_id=schedule_id,
                project_id=str(schedule["project_id"]),
                fire_key=fire_key or occurrence_key(due_at),
                due_at=due_at,
                origin=origin,
                now=moment,
            )
        except ScheduleConflict:
            # Another sweep, or a previous daemon, already owns this occurrence.
            return None

        outcome, reason, session_id, queued = await self._execute(
            schedule, spec, run, due_at=due_at, moment=moment, origin=origin
        )
        settled = await self.store.finish_run(
            str(run["id"]),
            outcome=outcome,
            reason=reason,
            session_id=session_id,
            queued_messages=queued,
            now=self._clock(),
        )
        await self.events.emit(
            "schedule_fired",
            session_id=session_id,
            source="schedule",
            schedule_id=schedule_id,
            project_id=str(schedule["project_id"]),
            label=spec.label,
            outcome=outcome,
            reason=reason,
            origin=origin,
            due_at=due_at,
        )
        if outcome == "failed" and self._notify is not None:
            await self._notify(
                agent_run_id=None,
                session_id=session_id,
                rule_id=f"schedule:{schedule_id}",
                kind="schedule-failed",
                title=f"Scheduled run failed: {spec.label}",
                message=reason or "the scheduled run could not start",
                severity="warn",
            )
        return settled

    async def _execute(
        self,
        schedule: dict[str, Any],
        spec: ScheduleSpec,
        run: dict[str, Any],
        *,
        due_at: float,
        moment: float,
        origin: str,
    ) -> tuple[str, str, str | None, int]:
        """Decide and perform. Returns `(outcome, reason, session_id, queued)`."""
        schedule_id = str(schedule["id"])
        if not bool(getattr(self.config, "scheduled_runs_enabled", True)):
            return "skipped", REASON_LABELS["install_disabled"], None, 0

        # A late timer fire is either replayed once or recorded as missed. A
        # manual run is never "late": the human is standing there.
        if (
            origin == "timer"
            and moment - due_at > MISSED_GRACE_SECONDS
            and not spec.catch_up
        ):
            return "missed", REASON_LABELS["missed_window"], None, 0

        project = self.projects.projects.get(str(schedule["project_id"]))
        if project is None:
            await self.store.set_enabled(
                schedule_id, False, next_fire_at=None, reason="project_missing", now=moment
            )
            return "skipped", REASON_LABELS["project_missing"], None, 0

        enabled = await self._gate(str(project.root))
        if "scheduled_runs" not in enabled:
            return "skipped", REASON_LABELS["automation_disabled"], None, 0

        if spec.overlap == "skip" and self._session_live(schedule.get("last_session_id")):
            return "skipped", REASON_LABELS["overlap"], None, 0

        cap = int(spec.daily_run_cap or 0)
        if cap:
            used = await self.store.runs_since(schedule_id, moment - 86_400)
            # The claim row for this very fire is already counted.
            if used > cap:
                return "skipped", REASON_LABELS["daily_cap"], None, 0

        concurrent = await self._live_scheduled_sessions()
        limit = int(getattr(self.config, "scheduled_runs_max_concurrent", 2))
        if limit and len(concurrent) >= limit:
            return "skipped", REASON_LABELS["concurrency_cap"], None, 0

        if spec.action == "resume":
            return await self._resume(schedule, spec, run, project=project, moment=moment)

        body: dict[str, Any] = {
            "project_id": str(project.id),
            "seed_text": spec.prompt,
        }
        if spec.backend:
            body["backend"] = spec.backend
        if spec.profile_id:
            body["profile_id"] = spec.profile_id
        if spec.cwd:
            body["cwd"] = spec.cwd
        label = (spec.session_name or spec.label).strip()[:80]
        if label:
            body["name"] = label
        try:
            session = await self._spawn(body)
        except Exception as exc:  # noqa: BLE001 - a bad definition must not kill the loop
            log.warning(
                "scheduled_spawn_failed schedule_id=%s project_id=%s error_type=%s error=%s",
                schedule_id,
                project.id,
                type(exc).__name__,
                exc,
            )
            return "failed", f"{type(exc).__name__}: {exc}"[:400], None, 0

        session_id = str(session.record.id)
        queued = await self._queue_messages(spec, session_id, run_id=str(run["id"]))
        return "spawned", "", session_id, queued

    # ----------------------------------------------------------------- resume

    async def _resume(
        self,
        schedule: dict[str, Any],
        spec: ScheduleSpec,
        run: dict[str, Any],
        *,
        project: Any,
        moment: float,
    ) -> tuple[str, str, str | None, int]:
        """Reopen the conversation this schedule points at.

        Every guard shared with a spawn has already run. What is left is the three
        questions only a resume has, in the order that makes each answerable:

        1. **Which run.** A pinned one, or wherever that conversation has got to.
           Resolved at fire time and never cached, because the whole point of
           `latest_of_session` is that the answer moves.
        2. **Is continuing still worth it.** Only for a rolling continuation, and only
           against a stated ceiling: each resume replays the accumulated conversation
           as fresh input, and past the ceiling the early context has been compacted
           into a summary, so "always a continuation" has quietly become a summary of a
           summary. Refusing is more useful than firing.
        3. **From where.** A `fork_point` schedule writes a fresh fork of the pinned
           moment first, so this fire starts from the same context as the last one
           rather than from the last one's output.

        The resume itself goes through `session_resume.resume_run` - the same code the
        Resume button calls - so the five refusals it can meet are the same five,
        worded the same way.
        """
        schedule_id = str(schedule["id"])
        if self._history is None:
            return "failed", REASON_LABELS["history_unavailable"], None, 0
        row = await self._target_row(spec)
        if row is None:
            await self.store.set_enabled(
                schedule_id, False, next_fire_at=None, reason="target_missing", now=moment
            )
            return "skipped", REASON_LABELS["target_missing"], None, 0
        if (full := self._context_refusal(spec, row)) is not None:
            return "skipped", full, None, 0
        try:
            fork = None
            conversation_id = ""
            if spec.target_kind == "fork_point":
                fork = await fork_run(
                    row,
                    sessions=self.sessions,
                    projects=self.projects,
                    target_project_id=str(project.id),
                    message_id=spec.target_cut_message_id,
                    mode=spec.target_cut_mode,
                )
                conversation_id = str(fork["conversation_id"])
            outcome = await resume_run(
                row,
                sessions=self.sessions,
                projects=self.projects,
                target_project_id=str(project.id),
                name=(spec.session_name or "").strip(),
                flow=f"scheduled resume {spec.label!r}",
                conversation_id=conversation_id,
                fork=fork,
            )
        except ResumeRefused as refusal:
            transient = refusal.code in TRANSIENT_RESUME_CODES
            reason = RESUME_REASON_LABELS.get(refusal.code, str(refusal))[:400]
            if refusal.code == "not_agent":
                await self.store.set_enabled(
                    schedule_id, False, next_fire_at=None, reason=refusal.code, now=moment
                )
            log.log(
                logging.INFO if transient else logging.WARNING,
                "scheduled resume %s of run %s refused: %s (%s)",
                schedule_id,
                row.get("id"),
                refusal,
                refusal.code,
            )
            return ("skipped" if transient else "failed"), reason, None, 0
        except Exception as exc:  # noqa: BLE001 - one definition must not kill the loop
            log.warning(
                "scheduled_resume_failed schedule_id=%s run_id=%s error_type=%s error=%s",
                schedule_id,
                row.get("id"),
                type(exc).__name__,
                exc,
            )
            return "failed", f"{type(exc).__name__}: {exc}"[:400], None, 0
        session_id = str(outcome.session.record.id)
        await self._record_resume_lineage(row, outcome)
        queued = await self._queue_messages(spec, session_id, run_id=str(run["id"]))
        return "spawned", "", session_id, queued

    async def _target_row(self, spec: ScheduleSpec) -> dict[str, Any] | None:
        """The history row this fire resumes, resolved now rather than at write time."""
        if spec.target_kind == "latest_of_session":
            return await resolve_latest_run(
                spec.target_run_id,
                history=self._history,
                automation_store=self._automation_store,
            )
        row: dict[str, Any] | None = await self._history.history_entry(spec.target_run_id)
        return row

    def _context_refusal(self, spec: ScheduleSpec, row: dict[str, Any]) -> str | None:
        """The ceiling on a rolling continuation, or None.

        Reads the conversation's own final fill rather than estimating one: the
        history row already carries what the harness reported, so this costs a
        dictionary lookup and cannot disagree with what the session sidebar showed.
        A row with no measurement is not evidence of a full conversation, so an
        unmeasured one is allowed through.
        """
        ceiling = float(spec.context_ceiling_pct or 0.0)
        if not ceiling:
            return None
        measured = row.get("final_context_pct")
        if not isinstance(measured, int | float) or measured <= 0:
            return None
        if float(measured) < ceiling:
            return None
        return (
            f"{RESUME_REASON_LABELS['context_ceiling']} "
            f"({round(float(measured) * 100)}% of its context window, "
            f"ceiling {round(ceiling * 100)}%)"
        )

    async def _record_resume_lineage(self, row: dict[str, Any], outcome: Any) -> None:
        """Record how the new run relates to the one it came from.

        Written after the pane is proved up, and never for a run the pane *inherited*:
        an edge from a conversation to itself would make every consumer that walks
        lineage see a cycle where nothing was forked. A fork earns a `branch` edge
        rather than a `resume` one, because that is what it is - and because
        `resolve_latest_run` follows `resume` edges, so calling a fork a resume would
        make every later fire of a rolling schedule chase last night's fork.
        """
        if self._automation_store is None:
            return
        child = outcome.session.record.agent_run_id or outcome.session.record.id
        if str(child) == str(row["id"]):
            return
        relation = "branch" if outcome.fork else "resume"
        metadata: dict[str, Any] = {
            "backend": row.get("backend"),
            "project_id": outcome.session.record.project_id,
            "origin": "schedule",
        }
        if outcome.fork:
            metadata.update(
                {
                    "strategy": "transcript_fork",
                    "source_conversation_id": row.get("native_id"),
                    "branch_conversation_id": outcome.conversation_id,
                    "from_message_id": outcome.fork.get("from_message_id"),
                    "from_message_role": outcome.fork.get("from_message_role"),
                    "mode": outcome.fork.get("mode"),
                    "cut_offset": outcome.fork.get("cut_offset"),
                }
            )
        try:
            await self._automation_store.add_lineage(str(row["id"]), str(child), relation, metadata)
        except Exception as exc:  # noqa: BLE001 - the session is up; the edge is bookkeeping
            log.warning("scheduled resume of %s could not record its lineage: %s", row["id"], exc)

    # ------------------------------------------------------------- follow-ups

    async def _queue_messages(
        self, spec: ScheduleSpec, session_id: str, *, run_id: str
    ) -> int:
        """Stage what this schedule says, in order, through the ordinary prompt queue.

        Bind-on-first-run means these can be written the moment the session exists:
        each keys to the session *and* the first agent run it gets, so a message can
        never land in a conversation it was not written for. A delay becomes a
        `not_before` constraint enforced by the queue, never a timer here.

        A **spawn**'s prompt is not here - it rides argv as the seed. A **resume**'s is,
        and has to be: the resumed pane's argv is already `--resume <id>`, and whether a
        positional prompt may follow that is per-harness luck rather than a contract.
        The queue is the contract, so the prompt goes through it like every other
        rule-authored message, which also means it obeys the auto-delivery gate rather
        than walking around it.
        """
        queued = 0
        base = self._clock()
        messages = list(spec.follow_ups)
        if spec.action == "resume" and spec.prompt.strip():
            messages.insert(0, FollowUp(body=spec.prompt))
        for index, item in enumerate(messages):
            constraints = (
                {"not_before": base + item.delay_seconds} if item.delay_seconds else None
            )
            try:
                await self._enqueue(
                    target_session_id=session_id,
                    body=item.body,
                    armed=item.armed,
                    sender_kind="rule",
                    sender_id=f"schedule:{run_id}",
                    sender_label=f"Schedule · {spec.label}",
                    constraints=constraints,
                    origin={"kind": "schedule", "label": spec.label, "index": index},
                )
            except Exception:  # noqa: BLE001 - the session started; the queue is best effort
                log.exception(
                    "scheduled message %d could not be queued for session %s",
                    index + 1,
                    session_id,
                )
                continue
            queued += 1
        return queued

    # ---------------------------------------------------------------- helpers

    def _session_live(self, session_id: Any) -> bool:
        if not session_id:
            return False
        session = self.sessions.sessions.get(str(session_id))
        if session is None:
            return False
        return str(getattr(session.record, "state", "")) not in {"exited", "crashed"}

    async def _live_scheduled_sessions(self) -> list[str]:
        return [
            session_id
            for session_id in await self.store.active_sessions()
            if self._session_live(session_id)
        ]

    # ------------------------------------------------------------- lifecycle

    async def restore(self) -> None:
        """Repair every enabled schedule's next fire at daemon start.

        A window that passed while the process was down is deliberately left in
        the past, so the next sweep applies the missed-window policy to it rather
        than pretending it never came due.

        Only two cases are recomputed. A **cron** schedule's next fire is a
        function of a timezone database and a definition that a redeploy or an
        upgrade can change underneath the cached value, so it is derived again.
        A schedule with **no** stored fire at all is repaired because an armed
        schedule that can never fire is the failure this feature must not have.
        An interval's stored fire is left exactly as it is: recomputing it would
        restart the interval on every daemon restart, and on a desktop app that
        reloads several times an hour a 6-hourly job would then never run.
        """
        moment = self._clock()
        for schedule in await self.store.list_schedules():
            if not schedule.get("enabled"):
                continue
            stored = schedule.get("next_fire_at")
            overdue = isinstance(stored, int | float) and float(stored) <= moment
            if overdue:
                continue
            if stored is not None and str(schedule.get("trigger_kind")) != "cron":
                continue
            spec = spec_from_row(schedule)
            following = first_occurrence(spec, now=moment)
            if following != stored:
                await self.store.set_next_fire(str(schedule["id"]), following, now=moment)

    async def status(self) -> dict[str, Any]:
        schedules = await self.store.list_schedules()
        live = await self._live_scheduled_sessions()
        return {
            "enabled": bool(getattr(self.config, "scheduled_runs_enabled", True)),
            "total": len(schedules),
            "armed": sum(1 for item in schedules if item.get("enabled")),
            "live_sessions": len(live),
            "max_concurrent": int(getattr(self.config, "scheduled_runs_max_concurrent", 2)),
        }
