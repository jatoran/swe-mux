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
from .land_store import LandConflict, LandEvent, LandStore
from .path_identity import same_path
from .verify_progress import VerifyProgress, sanitize_plan
from .verify_provenance import (
    TRUSTED_VERDICTS,
    VerifyProvenance,
    read_verify_provenance,
    verify_bypass_allowed,
)
from .worktree_verify import (
    MAX_HANDBACK_OUTPUT_BYTES,
    VerifyApprovalStore,
    approval_diff,
    describe_verify_command,
    run_worktree_verify,
)
from .worktree_verify import (
    SCRIPT_NAME as VERIFY_SCRIPT_NAME,
)

log = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 5.0
LAND_LOOP = "land_queue"

#: Per-origin-session requests per hour. A land is expensive in wall-clock rather than
#: in tokens, so the cap is about a runaway loop, not about spend.
DEFAULT_HOURLY_BUDGET = 12
_BUDGET_WINDOW_SECONDS = 3600.0

GRANTS = ("off", "draft", "granted")

#: States that count as an answer about a branch, for deciding whether an older refusal
#: still stands. The same set the strip's own supersession rule uses (`gitLand.ts`), and
#: it has to be: the daemon and the browser must agree about which refusals are live, or
#: approving would resume a row the operator can no longer see.
ANSWERING_STATES = ("landed", "verified", "already_landed", "handed_back", "refused")

#: How far back a resume looks for refusals to revive. The same window `GET /api/land`
#: hands the browser, so the daemon never resumes something the strip stopped drawing.
RESUME_SCAN_LIMIT = 200

