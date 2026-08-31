"""Phase 14: the land queue, against real repositories and real worktrees.

These drive `git` for real rather than mocking it. The whole safety argument of the
phase is a property of what Git does - `--ff-only` refuses divergence, `merge` reports
its own conflicts - so a fake that agrees with the implementation would prove nothing
about either.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from swe_mux import land_preconditions
from swe_mux.land_preconditions import evaluate_preconditions, read_repository_facts
from swe_mux.land_queue import LandQueueService, LandRefusal, handback_excerpt
from swe_mux.land_store import LandConflict, LandStore
from swe_mux.worktree_verify import (
    MAX_APPROVED_DIGESTS,
    VerifyApprovalStore,
    describe_verify_command,
    run_worktree_verify,
)

pytestmark = pytest.mark.anyio


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


class FakeConfig:
    land_queue_enabled = True
    land_hourly_budget = 12
    land_hold_timeout_seconds = 1800.0
    land_retry_verification = False
    land_verify_memo_seconds = 24 * 3600.0


class FakeQueue:
    """Stands in for the Phase 5 prompt queue, recording what it was handed."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.messages.append(kwargs)
        # Mirrors `PromptQueueStore.create_message`: the state is what arming
        # produced, and the service reads it back rather than trusting what it asked
        # for, because a correlated retry dedupes into an already-staged row.
        return {
            "id": f"msg_{len(self.messages)}",
            "state": "armed" if kwargs.get("armed") else "draft",
        }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def trunk(tmp_path: Path) -> Path:
    repo = tmp_path / "trunk"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "shared.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def add_worktree(trunk_root: Path, name: str) -> Path:
    path = trunk_root.parent / name
    git(trunk_root, "worktree", "add", "-b", f"worktree-{name}", str(path))
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    return path


