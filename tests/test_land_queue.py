"""Phase 14: the land queue, against real repositories and real worktrees.

These drive `git` for real rather than mocking it. The whole safety argument of the
phase is a property of what Git does - `--ff-only` refuses divergence, `merge` reports
its own conflicts - so a fake that agrees with the implementation would prove nothing
about either.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from swe_mux.land_preconditions import evaluate_preconditions, read_repository_facts
from swe_mux.land_queue import LandQueueService, LandRefusal, handback_excerpt
from swe_mux.land_store import LandConflict, LandStore
from swe_mux.worktree_verify import (
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


class FakeQueue:
    """Stands in for the Phase 5 prompt queue, recording what it was handed."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.messages.append(kwargs)
        return {"id": f"msg_{len(self.messages)}"}


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
) -> tuple[LandQueueService, LandStore, VerifyApprovalStore]:
    store = LandStore(tmp_path / "land.sqlite3")
    approvals = VerifyApprovalStore(tmp_path / "data")

    async def busy_sessions(_root: str) -> tuple[str, ...]:
        return busy

    async def project_values(_root: str) -> dict[str, Any]:
        return {}

    async def automation_gate(_root: str) -> frozenset[str]:
        return frozenset({"land_queue"})

    service = LandQueueService(
        store=store,
        approvals=approvals,
        config=config or FakeConfig(),
        automation_gate=automation_gate,
        grant_field=lambda _root: grant,
        project_values=project_values,
        comparison_ref=lambda _root: _resolved("main"),
        busy_sessions=busy_sessions,
        queue_message=queue,
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
        steps = [item["step"] for item in await store.events(row["id"])]
        assert steps == ["request", "reconcile", "verify", "land"]
    finally:
        store.close()


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
    """Approval is the operator's business, so it does not become the agent's task."""
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
        )
        results = await service.tick()
        assert results[0]["state"] == "refused"
        assert "not approved" in results[0]["reason"]
        assert queue.messages == []
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