#: How many distinct branch tips one queue reading asks git about. A standing refusal
#: nothing else has answered is rare - none or one on an ordinary Project - and this
#: reading happens on the Git tab's poll, so the bound is what keeps a pathological
#: history from turning a list into a git storm.
MAX_ABSORBED_PROBES = 8

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
        verify_grant_field: Callable[[str], str] | None = None,
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
        self._verify_grant_field = verify_grant_field
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
        """Return steps orphaned by a daemon restart to the queue.

        The `orphaned` audit rows are written by the store, in the same commit as
        the requeue - a second crash between the requeue and its record is exactly
        the hole this path must not have.
        """
        for row in await self._store.restore(now=self._clock()):
            log.warning(
                "land_step_orphaned request_id=%s branch=%s state=%s",
                row["id"],
                row["branch"],
                row["state"],
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

    def _verify_grant(self, project_root: str) -> str:
        """Whether this Project lets its agents' own gate edits run unapproved.

        `draft` when nothing is wired, which is Phase 14's behaviour: a service built
        without this collaborator has no way to read the Project's authority, and
        assuming the permissive answer would grant it by omission.
        """
        if self._verify_grant_field is None:
            return "draft"
        try:
            value = self._verify_grant_field(project_root)
        except Exception:  # noqa: BLE001 - an unreadable grant is the inert one
            return "draft"
        return value if value in GRANTS else "draft"

    async def _verify_provenance(self, row: dict[str, Any], info: Any) -> VerifyProvenance:
        """Where the bytes about to run came from, asked read-only of git."""
        from .git_monitor import read_git

        try:
            return await read_verify_provenance(
                git=read_git,
                worktree_root=row["worktree_root"],
                project_root=row["project_root"],
                source=str(info.source or ""),
                script_name=VERIFY_SCRIPT_NAME,
                trunk_ref=str(row.get("trunk_ref") or ""),
            )
        except Exception:  # noqa: BLE001 - a provenance read that fails grants nothing
            log.warning(
                "land_verify_provenance_failed request_id=%s worktree=%s",
                row["id"],
                row["worktree_root"],
                exc_info=True,
            )
            return VerifyProvenance(
                "unknown", False, "the provenance of the verification command could not be read"
            )

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
        report_success: bool = False,
        resumed_from: str = "",
    ) -> dict[str, Any]:
        """Enqueue a land or a verify-only run, or draft it for a human, or refuse it.

        An operator request bypasses the grant because the operator *is* the authority
        the grant defers to; it still passes every precondition the pipeline checks.

        `resumed_from` names a request this one replaces, and says one thing: **a human
        has already authorised this exact land, and the thing it was waiting on has
        happened.** It is set only by the operator clearing a verification block, so it
        skips the two checks that exist to decide whether a *new* agent request should
        start - the per-origin budget (the operator started this one, and charging an
        agent for the operator's approval would let a blocked branch exhaust an hour's
        allowance by being approved) and the `draft` grant (a human already decided this
        request once; drafting it again would ask the same question twice). It skips
        nothing else: the Project opt-in, an `off` grant, and every repository
        precondition are re-read, because those are standing permissions and the branch
        may have moved since the refusal.

        **The grant means something narrower for a verify-only request, and the reason
        is what the grant is about.** `off` refuses both, because it is the operator
        saying agents do not drive this machinery here. `draft` drafts a *land* - a
        human decides before a trunk moves - and enqueues a *verify*, because a
        verify-only run moves nothing: it merges the trunk into the requester's own
        branch, in the requester's own worktree, and runs bytes a human already
        approved. There is nothing for a human to decide in advance about that, and
        drafting it would put the cheap half of the pipeline behind the approval the
        expensive half exists to protect.

        `report_success` asks for the one outcome that never spoke. A conflict, a failed
        gate, a hold that expired and a refusal all answer their author; a *land* did
        not, on the argument that it announces itself by the trunk moving - which is
        true for a human watching the Git tab and false for the session that asked,
        because "waiting for a land" is precisely being idle and not looking. It is
        opt-in rather than always-on for the reason the silence was defensible in the
        first place: a fleet landing six branches would otherwise interrupt six agents
        that have moved on, and only the requester knows whether it has work gated on
        the land. It grants nothing new - the reply rides `_solicited_reply` under every
        bound a handback does, and spends the same single armed reply, which a land that
        succeeded has no other use for. Inert for `verify`, whose pass already reports.
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
            if resumed_from:
                budget = 0
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
            if grant == "draft" and lands and not resumed_from:
                return await self._draft(
                    project_id=project_id,
                    project_root=project_root,
                    worktree_root=worktree_root,
                    origin_session_id=origin_session_id,
                    origin_run_id=origin_run_id,
                    reason=reason,
                    report_success=report_success,
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
                report_success=report_success,
                # The claim and the trail's opening entry in one commit; the row
                # carries the branch and OID it claimed, so an event written
                # afterwards could have described a claim that never happened.
                event=LandEvent(
                    step="request",
                    outcome="queued",
                    detail={
                        "origin": origin,
                        "kind": kind,
                        "branch": facts.worktree_branch,
                        "oid": facts.worktree_head,
                        # In the trail because a message the queue sent, or did not
                        # send, has to be accountable to something the request asked
                        # for rather than to a default that may have changed since.
                        **({"report_success": True} if report_success else {}),
                        # The link back to the refusal this replaces. A redo is a new
                        # id by design - nothing reopens a terminal row, because the
                        # trail has to go on saying the refusal happened - so without
                        # this the two rows are unrelated facts about one branch.
                        **({"resumed_from": resumed_from} if resumed_from else {}),
                    },
                ),
                now=self._clock(),
            )
        except LandConflict as exc:
            raise LandRefusal("already_queued", str(exc)) from exc
        await self._emit("land_changed", row)
        return row

    async def resume_verification_blocked(
        self,
        *,
        project_id: str,
        project_root: str,
        worktree_root: str = "",
        digest: str = "",
        trusted_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Re-queue the lands a verification block ended, now that it is cleared.

        The operator's complaint this exists for: a land refused for an unapproved
        gate is **terminal**, so approving the bytes cleared the block and left the
        request dead, and the branch had to be asked for again by hand - or, where an
        agent asked, by an agent that had already been told its request was over.
        Approving is the moment the wait ends; the queue should end with it.

        **A redo is a new row, not a revived one**, which is the shape this design
        already uses for a bounced request (`land-queue.md`). `refused` stays terminal
        and the trail goes on saying the refusal happened; the new row names the old
        one in its opening event. Reopening the row in place would erase an audit entry
        and put a second writer on a terminal state.

        Two filters decide what is resumed, and both are narrow on purpose:

        - **Only refusals the block actually covered.** `digest` restricts it to the
          bytes just approved, and `worktree_root` to the checkout that presented them.
          Approving one worktree's copy says nothing about another's, exactly as the
          digest-scoped approval store does.
        - **Only refusals that still stand**, by the same supersession rule the strip
          draws its blocked-worktree gates from: a branch that has since landed,
          verified, or been answered any other way is not waiting on this.

        `trusted_only` is the grant path's filter. Raising `land_verify_grant` clears
        every block whose bytes this machine wrote and none of the others, so resuming
        a `foreign_author` refusal there would queue a land that is about to refuse for
        exactly the reason it refused before.

        A refusal raised by the re-request is *not* an error here: a branch that landed
        some other way, or that another request now claims, is a resume with nothing to
        do. It is logged and skipped, and the caller is told only what was queued.
        """
        # Scoped by **id**, not by root. A row stores the trunk root git resolved
        # (`facts.trunk_root`), which is not always the string the Project was
        # registered under - a different case, a symlink, a short path - and an equality
        # match on it silently resumes nothing. The id is the same string at both ends.
        rows = await self._store.list_requests(project_id=project_id, limit=RESUME_SCAN_LIMIT)
        if not rows and project_root:
            rows = await self._store.list_requests(
                project_root=project_root, limit=RESUME_SCAN_LIMIT
            )
        answered: dict[tuple[str, str], float] = {}
        for row in rows:
            if row["state"] not in ANSWERING_STATES:
                continue
            key = (row["project_root"], row["branch"])
            created = float(row["created_at"])
            if created > answered.get(key, 0.0):
                answered[key] = created
        resumed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if row["state"] != "refused":
                continue
            detail = row.get("detail") or {}
            if detail.get("code") != "unapproved":
                continue
            key = (row["project_root"], row["branch"])
            if key in seen or answered.get(key, 0.0) > float(row["created_at"]):
                continue
            if worktree_root and not same_path(row["worktree_root"], worktree_root):
                continue
            # From the refusal's own detail, not the row's `verify_digest`: a refusal
            # ends the request without recording a run, so that column is empty on
            # exactly the rows this resumes. The detail is what `_refuse` wrote and is
            # the same field the strip reads to find the bytes to offer.
            if digest and str(detail.get("verify_digest") or "") != digest:
                continue
            if trusted_only and str(detail.get("verify_provenance") or "") not in TRUSTED_VERDICTS:
                continue
            seen.add(key)
            try:
                fresh = await self.request(
                    project_id=row["project_id"] or project_id,
                    project_root=row["project_root"],
                    worktree_root=row["worktree_root"],
                    kind=str(row.get("kind") or "land"),
                    origin=str(row.get("origin") or "operator"),
                    origin_session_id=str(row.get("origin_session_id") or ""),
                    origin_run_id=str(row.get("origin_run_id") or ""),
                    # Inherited, because the redo is the same request: a session that
                    # asked to be told, and was told only that its land was refused,
                    # is owed the answer once the block is cleared.
                    report_success=bool(row.get("report_success")),
                    resumed_from=str(row["id"]),
                )
            except LandRefusal as refusal:
                log.info(
                    "land_resume_skipped request_id=%s branch=%s code=%s",
                    row["id"],
                    row["branch"],
                    refusal.code,
                )
                continue
            log.info(
                "land_resumed request_id=%s replaces=%s branch=%s",
                fresh["id"],
                row["id"],
                row["branch"],
            )
            resumed.append(fresh)
        return resumed

    async def _draft(
        self,
        *,
        project_id: str,
        project_root: str,
        worktree_root: str,
        origin_session_id: str,
        origin_run_id: str = "",
        reason: str = "",
        report_success: bool = False,
    ) -> dict[str, Any]:
        """Write an inert approval request instead of acting.

        The default. A drafted land starts nothing: a human is what turns it into a
        queued request, exactly as they do for `interrupt` and `end_session`.

        `report_success` travels with the draft because it is part of what the session
        asked for, and the approval is meant to enqueue *that* request rather than a
        similar one. Dropping it here would make the flag silently conditional on the
        Project's grant, which is a different feature than the one it is.
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
            report_success=report_success,
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
            event=LandEvent(step="request", outcome="cancelled"),
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
        event: LandEvent | None = None,
    ) -> dict[str, Any] | None:
        """Move a request past the gate - to the fast-forward, or to its own terminal green.

        The one place that decides where a cleared gate leads, so the three ways of
        clearing it (the gate ran, the change set was documentation, a queue-executed
        verdict already stood) cannot drift apart about it.

        `verified_oid` is set on every path and still means what it always meant - the
        OID this request cleared its gate at - so the branch-moved-after-clearing check
        in `_land` keeps working unchanged.

        `event` is the "why this gate was cleared without running" record, and it
        travels with the transition rather than ahead of it. All three skip paths used
        to write it first, which meant a `verify/skipped` or `verify/reused` entry could
        outlive a transition that then lost its race and never happened - a trail
        asserting a gate was skipped for a request still sitting in `reconciling`. A
        `LandConflict` now discards both together.
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
                event=event,
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
        return await self._clear_gate(
            row,
            head,
            expect=("reconciling",),
            gate="docs_only",
            summary=choice.reason,
            event=LandEvent(
                step="verify",
                outcome="skipped",
                reason=choice.reason,
                detail={"gate": "docs_only", "paths": list(choice.paths), "oid": head},
            ),
        )

    async def _reuse_verification(
        self, row: dict[str, Any], head: str, tree: str, memo: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Accept a verdict this queue already produced over these exact bytes and content.

        The saving the whole feature exists for: an agent asks the queue to verify, the
        queue runs the gate, and the `request_land` that follows finds the same tree
        under the same approved command and does not spend the minutes again.

        **A skipped gate is never silent** (`no silent caps`), so the reuse is written to
        the trail *with its key*, in the same commit that moves the row. The key is what
        makes the record auditable rather than a claim: a reader can ask which run
        produced the verdict and check that the tree it names is the tree that landed.
        """
        reuse = LandEvent(
            step="verify",
            outcome="reused",
            reason="these exact bytes already passed on this exact tree in this queue",
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
            event=reuse,
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
        return await self._clear_gate(
            row,
            head,
            expect=("reconciling",),
            # Unchanged rather than restated: whichever gate cleared this OID is still
            # the gate that cleared it, and overwriting the column here would relabel it.
            gate=None,
            digest=str(row.get("verify_digest") or ""),
            summary="the branch already contained the trunk and its verdict still stands",
            event=LandEvent(
                step="verify",
                outcome="standing",
                reason=(
                    "the branch already contained the trunk; its recorded verdict still stands"
                ),
                detail={"oid": head, "gate": row.get("verify_gate") or ""},
            ),
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
        # Whether these bytes may run without a human having read this exact digest.
        # Asked once, here, rather than inside the attempt loop: it is a question about
        # the bytes and the Project, and neither moves between a first attempt and a
        # retry. Asked *after* the memo, so a reuse that runs nothing never records a
        # bypass it did not exercise; and skipped entirely for an already-approved gate,
        # so the ordinary case costs no git.
        provenance: VerifyProvenance | None = None
        bypass = False
        if info.configured and not info.approved:
            provenance = await self._verify_provenance(row, info)
            bypass = verify_bypass_allowed(self._verify_grant(row["project_root"]), provenance)
            if bypass:
                # The trail's half of the trade. A bypassed run replaces "a human reads
                # this before it runs" with "a human can read what ran afterwards", and
                # that is only true if the diff is written down at the moment it is
                # still resolvable - the file moves on with the branch.
                await self._store.record_event(
                    request_id=request_id,
                    project_id=row["project_id"],
                    step="verify",
                    outcome="approval_bypassed",
                    reason=provenance.reason,
                    detail={
                        "digest": digest,
                        "source": info.source or "",
                        "previously_approved": info.previously_approved,
                        "diff": approval_diff(info),
                        **provenance.public_dict(),
                    },
                    now=self._clock(),
                )
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
                    bypass_approval=bypass,
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
                # The code, the digest, and which copy resolved it - not just a
                # sentence. A land refused on *this worktree's* edited gate has to be
                # answerable in the landing strip, and the strip cannot read a reason
                # string to learn which checkout's bytes to offer for approval.
                await self._refuse(
                    current,
                    final.failure_summary(),
                    detail={
                        "code": final.status,
                        "verify_digest": final.digest or "",
                        "verify_source": final.source or "",
                        "worktree_root": row["worktree_root"],
                        # Why the standing authority did not cover these bytes. Without
                        # it an `unapproved` refusal in a Project that grants agents the
                        # gate reads as a contradiction, and the reader's next move is
                        # to doubt the switch rather than to look at who wrote the
                        # script - which is the one fact that decided it.
                        "verify_provenance": (
                            provenance.verdict if provenance is not None else ""
                        ),
                        "verify_provenance_reason": (
                            provenance.reason if provenance is not None else ""
                        ),
                        "verify_grant": self._verify_grant(row["project_root"]),
                    },
                )
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
        # The one outcome that used to say nothing, and only when the request asked.
        # Composed before the transition, exactly as a handback is: the request has to
        # still be open for its own reply window to be holding the author's grant off
        # the idle lapse, which is the half without which arming is not delivery.
        reported = bool(row.get("report_success"))
        body = (
            self._landed_body(row, trunk_before=trunk_before, trunk_after=trunk_after)
            if reported
            else ""
        )
        message_id, armed, arming_reason = (
            await self._solicited_reply(row, body) if reported else ("", False, "")
        )
        landed = await self._store.transition(
            request_id,
            expect=("landing",),
            state="landed",
            reason="",
            trunk_before=trunk_before,
            landed_oid=trunk_after,
            # Named for the handback it was added for, but it is the id of whatever
            # message this request produced, and a reported land produces one.
            handback_message_id=message_id,
            event=LandEvent(
                step="land",
                outcome="landed",
                detail={
                    "trunk_before": trunk_before,
                    "trunk_after": trunk_after,
                    **(
                        {
                            "message_id": message_id,
                            # A draft nobody delivered reads, from the trail alone,
                            # exactly like an answer that arrived - the same defect the
                            # handback's arming record exists for.
                            "armed": armed,
                            "arming_reason": arming_reason,
                            # And the trail records what was *said*, not only that
                            # something was.
                            "body": body,
                        }
                        if reported
                        else {}
                    ),
                },
            ),
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
            event=LandEvent(
                step="request",
                outcome="already_landed",
                reason=reason,
                detail=dict(detail or {}),
            ),
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
        """End the request without running anything, and tell its author.

        The reply is the half this was missing. A handback has always answered the
        session that asked; a refusal answered nobody, so an agent that called
        `request_land` and went quiet - which is what waiting for a land *is* - sat
        idle while its request died of an unapproved gate or a moved branch. Observed
        2026-08-21, twice in one evening. It rides the same `_solicited_reply` bounds
        as a handback and for the same reason: it is the bounded, deterministic,
        daemon-authored answer to a request this very session made.
        """
        payload = dict(detail or {})
        body = self._refused_body(row, reason=reason, detail=payload)
        message_id, armed, arming_reason = await self._solicited_reply(row, body)
        updated = await self._store.transition(
            row["id"],
            expect=("queued", "waiting", "reconciling", "verifying", "landing"),
            state="refused",
            reason=reason,
            detail=payload,
            clear_waiting=True,
            handback_message_id=message_id,
            event=LandEvent(
                step="request",
                outcome="refused",
                reason=reason,
                detail={
                    **payload,
                    "message_id": message_id,
                    "armed": armed,
                    "arming_reason": arming_reason,
                    # The text itself, not just a pointer to it. An operator's own Land
                    # has no origin session, so `_solicited_reply` composes this and
                    # drops it on the floor - the one request whose author is standing
                    # in front of the queue was the only one that never got the
                    # explanation. Recording it also makes the trail say what was
                    # *said*, where before it said only that something was.
                    "body": body,
                },
            ),
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
            event=LandEvent(
                step=step,
                outcome="handed_back",
                reason=summary,
                detail={
                    "message_id": message_id,
                    "paths": list(paths),
                    # Whether the answer will reach its author without a human press,
                    # and why not when it will not. A handback that sat as a draft
                    # nobody delivered is the failure this records: from the row alone
                    # it read exactly like one that arrived (`land-queue.md`).
                    "armed": armed,
                    "arming_reason": arming_reason,
                    # The same rule the refusal event carries: the trail records what
                    # was said, so the message is readable from the row whether or not
                    # it ever reached a session.
                    "body": body,
                },
            ),
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

    def _refused_body(
        self, row: dict[str, Any], *, reason: str, detail: dict[str, Any]
    ) -> str:
        """A fixed template for a request the pipeline ended without running it.

        Its whole job is to keep the reader from re-reading their own branch. A
        refusal is a statement about the *setup* - an unapproved gate, no gate at all,
        a branch that moved out from under a verdict, a fast-forward Git would not
        do - and an agent told only "your land was refused" will go looking for the
        defect in its own diff, which is the one place it is not. So the message says
        what is wrong, whose act clears it, and, for the two cases that are nobody's
        code, that the branch is not the problem. No model writes any part of it.
        """
        what = "land" if self._lands(row) else "verification"
        code = str(detail.get("code") or "")
        lines = [
            f"The {what} of `{row['branch']}` was refused: {reason}.",
            "",
            f"Worktree: `{row['worktree_root']}`",
            f"Target: `{row['project_root']}` ({row['trunk_ref'] or 'HEAD'})",
            "",
        ]
        if code in {"unapproved", "not_configured"}:
            lines.extend(
                [
                    "**This is not a problem with your branch.** Nothing was run against it "
                    "and nothing about it was found wanting.",
                    "",
                    (
                        "The verification command this checkout resolves to is not "
                        "approved on this machine."
                        if code == "unapproved"
                        else "This Project has no verification command, so there is "
                        "nothing the queue is allowed to run."
                    ),
                ]
            )
            # A Project may let an agent's *own* edits to the gate run unapproved, so an
            # `unapproved` refusal here means that standing authority did not reach these
            # particular bytes. Saying which of the two it was is the difference between
            # a reader checking a switch and a reader checking an author.
            if code == "unapproved":
                reason = str(detail.get("verify_provenance_reason") or "")
                grant = str(detail.get("verify_grant") or "")
                if grant != "granted":
                    lines.extend(
                        [
                            "",
                            "This Project approves the gate's bytes individually "
                            "(`land_verify_grant` is `draft`).",
                        ]
                    )
                elif reason:
                    lines.extend(
                        [
                            "",
                            "This Project does let agents change the gate, but that "
                            f"authority did not cover these bytes: {reason}.",
                        ]
                    )
            lines.extend(
                [
                    "",
                    "Approving is a human act against the exact bytes, in the Git tab's "
                    "Landing strip. You cannot approve it yourself, and neither can the "
                    f"daemon. Once it is approved, request the {what} again.",
                ]
            )
        else:
            # The two object ids the reader needs and would otherwise reconstruct by
            # hand. A fast-forward refusal means the trunk moved past the base this
            # request was enqueued on, and "Diverging branches can't be fast-forwarded"
            # says none of that; with these it reads as "you were at X, trunk is at Y,
            # merge the trunk and ask again" - which is the whole of the handoff.
            requested = str(row.get("requested_oid") or "")
            trunk_before = str(detail.get("trunk_before") or "")
            if requested or trunk_before:
                if requested:
                    lines.append(f"Your branch when this was requested: `{requested[:12]}`")
                if trunk_before:
                    lines.append(f"The trunk when it ran: `{trunk_before[:12]}`")
                lines.append("")
            lines.append(
                f"Nothing was committed, merged, or left behind. Address the cause above "
                f"and request the {what} again."
            )
        return "\n".join(lines)

    def _landed_body(
        self, row: dict[str, Any], *, trunk_before: str, trunk_after: str
    ) -> str:
        """A fixed template for a land whose author asked to be told it happened.

        Written only for a request that set `report_success`, and it says the two facts
        a waiting session cannot see for itself: that the trunk moved, and what it moved
        to. It names the gate the same way the verify-only pass does, because "it
        landed" and "it landed without the gate being run" are different statements and
        the second is the one worth reading. No model writes any part of it.
        """
        gate = str(row.get("verify_gate") or "")
        proven = {
            "full": "The verification command passed.",
            "docs_only": (
                "The change set was documentation only, so the gate was skipped."
            ),
            "reused": (
                "These exact bytes had already passed on this exact tree in this "
                "queue, so the gate was not run again."
            ),
        }.get(gate, "")
        lines = [
            f"`{row['branch']}` landed on `{row['trunk_ref'] or 'the trunk'}`.",
            "",
            *([proven, ""] if proven else []),
            f"Worktree: `{row['worktree_root']}`",
            f"Target: `{row['project_root']}`",
            f"Trunk: `{trunk_before[:12]}` → `{trunk_after[:12]}`",
            "",
            "Your worktree is untouched and still on its branch, which now contains "
            "the merge of the trunk the queue made in order to verify it. Nothing "
            "further is required for the land itself.",
        ]
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

    async def _mark_absorbed(
        self, requests: list[dict[str, Any]], project_root: str | None
    ) -> None:
        """Say which unanswered refusals the trunk has since absorbed anyway.

        A refusal stops speaking for its branch once a *later request for that branch*
        got an answer, and until now that was the only thing that could answer one. So a
        branch landed **outside** the queue - by hand, which is what a fast-forward
        refusal tells its author to go and do - left its refusal standing forever, and
        every reader after that saw a live block over work that was already on the
        trunk. Observed 2026-08-29 on `worktree-spawn-model-selection`, whose refusal
        went on describing a divergence the operator had resolved by hand days earlier.

        The missing answer is one git question - *does the trunk contain the tip this
        request asked to land* - and it is asked here rather than written to the row.
        **Nothing is stored.** `refused` is terminal and the trail is an audit that must
        go on saying the refusal happened; what was ever wrong is which row *speaks* for
        the queue, so the fix belongs to the reading, exactly as the supersession rule
        it extends does. A `closed` column would be a second writer's opinion about a
        terminal row.

        It is asked of `requested_oid`, the tip the request carried, rather than of the
        branch as it stands: a branch that has since gained commits needs a new request
        anyway, and "the trunk contains what this asked for" is the precise thing that
        makes *this* refusal spent.

        Bounded to `MAX_ABSORBED_PROBES` distinct tips, newest first, over rows nothing
        else has answered - in practice none or one - so the Git tab's poll does not pay
        for a git call per row.
        """
        for row in requests:
            row["absorbed_by_trunk"] = False
        if not project_root:
            return
        answered: dict[tuple[str, str], float] = {}
        for row in requests:
            if row["state"] in ANSWERING_STATES:
                key = (row["project_root"], row["branch"])
                answered[key] = max(answered.get(key, 0.0), float(row["created_at"]))
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in requests:
            if row["state"] not in ("refused", "handed_back"):
                continue
            key = (row["project_root"], row["branch"])
            if answered.get(key, 0.0) > float(row["created_at"]):
                continue
            oid = str(row.get("requested_oid") or "")
            if not oid or oid in seen:
                continue
            seen.add(oid)
            candidates.append(row)
            if len(candidates) >= MAX_ABSORBED_PROBES:
                break
        if not candidates:
            return
        trunk_head = await self._head(project_root)
        if not trunk_head:
            return
        from .git_monitor import read_is_ancestor

        for row in candidates:
            oid = str(row["requested_oid"])
            # `None` is "the question could not be asked", which is not "no" - it leaves
            # the refusal standing, which is the direction that costs a reader a second
            # look rather than a hidden block.
            if await read_is_ancestor(project_root, oid, trunk_head) is True:
                for sibling in requests:
                    if str(sibling.get("requested_oid") or "") == oid:
                        sibling["absorbed_by_trunk"] = True

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
        await self._mark_absorbed(requests, project_root)
        installed = self._installed_enabled()
        project_enabled = False
        grant = "draft"
        verify_grant = "draft"
        if project_root:
            if self._automation_gate is None:
                project_enabled = True
            else:
                try:
                    project_enabled = "land_queue" in await self._automation_gate(project_root)
                except Exception:  # noqa: BLE001 - a gate that cannot answer reads as off
                    project_enabled = False
            grant = self._grant(project_root)
            verify_grant = self._verify_grant(project_root)
        return {
            "requests": requests,
            "hourly_budget": self._hourly_budget(),
            "hold_timeout_seconds": self._hold_timeout(),
            "retry_verification": bool(self._verify_retries()),
            "installed_enabled": installed,
            "project_enabled": project_enabled,
            "agent_grant": grant,
            "verify_grant": verify_grant,
        }

    def verify_command(
        self, worktree_root: str, values: dict[str, Any], *, project_root: str
    ) -> dict[str, Any]:
        info = describe_verify_command(
            Path(worktree_root), values, self._approvals, project_root=project_root
        )
        return info.public_dict()
