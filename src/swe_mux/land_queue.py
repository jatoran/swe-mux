"""Phase 14: the land queue - serialized, deterministic branch landing.

Landing a finished worktree branch is three fixed commands, and the operator serializes
them by hand whenever several agents finish at once. This service owns the mechanical
part so N branches land in sequence and the operator touches only the one that
genuinely conflicts.

The move is the one the rest of the control plane already made: deterministic code
executes a **fixed vocabulary** through existing trust boundaries, and a model is never
asked to choose a git operation. The vocabulary is exactly four commands - `merge` into
the branch inside its worktree, the repository's own verification command, `merge
--ff-only` in the primary checkout, and `merge --abort` to undo a conflicted reconcile.
Nothing here rebases, forces, resets, or cleans.

Fast-forward-only is what makes the trunk step safe for a machine. Git refuses it on
divergence and refuses to overwrite overlapping local changes, so the pipeline cannot
lose work by construction - the same property that already makes it the one merge shape
permitted outside a worktree.

What the pipeline never does is decide. A conflict and a verification failure both need
intelligence, and both belong to the branch's own agent, which holds the context; they
leave here as a bounded deterministic message through the Phase 5 queue.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .background_tasks import background
from .clipboard_store import looks_like_secret
from .git_operations import run_git_mutation
from .land_preconditions import (
    DEFAULT_HOLD_TIMEOUT_SECONDS,
    evaluate_preconditions,
    read_repository_facts,
)
from .land_store import LandConflict, LandStore
from .worktree_verify import (
    MAX_HANDBACK_OUTPUT_BYTES,
    VerifyApprovalStore,
    describe_verify_command,
    run_worktree_verify,
)

log = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 5.0
LAND_LOOP = "land_queue"

#: Per-origin-session requests per hour. A land is expensive in wall-clock rather than
#: in tokens, so the cap is about a runaway loop, not about spend.
DEFAULT_HOURLY_BUDGET = 12
_BUDGET_WINDOW_SECONDS = 3600.0

GRANTS = ("off", "draft", "granted")


class LandRefusal(Exception):
    """A request the service will not accept, with a reason a caller can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def handback_excerpt(output: bytes, *, limit: int = MAX_HANDBACK_OUTPUT_BYTES) -> str:
    """The tail of a gate's output, bounded and redacted, for an agent's prompt.

    The tail rather than the head: a failing suite says what failed at the end. Bounded
    because this becomes a prompt, and redacted through the same gate every other
    excerpt uses - a verification run that echoes a token is exactly what it is for.
    """
    text = output.decode("utf-8", "replace")
    if len(text) > limit:
        text = "... earlier output omitted ...\n" + text[-limit:]
    kept = [
        line if not looks_like_secret(line) else "[redacted]"
        for line in text.splitlines()
    ]
    return "\n".join(kept).strip()


async def unmerged_paths(cwd: str) -> tuple[str, ...]:
    """The paths Git currently holds unmerged, asked of the index rather than parsed.

    Not read from the merge command's own output, for two reasons that each decide it
    on their own. Git prints `CONFLICT (content): Merge conflict in <path>` on
    **stdout** while the mutation runner keeps stderr on failure, so the lines are not
    even present; and that text is prose, which a Git release or a locale may reword
    while `--diff-filter=U` means the same thing forever.
    """
    from .git_monitor import read_git

    code, output = await read_git(cwd, "diff", "--name-only", "--diff-filter=U")
    if code != 0:
        return ()
    return tuple(line.strip() for line in output.splitlines() if line.strip())


