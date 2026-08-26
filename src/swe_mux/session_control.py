"""Phase 7.6: agent session control - interrupt and end (CP §7.6, §16).

The first MCP tools that act on a running agent. MCP is transport, not authority
(`CONTROL_PLANE_ROADMAP.md` §7.1): this service owns every bound, and the MCP
layer is a thin caller. The bounds are the install master switch, the per-Project
three-position grant (`off` / `draft` / `granted`, defaulting to `draft` so a
human approves every action), Project scope, the fail-closed delivery-readiness
gate an interrupt must pass, a per-origin hourly budget, a reciprocal-cycle
guard, and idempotency.

The two capabilities are deliberately two tools with different blast radii:
`interrupt` stops the current turn and the session lives on; `end_session` ends
the session, the caller's own included. An agent may end itself, but it may not
erase itself: the tool returns before teardown and the record survives.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .harness import is_agent_harness
from .project_scope import (
    ProjectScope,
    record_scope,
    resolve_project_scope,
    split_qualified_target,
)
from .prompt_queue import QueueError

#: The end reason persisted for any agent-initiated end reached through this
#: surface - graceful or hard fallback - so a post-mortem can tell it apart from
#: an operator `killed` and a CLI `exited`.
AGENT_END_REASON = "agent_ended"

#: The window the per-origin budget and the reciprocal-cycle guard look back over.
_BUDGET_WINDOW_SECONDS = 3600.0
_CYCLE_WINDOW_SECONDS = 300.0


@dataclass(slots=True)
class _ControlAction:
    origin_session: str
    target_session: str
    kind: str
    at: float


class SessionControlService:
    """Authority and bounds around the interrupt/graceful-end daemon operations."""

    def __init__(
        self,
        *,
        sessions: Any,
        projects: Any,
        config: Any,
        events: Any,
        readiness_evaluate: Callable[[Any], dict[str, Any]],
        automation_gate: Callable[[str], Awaitable[frozenset[str]]],
        grant_field: Callable[[str], str],
        interrupt_op: Callable[[Any], Awaitable[None]],
        graceful_end_op: Callable[[Any, str], Awaitable[dict[str, Any]]],
        is_daemon_owner: Callable[[Any], bool],
        spawn_grant_field: Callable[[str], str] | None = None,
        spawn_op: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
        draft_spawn: Any = None,
        append_observation: Any = None,
        read_observations: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.sessions = sessions
        self.projects = projects
        self.config = config
        self.events = events
        self._readiness = readiness_evaluate
        self._automation_gate = automation_gate
        self._grant_field = grant_field
        self._interrupt_op = interrupt_op
        self._graceful_end_op = graceful_end_op
        self._is_daemon_owner = is_daemon_owner
        # Spawn (the Phase 7.6 follow-on). `draft_spawn` is the Phase 5 inert-
        # request producer this falls back to; `spawn_op` creates a session
        # directly on the granted path.
        self._spawn_grant_field = spawn_grant_field
        self._spawn_op = spawn_op
        self._draft_spawn = draft_spawn
        self._append_observation = append_observation
        self._read_observations = read_observations
        self._clock = clock
        self._recent: deque[_ControlAction] = deque(maxlen=2000)
        # correlation_id -> the prior result, so a retried call is idempotent
        # rather than acting twice.
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    # ---------------------------------------------------------------- public

    async def interrupt(
        self,
        caller: Any,
        *,
        target: str,
        reason: str = "",
        correlation_id: str | None = None,
        project: str = "",
    ) -> dict[str, Any]:
        return await self._perform(
            caller,
            identity=target,
            action="interrupt",
            reason=reason,
            correlation_id=correlation_id,
            project=project,
        )

    async def end_session(
        self,
        caller: Any,
        *,
        target: str,
        reason: str = "",
        correlation_id: str | None = None,
        project: str = "",
    ) -> dict[str, Any]:
        return await self._perform(
            caller,
            identity=target,
            action="end_session",
            reason=reason,
            correlation_id=correlation_id,
            project=project,
        )

    async def spawn(
        self,
        caller: Any,
        *,
        prompt: str,
        backend: str = "",
        name: str = "",
        reason: str = "",
        correlation_id: str | None = None,
        project: str = "",
    ) -> dict[str, Any]:
        """Create a session, or draft the request, per the target Project's grant.

        Authority is by target Project, like interrupt and end: a Project's
        `spawn_grant` of "granted" (with the `session_control` automation on) lets
        an agent create a session in it directly, inside a per-origin budget;
        anything else keeps the Phase 5 behaviour of an inert Fleet Queue request a
        human approves. The `project` argument names the target Project, defaulting
        to the caller's own; an agent can spawn into any registered Project that
        granted it.
        """
        if not self.config.request_spawn_enabled:
            raise QueueError(
                "request_spawn_disabled",
                "drafted spawn requests are disabled on this mux install",
                status=403,
            )
        text = str(prompt or "").strip()
        if not text:
            raise QueueError("invalid_body", "prompt must not be empty", status=400)
        if backend and not is_agent_harness(backend):
            raise QueueError(
                "invalid_backend", "backend must be a registered agent", status=400
            )
        correlation = str(correlation_id or "").strip()
        if correlation and correlation in self._idempotency:
            return self._idempotency[correlation]
        scope = resolve_project_scope(project, caller.record, self.projects)
        if scope.fleet:
            raise QueueError(
                "invalid_project",
                "a spawn belongs to one Project; name the Project the session "
                'should start in rather than passing "fleet"',
                status=400,
            )
        project_id = scope.project_id or str(caller.record.project_id or "")
        target_project = (
            self.projects.projects.get(project_id) if project_id else None
        )
        if target_project is None:
            raise QueueError(
                "no_project", "your session is not registered to a Project", status=409
            )
        grant = await self._resolve_spawn_grant(str(target_project.root))
        if grant != "granted":
            result = await self._draft_spawn_request(
                caller, prompt=text, backend=backend, name=name, reason=reason,
                project=project,
            )
        else:
            result = await self._spawn_now(
                caller, target_project, prompt=text, backend=backend, name=name,
                reason=reason,
            )
        if correlation:
            result = {**result, "correlation_id": correlation}
            self._idempotency[correlation] = result
        return result

    async def _draft_spawn_request(
        self, caller: Any, **kwargs: Any
    ) -> dict[str, Any]:
        if self._draft_spawn is None:
            raise QueueError(
                "request_spawn_unavailable",
                "spawn request storage is unavailable",
                status=503,
            )
        return dict(await self._draft_spawn(caller, **kwargs))

    async def _spawn_now(
        self,
        caller: Any,
        target_project: Any,
        *,
        prompt: str,
        backend: str,
        name: str,
        reason: str,
    ) -> dict[str, Any]:
        if self._spawn_op is None:
            raise QueueError(
                "spawn_unavailable",
                "this daemon cannot spawn a session directly",
                status=503,
            )
        self._enforce_spawn_budget(caller)
        resolved_backend = backend or str(getattr(caller.record, "backend", "") or "")
        body: dict[str, Any] = {
            "project_id": str(target_project.id),
            "backend": resolved_backend,
            "seed_text": prompt,
        }
        label = str(name or "").strip()[:80]
        if label:
            body["name"] = label
        session = await self._spawn_op(body)
        self._recent.append(
            _ControlAction(
                origin_session=str(caller.record.id),
                target_session=str(session.record.id),
                kind="spawn",
                at=self._clock(),
            )
        )
        await self.events.emit(
            "agent_session_control",
            session_id=str(caller.record.id),
            source="agent",
            action="spawn",
            outcome="spawned",
            target_session_id=str(session.record.id),
            target_name=str(session.record.name),
            origin_run_id=str(getattr(caller.record, "agent_run_id", "") or ""),
            project_id=str(target_project.id),
            reason=str(reason or "")[:500],
        )
        cross_project = str(target_project.id) != str(caller.record.project_id or "")
        return {
            "status": "spawned",
            "grant": "granted",
            "session_id": str(session.record.id),
            "name": str(session.record.name),
            "backend": str(session.record.backend),
            "project_id": str(target_project.id),
            "project_name": str(target_project.name),
            "cross_project": cross_project,
            "note": (
                "A live session was created and seeded with your prompt. Watch it "
                "with get_session / read_transcript, and end it with end_session "
                "when its work is done."
            ),
        }

    async def _resolve_spawn_grant(self, root: str) -> str:
        """off / draft / granted for spawning into a Project root.

        "off" collapses into "draft" here: without the `session_control`
        automation the Phase 5 inert-request path is exactly the desired default,
        so there is no separate refusal - a spawn always has the draft to fall back
        on, unlike interrupt/end which have nothing to do when disabled.
        """
        if not root or self._spawn_grant_field is None:
            return "draft"
        enabled = await self._automation_gate(root)
        if "session_control" not in enabled:
            return "draft"
        return self._spawn_grant_field(root)

    def _enforce_spawn_budget(self, caller: Any) -> None:
        budget = int(self.config.agent_spawn_hourly_budget)
        if budget <= 0:
            raise QueueError(
                "origin_budget_exhausted",
                "this install allows no agent-initiated spawns",
                status=429,
            )
        cutoff = self._clock() - _BUDGET_WINDOW_SECONDS
        used = sum(
            1
            for action in self._recent
            if action.kind == "spawn"
            and action.origin_session == str(caller.record.id)
            and action.at >= cutoff
        )
        if used >= budget:
            raise QueueError(
                "origin_budget_exhausted",
                f"this session has spawned {used} sessions in the last hour "
                f"(limit {budget})",
                status=429,
            )

    # --------------------------------------------------------------- guts

    async def _perform(
        self,
        caller: Any,
        *,
        identity: str,
        action: str,
        reason: str,
        correlation_id: str | None,
        project: str,
    ) -> dict[str, Any]:
        if not self.config.session_control_enabled:
            raise QueueError(
                "session_control_disabled",
                "agent session control is disabled on this mux install.",
                status=403,
            )
        correlation = str(correlation_id or "").strip()
        if correlation and correlation in self._idempotency:
            return self._idempotency[correlation]
        target, scope = self._resolve_target(
            caller, identity, project, allow_self=(action == "end_session")
        )
        target_root = self._project_root(target)
        grant = await self._resolve_grant(target_root)
        if grant == "off":
            raise QueueError(
                "session_control_not_enabled",
                "agent session control is not enabled for this session's Project. "
                "Enable the 'session control' automation for it.",
                status=403,
            )
        if grant == "draft":
            # A draft is inert and human-reviewed, so it is unbounded here, the
            # same as `request_spawn`. The budget and the cycle guard bound the
            # granted path, which is the one that acts.
            result = await self._draft(caller, target, action, reason, target_root)
        else:
            result = await self._act(caller, target, action, reason)
        if correlation:
            result = {**result, "correlation_id": correlation}
            self._idempotency[correlation] = result
        return result

    async def _act(
        self, caller: Any, target: Any, action: str, reason: str
    ) -> dict[str, Any]:
        self._enforce_budget(caller)
        self._enforce_cycle(caller, target)
        self._recent.append(
            _ControlAction(
                origin_session=str(caller.record.id),
                target_session=str(target.record.id),
                kind=action,
                at=self._clock(),
            )
        )
        if action == "interrupt":
            self._gate_readiness(target)
            await self._interrupt_op(target)
            await self._audit(caller, target, action, "interrupted", reason)
            return {
                "status": "interrupted",
                "action": action,
                "grant": "granted",
                **self._target_view(target),
            }
        # end_session. Self-termination is permitted and is the ordinary finished
        # worker case, but it is the caller's last act: the result returns before
        # teardown begins, and the record survives to be read back.
        is_self = str(target.record.id) == str(caller.record.id)
        await self._audit(caller, target, action, "ending", reason)
        if is_self:
            self._schedule(self._graceful_end_op(target, AGENT_END_REASON))
            return {
                "status": "ending",
                "action": action,
                "grant": "granted",
                "self": True,
                "note": (
                    "You are ending yourself. Your final turn is flushed and your "
                    "record stays readable via list_sessions(include_ended) and "
                    "get_session. This is your last action."
                ),
                **self._target_view(target),
            }
        outcome = await self._graceful_end_op(target, AGENT_END_REASON)
        return {
            "status": "ended",
            "action": action,
            "grant": "granted",
            "self": False,
            "final_state": outcome.get("final_state"),
            "graceful": outcome.get("graceful"),
            "end_reason": outcome.get("reason"),
            **self._target_view(target),
        }

    async def _draft(
        self, caller: Any, target: Any, action: str, reason: str, root: str
    ) -> dict[str, Any]:
        if self._append_observation is None:
            raise QueueError(
                "session_control_unavailable",
                "this daemon cannot store the drafted control request.",
                status=503,
            )
        verb = "interrupt" if action == "interrupt" else "end"
        body = (
            f"{self._caller_name(caller)} asks to {verb} "
            f"{self._target_name(target)}"
            + (f": {reason}" if reason.strip() else "")
        )
        request = {
            "action": action,
            "target_session_id": str(target.record.id),
            "target_name": self._target_name(target),
            "reason": str(reason or "")[:500],
            "from_session": str(caller.record.id),
            "from_name": self._caller_name(caller),
            "from_run_id": str(getattr(caller.record, "agent_run_id", "") or ""),
            "project_id": str(target.record.project_id or ""),
            "status": "pending",
        }
        appended = await self._append_observation(
            root, body, kind="control_request", request=request
        )
        request_id = str(appended.get("appended_id") or "")
        await self.events.emit(
            "agent_control_drafted",
            session_id=str(caller.record.id),
            source="agent",
            request_id=request_id,
            action=action,
            target_session_id=str(target.record.id),
            project_id=str(target.record.project_id or ""),
        )
        return {
            "status": "drafted",
            "action": action,
            "grant": "draft",
            "request_id": request_id,
            "note": (
                "This wrote an inert approval request and started nothing. A human "
                "approves it in the Fleet Queue, and the approval is what acts."
            ),
            **self._target_view(target),
        }

    # ---------------------------------------------------------- resolution

    def _resolve_target(
        self, caller: Any, identity: str, project: str, *, allow_self: bool
    ) -> tuple[Any, ProjectScope]:
        text = str(identity or "").strip()
        if not text:
            raise QueueError("unknown_target", "target session is required", status=400)
        scope = resolve_project_scope(project, caller.record, self.projects)
        if text.casefold() == "self":
            target = caller
        else:
            target = self._find_in_scope(text, scope)
            if target is None and not str(project or "").strip():
                qualifier, name = split_qualified_target(text)
                if qualifier:
                    try:
                        qualified = resolve_project_scope(
                            qualifier, caller.record, self.projects
                        )
                    except ValueError:
                        qualified = None
                    if qualified is not None:
                        found = self._find_in_scope(name, qualified)
                        if found is not None:
                            target, scope = found, qualified
        if target is None:
            # Scope miss and true miss answer identically: existence outside the
            # requested scope is never confirmed.
            raise QueueError(
                "unknown_target",
                "no such session in scope. To reach another Project, pass "
                'project:"fleet" or the Project name.',
                status=404,
            )
        if str(target.record.id) == str(caller.record.id) and not allow_self:
            raise QueueError(
                "self_not_allowed",
                "interrupt cannot target your own session; interrupt your own "
                "turn from your own terminal.",
                status=400,
            )
        if not is_agent_harness(target.record.backend):
            raise QueueError(
                "not_agent_target",
                "session control targets agent sessions only, never a shell or "
                "other pane.",
                status=400,
            )
        if self._is_daemon_owner(target):
            # In scope, but forbidden at any grant: ending the session that hosts
            # the daemon takes the daemon down (job-object inheritance). A clear
            # refusal beats a lie the caller cannot act on.
            raise QueueError(
                "forbidden_target",
                "that session hosts the running daemon; ending or interrupting it "
                "would take the daemon down, so it is never a valid target.",
                status=403,
            )
        if target.record.state in {"exited", "crashed"}:
            raise QueueError(
                "target_ended", "the target session has already ended", status=409
            )
        return target, scope

    def _find_in_scope(self, text: str, scope: ProjectScope) -> Any:
        matches = [
            session
            for session in self.sessions.sessions.values()
            if scope.admits(*record_scope(session.record))
            and (
                str(session.record.id) == text
                or str(getattr(session.record, "agent_run_id", "") or "") == text
                or str(session.record.name) == text
            )
        ]
        if len(matches) > 1:
            candidates = sorted(str(session.record.id) for session in matches)
            raise QueueError(
                "ambiguous_target",
                f'"{text}" matches {len(matches)} sessions in scope; repeat with '
                f"one of these session ids: {', '.join(candidates)}",
                status=409,
                candidates=candidates,
            )
        return matches[0] if matches else None

    # ------------------------------------------------------------- bounds

    def _enforce_budget(self, caller: Any) -> None:
        budget = int(self.config.session_control_hourly_budget)
        if budget <= 0:
            raise QueueError(
                "origin_budget_exhausted",
                "this install allows no agent session-control actions.",
                status=429,
            )
        cutoff = self._clock() - _BUDGET_WINDOW_SECONDS
        used = sum(
            1
            for action in self._recent
            if action.kind != "spawn"
            and action.origin_session == str(caller.record.id)
            and action.at >= cutoff
        )
        if used >= budget:
            raise QueueError(
                "origin_budget_exhausted",
                f"this session has taken {used} control actions in the last hour "
                f"(limit {budget}).",
                status=429,
            )

    def _enforce_cycle(self, caller: Any, target: Any) -> None:
        cutoff = self._clock() - _CYCLE_WINDOW_SECONDS
        caller_id = str(caller.record.id)
        target_id = str(target.record.id)
        if caller_id == target_id:
            return
        # A→B while B→A is a loop. If the target recently controlled the caller,
        # the caller controlling the target back closes the ring.
        for action in self._recent:
            if (
                action.at >= cutoff
                and action.origin_session == target_id
                and action.target_session == caller_id
            ):
                raise QueueError(
                    "relay_cycle",
                    "that session recently controlled yours; interrupting or "
                    "ending it back closes a control loop. Ask a human instead.",
                    status=409,
                )

    def _gate_readiness(self, target: Any) -> None:
        evaluation = self._readiness(target)
        state = str(evaluation.get("delivery_state") or "unknown")
        if state != "safe":
            # Fail closed: `blocked` refuses and `unknown` never authorizes.
            # Interrupting a session mid-approval-prompt or in a menu is
            # corruption, not a stop.
            raise QueueError(
                "readiness_not_safe",
                f"the target is not safe to interrupt (readiness: {state}). "
                "Interrupting it now could corrupt an approval prompt or a menu.",
                status=409,
                delivery_state=state,
                readiness_reason=evaluation.get("reason"),
            )

    # --------------------------------------------------------------- utils

    async def _audit(
        self, caller: Any, target: Any, action: str, outcome: str, reason: str
    ) -> None:
        await self.events.emit(
            "agent_session_control",
            session_id=str(caller.record.id),
            source="agent",
            action=action,
            outcome=outcome,
            target_session_id=str(target.record.id),
            target_name=self._target_name(target),
            origin_run_id=str(getattr(caller.record, "agent_run_id", "") or ""),
            project_id=str(target.record.project_id or ""),
            reason=str(reason or "")[:500],
        )

    def _project_root(self, session: Any) -> str:
        project_id = str(getattr(session.record, "project_id", "") or "")
        if project_id and self.projects is not None:
            project = self.projects.projects.get(project_id)
            if project is not None:
                return str(project.root)
        return ""

    @staticmethod
    def _target_view(target: Any) -> dict[str, Any]:
        return {
            "target_session_id": str(target.record.id),
            "target_name": str(target.record.name),
            "target_project_id": str(target.record.project_id or ""),
        }

    @staticmethod
    def _caller_name(caller: Any) -> str:
        return str(getattr(caller.record, "name", "") or caller.record.id)

    @staticmethod
    def _target_name(target: Any) -> str:
        return str(getattr(target.record, "name", "") or target.record.id)

    def _schedule(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _resolve_grant(self, root: str) -> str:
        """The effective grant for a Project root: off / draft / granted.

        `off` means the `session_control` automation is switched off for the
        Project (it is on by default since 2026-08-25, withdrawn with an
        explicit `session_control = false`); the draft/granted split lives in
        the Project config field, which now also defaults to `granted` and is
        lowered by writing `draft`.
        """
        if not root:
            return "off"
        enabled = await self._automation_gate(root)
        if "session_control" not in enabled:
            return "off"
        return self._grant_field(root)
