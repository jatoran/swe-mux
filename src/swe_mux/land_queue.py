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

A request comes in one of two **kinds**, and the kind decides exactly one thing: whether
the last step happens. A `land` runs the whole pipeline. A `verify` stops after the gate,
moves no trunk, and reports the verdict back to the session that asked. Every step before
that is identical, which is what makes a verify-only verdict worth keeping: it is the
verdict a land would have produced. Kept, it is - keyed by the git **tree** the gate ran
over and the **digest** of the command that ran, so a later land over the same content
under the same approved bytes skips the gate and records that it did.
Only a run this queue executed is ever recorded that way (`land-queue.md`).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from .background_tasks import background
from .clipboard_store import looks_like_secret
from .git_operations import run_git_mutation
from .land_classify import GateChoice, classify_change_set, read_change_set
from .land_preconditions import (
    DEFAULT_HOLD_TIMEOUT_SECONDS,
    evaluate_preconditions,
    read_repository_facts,
)
from .land_store import LandConflict, LandStore
from .verify_progress import VerifyProgress, sanitize_plan
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

#: Armed replies one land request may spend. One request has exactly one outcome, so
#: one bounded answer is the whole of what a `request_land` consented to; a second
#: message to the same session would be an unsolicited write wearing this authority.
#: Stated as a number and claimed atomically (`LandStore.claim_armed_reply`) rather
#: than left to the state machine, which happens to allow only one handback today.
#: A verify-only request spends the same one, on whichever of its two outcomes happens.
ARMED_REPLIES_PER_REQUEST = 1

#: How long a queue-executed green verdict stands, when the config says nothing.
DEFAULT_VERIFY_MEMO_SECONDS = 24 * 3600.0


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