def commit(repo: Path, filename: str, text: str, message: str) -> str:
    (repo / filename).write_text(text, encoding="utf-8")
    git(repo, "add", filename)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write_verify(worktree: Path, *, exit_code: int = 0, noise: str = "verified") -> None:
    """A verification script in the repository's own Bash convention."""
    script = worktree / ".worktree-verify"
    script.write_text(
        f"#!/usr/bin/env bash\necho '{noise}'\nexit {exit_code}\n",
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    git(worktree, "add", ".worktree-verify")
    git(worktree, "commit", "-m", "add verification")


def approve(store: VerifyApprovalStore, worktree: Path, trunk_root: Path) -> None:
    info = describe_verify_command(worktree, {}, store, project_root=str(trunk_root))
    assert info.digest is not None
    store.approve(str(trunk_root), info.digest, snapshot=info.current_source)


def build_service(
    tmp_path: Path,
    trunk_root: Path,
    *,
    queue: FakeQueue | None = None,
    busy: tuple[str, ...] = (),
    config: Any = None,
    grant: str = "granted",
    verify_grant: str = "draft",
    automations: set[str] | None = None,
    session_runs: dict[str, str] | None = None,
    facts: list[dict[str, Any]] | None = None,
    drafts: list[dict[str, Any]] | None = None,
) -> tuple[LandQueueService, LandStore, VerifyApprovalStore]:
    store = LandStore(tmp_path / "land.sqlite3")
    approvals = VerifyApprovalStore(tmp_path / "data")

    async def busy_sessions(_root: str) -> tuple[str, ...]:
        return busy

    async def project_values(_root: str) -> dict[str, Any]:
        return {}

    # Mutable so a test can switch the Project off *mid-flight*, which is the case
    # that matters: the gate is re-read when the handback is written, not trusted
    # from the moment the request was accepted.
    enabled = {"land_queue"} if automations is None else automations

    async def automation_gate(_root: str) -> frozenset[str]:
        return frozenset(enabled)

    runs = {"sess_1": "run_1"} if session_runs is None else session_runs

    async def record_fact(**fact: Any) -> str:
        if facts is not None:
            facts.append(fact)
        return "fact_1"

    async def draft_request(**drafted: Any) -> dict[str, Any]:
        """Stands in for the Fleet Queue's inert `land_request` observation."""
        if drafts is not None:
            drafts.append(drafted)
        return {"id": f"obs_{len(drafts or [])}", "state": "draft_requested"}

    service = LandQueueService(
        store=store,
        approvals=approvals,
        config=config or FakeConfig(),
        automation_gate=automation_gate,
        grant_field=lambda _root: grant,
        # `draft` by default so every pre-existing test keeps asserting Phase 14's
        # approve-every-digest behaviour; the tests that mean the new authority ask
        # for it by name.
        verify_grant_field=lambda _root: verify_grant,
        project_values=project_values,
        comparison_ref=lambda _root: _resolved("main"),
        busy_sessions=busy_sessions,
        session_run=lambda session_id: runs.get(session_id, ""),
        queue_message=queue,
        record_fact=record_fact,
        draft_request=draft_request if drafts is not None else None,
    )
    return service, store, approvals


async def _resolved(value: str) -> str:
    return value


# -- preconditions ----------------------------------------------------------


async def test_a_linked_worktree_is_never_a_land_target(trunk: Path) -> None:
    """The trunk must be the main tree, decided by git dirs rather than by name."""
    worktree = add_worktree(trunk, "alpha")
    facts = await read_repository_facts(str(trunk), str(worktree))
    result = evaluate_preconditions(facts, branch="main")
    assert result.disposition == "refuse"
    assert "primary checkout" in result.reason


async def test_a_working_session_holds_rather_than_refusing(trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    facts = await read_repository_facts(str(worktree), str(trunk))
    result = evaluate_preconditions(
        facts, branch="worktree-alpha", busy_sessions=("sess_1",)
    )
    assert result.disposition == "hold"
    assert (result.detail or {})["sessions"] == ["sess_1"]


async def test_repository_safety_reads_have_a_contention_tolerant_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadlines: list[float] = []

    async def fake_read_git(
        _cwd: str, *_args: str, timeout_seconds: float
    ) -> tuple[int, str]:
        deadlines.append(timeout_seconds)
        return 0, ""

    monkeypatch.setattr(land_preconditions, "read_git", fake_read_git)

    await land_preconditions._read_land_git("checkout", "status", "--porcelain")

    assert deadlines == [land_preconditions.LAND_GIT_TIMEOUT_SECONDS]
    assert land_preconditions.LAND_GIT_TIMEOUT_SECONDS > 4.0


async def test_a_timed_out_safety_read_holds_rather_than_refusing(
    trunk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape of the 2026-08-30 gate flake: one transient git failure under host
    contention used to fold into a falsy fact and produce a *permanent* refusal
    ("not a registered worktree", "a linked worktree", "a detached HEAD") for a
    perfectly healthy checkout. Unknown must read as unknown - a hold - for every
    safety read, so each one is failed here in turn against a real repository."""
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    real_read = land_preconditions.read_git

    failing_reads: tuple[tuple[str, ...], ...] = (
        ("worktree", "list"),
        ("rev-parse", "--git-common-dir"),
        ("rev-parse", "HEAD"),
        ("rev-parse", "--abbrev-ref"),
        ("merge-base", "--is-ancestor"),
    )
    for failing in failing_reads:

        async def flaky(
            cwd: str, *args: str, _failing: tuple[str, ...] = failing, **kwargs: Any
        ) -> tuple[int, str]:
            if args[: len(_failing)] == _failing:
                return 124, "git timed out after 15s"
            return await real_read(cwd, *args, **kwargs)

        monkeypatch.setattr(land_preconditions, "read_git", flaky)
        facts = await read_repository_facts(str(worktree), str(trunk))
        assert facts.readable is False, failing
        assert "timed out" in facts.error, failing
        result = evaluate_preconditions(facts, branch="worktree-alpha")
        assert result.disposition == "hold", failing
        assert "could not be read" in result.reason, failing


async def test_a_genuinely_foreign_checkout_still_refuses(
    tmp_path: Path, trunk: Path
) -> None:
    """Honest reads must not turn every "no" into a hold. A repository that Git
    itself lists as no worktree of the trunk gets the real, permanent refusal."""
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    git(stranger, "init", "-b", "main")
    git(stranger, "config", "user.name", "Test User")
    git(stranger, "config", "user.email", "test@example.invalid")
    (stranger / "other.txt").write_text("other\n", encoding="utf-8")
    git(stranger, "add", "other.txt")
    git(stranger, "commit", "-m", "other")

    facts = await read_repository_facts(str(stranger), str(trunk))
    assert facts.readable is True
    result = evaluate_preconditions(facts, branch="main")
    assert result.disposition == "refuse"
    assert "not a registered worktree" in result.reason


async def test_a_dirty_worktree_holds_but_untracked_files_do_not(trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    (worktree / "scratch.log").write_text("ignore me\n", encoding="utf-8")
    facts = await read_repository_facts(str(worktree), str(trunk))
    assert evaluate_preconditions(facts, branch="worktree-alpha").disposition == "ready"

    (worktree / "shared.txt").write_text("edited\n", encoding="utf-8")
    facts = await read_repository_facts(str(worktree), str(trunk))
    result = evaluate_preconditions(facts, branch="worktree-alpha")
    assert result.disposition == "hold"
    assert "uncommitted" in result.reason


async def test_a_dirty_trunk_blocks_only_the_files_the_land_would_overwrite(
    trunk: Path,
) -> None:
    """The pre-check asks the same question `--ff-only` does, not a broader one.

    A whole-checkout dirty test is not a stricter safety net; it is a different and
    wrong question, and it deadlocks any machine whose daemon writes into its own
    primary checkout - which is exactly how enabling this feature blocked every land
    on the repository that ships it.
    """
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")

    # An unrelated local edit in the trunk. The land does not touch this file.
    (trunk / "shared.txt").write_text("operator edit\n", encoding="utf-8")
    facts = await read_repository_facts(str(worktree), str(trunk))
    assert facts.incoming_paths == ("alpha.txt",)
    assert evaluate_preconditions(facts, branch="worktree-alpha").disposition == "ready"

    # An edit to a file the land *would* overwrite. Now it holds, and names it.
    (trunk / "alpha.txt").write_text("conflicting operator edit\n", encoding="utf-8")
    git(trunk, "add", "alpha.txt")
    facts = await read_repository_facts(str(worktree), str(trunk))
    result = evaluate_preconditions(facts, branch="worktree-alpha")
    assert result.disposition == "hold"
    assert (result.detail or {})["paths"] == ["alpha.txt"]


async def test_a_branch_already_on_the_trunk_is_not_landed_again(trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    tip = commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    git(trunk, "merge", "--ff-only", "worktree-alpha")

    facts = await read_repository_facts(str(worktree), str(trunk))
    assert facts.already_landed is True
    result = evaluate_preconditions(facts, branch="worktree-alpha")
    assert result.disposition == "already_landed"
    assert not result.ready
    assert git(trunk, "rev-parse", "HEAD") == tip


async def test_requesting_an_already_landed_branch_is_refused_at_once(
    tmp_path: Path, trunk: Path
) -> None:
    """Answered on the press rather than a sweep later, so the panel is not confusing."""
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    git(trunk, "merge", "--ff-only", "worktree-alpha")
    service, store, _ = build_service(tmp_path, trunk)
    try:
        with pytest.raises(LandRefusal) as caught:
            await service.request(
                project_id="p", project_root=str(trunk), worktree_root=str(worktree)
            )
        assert caught.value.code == "already_landed"
        assert await store.list_requests() == []
    finally:
        store.close()


async def test_a_branch_that_lands_underneath_the_queue_settles_without_verifying(
    tmp_path: Path, trunk: Path
) -> None:
    """The trunk can gain these commits between the request and its turn.

    It settles as `already_landed` rather than `landed`: nothing was refused, and
    claiming a trunk movement that did not happen would corrupt the one thing the
    ledger exists to record.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        # The operator lands it by hand while it sits in the queue.
        git(trunk, "merge", "--ff-only", "worktree-alpha")

        results = await service.tick()
        assert results[0]["state"] == "already_landed"
        assert results[0]["verified_oid"] == ""
        outcomes = [item["outcome"] for item in await store.events(row["id"])]
        assert outcomes == ["queued", "already_landed"]
    finally:
        store.close()


async def test_an_unreadable_repository_holds_and_never_reads_as_ready(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "not-a-repo"
    missing.mkdir()
    facts = await read_repository_facts(str(missing), str(missing))
    result = evaluate_preconditions(facts, branch="anything")
    assert result.disposition == "hold"
    assert not result.ready


# -- the verification gate ---------------------------------------------------


async def test_verification_refuses_to_run_until_its_exact_bytes_are_approved(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    approvals = VerifyApprovalStore(tmp_path / "data")

    unapproved = await run_worktree_verify(
        worktree, {}, approvals, project_root=str(trunk), request_id="req"
    )
    assert unapproved.status == "unapproved"
    assert unapproved.exit_code is None

    approve(approvals, worktree, trunk)
    passed = await run_worktree_verify(
        worktree, {}, approvals, project_root=str(trunk), request_id="req"
    )
    assert passed.status == "passed"
    assert passed.exit_code == 0


async def test_editing_the_verification_script_un_approves_it(
    tmp_path: Path, trunk: Path
) -> None:
    """An agent cannot approve the command its own land runs."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    approvals = VerifyApprovalStore(tmp_path / "data")
    approve(approvals, worktree, trunk)

    (worktree / ".worktree-verify").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n"
    )
    info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
    assert info.approved is False
    assert info.previously_approved is True

    result = await run_worktree_verify(
        worktree, {}, approvals, project_root=str(trunk), request_id="req"
    )
    assert result.status == "unapproved"
    assert "changed since it was approved" in (result.error or "")


async def test_a_granted_project_runs_a_gate_its_own_agents_edited(
    tmp_path: Path, trunk: Path
) -> None:
    """The whole point of `land_verify_grant`: a branch that edits the gate still lands.

    Nothing here is approved. The bytes were written in this checkout by this
    repository's own identity, and that plus the Project's standing authority is what
    lets them run - which is the case that used to stall every parallel wave that
    touched the script.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, noise="edited gate")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk, verify_grant="granted")
    try:
        info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
        assert info.approved is False
        row = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "landed"

        events = await store.events(row["id"])
        bypassed = [item for item in events if item["outcome"] == "approval_bypassed"]
        assert len(bypassed) == 1
        assert bypassed[0]["detail"]["verdict"] == "local_author"
        # The trail's half of the trade: what ran is readable afterwards, because it
        # was never read before.
        assert "edited gate" in bypassed[0]["detail"]["diff"]
        # And the run says which authority carried it, rather than leaving a reader to
        # reconstruct that from a grant that may since have moved.
        verify = [
            item
            for item in events
            if item["step"] == "verify" and item["outcome"] == "passed"
        ]
        assert verify[0]["detail"]["approval"] == "bypassed"
        # Nothing durable was granted. Lowering the authority takes the permission away
        # completely rather than leaving an auto-approved digest standing.
        assert approvals.ever_approved(str(trunk)) is False
    finally:
        store.close()


async def test_the_same_branch_is_refused_when_the_project_approves_each_digest(
    tmp_path: Path, trunk: Path
) -> None:
    """The switch is load-bearing, not decoration."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, noise="edited gate")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    queue = FakeQueue()
    service, store, _ = build_service(tmp_path, trunk, queue=queue, verify_grant="draft")
    try:
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin_session_id="sess_1",
        )
        assert (await service.tick())[0]["state"] == "refused"
        refused = [
            item
            for item in await store.events(row["id"])
            if item["outcome"] == "refused"
        ]
        assert refused[0]["detail"]["code"] == "unapproved"
        assert "approves the gate's bytes individually" in queue.messages[0]["body"]
    finally:
        store.close()


async def test_a_gate_someone_else_wrote_is_refused_even_where_agents_may_edit_it(
    tmp_path: Path, trunk: Path
) -> None:
    """The reason the grant alone is not the whole authority.

    A branch can arrive from a contributor now, and its `.worktree-verify` is branch
    content the daemon would otherwise execute unattended. The Project says agents may
    change the gate; the provenance says these bytes are not its agents'.
    """
    worktree = add_worktree(trunk, "alpha")
    script = worktree / ".worktree-verify"
    script.write_text(
        "#!/usr/bin/env bash\necho 'theirs'\nexit 0\n", encoding="utf-8", newline="\n"
    )
    script.chmod(0o755)
    git(worktree, "add", ".worktree-verify")
    git(
        worktree,
        "-c",
        "user.email=stranger@example.invalid",
        "-c",
        "user.name=Stranger",
        "commit",
        "-m",
        "helpful change",
    )
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    queue = FakeQueue()
    service, store, _ = build_service(tmp_path, trunk, queue=queue, verify_grant="granted")
    try:
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin_session_id="sess_1",
        )
        assert (await service.tick())[0]["state"] == "refused"
        events = await store.events(row["id"])
        assert not [item for item in events if item["outcome"] == "approval_bypassed"]
        refused = [item for item in events if item["outcome"] == "refused"]
        assert refused[0]["detail"]["verify_provenance"] == "foreign_author"
        # A refusal inside a Project that grants the authority is unreadable without
        # saying which of the two decided it.
        body = queue.messages[0]["body"]
        assert "does let agents change the gate" in body
        assert "stranger@example.invalid" in body
    finally:
        store.close()


async def test_a_refusal_keeps_the_message_it_wrote_even_with_nobody_to_send_it_to(
    tmp_path: Path, trunk: Path
) -> None:
    """An operator's Land has no origin session, so the explanation went nowhere.

    The queue composes one bounded message per outcome and hands it to the session that
    asked; `_solicited_reply` drops it when there is none. The requester standing in
    front of the queue was the only one who never saw why it stopped, and the trail said
    only that something had been said.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, noise="edited gate")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    queue = FakeQueue()
    service, store, _ = build_service(tmp_path, trunk, queue=queue, verify_grant="draft")
    try:
        row = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "refused"
        # Nothing was staged, because there is no session that asked.
        assert queue.messages == []
        refused = [
            item for item in await store.events(row["id"]) if item["outcome"] == "refused"
        ]
        body = refused[0]["detail"]["body"]
        assert "was refused" in body
        assert row["branch"] in body
        assert "not a problem with your branch" in body
    finally:
        store.close()


async def test_a_fast_forward_refusal_names_the_two_object_ids_a_reader_needs(
    tmp_path: Path, trunk: Path
) -> None:
    """"Diverging branches can't be fast-forwarded" says nothing a reader can act on.

    The cause is always that the trunk moved past the base the request was enqueued on,
    and both object ids are on the row - so naming them turns the message into "you were
    at X, the trunk is at Y, merge it and ask again" instead of a git error string an
    operator has to go and reconstruct by hand.
    """
    worktree = add_worktree(trunk, "alpha")
    service, store, _ = build_service(tmp_path, trunk)
    try:
        row = {
            "id": "lnd_x",
            "branch": "worktree-alpha",
            "kind": "land",
            "worktree_root": str(worktree),
            "project_root": str(trunk),
            "trunk_ref": "main",
            "requested_oid": "66200fbaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        }
        body = service._refused_body(
            row,
            reason="the fast-forward was refused: Diverging branches can't be fast-forwarded",
            detail={"trunk_before": "2130b4daaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        )
        assert "66200fbaaaaa" in body
        assert "2130b4daaaaa" in body
        assert "request the land again" in body
    finally:
        store.close()


async def test_a_refusal_the_trunk_has_since_absorbed_stops_speaking(
    tmp_path: Path, trunk: Path
) -> None:
    """The branch landed by hand, which is what a fast-forward refusal tells you to do.

    Until this, a refusal could only be answered by a *later request for the same
    branch*, so landing outside the queue left the refusal standing forever over work
    the trunk already had. Nothing is written back: `refused` stays terminal and the
    trail goes on saying the refusal happened; what changes is the reading.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, noise="edited gate")
    tip = commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, _ = build_service(tmp_path, trunk, verify_grant="draft")
    try:
        row = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "refused"
        snapshot = await service.status(project_id="p", project_root=row["project_root"])
        assert [item["absorbed_by_trunk"] for item in snapshot["requests"]] == [False]

        # Landed by hand, exactly as the refusal's own message tells its reader to.
        git(trunk, "merge", "--ff-only", "worktree-alpha")
        assert git(trunk, "rev-parse", "HEAD") == tip

        snapshot = await service.status(project_id="p", project_root=row["project_root"])
        assert [item["absorbed_by_trunk"] for item in snapshot["requests"]] == [True]
        # And the record is untouched: an audit does not stop saying what happened.
        assert (await store.get(row["id"]) or {})["state"] == "refused"
    finally:
        store.close()


async def test_approving_the_bytes_re_queues_the_land_the_block_ended(
    tmp_path: Path, trunk: Path
) -> None:
    """The operator's complaint: clearing the block left the request dead.

    A refusal is terminal, so approving the gate used to fix the *next* land and leave
    this one needing to be asked for again by hand.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, noise="edited gate")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk, verify_grant="draft")
    try:
        first = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "refused"

        info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
        assert info.digest is not None
        approvals.approve(str(trunk), info.digest, snapshot=info.current_source)
        resumed = await service.resume_verification_blocked(
            project_id="p",
            project_root=first["project_root"],
            worktree_root=str(worktree),
            digest=info.digest,
        )
        assert len(resumed) == 1
        # A redo is a new id: the refusal stays terminal so the trail goes on saying it
        # happened, and the new row names the old one rather than reopening it.
        assert resumed[0]["id"] != first["id"]
        assert resumed[0]["state"] == "queued"
        opening = [
            item for item in await store.events(resumed[0]["id"]) if item["step"] == "request"
        ]
        assert opening[0]["detail"]["resumed_from"] == first["id"]
        assert (await store.get(first["id"]) or {})["state"] == "refused"

        assert (await service.tick())[0]["state"] == "landed"
    finally:
        store.close()


async def test_a_resume_only_revives_what_the_approval_actually_covered(
    tmp_path: Path, trunk: Path
) -> None:
    """Approving one worktree's copy says nothing about another's, exactly as the
    digest-scoped store says. A resume that ignored the digest would queue a land whose
    own bytes are still unapproved."""
    alpha = add_worktree(trunk, "alpha")
    beta = add_worktree(trunk, "beta")
    write_verify(alpha, noise="alpha gate")
    write_verify(beta, noise="beta gate")
    commit(alpha, "alpha.txt", "alpha\n", "alpha work")
    commit(beta, "beta.txt", "beta\n", "beta work")
    service, store, approvals = build_service(tmp_path, trunk, verify_grant="draft")
    try:
        for worktree in (alpha, beta):
            await service.request(
                project_id="p", project_root=str(trunk), worktree_root=str(worktree)
            )
        # Two refusals, one per sweep: the queue runs one request per trunk at a time.
        assert (await service.tick())[0]["state"] == "refused"
        assert (await service.tick())[0]["state"] == "refused"

        info = describe_verify_command(alpha, {}, approvals, project_root=str(trunk))
        assert info.digest is not None
        approvals.approve(str(trunk), info.digest, snapshot=info.current_source)
        resumed = await service.resume_verification_blocked(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(alpha),
            digest=info.digest,
        )
        assert [row["branch"] for row in resumed] == ["worktree-alpha"]
    finally:
        store.close()


async def test_a_refusal_a_later_request_already_answered_is_not_resumed(
    tmp_path: Path, trunk: Path
) -> None:
    """The same supersession rule the strip draws its blocked gates from.

    A branch that has since been answered is not waiting on this approval, and reviving
    it would queue a land nobody asked for.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, noise="edited gate")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk, verify_grant="draft")
    try:
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "refused"

        info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
        assert info.digest is not None
        approvals.approve(str(trunk), info.digest, snapshot=info.current_source)
        # A later request for the same branch that got its own answer.
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "landed"

        assert await service.resume_verification_blocked(
            project_id="p", project_root=str(trunk), digest=info.digest
        ) == []
    finally:
        store.close()


async def test_a_resume_spends_no_budget_and_asks_no_second_approval(
    tmp_path: Path, trunk: Path
) -> None:
    """The operator's approval started this one.

    Charging the agent for it would let a blocked branch burn an hour's allowance by
    being approved, and re-drafting it would ask a human the question they answered
    when the request was made. Both checks decide whether a *new* agent request should
    start; a resume is neither new nor the agent's.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, noise="edited gate")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")

    class OneRequest(FakeConfig):
        land_hourly_budget = 1

    drafts: list[dict[str, Any]] = []
    service, store, approvals = build_service(
        tmp_path, trunk, config=OneRequest(), verify_grant="draft", drafts=drafts
    )
    try:
        first = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        assert (await service.tick())[0]["state"] == "refused"
        # The one request this session was allowed has been spent.
        with pytest.raises(LandRefusal, match="budget_exhausted|requests in the last hour"):
            await service.request(
                project_id="p",
                project_root=str(trunk),
                worktree_root=str(worktree),
                origin="agent",
                origin_session_id="sess_1",
            )

        info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
        assert info.digest is not None
        approvals.approve(str(trunk), info.digest, snapshot=info.current_source)
        resumed = await service.resume_verification_blocked(
            project_id="p", project_root=first["project_root"], digest=info.digest
        )
        assert len(resumed) == 1
        assert resumed[0]["state"] == "queued"
        # It keeps the agent's identity, so the verdict still reaches the session that
        # asked - it simply is not charged to it.
        assert resumed[0]["origin"] == "agent"
        assert resumed[0]["origin_session_id"] == "sess_1"
    finally:
        store.close()


async def test_a_resume_enqueues_where_a_fresh_agent_request_would_draft(
    tmp_path: Path, trunk: Path
) -> None:
    """Lowering `land_grant` to `draft` between the refusal and the approval must not
    turn the resume into a second approval request: a human already decided this one."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    drafts: list[dict[str, Any]] = []
    service, store, _ = build_service(tmp_path, trunk, grant="draft", drafts=drafts)
    try:
        drafted = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        assert drafted["state"] == "draft_requested"
        assert len(drafts) == 1

        resumed = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
            resumed_from="lnd_earlier",
        )
        assert resumed["state"] == "queued"
        assert len(drafts) == 1
    finally:
        store.close()


async def test_a_config_command_and_a_script_are_separate_authorities(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    approvals = VerifyApprovalStore(tmp_path / "data")
    approve(approvals, worktree, trunk)

    values = {"worktree": {"verify_command": "exit 0"}}
    info = describe_verify_command(worktree, values, approvals, project_root=str(trunk))
    assert info.source == "project_config"
    assert info.approved is False


async def test_a_failing_gate_reports_its_real_exit_code(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, exit_code=3, noise="boom")
    approvals = VerifyApprovalStore(tmp_path / "data")
    approve(approvals, worktree, trunk)
    result = await run_worktree_verify(
        worktree, {}, approvals, project_root=str(trunk), request_id="req"
    )
    assert result.status == "failed"
    assert result.exit_code == 3
    assert b"boom" in result.output


async def test_approving_one_copy_never_un_approves_another(
    tmp_path: Path, trunk: Path
) -> None:
    """The loop that made two landings block each other, and its fix.

    The gate resolves per worktree, so two checkouts of one Project can present two
    different sets of bytes. The store used to hold one slot per Project root, which
    meant approving the second silently withdrew the first - and with a land queued on
    each, the operator approved them in turn forever (observed 2026-08-21). Approving
    bytes now says only what it says.
    """
    alpha = add_worktree(trunk, "alpha")
    beta = add_worktree(trunk, "beta")
    write_verify(alpha, noise="alpha gate")
    write_verify(beta, noise="beta gate")
    approvals = VerifyApprovalStore(tmp_path / "data")

    approve(approvals, alpha, trunk)
    approve(approvals, beta, trunk)

    for worktree in (alpha, beta):
        info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
        assert info.approved is True, worktree.name
        result = await run_worktree_verify(
            worktree, {}, approvals, project_root=str(trunk), request_id="req"
        )
        assert result.status == "passed", worktree.name


def test_a_single_slot_trust_file_still_grants_what_it_granted(
    tmp_path: Path, trunk: Path
) -> None:
    """A store written by an older daemon keeps its authority, and reading never rewrites it.

    An approval store that migrated itself on read would be writing authority as a side
    effect of answering a question about it.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    approvals = VerifyApprovalStore(tmp_path / "data")
    info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
    assert info.digest is not None
    approvals.path.parent.mkdir(parents=True, exist_ok=True)
    approvals.path.write_text(
        json.dumps(
            {str(Path(trunk).resolve()): {"digest": info.digest, "snapshot": "old bytes"}}
        ),
        encoding="utf-8",
    )

    assert approvals.is_approved(str(trunk), info.digest) is True
    assert approvals.approved_snapshot(str(trunk)) == "old bytes"
    assert json.loads(approvals.path.read_text(encoding="utf-8")) == {
        str(Path(trunk).resolve()): {"digest": info.digest, "snapshot": "old bytes"}
    }

    # The next approval carries the old grant forward into the new shape rather than
    # replacing it, which is the whole point of the change.
    approvals.approve(str(trunk), "b" * 64, snapshot="new bytes")
    assert approvals.is_approved(str(trunk), info.digest) is True
    assert approvals.is_approved(str(trunk), "b" * 64) is True
    assert approvals.approved_snapshot(str(trunk)) == "new bytes"


def test_retained_approvals_are_capped_and_the_oldest_is_withdrawn(tmp_path: Path) -> None:
    """Bounded authority. The eviction is a real un-approval, so it is oldest-first."""
    approvals = VerifyApprovalStore(tmp_path / "data")
    root = str(tmp_path / "project")
    digests = [f"{index:064x}" for index in range(MAX_APPROVED_DIGESTS + 2)]
    for digest in digests:
        approvals.approve(root, digest, snapshot=f"bytes {digest[-2:]}")

    assert approvals.is_approved(root, digests[0]) is False
    assert approvals.is_approved(root, digests[1]) is False
    assert all(approvals.is_approved(root, digest) for digest in digests[2:])
    # Only the newest keeps its bytes: the snapshot answers "what changed since you
    # approved", which is asked against the last thing approved.
    assert approvals.approved_snapshot(root) == f"bytes {digests[-1][-2:]}"
    assert approvals.approved_digest(root) == digests[-1]


def test_revoking_withdraws_exactly_what_it_names(tmp_path: Path) -> None:
    approvals = VerifyApprovalStore(tmp_path / "data")
    root = str(tmp_path / "project")
    approvals.approve(root, "a" * 64, snapshot="a")
    approvals.approve(root, "b" * 64, snapshot="b")

    approvals.revoke(root, "a" * 64)
    assert approvals.is_approved(root, "a" * 64) is False
    assert approvals.is_approved(root, "b" * 64) is True

    approvals.revoke(root)
    assert approvals.ever_approved(root) is False


def test_a_handback_excerpt_is_bounded_and_redacted() -> None:
    body = handback_excerpt(
        b"line one\nexport TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nline three\n"
    )
    assert "ghp_" not in body
    assert "line three" in body
    long = handback_excerpt(b"x" * 20_000, limit=200)
    assert len(long) < 400
    assert "earlier output omitted" in long


# -- the store ---------------------------------------------------------------


async def test_one_branch_cannot_be_queued_twice(tmp_path: Path) -> None:
    store = LandStore(tmp_path / "land.sqlite3")
    try:
        await store.enqueue(
            project_id="p",
            project_root="/repo",
            worktree_root="/wt",
            branch="feature",
            requested_oid="a" * 40,
            trunk_ref="main",
        )
        with pytest.raises(LandConflict):
            await store.enqueue(
                project_id="p",
                project_root="/repo",
                worktree_root="/wt",
                branch="feature",
                requested_oid="b" * 40,
                trunk_ref="main",
            )
    finally:
        store.close()


async def test_one_trunk_cannot_have_two_steps_in_flight(tmp_path: Path) -> None:
    """Serialisation is a property of the schema, not of the worker's care."""
    store = LandStore(tmp_path / "land.sqlite3")
    try:
        first = await store.enqueue(
            project_id="p",
            project_root="/repo",
            worktree_root="/wt-a",
            branch="a",
            requested_oid="a" * 40,
            trunk_ref="main",
        )
        second = await store.enqueue(
            project_id="p",
            project_root="/repo",
            worktree_root="/wt-b",
            branch="b",
            requested_oid="b" * 40,
            trunk_ref="main",
        )
        await store.transition(first["id"], expect=("queued",), state="reconciling")
        with pytest.raises(LandConflict):
            await store.transition(second["id"], expect=("queued",), state="reconciling")
    finally:
        store.close()


async def test_a_restart_returns_an_orphaned_step_to_the_queue(tmp_path: Path) -> None:
    store = LandStore(tmp_path / "land.sqlite3")
    try:
        row = await store.enqueue(
            project_id="p",
            project_root="/repo",
            worktree_root="/wt",
            branch="a",
            requested_oid="a" * 40,
            trunk_ref="main",
        )
        await store.transition(row["id"], expect=("queued",), state="verifying")
        recovered = await store.restore()
        assert [item["id"] for item in recovered] == [row["id"]]
        assert (await store.get(row["id"]))["state"] == "queued"
    finally:
        store.close()


# -- the pipeline ------------------------------------------------------------


async def test_a_clean_branch_reconciles_verifies_and_fast_forwards(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    tip = commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert row["state"] == "queued"
        results = await service.tick()
        assert results[0]["state"] == "landed"
        assert git(trunk, "rev-parse", "HEAD") == tip
        events = await store.events(row["id"])
        steps = [item["step"] for item in events]
        assert steps == ["request", "reconcile", "classify", "verify", "land"]
        # The classification is recorded on the ordinary path too, not only when it
        # skips something: a trail that names the gate only when it was skipped cannot
        # be read as "which gate ran", only as "did anything unusual happen".
        classified = next(item for item in events if item["step"] == "classify")
        assert classified["outcome"] == "full"
        assert "alpha.txt" in classified["detail"]["disqualifying"]
        assert (await store.get(row["id"]))["verify_gate"] == "full"
    finally:
        store.close()


async def test_the_gate_result_becomes_a_tier0_test_fact(
    tmp_path: Path, trunk: Path
) -> None:
    """The land gate is the only test run most branches ever get.

    It runs out-of-band, so no tool call and no transcript records it: one
    `test_result` fact stood against 4,485 `command_result` facts in a measured
    24-hour window (2026-08-21), and declared-vs-verified could only ever answer
    "nothing verified" about a substrate rather than about an agent.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    facts: list[dict[str, Any]] = []
    service, store, approvals = build_service(tmp_path, trunk, facts=facts)
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
            origin_run_id="run_1",
        )
        await service.tick()
        verified = [item for item in facts if item["kind"] == "test_result"]
        assert len(verified) == 1
        outcome = verified[0]["detail"]["test_outcome"]
        assert verified[0]["agent_run_id"] == "run_1"
        assert outcome["failed"] == 0
        assert outcome["failing_tests"] == []  # a green gate names no failures
    finally:
        store.close()


async def test_a_failed_gate_never_records_an_empty_failing_set(
    tmp_path: Path, trunk: Path
) -> None:
    # `failing_tests: []` is read by every consumer as "nothing is failing". A gate
    # that fell over on ruff or tsc names no tests, so it must omit the key and
    # state a failure count instead — the distinction between "no failures" and
    # "failures not enumerated" is the whole value of the field.
    from swe_mux.land_queue import verify_test_outcome

    class _Outcome:
        passed = False
        status = "failed"
        exit_code = 1
        output = b"ruff: 3 errors\n"

    outcome = verify_test_outcome(_Outcome())
    assert outcome["failed"] >= 1
    assert "failing_tests" not in outcome


async def test_a_second_branch_reconciles_against_the_first_result(
    tmp_path: Path, trunk: Path
) -> None:
    """The `advance` rule: one landing must not strand the other agents."""
    alpha = add_worktree(trunk, "alpha")
    beta = add_worktree(trunk, "beta")
    write_verify(alpha)
    write_verify(beta)
    commit(alpha, "alpha.txt", "alpha\n", "alpha work")
    commit(beta, "beta.txt", "beta\n", "beta work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, alpha, trunk)
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(alpha)
        )
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(beta)
        )
        first = await service.tick()
        assert [item["state"] for item in first] == ["landed"]

        # Beta's verification script is the same bytes, so the same approval covers it.
        second = await service.tick()
        assert [item["state"] for item in second] == ["landed"]
        log = git(trunk, "log", "--oneline")
        assert "alpha work" in log
        assert "beta work" in log
    finally:
        store.close()


# -- the documentation-only fast path ----------------------------------------
#
# Every test here approves a gate that **fails**, which is the only way to prove a skip
# is a skip: a passing gate lands either way, so it cannot tell "the classifier skipped
# it" apart from "the gate ran and was quick".


def docs_trunk(trunk_root: Path) -> None:
    """Put the verification script on the trunk, where a branch inherits it.

    `write_verify` commits into whatever checkout it is handed, so calling it on a
    worktree puts `.worktree-verify` in that branch's *incoming* change set - which is
    not documentation, and would classify every branch in this section as mixed.
    """
    write_verify(trunk_root, exit_code=1, noise="the gate must not have run")


async def test_a_documentation_only_branch_lands_without_running_the_gate(
    tmp_path: Path, trunk: Path
) -> None:
    docs_trunk(trunk)
    worktree = add_worktree(trunk, "alpha")
    tip = commit(worktree, "NOTES.md", "words\n", "docs work")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        results = await service.tick()
        # The approved gate exits 1. Reaching `landed` is therefore proof it never ran.
        assert results[0]["state"] == "landed"
        assert results[0]["verify_gate"] == "docs_only"
        assert git(trunk, "rev-parse", "HEAD") == tip
        assert queue.messages == []

        events = await store.events(row["id"])
        assert [item["step"] for item in events] == [
            "request", "reconcile", "classify", "verify", "land",
        ]
        classified = next(item for item in events if item["step"] == "classify")
        assert classified["outcome"] == "docs_only"
        assert classified["detail"]["paths"] == ["NOTES.md"]
        assert classified["detail"]["disqualifying"] == []
        assert "are documentation" in classified["reason"]
        # The verify step is still *in* the trail rather than absent from it. An
        # absent step is the shape a silent skip would take, so the skip is recorded
        # as an outcome of the step it replaced.
        skipped = next(item for item in events if item["step"] == "verify")
        assert skipped["outcome"] == "skipped"
        assert skipped["reason"] == classified["reason"]
    finally:
        store.close()


async def test_one_source_file_puts_the_branch_back_on_the_full_gate(
    tmp_path: Path, trunk: Path
) -> None:
    """The mixed case, which is every case the allowlist does not cover completely."""
    docs_trunk(trunk)
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "NOTES.md", "words\n", "docs work")
    commit(worktree, "alpha.txt", "alpha\n", "code work")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        results = await service.tick()
        assert results[0]["state"] == "handed_back"
        assert results[0]["verify_gate"] == "full"
        assert "exit code 1" in results[0]["reason"]
        assert "the gate must not have run" in queue.messages[0]["body"]
        assert git(trunk, "log", "--oneline", "-1").endswith("add verification")

        classified = next(
            item for item in await store.events(row["id"]) if item["step"] == "classify"
        )
        assert classified["outcome"] == "full"
        assert classified["detail"]["disqualifying"] == ["alpha.txt"]
    finally:
        store.close()


async def test_the_trunks_own_source_commits_do_not_disqualify_a_docs_branch(
    tmp_path: Path, trunk: Path
) -> None:
    """The classification is of what the *trunk gains*, not of what the branch contains.

    The reconcile merges the trunk into the branch, so after it the branch's history
    holds the trunk's source commits too. Classifying the branch's whole history would
    read those as incoming code and put every documentation branch back on the full
    gate the moment anybody else landed anything.
    """
    docs_trunk(trunk)
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "NOTES.md", "words\n", "docs work")
    commit(trunk, "engine.py", "x = 1\n", "trunk source work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        results = await service.tick()
        assert results[0]["state"] == "landed"
        assert results[0]["verify_gate"] == "docs_only"
        classified = next(
            item for item in await store.events(row["id"]) if item["step"] == "classify"
        )
        assert classified["detail"]["paths"] == ["NOTES.md"]
    finally:
        store.close()


async def test_a_docs_branch_still_hands_back_a_conflict(tmp_path: Path, trunk: Path) -> None:
    """The fast path replaces the gate and nothing else.

    Reconcile runs first and is unchanged, so a documentation branch that conflicts is
    handed back exactly like any other - the classification never happens, because
    there is nothing yet to classify.
    """
    docs_trunk(trunk)
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "NOTES.md", "from the branch\n", "branch docs")
    commit(trunk, "NOTES.md", "from the trunk\n", "trunk docs")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        results = await service.tick()
        assert results[0]["state"] == "handed_back"
        assert results[0]["verify_gate"] == ""
        steps = [item["step"] for item in await store.events(row["id"])]
        assert "classify" not in steps
    finally:
        store.close()


async def test_a_conflict_hands_back_and_leaves_the_worktree_alone(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "shared.txt", "from the branch\n", "branch edit")
    commit(trunk, "shared.txt", "from the trunk\n", "trunk edit")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        results = await service.tick()
        assert results[0]["state"] == "handed_back"
        assert "conflicted" in results[0]["reason"]
        assert "shared.txt" in results[0]["detail"]["paths"]

        # The worktree is exactly as it was found: no merge left in progress.
        assert git(worktree, "status", "--porcelain") == ""
        assert not (Path(git(worktree, "rev-parse", "--absolute-git-dir")) / "MERGE_HEAD").exists()

        assert len(queue.messages) == 1
        message = queue.messages[0]
        assert message["sender_kind"] == "rule"
        assert message["target_session_id"] == "sess_1"
        assert "shared.txt" in message["body"]
        assert message["correlation_id"] == row["correlation_id"]
    finally:
        store.close()


async def test_a_verification_failure_hands_back_with_its_output(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, exit_code=1, noise="two tests failed")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        results = await service.tick()
        assert results[0]["state"] == "handed_back"
        assert "exit code 1" in results[0]["reason"]
        assert "two tests failed" in queue.messages[0]["body"]
        # The trunk never moved.
        assert git(trunk, "log", "--oneline", "-1").endswith("initial")
    finally:
        store.close()


async def test_an_unapproved_gate_refuses_rather_than_handing_back(
    tmp_path: Path, trunk: Path
) -> None:
    """Approval is the operator's business, so it does not become the agent's task.

    The agent is still *told*, which is the half this used to get wrong: it refused
    silently, so a session that asked to land and went quiet - which is what waiting for
    a land is - sat idle while its request died. The message says the branch is not the
    problem and that a human presses approve, which is precisely what keeps the approval
    from becoming the agent's task while it reads.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    queue = FakeQueue()
    service, store, _ = build_service(tmp_path, trunk, queue=queue)
    try:
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
            origin_run_id="run_1",
        )
        results = await service.tick()
        assert results[0]["state"] == "refused"
        assert "not approved" in results[0]["reason"]
        assert results[0]["detail"]["code"] == "unapproved"
        # The row's own spelling of the root, so the strip can offer *that* checkout's
        # copy for approval without re-deriving which one refused.
        assert results[0]["detail"]["worktree_root"] == results[0]["worktree_root"]
        assert len(queue.messages) == 1
        body = queue.messages[0]["body"]
        assert "was refused" in body
        assert "not a problem with your branch" in body
        assert "You cannot approve it yourself" in body
        # The same armed channel a handback rides, under the same bounds: this is the
        # session's own request being answered.
        assert queue.messages[0]["armed"] is True
        assert queue.messages[0]["solicited_by"] == results[0]["id"]
    finally:
        store.close()


async def test_a_busy_worktree_waits_and_then_lands(tmp_path: Path, trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    tip = commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    busy: list[str] = ["sess_1"]

    store = LandStore(tmp_path / "land.sqlite3")
    approvals = VerifyApprovalStore(tmp_path / "data")

    async def busy_sessions(_root: str) -> tuple[str, ...]:
        return tuple(busy)

    async def project_values(_root: str) -> dict[str, Any]:
        return {}

    service = LandQueueService(
        store=store,
        approvals=approvals,
        config=FakeConfig(),
        grant_field=lambda _root: "granted",
        project_values=project_values,
        busy_sessions=busy_sessions,
    )
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        held = await service.tick()
        assert held[0]["state"] == "waiting"
        assert "still working" in held[0]["reason"]
        assert git(trunk, "rev-parse", "HEAD") != tip

        busy.clear()
        landed = await service.tick()
        assert landed[0]["state"] == "landed"
        assert git(trunk, "rev-parse", "HEAD") == tip
    finally:
        store.close()


async def test_a_hold_that_never_clears_hands_back(tmp_path: Path, trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")

    class Impatient(FakeConfig):
        land_hold_timeout_seconds = 60.0

    queue = FakeQueue()
    now = [1_000_000.0]
    store = LandStore(tmp_path / "land.sqlite3")
    approvals = VerifyApprovalStore(tmp_path / "data")

    async def busy_sessions(_root: str) -> tuple[str, ...]:
        return ("sess_1",)

    async def project_values(_root: str) -> dict[str, Any]:
        return {}

    service = LandQueueService(
        store=store,
        approvals=approvals,
        config=Impatient(),
        grant_field=lambda _root: "granted",
        project_values=project_values,
        busy_sessions=busy_sessions,
        queue_message=queue,
        clock=lambda: now[0],
    )
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        assert (await service.tick())[0]["state"] == "waiting"
        now[0] += 3600.0
        timed_out = await service.tick()
        assert timed_out[0]["state"] == "handed_back"
        assert "could not start" in timed_out[0]["reason"]
    finally:
        store.close()


async def test_a_diverged_trunk_refuses_the_fast_forward_and_never_forces(
    tmp_path: Path, trunk: Path
) -> None:
    """The whole safety proof: Git refuses, and a refusal is reported, not retried."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )

        # The trunk gains a commit the branch has not reconciled with, after the
        # branch was queued. The reconcile absorbs it, so force divergence by making
        # the trunk move again between verify and land.
        original_land = service._land
        moved: list[str] = []

        async def move_then_land(row: dict[str, Any]) -> dict[str, Any]:
            if not moved:
                moved.append(commit(trunk, "trunk.txt", "trunk\n", "trunk edit"))
            return await original_land(row)

        service._land = move_then_land  # type: ignore[method-assign]
        results = await service.tick()
        assert results[0]["state"] == "refused"
        assert "fast-forward was refused" in results[0]["reason"]
        assert git(trunk, "rev-parse", "HEAD") == moved[0]
    finally:
        store.close()


async def test_a_branch_that_moved_after_verifying_is_refused(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        original_verify = service._verify

        async def verify_then_move(row: dict[str, Any]) -> dict[str, Any] | None:
            verified = await original_verify(row)
            commit(worktree, "late.txt", "late\n", "a commit after verifying")
            return verified

        service._verify = verify_then_move  # type: ignore[method-assign]
        results = await service.tick()
        assert results[0]["state"] == "refused"
        assert "moved after it verified" in results[0]["reason"]
        assert git(trunk, "log", "--oneline", "-1").endswith("initial")
    finally:
        store.close()


# -- what a running gate reports about itself --------------------------------


def write_stepped_verify(
    worktree: Path, *, steps: tuple[str, ...], exit_code: int = 0, pause: str = ""
) -> None:
    """A gate that announces its own steps, in this repository's own `step()` shape."""
    lines = ["#!/usr/bin/env bash", "set -e", "step() { printf '\\n=== %s ===\\n' \"$*\" >&2; }"]
    for name in steps:
        lines.append(f'step "{name}"')
        lines.append("echo working")
        if pause:
            lines.append(f"sleep {pause}")
    lines.append(f"exit {exit_code}")
    script = worktree / ".worktree-verify"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    script.chmod(0o755)
    git(worktree, "add", ".worktree-verify")
    git(worktree, "commit", "-m", "add stepped verification")


async def test_a_passing_gate_records_its_steps_for_the_next_run_to_be_measured_against(
    tmp_path: Path, trunk: Path
) -> None:
    """A total is a measurement of these exact bytes, never an estimate of them."""
    worktree = add_worktree(trunk, "alpha")
    write_stepped_verify(worktree, steps=("pytest", "ruff", "mypy"))
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
        # Keyed by the trunk root the pipeline resolved, which is the one it writes and
        # reads under; the path the caller happened to type is not necessarily equal to it.
        row = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        root = row["project_root"]
        assert await store.verify_plan(root, info.digest or "") is None

        assert (await service.tick())[0]["state"] == "landed"

        plan = await store.verify_plan(root, info.digest or "")
        assert plan is not None
        assert plan["steps"] == ["pytest", "ruff", "mypy"]
        assert plan["duration_ms"] > 0
    finally:
        store.close()


async def test_a_failing_gate_records_no_plan(tmp_path: Path, trunk: Path) -> None:
    """A gate stopped by `set -e` announced a *prefix* of its steps.

    Recording that would predict a permanently shorter run, so every later gate would
    read as nearly finished from its second step onward - which is worse than showing
    no total at all.
    """
    worktree = add_worktree(trunk, "alpha")
    write_stepped_verify(worktree, steps=("pytest",), exit_code=1)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        info = describe_verify_command(worktree, {}, approvals, project_root=str(trunk))
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin_session_id="sess_1",
        )
        assert (await service.tick())[0]["state"] == "handed_back"
        assert await store.verify_plan(row["project_root"], info.digest or "") is None
        # The steps it did announce are still in the audit trail, where they describe
        # what happened rather than predicting what will.
        verify = [item for item in await store.events(row["id"]) if item["step"] == "verify"]
        assert verify[0]["detail"]["steps"] == ["pytest"]
        assert verify[0]["detail"]["output_lines"] > 0
    finally:
        store.close()


async def _watch_a_step(
    service: LandQueueService, running: asyncio.Task[Any], project_root: str
) -> dict[str, Any] | None:
    """Poll `status()` until the running gate has announced a step, or the run ends.

    Waits for a *step* rather than for any snapshot: the row enters `verifying` before
    the subprocess has printed anything, and `step_index: 0` there is the honest reading
    of a gate that has not announced one yet.
    """
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        snapshot = await service.status(project_id="p", project_root=project_root)
        for row in snapshot["requests"]:
            live = row["verify_progress"]
            if live is not None and live["step_index"] >= 1:
                return dict(live)
        if running.done():
            return None
    return None


async def test_a_recorded_plan_supplies_the_total_a_first_run_could_not(
    tmp_path: Path, trunk: Path
) -> None:
    """"step 2 of 3" only after a byte-identical run measured what 3 was.

    Two branches carrying the same gate share one plan, because the plan is keyed by the
    bytes rather than by the branch - which is also why editing the gate withdraws it.
    """
    alpha = add_worktree(trunk, "alpha")
    beta = add_worktree(trunk, "beta")
    for tree in (alpha, beta):
        write_stepped_verify(tree, steps=("pytest", "ruff", "mypy"), pause="0.3")
    commit(alpha, "alpha.txt", "alpha\n", "alpha work")
    commit(beta, "beta.txt", "beta\n", "beta work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, alpha, trunk)
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(alpha)
        )
        first = asyncio.create_task(service.tick())
        seen_first = await _watch_a_step(service, first, str(trunk))
        assert (await first)[0]["state"] == "landed"
        assert seen_first is not None
        # Nothing had measured these bytes yet, so no total was invented for them.
        assert seen_first["expected_step_count"] is None

        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(beta)
        )
        second = asyncio.create_task(service.tick())
        seen_second = await _watch_a_step(service, second, str(trunk))
        assert (await second)[0]["state"] == "landed"
        assert seen_second is not None
        assert seen_second["expected_step_count"] == 3
        assert seen_second["expected_steps"] == ["pytest", "ruff", "mypy"]
        assert seen_second["step_index"] <= 3
    finally:
        store.close()


async def test_status_reports_a_running_gate_and_only_a_running_one(
    tmp_path: Path, trunk: Path
) -> None:
    """The progress reading is a fact about a live process, so it exists only while one is.

    A snapshot left on a finished row would be a claim about a run that is over, and a
    row a restart returned to `queued` has no run at all - both would read exactly like
    a gate that is moving.
    """
    worktree = add_worktree(trunk, "alpha")
    write_stepped_verify(worktree, steps=("pytest", "ruff"), pause="0.4")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        queued = await service.status(project_id="p", project_root=str(trunk))
        assert queued["requests"][0]["verify_progress"] is None

        running = asyncio.create_task(service.tick())
        seen: dict[str, Any] | None = None
        deadline = time.monotonic() + 60
        # Waits for a *step*, not merely for a snapshot: the row enters `verifying`
        # before the subprocess has printed anything, and `step_index: 0` there is the
        # honest reading of a gate that has not announced a step yet.
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            snapshot = await service.status(project_id="p", project_root=str(trunk))
            live = snapshot["requests"][0]["verify_progress"]
            if live is not None and live["step_index"] >= 1:
                seen = live
                break
            if running.done():
                break
        results = await running

        assert results[0]["state"] == "landed"
        assert seen is not None, "the gate never reported a step while it was running"
        assert seen["step_index"] >= 1
        assert seen["step_name"] in {"pytest", "ruff"}
        assert seen["elapsed_ms"] >= 0
        # No plan on the first run of these bytes, so no total is invented for it.
        assert seen["expected_step_count"] is None
        assert seen["attempt"] == 1

        landed = await service.status(project_id="p", project_root=str(trunk))
        assert landed["requests"][0]["state"] == "landed"
        assert landed["requests"][0]["verify_progress"] is None
    finally:
        store.close()


# -- authority ---------------------------------------------------------------


async def test_a_draft_grant_starts_nothing(tmp_path: Path, trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    drafted: list[dict[str, Any]] = []

    async def draft_request(**kwargs: Any) -> dict[str, Any]:
        drafted.append(kwargs)
        return {"observation_id": "obs_1"}

    store = LandStore(tmp_path / "land.sqlite3")
    service = LandQueueService(
        store=store,
        approvals=VerifyApprovalStore(tmp_path / "data"),
        config=FakeConfig(),
        grant_field=lambda _root: "draft",
        draft_request=draft_request,
    )
    try:
        result = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        assert result["state"] == "drafted"
        assert len(drafted) == 1
        assert await store.list_requests() == []
    finally:
        store.close()


async def test_an_off_grant_refuses_an_agent_but_not_the_operator(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, _ = build_service(tmp_path, trunk, grant="off")
    try:
        with pytest.raises(LandRefusal) as caught:
            await service.request(
                project_id="p",
                project_root=str(trunk),
                worktree_root=str(worktree),
                origin="agent",
                origin_session_id="sess_1",
            )
        assert caught.value.code == "land_denied"
        operator = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert operator["state"] == "queued"
    finally:
        store.close()


async def test_the_install_switch_stops_every_land(tmp_path: Path, trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")

    class SwitchedOff(FakeConfig):
        land_queue_enabled = False

    service, store, approvals = build_service(tmp_path, trunk, config=SwitchedOff())
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert await service.tick() == []
        assert git(trunk, "log", "--oneline", "-1").endswith("initial")
        with pytest.raises(LandRefusal) as caught:
            await service.request(
                project_id="p",
                project_root=str(trunk),
                worktree_root=str(worktree),
                origin="agent",
                origin_session_id="sess_1",
            )
        assert caught.value.code == "automation_disabled"
    finally:
        store.close()


async def test_the_hourly_budget_bounds_a_runaway_requester(
    tmp_path: Path, trunk: Path
) -> None:
    class Tight(FakeConfig):
        land_hourly_budget = 1

    alpha = add_worktree(trunk, "alpha")
    beta = add_worktree(trunk, "beta")
    commit(alpha, "alpha.txt", "alpha\n", "alpha work")
    commit(beta, "beta.txt", "beta\n", "beta work")
    service, store, _ = build_service(tmp_path, trunk, config=Tight())
    try:
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(alpha),
            origin="agent",
            origin_session_id="sess_1",
        )
        with pytest.raises(LandRefusal) as caught:
            await service.request(
                project_id="p",
                project_root=str(trunk),
                worktree_root=str(beta),
                origin="agent",
                origin_session_id="sess_1",
            )
        assert caught.value.code == "budget_exhausted"
    finally:
        store.close()


# -- the handback's arming --------------------------------------------------
#
# The defect these cover, observed live twice on 2026-08-21: a conflict handback
# reached the requesting session as a `rule`-sender DRAFT, and the requester idled
# forever unaware its land had bounced, until a human pressed send. The narrowing is
# stated in `land-queue.md`: the request is the consent, and it reaches exactly as far
# as the request did and no further.


async def _bounced(
    tmp_path: Path,
    trunk: Path,
    *,
    queue: FakeQueue,
    origin: str = "agent",
    origin_session_id: str = "sess_1",
    origin_run_id: str = "run_1",
    **service_kwargs: Any,
) -> tuple[dict[str, Any], LandStore]:
    """One land that hands back, with the conflict already arranged."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "shared.txt", "branch\n", "branch edit")
    commit(trunk, "shared.txt", "trunk\n", "trunk edit")
    service, store, approvals = build_service(
        tmp_path, trunk, queue=queue, **service_kwargs
    )
    approve(approvals, worktree, trunk)
    row = await service.request(
        project_id="p",
        project_root=str(trunk),
        worktree_root=str(worktree),
        origin=origin,
        origin_session_id=origin_session_id,
        origin_run_id=origin_run_id,
    )
    results = await service.tick()
    assert results[0]["state"] == "handed_back"
    return row, store


async def test_a_handback_arms_for_the_session_that_asked(
    tmp_path: Path, trunk: Path
) -> None:
    """The whole point: the answer to a `request_land` needs no human press.

    Armed *and* naming the request that solicited it, because arming is never the
    sender's claim - `solicited_by` is what the queue's floor reads before it lets a
    `rule` sender arrive armed at all (`prompt_queue.enqueue`).
    """
    queue = FakeQueue()
    row, store = await _bounced(tmp_path, trunk, queue=queue)
    try:
        assert queue.messages[0]["target_session_id"] == "sess_1"
        assert queue.messages[0]["armed"] is True
        assert queue.messages[0]["solicited_by"] == row["id"]
        assert queue.messages[0]["sender_kind"] == "rule"
        handed = next(
            item
            for item in await store.events(row["id"])
            if item["outcome"] == "handed_back"
        )
        assert handed["detail"]["armed"] is True
    finally:
        store.close()


async def test_an_operator_land_hands_back_as_a_draft(
    tmp_path: Path, trunk: Path
) -> None:
    """No session asked, so there is no consent to spend.

    The operator's own land is started from a surface a human is already looking at;
    nothing about it authorizes an unattended write into a terminal.
    """
    queue = FakeQueue()
    row, store = await _bounced(tmp_path, trunk, queue=queue, origin="operator")
    try:
        assert queue.messages[0]["armed"] is False
        assert queue.messages[0]["solicited_by"] is None
        handed = next(
            item
            for item in await store.events(row["id"])
            if item["outcome"] == "handed_back"
        )
        assert handed["detail"]["arming_reason"] == "the request was not made by a session"
    finally:
        store.close()


async def test_the_project_switch_kills_the_unattended_handback(
    tmp_path: Path, trunk: Path
) -> None:
    """Turning the land queue off for the Project stops the unattended half too.

    Read at handback time rather than trusted from enqueue time: an operator who
    switches it off mid-flight is switching off the thing they can see, and a request
    already in the queue would otherwise keep the authority it was granted under.
    The message is still enqueued - refusing arming never refuses the message.
    """
    queue = FakeQueue()
    automations = {"land_queue"}
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "shared.txt", "branch\n", "branch edit")
    commit(trunk, "shared.txt", "trunk\n", "trunk edit")
    service, store, approvals = build_service(
        tmp_path, trunk, queue=queue, automations=automations
    )
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
            origin_run_id="run_1",
        )
        automations.clear()
        assert (await service.tick())[0]["state"] == "handed_back"
        assert len(queue.messages) == 1
        assert queue.messages[0]["armed"] is False
        handed = next(
            item
            for item in await store.events(row["id"])
            if item["outcome"] == "handed_back"
        )
        assert "not enabled for this Project" in handed["detail"]["arming_reason"]
    finally:
        store.close()


async def test_a_replaced_conversation_does_not_inherit_the_consent(
    tmp_path: Path, trunk: Path
) -> None:
    """A session that resumed into a new run is a different correspondent.

    The same run binding every auto-delivery grant carries: the predecessor asked, so
    the predecessor consented, and the conversation reading that terminal now did not.
    """
    queue = FakeQueue()
    row, store = await _bounced(
        tmp_path, trunk, queue=queue, session_runs={"sess_1": "run_2"}
    )
    try:
        assert queue.messages[0]["armed"] is False
        handed = next(
            item
            for item in await store.events(row["id"])
            if item["outcome"] == "handed_back"
        )
        assert handed["detail"]["arming_reason"] == "the requesting conversation was replaced"
    finally:
        store.close()


async def test_an_unaskable_run_fails_closed(tmp_path: Path, trunk: Path) -> None:
    """A check that could not be made is not a check that passed."""
    queue = FakeQueue()
    row, store = await _bounced(tmp_path, trunk, queue=queue, session_runs={})
    try:
        assert queue.messages[0]["armed"] is False
        handed = next(
            item
            for item in await store.events(row["id"])
            if item["outcome"] == "handed_back"
        )
        assert "could not be identified" in handed["detail"]["arming_reason"]
    finally:
        store.close()


async def test_one_request_spends_exactly_one_armed_reply(
    tmp_path: Path, trunk: Path
) -> None:
    """The cap is a number claimed atomically, not an inference from the state machine.

    Asserted against the store directly because the pipeline reaches a terminal state
    after one handback: the cap exists so a second bounded template - a completion
    notice, a future step - cannot quietly wear this authority twice.
    """
    queue = FakeQueue()
    row, store = await _bounced(tmp_path, trunk, queue=queue)
    try:
        current = await store.get(row["id"])
        assert current is not None
        assert current["armed_replies"] == 1
        assert await store.claim_armed_reply(row["id"], cap=1) is False
    finally:
        store.close()


async def test_an_open_request_is_reply_window_evidence_and_a_finished_one_is_not(
    tmp_path: Path, trunk: Path
) -> None:
    """The other half of the consent, read the way auto-delivery reads it.

    A session that asked to land goes quiet by definition, so its grant lapses exactly
    while the pipeline computes the answer and the armed handback arrives with nothing
    to deliver it. A terminal request opens no window: the answer is already written.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk, queue=FakeQueue())
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
            origin_run_id="run_1",
        )
        open_now = await service.origin_windows(["sess_1", "sess_2"], 0.0)
        assert open_now["sess_1"]["kind"] == "land"
        assert open_now["sess_1"]["request_id"] == row["id"]
        assert "sess_2" not in open_now
        # A floor above the row's own `updated_at` is a queue that has stopped moving.
        assert await service.origin_windows(["sess_1"], time.time() + 60) == {}

        assert (await service.tick())[0]["state"] == "landed"
        assert await service.origin_windows(["sess_1"], 0.0) == {}
    finally:
        store.close()


# -- verify without landing, and never running one gate twice -----------------
#
# The economy here is measurable rather than aesthetic: the gate is minutes long in this
# repository, and the observed waste was a session running it by hand and the queue
# immediately running the identical bytes over the identical tree.
#
# Every test that claims a gate was *not* run proves it by counting executions, because
# no state can tell "the gate was skipped" apart from "the gate ran and was quick".


def write_counting_verify(worktree: Path, counter: Path, *, exit_code: int = 0) -> None:
    """A gate that records each execution outside both checkouts.

    Outside on purpose: a counter written inside either tree would change the very tree
    hash the verdict is keyed on, so the instrument would destroy what it measures.
    """
    script = worktree / ".worktree-verify"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"echo run >> '{counter.as_posix()}'\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    git(worktree, "add", ".worktree-verify")
    git(worktree, "commit", "-m", "add verification")


def gate_runs(counter: Path) -> int:
    return len(counter.read_text(encoding="utf-8").splitlines()) if counter.exists() else 0


def gate_digest(worktree: Path, approvals: VerifyApprovalStore, trunk_root: Path) -> str:
    return describe_verify_command(
        worktree, {}, approvals, project_root=str(trunk_root)
    ).digest or ""


async def test_a_verify_only_request_runs_the_gate_and_moves_no_trunk(
    tmp_path: Path, trunk: Path
) -> None:
    """The whole of what a verify-only request promises, and the whole of what it does not."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    before = git(trunk, "rev-parse", "HEAD")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
        )
        assert row["kind"] == "verify"
        results = await service.tick()
        assert results[0]["state"] == "verified"
        # Not `landed`, and not a moved trunk. That is the whole of the kind.
        assert git(trunk, "rev-parse", "HEAD") == before
        steps = [item["step"] for item in await store.events(row["id"])]
        assert steps == ["request", "reconcile", "classify", "verify", "verify"]
        stored = await store.get(row["id"])
        assert stored is not None
        assert stored["verify_gate"] == "full"
        assert stored["verified_oid"] == git(worktree, "rev-parse", "HEAD")
    finally:
        store.close()


async def test_a_verify_only_failure_hands_back_and_still_moves_nothing(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, exit_code=3, noise="boom")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    before = git(trunk, "rev-parse", "HEAD")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
            origin="agent",
            origin_session_id="sess_1",
            origin_run_id="run_1",
        )
        results = await service.tick()
        assert results[0]["id"] == row["id"]
        assert results[0]["state"] == "handed_back"
        assert "exit code 3" in results[0]["reason"]
        assert git(trunk, "rev-parse", "HEAD") == before
        # The template speaks in the requester's own vocabulary: this session asked for
        # a verification, not for a land.
        assert "The verification of `worktree-alpha` stopped" in queue.messages[0]["body"]
        assert "boom" in queue.messages[0]["body"]
        # And nothing stands afterwards: only a *pass* is a verdict worth reusing.
        tree = git(worktree, "rev-parse", "HEAD^{tree}")
        digest = gate_digest(worktree, approvals, trunk)
        # Keyed by the root the pipeline recorded, which is the one Git resolved rather
        # than the one the test typed; a lookup under the other silently misses.
        assert await store.verify_memo(row["project_root"], tree, digest) is None
    finally:
        store.close()


async def test_a_land_reuses_a_verify_only_green_over_the_same_tree(
    tmp_path: Path, trunk: Path
) -> None:
    """The saving, proven by counting: two requests, one gate execution.

    The reuse is also *in the trail with its key*, because a skipped gate that left no
    trace is indistinguishable from one that ran - the same rule the documentation fast
    path is held to.
    """
    counter = tmp_path / "gate-runs.log"
    worktree = add_worktree(trunk, "alpha")
    write_counting_verify(worktree, counter)
    tip = commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    tree = git(worktree, "rev-parse", "HEAD^{tree}")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
        )
        assert (await service.tick())[0]["state"] == "verified"
        assert gate_runs(counter) == 1

        landing = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "landed"
        assert git(trunk, "rev-parse", "HEAD") == tip
        # The gate did not run a second time.
        assert gate_runs(counter) == 1

        stored = await store.get(landing["id"])
        assert stored is not None
        assert stored["verify_gate"] == "reused"
        reused = next(
            item for item in await store.events(landing["id"]) if item["step"] == "verify"
        )
        assert reused["outcome"] == "reused"
        assert reused["detail"]["tree"] == tree
        assert reused["detail"]["source_kind"] == "verify"
        assert reused["detail"]["digest"] == gate_digest(worktree, approvals, trunk)
    finally:
        store.close()


async def test_a_moved_trunk_makes_the_land_verify_again(
    tmp_path: Path, trunk: Path
) -> None:
    """A new tree is a miss, and that is correct rather than a miss to fix.

    The reconcile produced content nothing has ever verified, so the verdict standing
    against the old tree says nothing about it.
    """
    counter = tmp_path / "gate-runs.log"
    worktree = add_worktree(trunk, "alpha")
    write_counting_verify(worktree, counter)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
        )
        assert (await service.tick())[0]["state"] == "verified"
        assert gate_runs(counter) == 1

        commit(trunk, "trunk.txt", "moved\n", "trunk moved")
        landing = await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "landed"
        assert gate_runs(counter) == 2
        stored = await store.get(landing["id"])
        assert stored is not None
        assert stored["verify_gate"] == "full"
    finally:
        store.close()


async def test_a_verdict_is_keyed_to_the_bytes_that_produced_it(
    tmp_path: Path, trunk: Path
) -> None:
    """The digest is half the key, so different bytes have nothing standing at all."""
    counter = tmp_path / "gate-runs.log"
    worktree = add_worktree(trunk, "alpha")
    write_counting_verify(worktree, counter)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    tree = git(worktree, "rev-parse", "HEAD^{tree}")
    service, store, approvals = build_service(tmp_path, trunk)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
        )
        assert (await service.tick())[0]["state"] == "verified"
        digest = gate_digest(worktree, approvals, trunk)
        root = row["project_root"]
        assert await store.verify_memo(root, tree, digest) is not None
        assert await store.verify_memo(root, tree, "some-other-digest") is None
    finally:
        store.close()


async def test_a_stale_verdict_is_not_reused(tmp_path: Path, trunk: Path) -> None:
    """The freshness bound is real: a tree hash is a claim about content, not a machine.

    An installed dependency, a toolchain, an OS update - none of them changes the tree,
    and any of them can change what the gate says. So a verdict expires.
    """
    store = LandStore(tmp_path / "land.sqlite3")
    try:
        await store.record_verify_memo(
            project_root=str(trunk),
            tree_oid="t" * 40,
            digest="d" * 64,
            request_id="lnd_1",
            request_kind="verify",
            branch="worktree-alpha",
            worktree_root=str(trunk),
            commit_oid="c" * 40,
            duration_ms=1000.0,
            now=1000.0,
        )
        fresh = await store.verify_memo(str(trunk), "t" * 40, "d" * 64, not_before=900.0)
        assert fresh is not None
        assert fresh["request_kind"] == "verify"
        assert await store.verify_memo(
            str(trunk), "t" * 40, "d" * 64, not_before=1100.0
        ) is None
        # Half a key identifies nothing, so it is never a hit.
        assert await store.verify_memo(str(trunk), "", "d" * 64) is None
        assert await store.verify_memo(str(trunk), "t" * 40, "") is None
    finally:
        store.close()


async def test_reuse_switched_off_runs_the_gate_every_time(
    tmp_path: Path, trunk: Path
) -> None:
    counter = tmp_path / "gate-runs.log"
    worktree = add_worktree(trunk, "alpha")
    write_counting_verify(worktree, counter)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")

    class NoReuse(FakeConfig):
        land_verify_memo_seconds = 0.0

    service, store, approvals = build_service(tmp_path, trunk, config=NoReuse())
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
        )
        assert (await service.tick())[0]["state"] == "verified"
        await service.request(
            project_id="p", project_root=str(trunk), worktree_root=str(worktree)
        )
        assert (await service.tick())[0]["state"] == "landed"
        assert gate_runs(counter) == 2
    finally:
        store.close()


async def test_a_documentation_only_verify_reports_the_skip_and_records_nothing(
    tmp_path: Path, trunk: Path
) -> None:
    """Nothing ran, so there is nothing to reuse - and the row still says which gate."""
    docs_trunk(trunk)
    worktree = add_worktree(trunk, "docs")
    commit(worktree, "README.md", "# docs\n", "docs work")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
            origin="agent",
            origin_session_id="sess_1",
            origin_run_id="run_1",
        )
        assert (await service.tick())[0]["state"] == "verified"
        stored = await store.get(row["id"])
        assert stored is not None
        assert stored["verify_gate"] == "docs_only"
        tree = git(worktree, "rev-parse", "HEAD^{tree}")
        assert await store.verify_memo(
            row["project_root"], tree, gate_digest(worktree, approvals, trunk)
        ) is None
        assert "documentation only" in queue.messages[0]["body"]
    finally:
        store.close()


async def test_a_verify_result_arms_for_the_session_that_asked(
    tmp_path: Path, trunk: Path
) -> None:
    """A land announces itself by the trunk moving; a verify has only the message.

    So the pass is reported over the same solicited-reply authority a handback uses,
    under every one of the same bounds, and it spends the same single armed reply.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        row = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
            origin="agent",
            origin_session_id="sess_1",
            origin_run_id="run_1",
        )
        assert (await service.tick())[0]["state"] == "verified"
        assert queue.messages[0]["target_session_id"] == "sess_1"
        assert queue.messages[0]["armed"] is True
        assert queue.messages[0]["solicited_by"] == row["id"]
        assert "Nothing was landed" in queue.messages[0]["body"]
        reported = next(
            item for item in await store.events(row["id"]) if item["outcome"] == "reported"
        )
        assert reported["detail"]["armed"] is True
        # One request, one bounded answer. The cap is per request, not per outcome.
        stored = await store.get(row["id"])
        assert stored is not None
        assert stored["armed_replies"] == 1
    finally:
        store.close()


async def test_an_operator_verify_writes_no_message(tmp_path: Path, trunk: Path) -> None:
    """No session asked, so there is nobody the answer is owed to.

    An operator's verify is started from a surface they are already looking at, and its
    result is on the row there. Nothing about it authorizes a write into a terminal.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    queue = FakeQueue()
    service, store, approvals = build_service(tmp_path, trunk, queue=queue)
    try:
        approve(approvals, worktree, trunk)
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
        )
        assert (await service.tick())[0]["state"] == "verified"
        assert queue.messages == []
    finally:
        store.close()


async def test_a_draft_grant_enqueues_a_verify_but_still_drafts_a_land(
    tmp_path: Path, trunk: Path
) -> None:
    """The grant is about moving a trunk, and a verify-only run cannot move one.

    Drafting it would put the cheap half of the pipeline behind the approval the
    expensive half exists to protect, which is how a gate ends up being run by hand.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    drafted: list[dict[str, Any]] = []

    async def draft_request(**kwargs: Any) -> dict[str, Any]:
        drafted.append(kwargs)
        return {"observation_id": "obs_1"}

    store = LandStore(tmp_path / "land.sqlite3")
    service = LandQueueService(
        store=store,
        approvals=VerifyApprovalStore(tmp_path / "data"),
        config=FakeConfig(),
        grant_field=lambda _root: "draft",
        draft_request=draft_request,
    )
    try:
        verified = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
            origin="agent",
            origin_session_id="sess_1",
        )
        assert verified["state"] == "queued"
        assert verified["kind"] == "verify"
        assert drafted == []
        # And the land the grant *is* about is still drafted, on the same branch.
        await store.transition(
            verified["id"], expect=("queued",), state="cancelled", reason="test"
        )
        landing = await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            origin="agent",
            origin_session_id="sess_1",
        )
        assert landing["state"] == "drafted"
        assert len(drafted) == 1
    finally:
        store.close()