class LandQueueService:
    """Authority, ordering, and the fixed git vocabulary around a land.

    Every collaborator is injected. The service knows nothing about HTTP, MCP, or the
    session registry beyond the four questions it asks of them, which is what lets the
    pipeline be tested against a real repository with no daemon around it.
    """

    def __init__(
        self,
        *,
        store: LandStore,
        approvals: VerifyApprovalStore,
        config: Any,
        events: Any = None,
        automation_gate: Callable[[str], Awaitable[frozenset[str]]] | None = None,
        grant_field: Callable[[str], str] | None = None,
        project_values: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        comparison_ref: Callable[[str], Awaitable[str | None]] | None = None,
        busy_sessions: Callable[[str], Awaitable[tuple[str, ...]]] | None = None,
        queue_message: Callable[..., Awaitable[Any]] | None = None,
        record_fact: Callable[..., Awaitable[Any]] | None = None,
        draft_request: Callable[..., Awaitable[Any]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._approvals = approvals
        self._config = config
        self._events = events
        self._automation_gate = automation_gate
        self._grant_field = grant_field
        self._project_values = project_values
        self._comparison_ref = comparison_ref
        self._busy_sessions = busy_sessions
        self._queue_message = queue_message
        self._record_fact = record_fact
        self._draft_request = draft_request
        self._clock = clock
        self._roots_in_flight: set[str] = set()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        background.start(LAND_LOOP, self._run)

    async def stop(self) -> None:
        await background.stop(LAND_LOOP)

    async def restore(self) -> None:
        """Return steps orphaned by a daemon restart to the queue."""
        recovered = await self._store.restore(now=self._clock())
        for row in recovered:
            log.warning(
                "land_step_orphaned request_id=%s branch=%s state=%s",
                row["id"],
                row["branch"],
                row["state"],
            )
            await self._store.record_event(
                request_id=row["id"],
                project_id=row["project_id"],
                step=row["state"],
                outcome="orphaned",
                reason="daemon restarted mid-step",
            )

    async def _run(self) -> None:
        while True:
            # The wait is outside the guard on purpose: time spent sleeping is not
            # this loop's cost, and counting it as such is how a cheap loop ends up
            # at the top of the `costliest` list (`background_tasks.py`).
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            with background.iteration(LAND_LOOP):
                await self.tick()

    # -- authority ------------------------------------------------------------

    def _installed_enabled(self) -> bool:
        """The install-wide emergency stop. Off means no branch lands anywhere."""
        return bool(getattr(self._config, "land_queue_enabled", True))

    async def _enabled(self, project_root: str) -> bool:
        if not self._installed_enabled():
            return False
        if self._automation_gate is None:
            return True
        try:
            return "land_queue" in await self._automation_gate(project_root)
        except Exception:  # noqa: BLE001 - a gate that cannot answer is off
            log.warning("land_gate_unreadable root=%s", project_root)
            return False

    def _grant(self, project_root: str) -> str:
        if self._grant_field is None:
            return "draft"
        try:
            value = self._grant_field(project_root)
        except Exception:  # noqa: BLE001 - an unreadable grant is the inert one
            return "draft"
        return value if value in GRANTS else "draft"

    def _hourly_budget(self) -> int:
        return max(0, int(getattr(self._config, "land_hourly_budget", DEFAULT_HOURLY_BUDGET)))

    def _hold_timeout(self) -> float:
        return float(
            getattr(self._config, "land_hold_timeout_seconds", DEFAULT_HOLD_TIMEOUT_SECONDS)
        )

    def _verify_retries(self) -> int:
        """At most one, and only when configured. Never silent, because a flaky gate
        that loops is worse than one that stops."""
        return 1 if bool(getattr(self._config, "land_retry_verification", False)) else 0

    # -- requesting -----------------------------------------------------------

    async def request(
        self,
        *,
        project_id: str,
        project_root: str,
        worktree_root: str,
        origin: str = "operator",
        origin_session_id: str = "",
        origin_run_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Enqueue a land, or draft it for a human, or refuse it.

        An operator request bypasses the grant because the operator *is* the authority
        the grant defers to; it still passes every precondition the pipeline checks.
        """
        if origin == "agent":
            if not await self._enabled(project_root):
                raise LandRefusal(
                    "automation_disabled",
                    "the land queue is not enabled for this Project",
                )
            grant = self._grant(project_root)
            if grant == "off":
                raise LandRefusal("land_denied", "agent-initiated landing is off for this Project")
            budget = self._hourly_budget()
            if budget and origin_session_id:
                used = await self._store.origin_count_since(
                    origin_session_id, self._clock() - _BUDGET_WINDOW_SECONDS
                )
                if used >= budget:
                    raise LandRefusal(
                        "budget_exhausted",
                        f"this session has made {used} land requests in the last hour",
                    )
            if grant == "draft":
                return await self._draft(
                    project_id=project_id,
                    project_root=project_root,
                    worktree_root=worktree_root,
                    origin_session_id=origin_session_id,
                    origin_run_id=origin_run_id,
                    reason=reason,
                )

        facts = await read_repository_facts(worktree_root, project_root)
        if not facts.readable or not facts.worktree_branch:
            raise LandRefusal(
                "unreadable_repository",
                facts.error or "the worktree's branch could not be read",
            )
        if facts.worktree_branch == "HEAD":
            raise LandRefusal(
                "detached_head",
                "the worktree is on a detached HEAD; create a named branch before landing",
            )
        if facts.already_landed:
            # Answered here rather than one sweep later, so pressing Land on a branch
            # that is already on the trunk says so at once instead of producing a row
            # that resolves a tick afterwards. The pipeline keeps its own check for
            # the case this cannot see: the trunk gaining these commits between the
            # request and its turn in the queue.
            raise LandRefusal(
                "already_landed",
                f"{facts.worktree_branch} is already on the trunk; there is nothing to land",
            )
        trunk_ref = ""
        if self._comparison_ref is not None:
            trunk_ref = (await self._comparison_ref(project_root)) or ""
        try:
            row = await self._store.enqueue(
                project_id=project_id,
                project_root=facts.trunk_root or project_root,
                worktree_root=facts.worktree_root or worktree_root,
                branch=facts.worktree_branch,
                requested_oid=facts.worktree_head,
                trunk_ref=trunk_ref or facts.trunk_branch,
                origin=origin,
                origin_session_id=origin_session_id,
                origin_run_id=origin_run_id,
                correlation_id=f"land:{uuid.uuid4().hex[:12]}",
                now=self._clock(),
            )
        except LandConflict as exc:
            raise LandRefusal("already_queued", str(exc)) from exc
        await self._store.record_event(
            request_id=row["id"],
            project_id=project_id,
            step="request",
            outcome="queued",
            detail={"origin": origin, "branch": row["branch"], "oid": row["requested_oid"]},
            now=self._clock(),
        )
        await self._emit("land_changed", row)
        return row

    async def _draft(
        self,
        *,
        project_id: str,
        project_root: str,
        worktree_root: str,
        origin_session_id: str,
        origin_run_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Write an inert approval request instead of acting.

        The default. A drafted land starts nothing: a human is what turns it into a
        queued request, exactly as they do for `interrupt` and `end_session`.
        """
        if self._draft_request is None:
            raise LandRefusal("draft_unavailable", "drafted land requests are not available here")
        branch = ""
        facts = await read_repository_facts(worktree_root, project_root)
        if facts.readable:
            branch = facts.worktree_branch
        result = await self._draft_request(
            project_root=project_root,
            project_id=project_id,
            worktree_root=facts.worktree_root or worktree_root,
            branch=branch,
            origin_session_id=origin_session_id,
            origin_run_id=origin_run_id,
            reason=reason,
        )
        return {
            "state": "drafted",
            "grant": "draft",
            "branch": branch,
            "note": (
                "This wrote an inert approval request and started nothing. A human "
                "approves it in the Fleet Queue, and the approval is what enqueues "
                "the land."
            ),
            **dict(result or {}),
        }

    async def cancel(self, request_id: str) -> dict[str, Any]:
        row = await self._store.transition(
            request_id,
            expect=("queued", "waiting"),
            state="cancelled",
            reason="cancelled by the operator",
            clear_waiting=True,
            now=self._clock(),
        )
        await self._store.record_event(
            request_id=request_id,
            project_id=row["project_id"],
            step="request",
            outcome="cancelled",
            now=self._clock(),
        )
        await self._emit("land_changed", row)
        return row

    # -- the pipeline ---------------------------------------------------------

    async def tick(self) -> list[dict[str, Any]]:
        """Advance one item per trunk, serially, for every trunk with work."""
        if not self._installed_enabled():
            return []
        roots = await self._store.active_roots()
        results: list[dict[str, Any]] = []
        for root in roots:
            if root in self._roots_in_flight:
                continue
            outcome = await self._advance_root(root)
            if outcome is not None:
                results.append(outcome)
        return results

    async def _advance_root(self, project_root: str) -> dict[str, Any] | None:
        if await self._store.inflight(project_root) is not None:
            return None
        row = await self._store.next_ready(project_root)
        if row is None:
            return None
        self._roots_in_flight.add(project_root)
        try:
            return await self._process(row)
        finally:
            self._roots_in_flight.discard(project_root)

    async def _process(self, row: dict[str, Any]) -> dict[str, Any]:
        request_id = row["id"]
        gate = await self._check(row)
        if gate is not None:
            return gate
        reconciled = await self._reconcile(row)
        if reconciled is None:
            return await self._reload(request_id)
        verified = await self._verify(reconciled)
        if verified is None:
            return await self._reload(request_id)
        return await self._land(verified)

    async def _reload(self, request_id: str) -> dict[str, Any]:
        row = await self._store.get(request_id)
        return row or {"id": request_id, "state": "unknown"}

    async def _check(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Evaluate preconditions, and hold or refuse when they are not met."""
        facts = await read_repository_facts(row["worktree_root"], row["project_root"])
        busy: tuple[str, ...] = ()
        if self._busy_sessions is not None:
            try:
                busy = await self._busy_sessions(row["worktree_root"])
            except Exception:  # noqa: BLE001 - an unknown answer holds rather than proceeds
                log.warning("land_busy_probe_failed request_id=%s", row["id"])
                busy = ("unknown",)
        result = evaluate_preconditions(facts, branch=row["branch"], busy_sessions=busy)
        if result.ready:
            return None
        if result.disposition == "already_landed":
            return await self._settle_already_landed(row, result.reason, result.detail)
        if result.disposition == "refuse":
            return await self._refuse(row, result.reason, detail=result.detail)
        waited = self._clock() - float(row.get("waiting_since") or self._clock())
        if row["state"] == "waiting" and waited >= self._hold_timeout():
            return await self._hand_back(
                row,
                step="precondition",
                summary=(
                    f"the land could not start for {int(waited // 60)} minutes: {result.reason}"
                ),
            )
        if row["state"] != "waiting" or row["reason"] != result.reason:
            updated = await self._store.transition(
                row["id"],
                expect=("queued", "waiting"),
                state="waiting",
                reason=result.reason,
                detail=dict(result.detail or {}),
                waiting_since=row.get("waiting_since") or self._clock(),
                now=self._clock(),
            )
            await self._emit("land_changed", updated)
            return updated
        return row

    async def _reconcile(self, row: dict[str, Any]) -> dict[str, Any] | None:
        request_id = row["id"]
        trunk_ref = row["trunk_ref"] or "HEAD"
        try:
            current = await self._store.transition(
                request_id,
                expect=("queued", "waiting"),
                state="reconciling",
                reason="",
                clear_waiting=True,
                now=self._clock(),
            )
        except LandConflict:
            return None
        await self._emit("land_changed", current)
        operation_id = f"land-reconcile-{request_id}"
        result = await run_git_mutation(
            row["worktree_root"],
            "merge",
            "--no-edit",
            trunk_ref,
            operation="land_reconcile",
            operation_id=operation_id,
        )
        if result.code != 0:
            # Read the conflict from the index *before* aborting: the abort is what
            # clears it, so the order here is load-bearing rather than stylistic.
            paths = await unmerged_paths(row["worktree_root"])
            # Leave the worktree exactly as the pipeline found it. An abandoned merge
            # state would make the agent's next turn start by cleaning up after us.
            await run_git_mutation(
                row["worktree_root"],
                "merge",
                "--abort",
                operation="land_reconcile_abort",
                operation_id=operation_id,
                timeout_seconds=60.0,
            )
            await self._store.record_event(
                request_id=request_id,
                project_id=row["project_id"],
                step="reconcile",
                outcome="conflict" if paths else "failed",
                reason=result.output[:2000],
                detail={"paths": list(paths)},
                now=self._clock(),
            )
            summary = (
                f"merging {trunk_ref} into {row['branch']} conflicted in "
                f"{len(paths)} file(s)"
                if paths
                else f"merging {trunk_ref} into {row['branch']} failed"
            )
            await self._hand_back(
                current,
                step="reconcile",
                summary=summary,
                paths=paths,
                output=result.output.encode("utf-8", "replace"),
            )
            return None
        already = "Already up to date" in result.output
        head = await self._head(row["worktree_root"])
        await self._store.record_event(
            request_id=request_id,
            project_id=row["project_id"],
            step="reconcile",
            outcome="already_current" if already else "merged",
            detail={"oid": head, "trunk_ref": trunk_ref},
            now=self._clock(),
        )
        return {**current, "reconciled_oid": head, "_already_current": already}

    async def _verify(self, row: dict[str, Any]) -> dict[str, Any] | None:
        request_id = row["id"]
        head = row.get("reconciled_oid") or await self._head(row["worktree_root"])
        # The one case where re-running the gate proves nothing: the branch already
        # contained the trunk, so the OID that passed before is the OID standing now.
        if row.get("_already_current") and row.get("verified_oid") == head:
            return {**row, "verified_oid": head}
        try:
            current = await self._store.transition(
                request_id,
                expect=("reconciling",),
                state="verifying",
                reason="",
                reconciled_oid=head,
                now=self._clock(),
            )
        except LandConflict:
            return None
        await self._emit("land_changed", current)
        values = await self._values(row["project_root"])
        attempts = 1 + self._verify_retries()
        outcomes = []
        for attempt in range(attempts):
            outcome = await run_worktree_verify(
                Path(row["worktree_root"]),
                values,
                self._approvals,
                project_root=row["project_root"],
                request_id=request_id,
            )
            outcomes.append(outcome)
            await self._store.record_event(
                request_id=request_id,
                project_id=row["project_id"],
                step="verify",
                outcome=outcome.status,
                reason=outcome.failure_summary() if not outcome.passed else "",
                detail={**outcome.public_dict(), "attempt": attempt + 1, "oid": head},
                now=self._clock(),
            )
            if outcome.passed:
                break
            if outcome.status in {"not_configured", "unapproved"}:
                # Neither is a branch problem and neither improves on a retry.
                break
        final = outcomes[-1]
        if len(outcomes) == 2 and outcomes[0].exit_code != outcomes[1].exit_code:
            # Two unlike failures are evidence about the gate, not about the branch.
            final = outcomes[0]
            await self._store.record_event(
                request_id=request_id,
                project_id=row["project_id"],
                step="verify",
                outcome="unstable",
                reason="the retry failed differently from the first attempt",
                detail={
                    "first_exit_code": outcomes[0].exit_code,
                    "second_exit_code": outcomes[1].exit_code,
                },
                now=self._clock(),
            )
        if not final.passed:
            if final.status in {"not_configured", "unapproved"}:
                await self._refuse(current, final.failure_summary())
                return None
            await self._hand_back(
                current,
                step="verify",
                summary=final.failure_summary(),
                output=final.output,
            )
            return None
        updated = await self._store.transition(
            request_id,
            expect=("verifying",),
            state="landing",
            reason="",
            verified_oid=head,
            verify_digest=final.digest or "",
            bump_verify_attempts=True,
            now=self._clock(),
        )
        await self._emit("land_changed", updated)
        return updated

    async def _land(self, row: dict[str, Any]) -> dict[str, Any]:
        request_id = row["id"]
        verified = row.get("verified_oid") or ""
        facts = await read_repository_facts(row["worktree_root"], row["project_root"])
        if facts.worktree_head != verified:
            return await self._refuse(
                row,
                "the branch moved after it verified; request the land again",
                detail={"verified": verified, "current": facts.worktree_head},
            )
        trunk_before = facts.trunk_head
        result = await run_git_mutation(
            row["project_root"],
            "merge",
            "--ff-only",
            row["branch"],
            operation="land_ff",
            operation_id=f"land-ff-{request_id}",
        )
        if result.code != 0:
            await self._store.record_event(
                request_id=request_id,
                project_id=row["project_id"],
                step="land",
                outcome="refused",
                reason=result.output[:2000],
                now=self._clock(),
            )
            # Never a retried force. Git refused for a reason the operator has to see.
            return await self._refuse(
                row,
                f"the fast-forward was refused: {result.output.splitlines()[0][:200]}"
                if result.output
                else "the fast-forward was refused",
                detail={"trunk_before": trunk_before},
            )
        trunk_after = await self._head(row["project_root"])
        landed = await self._store.transition(
            request_id,
            expect=("landing",),
            state="landed",
            reason="",
            trunk_before=trunk_before,
            landed_oid=trunk_after,
            now=self._clock(),
        )
        await self._store.record_event(
            request_id=request_id,
            project_id=row["project_id"],
            step="land",
            outcome="landed",
            detail={"trunk_before": trunk_before, "trunk_after": trunk_after},
            now=self._clock(),
        )
        await self._fact(landed, "land_landed", {"trunk_after": trunk_after})
        log.info(
            "land_completed request_id=%s branch=%s trunk=%s..%s",
            request_id,
            landed["branch"],
            trunk_before[:12],
            trunk_after[:12],
        )
        await self._emit("land_changed", landed)
        return landed

    # -- outcomes -------------------------------------------------------------

    async def _settle_already_landed(
        self,
        row: dict[str, Any],
        reason: str,
        detail: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Close a request whose branch the trunk already contains.

        Its own terminal state rather than `landed` or `refused`, because it is
        neither. Nothing was refused - the request was reasonable and the answer is
        that it is already true - and reporting `landed` would claim a trunk movement
        that did not happen, in a ledger whose whole purpose is recording which OID
        moved what.
        """
        updated = await self._store.transition(
            row["id"],
            expect=("queued", "waiting"),
            state="already_landed",
            reason=reason,
            detail=dict(detail or {}),
            clear_waiting=True,
            now=self._clock(),
        )
        await self._store.record_event(
            request_id=row["id"],
            project_id=row["project_id"],
            step="request",
            outcome="already_landed",
            reason=reason,
            detail=dict(detail or {}),
            now=self._clock(),
        )
        await self._emit("land_changed", updated)
        return updated

    async def _refuse(
        self,
        row: dict[str, Any],
        reason: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated = await self._store.transition(
            row["id"],
            expect=("queued", "waiting", "reconciling", "verifying", "landing"),
            state="refused",
            reason=reason,
            detail=dict(detail or {}),
            clear_waiting=True,
            now=self._clock(),
        )
        await self._store.record_event(
            request_id=row["id"],
            project_id=row["project_id"],
            step="request",
            outcome="refused",
            reason=reason,
            detail=dict(detail or {}),
            now=self._clock(),
        )
        await self._fact(updated, "land_refused", {"reason": reason})
        await self._emit("land_changed", updated)
        return updated

    async def _hand_back(
        self,
        row: dict[str, Any],
        *,
        step: str,
        summary: str,
        paths: tuple[str, ...] = (),
        output: bytes | None = None,
    ) -> dict[str, Any]:
        """Return the request to the agent that made it, as a bounded template.

        Deliberately a message rather than an action: the pipeline has no way to
        resolve a conflict and no business trying. It rides the Phase 5 queue as a
        draft by default, so nothing reaches an agent mid-turn without the ordinary
        readiness and auto-delivery contract applying.
        """
        body = self._handback_body(row, step=step, summary=summary, paths=paths, output=output)
        message_id = ""
        if self._queue_message is not None and row.get("origin_session_id"):
            try:
                message = await self._queue_message(
                    target_session_id=row["origin_session_id"],
                    body=body,
                    sender_kind="rule",
                    sender_id="land_queue",
                    sender_label="Land queue",
                    correlation_id=row["correlation_id"] or row["id"],
                )
                message_id = str((message or {}).get("id") or "")
            except Exception:  # noqa: BLE001 - a failed handback must not lose the row
                log.warning("land_handback_failed request_id=%s", row["id"])
        updated = await self._store.transition(
            row["id"],
            expect=("queued", "waiting", "reconciling", "verifying", "landing"),
            state="handed_back",
            reason=summary,
            detail={"step": step, "paths": list(paths)},
            clear_waiting=True,
            handback_message_id=message_id,
            now=self._clock(),
        )
        await self._store.record_event(
            request_id=row["id"],
            project_id=row["project_id"],
            step=step,
            outcome="handed_back",
            reason=summary,
            detail={"message_id": message_id, "paths": list(paths)},
            now=self._clock(),
        )
        await self._fact(updated, "land_handed_back", {"step": step, "reason": summary})
        await self._emit("land_changed", updated)
        return updated

    def _handback_body(
        self,
        row: dict[str, Any],
        *,
        step: str,
        summary: str,
        paths: tuple[str, ...],
        output: bytes | None,
    ) -> str:
        """A fixed template. No model writes any part of this message."""
        lines = [
            f"The land of `{row['branch']}` stopped at {step}: {summary}.",
            "",
            f"Worktree: `{row['worktree_root']}`",
            f"Target: `{row['project_root']}` ({row['trunk_ref'] or 'HEAD'})",
        ]
        if paths:
            lines.extend(["", "Conflicting files:"])
            lines.extend(f"- `{path}`" for path in paths[:40])
            if len(paths) > 40:
                lines.append(f"- ... and {len(paths) - 40} more")
        if output:
            excerpt = handback_excerpt(output)
            if excerpt:
                lines.extend(["", "Output tail:", "```", excerpt, "```"])
        lines.extend(
            [
                "",
                "Resolve this in your worktree, then request the land again. "
                "The worktree was left as it was found; nothing was committed for you.",
            ]
        )
        return "\n".join(lines)

    # -- collaborators --------------------------------------------------------

    async def _head(self, cwd: str) -> str:
        from .git_monitor import read_git

        code, output = await read_git(cwd, "rev-parse", "HEAD")
        return output.strip() if code == 0 else ""

    async def _values(self, project_root: str) -> dict[str, Any]:
        if self._project_values is None:
            return {}
        try:
            return await self._project_values(project_root)
        except Exception:  # noqa: BLE001 - an unreadable config configures nothing
            log.warning("land_project_values_failed root=%s", project_root)
            return {}

    async def _fact(self, row: dict[str, Any], kind: str, detail: dict[str, Any]) -> None:
        """Mirror a step into Tier 0 when the request has a session to attribute it to.

        The `land_events` table is the authoritative audit and is written either way.
        This is the *joinable* copy, so a land shows up beside the run's other facts -
        which an operator-initiated land, having no session, simply does not have.
        """
        if self._record_fact is None or not row.get("origin_session_id"):
            return
        try:
            await self._record_fact(
                session_id=row["origin_session_id"],
                kind=kind,
                target=row["branch"],
                agent_run_id=row.get("origin_run_id") or None,
                project_id=row.get("project_id") or None,
                detail={"request_id": row["id"], "worktree": row["worktree_root"], **detail},
            )
        except Exception:  # noqa: BLE001 - audit must never break the pipeline
            log.warning("land_fact_failed request_id=%s kind=%s", row["id"], kind)

    async def _emit(self, event_type: str, row: dict[str, Any]) -> None:
        if self._events is None:
            return
        try:
            await self._events.publish(
                event_type,
                {
                    "request_id": row.get("id"),
                    "project_id": row.get("project_id"),
                    "state": row.get("state"),
                    "branch": row.get("branch"),
                },
            )
        except Exception:  # noqa: BLE001 - a dropped notification is not a failed land
            log.debug("land_event_publish_failed type=%s", event_type)

    # -- reads ----------------------------------------------------------------

    async def status(
        self, *, project_id: str | None = None, project_root: str | None = None
    ) -> dict[str, Any]:
        requests = await self._store.list_requests(project_id=project_id, limit=100)
        # The two authority facts the Land panel cannot otherwise tell apart from a
        # quiet queue. An operator request bypasses the Project opt-in on purpose - the
        # operator is the authority the grant defers to - but the *sweep* stops dead on
        # the install switch, so a queue with that off accepts requests and then never
        # advances one. Reporting it is what lets the panel say so instead of spinning.
        installed = self._installed_enabled()
        project_enabled = False
        grant = "draft"
        if project_root:
            if self._automation_gate is None:
                project_enabled = True
            else:
                try:
                    project_enabled = "land_queue" in await self._automation_gate(project_root)
                except Exception:  # noqa: BLE001 - a gate that cannot answer reads as off
                    project_enabled = False
            grant = self._grant(project_root)
        return {
            "requests": requests,
            "hourly_budget": self._hourly_budget(),
            "hold_timeout_seconds": self._hold_timeout(),
            "retry_verification": bool(self._verify_retries()),
            "installed_enabled": installed,
            "project_enabled": project_enabled,
            "agent_grant": grant,
        }

    def verify_command(
        self, worktree_root: str, values: dict[str, Any], *, project_root: str
    ) -> dict[str, Any]:
        info = describe_verify_command(
            Path(worktree_root), values, self._approvals, project_root=project_root
        )
        return info.public_dict()