def verify_test_outcome(outcome: Any) -> dict[str, Any]:
    """The gate's verdict in the Tier 0 test-outcome shape.

    Two rules keep this honest. The counts come from parsing the gate's own
    output where it printed a runner summary, so a real failing-test list is
    carried rather than invented. And a **failed gate never reports an empty
    failing set**: `failing_tests: []` is read by every consumer as "nothing is
    failing", so a gate that fell over on ruff or tsc — steps that name no tests —
    omits the key entirely and states a failure count instead. The distinction
    between "no failures" and "failures not enumerated" is the whole value of the
    field.
    """
    from .observation import parse_test_outcome

    text = bytes(getattr(outcome, "output", b"") or b"").decode("utf-8", "replace")
    parsed = parse_test_outcome(text) or {}
    result: dict[str, Any] = {
        "framework": "worktree-verify",
        "parsed_framework": parsed.get("framework"),
        "passed": int(parsed.get("passed") or 0),
        "skipped": int(parsed.get("skipped") or 0),
        "status": getattr(outcome, "status", ""),
    }
    if outcome.passed:
        return {**result, "failed": 0, "errors": 0, "failing_tests": []}
    failing = [str(name) for name in (parsed.get("failing_tests") or [])]
    result["failed"] = max(int(parsed.get("failed") or 0), 1)
    result["errors"] = int(parsed.get("errors") or 0)
    if failing:
        result["failing_tests"] = failing
    return result


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
        session_run: Callable[[str], str] | None = None,
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
        self._session_run = session_run
        self._queue_message = queue_message
        self._record_fact = record_fact
        self._draft_request = draft_request
        self._clock = clock
        self._roots_in_flight: set[str] = set()
        #: The one gate currently running, by request id. In memory rather than in the
        #: store because it is a reading of a live process: a daemon that restarts
        #: returns the step to `queued` and re-runs it from scratch, so a persisted
        #: half-progress would be a claim about a run that no longer exists.
        self._verify_live: dict[str, VerifyProgress] = {}

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

    def _memo_seconds(self) -> float:
        """How long a queue-executed green verdict stands. Zero disables reuse."""
        return max(
            0.0,
            float(getattr(self._config, "land_verify_memo_seconds", DEFAULT_VERIFY_MEMO_SECONDS)),
        )

    # -- requesting -----------------------------------------------------------

    async def request(
        self,
        *,
        project_id: str,
        project_root: str,
        worktree_root: str,
        kind: str = "land",
        origin: str = "operator",
        origin_session_id: str = "",
        origin_run_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Enqueue a land or a verify-only run, or draft it for a human, or refuse it.

        An operator request bypasses the grant because the operator *is* the authority
        the grant defers to; it still passes every precondition the pipeline checks.

        **The grant means something narrower for a verify-only request, and the reason
        is what the grant is about.** `off` refuses both, because it is the operator
        saying agents do not drive this machinery here. `draft` drafts a *land* - a
        human decides before a trunk moves - and enqueues a *verify*, because a
        verify-only run moves nothing: it merges the trunk into the requester's own
        branch, in the requester's own worktree, and runs bytes a human already
        approved. There is nothing for a human to decide in advance about that, and
        drafting it would put the cheap half of the pipeline behind the approval the
        expensive half exists to protect.
        """
        if kind not in ("land", "verify"):
            raise LandRefusal("unknown_kind", f"unknown request kind: {kind}")
        lands = kind == "land"
        if origin == "agent":
            if not await self._enabled(project_root):
                raise LandRefusal(
                    "automation_disabled",
                    "the land queue is not enabled for this Project",
                )
            grant = self._grant(project_root)
            if grant == "off":
                raise LandRefusal(
                    "land_denied",
                    "agent-initiated landing is off for this Project"
                    if lands
                    else "agent-initiated use of the land queue is off for this Project",
                )
            budget = self._hourly_budget()
            if budget and origin_session_id:
                used = await self._store.origin_count_since(
                    origin_session_id, self._clock() - _BUDGET_WINDOW_SECONDS
                )
                if used >= budget:
                    # Verify-only requests count against the same budget, because the
                    # budget bounds wall-clock rather than trunk movements and a gate
                    # costs the same minutes whichever step follows it.
                    raise LandRefusal(
                        "budget_exhausted",
                        f"this session has made {used} land-queue requests in the last hour",
                    )
            if grant == "draft" and lands:
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
        if facts.already_landed and not lands:
            # A verify-only run over a branch the trunk already contains would verify
            # the trunk. Refused for the same reason a land of it is: there is nothing
            # here the gate has not already been asked about.
            raise LandRefusal(
                "already_landed",
                f"{facts.worktree_branch} is already on the trunk; there is nothing to verify",
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
                kind=kind,
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
            detail={
                "origin": origin,
                "kind": kind,
                "branch": row["branch"],
                "oid": row["requested_oid"],
            },
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

    @staticmethod
    def _lands(row: dict[str, Any]) -> bool:
        """Whether this request ends by moving a trunk.

        Read from the row rather than carried in a parallel field, and defaulting to
        `True`, so a row written before the column existed is what it always was.
        """
        return str(row.get("kind") or "land") == "land"

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
        # The only step a verify-only request does not take. Everything above it is
        # identical, which is the point: the verdict a verify-only run produces is the
        # verdict a land would have produced, or it would not be worth reusing.
        if not self._lands(verified):
            return verified
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
        result = evaluate_preconditions(
            facts,
            branch=row["branch"],
            busy_sessions=busy,
            lands=self._lands(row),
        )
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
                    f"the {'land' if self._lands(row) else 'verification'} could not start"
                    f" for {int(waited // 60)} minutes: {result.reason}"
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

    async def _classify(self, row: dict[str, Any], head: str) -> GateChoice:
        """Decide which gate this land runs, and record the decision either way.

        Between reconcile and verify, because the change set that matters is the one the
        trunk will actually gain: after the reconcile the branch contains the trunk, so
        the trunk's HEAD to the branch's tip is exactly what a fast-forward applies -
        the branch's own commits plus anything it merged in from elsewhere.

        The event is written on **both** outcomes and before either gate runs. A skipped
        gate that left no trace would be indistinguishable in the trail from one that
        passed, which is the failure this whole path is most able to cause and least able
        to notice (`no silent caps`).
        """
        trunk_head = await self._head(row["project_root"])
        entries = await read_change_set(row["project_root"], trunk_head, head)
        choice = classify_change_set(entries)
        await self._store.record_event(
            request_id=row["id"],
            project_id=row["project_id"],
            step="classify",
            outcome=choice.gate,
            reason=choice.reason,
            detail={**choice.public_dict(), "base": trunk_head, "tip": head},
            now=self._clock(),
        )
        log.info(
            "land_gate_classified request_id=%s branch=%s gate=%s paths=%d reason=%s",
            row["id"],
            row["branch"],
            choice.gate,
            choice.path_count,
            choice.reason,
        )
        return choice

    async def _clear_gate(
        self,
        row: dict[str, Any],
        head: str,
        *,
        expect: tuple[str, ...],
        gate: str | None,
        summary: str,
        digest: str = "",
        duration_ms: float = 0.0,
        bump_attempts: bool = False,
    ) -> dict[str, Any] | None:
        """Move a request past the gate - to the fast-forward, or to its own terminal green.

        The one place that decides where a cleared gate leads, so the three ways of
        clearing it (the gate ran, the change set was documentation, a queue-executed
        verdict already stood) cannot drift apart about it.

        `verified_oid` is set on every path and still means what it always meant - the
        OID this request cleared its gate at - so the branch-moved-after-clearing check
        in `_land` keeps working unchanged.
        """
        lands = self._lands(row)
        try:
            updated = await self._store.transition(
                row["id"],
                expect=expect,
                state="landing" if lands else "verified",
                reason="" if lands else summary,
                reconciled_oid=head,
                verified_oid=head,
                verify_digest=digest,
                verify_gate=gate,
                bump_verify_attempts=bump_attempts,
                now=self._clock(),
            )
        except LandConflict:
            return None
        if not lands:
            await self._report_verified(updated, summary=summary, duration_ms=duration_ms)
        await self._emit("land_changed", updated)
        return updated

    async def _skip_verification(
        self, row: dict[str, Any], head: str, choice: GateChoice
    ) -> dict[str, Any] | None:
        """Move a documentation-only change set straight past the gate.

        The row never enters `verifying`, because it never verifies: a state that says
        otherwise for a second and a half is a small lie in the one place the queue is
        read for what actually happened.
        """
        await self._store.record_event(
            request_id=row["id"],
            project_id=row["project_id"],
            step="verify",
            outcome="skipped",
            reason=choice.reason,
            detail={"gate": "docs_only", "paths": list(choice.paths), "oid": head},
            now=self._clock(),
        )
        return await self._clear_gate(
            row,
            head,
            expect=("reconciling",),
            gate="docs_only",
            summary=choice.reason,
        )

    async def _reuse_verification(
        self, row: dict[str, Any], head: str, tree: str, memo: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Accept a verdict this queue already produced over these exact bytes and content.

        The saving the whole feature exists for: an agent asks the queue to verify, the
        queue runs the gate, and the `request_land` that follows finds the same tree
        under the same approved command and does not spend the minutes again.

        **A skipped gate is never silent** (`no silent caps`), so the reuse is written to
        the trail *with its key* before the row moves. The key is what makes the record
        auditable rather than a claim: a reader can ask which run produced the verdict
        and check that the tree it names is the tree that landed.
        """
        await self._store.record_event(
            request_id=row["id"],
            project_id=row["project_id"],
            step="verify",
            outcome="reused",
            reason=(
                "these exact bytes already passed on this exact tree in this queue"
            ),
            detail={
                "gate": "reused",
                "tree": tree,
                "digest": memo["digest"],
                "source_request_id": memo["request_id"],
                "source_kind": memo["request_kind"],
                "source_branch": memo["branch"],
                "observed_at": memo["observed_at"],
                "duration_ms": memo["duration_ms"],
                "oid": head,
            },
            now=self._clock(),
        )
        log.info(
            "land_gate_reused request_id=%s branch=%s tree=%s source=%s",
            row["id"],
            row["branch"],
            tree[:12],
            memo["request_id"],
        )
        return await self._clear_gate(
            row,
            head,
            expect=("reconciling",),
            gate="reused",
            digest=str(memo["digest"]),
            duration_ms=float(memo["duration_ms"]),
            summary="verification was already passed on these exact bytes and this exact tree",
        )

    async def _standing_verdict(self, row: dict[str, Any], head: str) -> dict[str, Any] | None:
        """Clear the gate for a request whose own recorded verdict still stands.

        The branch already contained the trunk, so the reconcile moved nothing and the
        OID that cleared the gate before is the OID standing now. It goes through
        `_clear_gate` rather than short-circuiting to the next step, because the row is
        in `reconciling` at this point and every later step expects it to have moved -
        a request that reached here after a restart used to fall through in `reconciling`
        and be refused by its own next transition.
        """
        await self._store.record_event(
            request_id=row["id"],
            project_id=row["project_id"],
            step="verify",
            outcome="standing",
            reason="the branch already contained the trunk; its recorded verdict still stands",
            detail={"oid": head, "gate": row.get("verify_gate") or ""},
            now=self._clock(),
        )
        return await self._clear_gate(
            row,
            head,
            expect=("reconciling",),
            # Unchanged rather than restated: whichever gate cleared this OID is still
            # the gate that cleared it, and overwriting the column here would relabel it.
            gate=None,
            digest=str(row.get("verify_digest") or ""),
            summary="the branch already contained the trunk and its verdict still stands",
        )

    async def _verify(self, row: dict[str, Any]) -> dict[str, Any] | None:
        request_id = row["id"]
        head = row.get("reconciled_oid") or await self._head(row["worktree_root"])
        # The one case where re-running the gate proves nothing: the branch already
        # contained the trunk, so the OID that passed before is the OID standing now.
        if row.get("_already_current") and row.get("verified_oid") == head:
            return await self._standing_verdict(row, head)
        # The other case, and the only one decided from the change set itself: every
        # path this land would add to the trunk is documentation, so the gate would
        # spend minutes proving that markdown does not fail pytest.
        choice = await self._classify(row, head)
        if choice.skips_verification:
            return await self._skip_verification(row, head, choice)
        values = await self._values(row["project_root"])
        # The bytes about to run, resolved once so the memo, the plan, and the run all
        # speak of the same digest. A gate that was edited has a different digest and
        # therefore neither a standing verdict nor a plan, which is the honest answer:
        # both describe a script that is no longer there.
        info = describe_verify_command(
            Path(row["worktree_root"]),
            values,
            self._approvals,
            project_root=row["project_root"],
        )
        digest = info.digest or ""
        # And the third way to clear the gate: this queue already ran these exact bytes
        # over this exact tree and watched them pass. Looked up *after* classify so the
        # classification is recorded on both outcomes exactly as before, and only on the
        # full-gate path, because a change set that skips the gate has nothing to reuse.
        tree = await self._tree(row["worktree_root"])
        memo = await self._standing_memo(row["project_root"], tree, digest)
        if memo is not None:
            return await self._reuse_verification(row, head, tree, memo)
        try:
            current = await self._store.transition(
                request_id,
                expect=("reconciling",),
                state="verifying",
                reason="",
                reconciled_oid=head,
                verify_gate="full",
                now=self._clock(),
            )
        except LandConflict:
            return None
        await self._emit("land_changed", current)
        attempts = 1 + self._verify_retries()
        plan = await self._store.verify_plan(row["project_root"], digest)
        expected = tuple(sanitize_plan((plan or {}).get("steps") or []))
        outcomes = []
        for attempt in range(attempts):
            tracker = VerifyProgress(
                expected_steps=expected,
                attempt=attempt + 1,
                attempts=attempts,
                clock=self._clock,
            )
            self._verify_live[request_id] = tracker
            try:
                outcome = await run_worktree_verify(
                    Path(row["worktree_root"]),
                    values,
                    self._approvals,
                    project_root=row["project_root"],
                    request_id=request_id,
                    progress=tracker,
                )
            finally:
                self._verify_live.pop(request_id, None)
            outcomes.append(outcome)
            if outcome.passed and outcome.steps:
                # Only a pass. A gate that stopped on a failure announced a prefix of
                # its steps, and recording that would predict a permanently shorter run.
                await self._store.record_verify_plan(
                    project_root=row["project_root"],
                    digest=outcome.digest or digest,
                    steps=outcome.steps,
                    duration_ms=outcome.duration_ms,
                    now=self._clock(),
                )
            if outcome.passed:
                # The verdict, keyed by what decided it. Written here rather than at the
                # end of the pipeline because the fact being recorded is "this queue ran
                # these bytes over this tree and they passed", which is complete now and
                # is unchanged by whether the fast-forward that follows succeeds.
                #
                # **Only a run this queue executed reaches this line.** There is no other
                # writer, and that is the trust boundary rather than an accident of where
                # the call sits: an agent's own shell run is self-reported and has a
                # file-swap loophole - run modified bytes, restore the approved file - so
                # a result it hands over proves nothing about the approved gate.
                await self._store.record_verify_memo(
                    project_root=row["project_root"],
                    tree_oid=tree,
                    digest=outcome.digest or digest,
                    request_id=request_id,
                    request_kind=str(row.get("kind") or "land"),
                    branch=row["branch"],
                    worktree_root=row["worktree_root"],
                    commit_oid=head,
                    duration_ms=outcome.duration_ms,
                    now=self._clock(),
                )
            await self._store.record_event(
                request_id=request_id,
                project_id=row["project_id"],
                step="verify",
                outcome=outcome.status,
                reason=outcome.failure_summary() if not outcome.passed else "",
                detail={**outcome.public_dict(), "attempt": attempt + 1, "oid": head},
                now=self._clock(),
            )
            if outcome.status in {"passed", "failed"}:
                # Only a gate that actually ran is a test fact. `not_configured`
                # and `unapproved` are statements about the setup, and recording
                # them as a failed test run would put a verdict on the branch that
                # nothing ever tested.
                await self._verify_fact(row, outcome, attempt + 1)
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
        return await self._clear_gate(
            row,
            head,
            expect=("verifying",),
            gate="full",
            digest=final.digest or "",
            duration_ms=final.duration_ms,
            bump_attempts=True,
            summary=f"verification passed in {final.duration_ms / 1000:.0f}s",
        )

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
        resolve a conflict and no business trying. It rides the Phase 5 queue, and
        every readiness and auto-delivery gate still decides whether it is delivered
        - all `_reply_arming` adds is that the request's own author does not have to
        press send for the answer it asked for.
        """
        body = self._handback_body(row, step=step, summary=summary, paths=paths, output=output)
        message_id, armed, arming_reason = await self._solicited_reply(row, body)
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
            detail={
                "message_id": message_id,
                "paths": list(paths),
                # Whether the answer will reach its author without a human press, and
                # why not when it will not. A handback that sat as a draft nobody
                # delivered is the failure this records: from the row alone it read
                # exactly like one that arrived (`land-queue.md`).
                "armed": armed,
                "arming_reason": arming_reason,
            },
            now=self._clock(),
        )
        await self._fact(updated, "land_handed_back", {"step": step, "reason": summary})
        await self._emit("land_changed", updated)
        return updated

    async def _solicited_reply(
        self, row: dict[str, Any], body: str
    ) -> tuple[str, bool, str]:
        """Put one bounded answer to this request into its author's prompt queue.

        The single place the queue writes to a session, so a handback and a verify-only
        result cannot differ about who may be answered or how the answer is armed. What
        it returns is the message id, whether the row really arrived armed, and - when it
        did not - why not: a draft nobody delivered otherwise reads, from the trail
        alone, exactly like an answer that arrived.
        """
        if self._queue_message is None or not row.get("origin_session_id"):
            return "", False, "no origin session"
        armed, arming_reason = await self._reply_arming(row)
        try:
            message = await self._queue_message(
                target_session_id=row["origin_session_id"],
                body=body,
                armed=armed,
                solicited_by=row["id"] if armed else None,
                sender_kind="rule",
                sender_id="land_queue",
                sender_label="Land queue",
                correlation_id=row["correlation_id"] or row["id"],
            )
            # Read the arming back off the row rather than reporting what was asked
            # for: a retry dedupes into the message it already created, which may have
            # been staged under different conditions.
            return (
                str((message or {}).get("id") or ""),
                str((message or {}).get("state") or "") == "armed",
                arming_reason,
            )
        except Exception:  # noqa: BLE001 - a failed reply must not lose the row
            log.warning("land_reply_failed request_id=%s", row["id"])
            return "", False, "the message could not be staged"

    async def _report_verified(
        self, row: dict[str, Any], *, summary: str, duration_ms: float
    ) -> None:
        """Tell a verify-only request's author what the gate said.

        A land announces itself by the trunk moving; a verify-only run has no such
        evidence, so the message *is* the result. It rides the same solicited-reply
        authority as a handback and for the same reason - it is the bounded,
        deterministic, daemon-authored answer to a request this very session made - and
        it spends the same single armed reply, which is why a verify-only request that
        passed cannot then also hand back.
        """
        message_id, armed, arming_reason = await self._solicited_reply(
            row, self._verified_body(row, summary=summary, duration_ms=duration_ms)
        )
        await self._store.record_event(
            request_id=row["id"],
            project_id=row["project_id"],
            step="verify",
            outcome="reported",
            reason=summary,
            detail={
                "message_id": message_id,
                "armed": armed,
                "arming_reason": arming_reason,
            },
            now=self._clock(),
        )
        await self._fact(row, "land_verified", {"gate": row.get("verify_gate") or ""})
        log.info(
            "land_verify_only_completed request_id=%s branch=%s gate=%s armed=%s",
            row["id"],
            row["branch"],
            row.get("verify_gate") or "",
            armed,
        )

    async def _reply_arming(self, row: dict[str, Any]) -> tuple[bool, str]:
        """Whether this request's answer may reach its author without a human press.

        The Phase 5 floor is that a non-human sender's write ends at a human, and it
        is right about the thing it was written for: an *unsolicited* write into
        somebody's terminal. A handback is not that. It is the bounded, deterministic,
        daemon-authored answer to a `request_land` this very session made, addressed
        to nobody else, saying nothing a model wrote. The request is the consent, so
        the floor is narrowed exactly as far as the request reaches and no further:

        - **Only the origin.** The target is the request's recorded
          ``origin_session_id`` and there is no argument that could make it another
          session, the same way `request_land` has no target argument.
        - **Only the run that asked.** A session that resumed into a new conversation
          is a different correspondent; its predecessor's consent is not its own, which
          is the same run binding every auto-delivery grant carries.
        - **Only an agent's own request.** An operator's land was not asked for by the
          session, so it has no origin to answer and no consent to spend.
        - **Only while the Project still permits landing.** The install stop and the
          per-Project `land_queue` opt-in are read here rather than trusted from
          enqueue time: turning the feature off must stop the unattended half too,
          and it is the switch an operator reaches for.
        - **Once.** `ARMED_REPLIES_PER_REQUEST`, claimed atomically.

        Refusing arming is never refusing the message: it is still enqueued, as the
        draft it used to always be, and a human can still send it.
        """
        origin_session_id = str(row.get("origin_session_id") or "")
        if str(row.get("origin") or "operator") != "agent":
            return False, "the request was not made by a session"
        if not await self._enabled(row["project_root"]):
            return False, "the land queue is not enabled for this Project"
        expected_run = str(row.get("origin_run_id") or "")
        if self._session_run is None:
            # A check that could not be made is not a check that passed, which is the
            # same rule the preconditions run under. Without a way to ask which run the
            # origin is on, the consent cannot be shown to still belong to it.
            return False, "the requesting conversation could not be identified"
        try:
            live_run = str(self._session_run(origin_session_id) or "")
        except Exception:  # noqa: BLE001 - an unreadable run is not the run that asked
            live_run = ""
        if not expected_run or not live_run:
            return False, "the requesting conversation could not be identified"
        if expected_run != live_run:
            return False, "the requesting conversation was replaced"
        if not await self._store.claim_armed_reply(
            row["id"], cap=ARMED_REPLIES_PER_REQUEST
        ):
            return False, "this request has already spent its armed reply"
        return True, "answering this session's own land request"

    async def origin_windows(
        self, session_ids: Sequence[str], since: float
    ) -> dict[str, dict[str, Any]]:
        """Sessions whose own queue request is still open, for the reply window.

        The other half of the same consent, and the half without which arming would
        not be enough: a session that asks to land then goes quiet *by definition* -
        it is waiting - so the idle lapse closes its grant precisely while the answer
        is being computed, and the armed handback arrives with nothing to deliver it.
        This is the same shape `auto-delivery.md` already gives a delivered agent
        message, with the land request in place of the message: bounded by the
        exchange's own end (a terminal request opens nothing) and by ``since``.

        A verify-only request waits in exactly the same way and is covered by exactly
        the same window. The evidence keeps the pipe's name (`kind: "land"`) and carries
        the request's own `request_kind` beside it, rather than growing a second kind
        for one queue.
        """
        found = await self._store.open_origin_requests(session_ids, since)
        return {
            session_id: {"kind": "land", **entry}
            for session_id, entry in found.items()
        }

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
        what = "land" if self._lands(row) else "verification"
        lines = [
            f"The {what} of `{row['branch']}` stopped at {step}: {summary}.",
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
                f"Resolve this in your worktree, then request the {what} again. "
                "The worktree was left as it was found; nothing was committed for you.",
            ]
        )
        return "\n".join(lines)

    def _verified_body(
        self, row: dict[str, Any], *, summary: str, duration_ms: float
    ) -> str:
        """A fixed template for a verify-only request that cleared its gate.

        It states what was proven, what was *not* done, and - because that is the whole
        economy of the feature - that a land of this exact tree will not spend the gate
        again. No model writes any part of it.
        """
        gate = str(row.get("verify_gate") or "")
        proven = {
            "full": "The verification command passed.",
            "docs_only": (
                "The change set is documentation only, so the gate was skipped - "
                "a land of it would skip the gate for the same reason."
            ),
            "reused": (
                "These exact bytes had already passed on this exact tree in this "
                "queue, so the gate was not run again."
            ),
        }.get(gate, summary)
        lines = [
            f"`{row['branch']}` cleared verification. **Nothing was landed.**",
            "",
            proven,
            "",
            f"Worktree: `{row['worktree_root']}`",
            f"Target: `{row['project_root']}` ({row['trunk_ref'] or 'HEAD'})",
            f"Verified at: `{(row.get('verified_oid') or '')[:12]}`"
            + (f" · {duration_ms / 1000:.0f}s" if duration_ms > 0 else ""),
            "",
            "The trunk was merged into your branch to verify it, so your branch now "
            "contains that merge. Request the land when you are ready: while this tree "
            "and this verification command both stand, the land will reuse this result "
            "rather than run the gate again.",
        ]
        return "\n".join(lines)

    # -- collaborators --------------------------------------------------------

    async def _head(self, cwd: str) -> str:
        from .git_monitor import read_git

        code, output = await read_git(cwd, "rev-parse", "HEAD")
        return output.strip() if code == 0 else ""

    async def _tree(self, cwd: str) -> str:
        """The content hash of this checkout's committed state.

        The tree rather than the commit, because the gate's verdict is about *content*
        and two commits routinely carry one tree: a reconcile that merged an unchanged
        trunk produces a new commit over identical content, which is exactly the case a
        commit-keyed memo would miss.

        An unreadable answer is `''`, which the memo store rejects as half a key - so a
        repository that cannot be read runs the gate rather than reusing something.
        """
        from .git_monitor import read_git

        code, output = await read_git(cwd, "rev-parse", "HEAD^{tree}")
        return output.strip() if code == 0 else ""

    async def _standing_memo(
        self, project_root: str, tree: str, digest: str
    ) -> dict[str, Any] | None:
        """A green verdict this queue produced over this exact content, if one stands.

        Bounded by `land_verify_memo_seconds`, which is a real bound rather than
        hygiene: a tree hash is a claim about content, and the gate's verdict also
        depends on the machine underneath - an installed dependency, a toolchain, an OS
        update, none of which changes the tree. Outside the bound the gate runs, which
        is the fail-closed direction (a needless run costs minutes; a wrongly reused
        verdict costs a trunk).
        """
        window = self._memo_seconds()
        if window <= 0 or not tree or not digest:
            return None
        try:
            return await self._store.verify_memo(
                project_root, tree, digest, not_before=self._clock() - window
            )
        except Exception:  # noqa: BLE001 - a memo that cannot be read is a memo that is absent
            log.warning("land_verify_memo_unreadable root=%s tree=%s", project_root, tree[:12])
            return None

    async def _values(self, project_root: str) -> dict[str, Any]:
        if self._project_values is None:
            return {}
        try:
            return await self._project_values(project_root)
        except Exception:  # noqa: BLE001 - an unreadable config configures nothing
            log.warning("land_project_values_failed root=%s", project_root)
            return {}

    async def _verify_fact(self, row: dict[str, Any], outcome: Any, attempt: int) -> None:
        """Record the gate's verdict as a Tier 0 `test_result` fact.

        The gate is the only test run most branches ever get, and it runs
        out-of-band: the daemon executes it, so no tool call and no transcript
        record it, and the substrate saw one `test_result` fact against 4,485
        `command_result` facts in a measured 24-hour window (2026-08-21). With
        nothing to read, declared-vs-verified could only ever say "nothing
        verified", which is a statement about capture rather than about the agent
        — so it now says nothing at all for a run with no test facts, and this is
        what puts them there.

        Attributed to the session that asked for the land, like every other land
        fact: an operator-initiated land has no session and records nothing.
        """
        if self._record_fact is None or not row.get("origin_session_id"):
            return
        try:
            await self._record_fact(
                session_id=row["origin_session_id"],
                kind="test_result",
                target=row["branch"],
                agent_run_id=row.get("origin_run_id") or None,
                project_id=row.get("project_id") or None,
                detail={
                    "request_id": row["id"],
                    "worktree": row["worktree_root"],
                    "tool": "worktree-verify",
                    "attempt": attempt,
                    "success": bool(outcome.passed),
                    "exit_code": outcome.exit_code,
                    "test_outcome": verify_test_outcome(outcome),
                },
            )
        except Exception:  # noqa: BLE001 - audit must never break the pipeline
            log.warning("land_verify_fact_failed request_id=%s", row["id"])

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
        # A running gate's own reading of itself, attached to the row it belongs to.
        # Only ever present on a row that is verifying right now, and only for a gate
        # this daemon is watching: a request that a restart returned to `queued` has no
        # live run, and reporting a stale snapshot would be worse than reporting none.
        for row in requests:
            tracker = self._verify_live.get(row["id"])
            row["verify_progress"] = (
                tracker.snapshot(now=self._clock())
                if tracker is not None and row["state"] == "verifying"
                else None
            )
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