async def test_an_off_grant_refuses_a_verify_too(tmp_path: Path, trunk: Path) -> None:
    """`off` is the operator saying agents do not drive this machinery here."""
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, _ = build_service(tmp_path, trunk, grant="off")
    try:
        with pytest.raises(LandRefusal) as caught:
            await service.request(
                project_id="p",
                project_root=str(trunk),
                worktree_root=str(worktree),
                kind="verify",
                origin="agent",
                origin_session_id="sess_1",
            )
        assert caught.value.code == "land_denied"
    finally:
        store.close()


async def test_one_branch_cannot_be_verified_and_landed_at_once(
    tmp_path: Path, trunk: Path
) -> None:
    """One branch, one request, whatever the two asked for.

    Both kinds reconcile the same worktree and run the same gate, so a second in-flight
    request would be two pipelines over one checkout - which is exactly what the active
    index has always refused.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    service, store, _ = build_service(tmp_path, trunk)
    try:
        await service.request(
            project_id="p",
            project_root=str(trunk),
            worktree_root=str(worktree),
            kind="verify",
        )
        with pytest.raises(LandRefusal) as caught:
            await service.request(
                project_id="p", project_root=str(trunk), worktree_root=str(worktree)
            )
        assert caught.value.code == "already_queued"
    finally:
        store.close()


async def test_a_verify_only_request_ignores_a_dirty_trunk(trunk: Path) -> None:
    """The trunk's own uncommitted work is not a hazard a verify-only run can reach.

    Holding on it would make the cheap request wait for something it cannot cause, and a
    hold nothing the requester does can clear is a stall rather than a guard.
    """
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    # An operator edit to a file the land *would* overwrite, which is the only shape
    # that holds a land at all.
    (trunk / "alpha.txt").write_text("conflicting operator edit\n", encoding="utf-8")
    git(trunk, "add", "alpha.txt")
    facts = await read_repository_facts(str(worktree), str(trunk))
    landing = evaluate_preconditions(facts, branch="worktree-alpha")
    assert landing.disposition == "hold"
    verifying = evaluate_preconditions(facts, branch="worktree-alpha", lands=False)
    assert verifying.ready


async def test_an_already_landed_branch_has_nothing_to_verify(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    commit(worktree, "alpha.txt", "alpha\n", "alpha work")
    git(trunk, "merge", "--ff-only", "worktree-alpha")
    service, store, _ = build_service(tmp_path, trunk)
    try:
        with pytest.raises(LandRefusal) as caught:
            await service.request(
                project_id="p",
                project_root=str(trunk),
                worktree_root=str(worktree),
                kind="verify",
            )
        assert caught.value.code == "already_landed"
        assert "nothing to verify" in caught.value.message
    finally:
        store.close()
