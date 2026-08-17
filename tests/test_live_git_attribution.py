"""Tier A: real agent writes, a real commit, and the attribution join between them.

The live half of Phase 7.8. Committer isolation is exercised on the daemon and in
`test_git_provenance.py`; what needs a *real* CLI is the contributor side, because
the join reads whatever shape each harness's write tool leaves in the transcript.
Claude writes whole-file content and can be confirmed byte-for-byte against the
committed blob; codex writes an `apply_patch` envelope and can only be matched by
path. That difference is per-harness and invisible to a stub, so it is asserted
here against real runs rather than assumed.

Like the rest of the live suite this binds no port and starts no daemon: a real CLI
writes into a temporary repository, the transcript is replayed through the
production observer into a real `Tier0Store`, and the real matchers run against
real `git diff-tree` and blob bytes.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from swe_mux.git_monitor import read_blob_digest, read_commit_changes
from swe_mux.git_provenance import candidate_writes, resolve_contributors
from swe_mux.harness import live_automation_harnesses
from swe_mux.tier0_store import Tier0Store
from tests.support.live_facts import capture_facts_from_transcript
from tests.test_live_agent_conformance import _probe_transcript

RUN_AUTO = os.environ.get("SWEMUX_RUN_LIVE_AUTOMATIONS_TESTS") == "1"
ATTRIBUTION_HARNESSES = list(live_automation_harnesses())
#: Far past any timestamp a replayed transcript can carry (2100-01-01).
_NO_HORIZON = 4102444800.0

#: How strongly each harness's write can be matched to a committed file, and why.
#: `content` means the harness recorded a hash of the whole file it wrote, so the
#: digest of what the agent wrote equals the digest of what git stored. `path`
#: means it did not, so the match is by file and time — correct, and honestly
#: weaker. A harness absent here defaults to `path`, the claim that cannot be wrong.
WRITE_MATCH_STRENGTH: dict[str, str] = {
    "claude": "content",
    # omp records its write with a relative target and no content hash at all, so
    # the file and its session's checkout are all there is to match on.
    "omp": "path",
    "pi": "content",
    # codex writes through apply_patch on its shell/exec tool, so the call is a
    # command fact and the write is only visible in the `patch_apply_end` result —
    # which carries a hash of the file contents it applied, not of the envelope.
    "codex": "content",
}

# One physical line each: codex and pi launch through a `.cmd` shim wrapped in
# `cmd.exe /c`, which truncates an argv value at its first newline.
_WRITE_CALC = (
    "Use your file-writing or editing tool (not a shell command) to create a file "
    "named calc.py in the current directory containing exactly this one line of "
    "Python: def add(a, b): return a + b -- then reply with the single word "
    "WROTE_OK. Do not run any tests or other commands."
)
_WRITE_UTIL = (
    "Use your file-writing or editing tool (not a shell command) to create a file "
    "named util.py in the current directory containing exactly this one line of "
    "Python: def double(a): return a * 2 -- then reply with the single word "
    "WROTE_OK. Do not run any tests or other commands."
)
#: The bytes each prompt dictates, used only to stand a file up when the harness's
#: own sandbox refused the write (codex under `workspace-write` frequently does).
#: The join under test is fact-to-commit, so what matters is that the file the
#: commit carries is the file the agent's recorded write named — and, where the
#: harness hashed content, that the hashes agree.
_BODIES = {"calc.py": b"def add(a, b): return a + b\n", "util.py": b"def double(a): return a * 2\n"}


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.name", "Live Canary")
    _git(root, "config", "user.email", "canary@example.invalid")


def _ensure_committable(
    repository: Path, name: str, facts: list[dict[str, object]], backend: str
) -> None:
    """Make sure the commit can carry `name`, whatever the harness's sandbox did.

    A harness that wrote the file leaves it alone. One whose sandbox refused the
    write must still have *recorded* a write naming the file — that recorded fact
    is the thing under test — and the prompt's bytes stand in so there is a commit
    to match it against. A harness that neither wrote nor recorded is a real
    failure and is asserted as one.
    """
    if (repository / name).is_file():
        return
    named = [
        fact
        for fact in facts
        if str(fact.get("target") or "").replace("\\", "/").casefold().endswith(name)
    ]
    assert named, f"{backend} left no {name} and recorded no write naming it"
    (repository / name).write_bytes(_BODIES[name])


def test_every_fact_producing_harness_declares_its_write_match_strength() -> None:
    """A harness joins the attribution canary with a stated strength, or fails here.

    The registry-derived guard: adding a harness must not silently inherit
    `content` (which would fail confusingly on a patch-based writer) nor silently
    drop out of the canary.
    """
    assert ATTRIBUTION_HARNESSES, "the attribution canary covers no harness at all"
    for backend in ATTRIBUTION_HARNESSES:
        assert backend in WRITE_MATCH_STRENGTH, (
            f"{backend} produces write facts but declares no expected match strength; "
            f"add it to WRITE_MATCH_STRENGTH"
        )
    assert set(WRITE_MATCH_STRENGTH) <= set(ATTRIBUTION_HARNESSES)
    assert set(WRITE_MATCH_STRENGTH.values()) <= {"content", "path"}


@pytest.mark.live_agent
@pytest.mark.live_automations
@pytest.mark.skipif(not RUN_AUTO, reason="set SWEMUX_RUN_LIVE_AUTOMATIONS_TESTS=1")
@pytest.mark.parametrize("backend", ATTRIBUTION_HARNESSES)
async def test_a_commit_records_every_session_whose_real_write_it_contains(
    backend: str, tmp_path: Path
) -> None:
    """Two real runs write two files; one commit carries both, and names both.

    This is the multi-contributor case with real evidence: each run is captured
    under its own session id, a single commit sweeps both files, and the matchers
    must attribute each file to the run that actually wrote it. Git records one
    author for that commit; mux records both contributors.
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    _repository(repository)
    store = Tier0Store(tmp_path / "tier0.db")
    try:
        first = await _probe_transcript(
            backend, repository, time.time(), _WRITE_CALC, mode="automations"
        )
        assert first.exists()
        assert await capture_facts_from_transcript(
            backend, first, store, session_id="one", agent_run_id="run-one", project_id="p1"
        ), f"{backend} first write run produced no Tier 0 facts"

        second = await _probe_transcript(
            backend, repository, time.time(), _WRITE_UTIL, mode="automations"
        )
        assert second.exists()
        assert await capture_facts_from_transcript(
            backend, second, store, session_id="two", agent_run_id="run-two", project_id="p1"
        ), f"{backend} second write run produced no Tier 0 facts"

        # The replay stamps facts with the transcript clock, which is not wall
        # clock, so the window here is deliberately unbounded. In the daemon the
        # window is the commit's own, which spans the real writes by construction.
        facts = await store.write_facts_for_project("p1", since=0.0, until=_NO_HORIZON)
        for name in ("calc.py", "util.py"):
            _ensure_committable(repository, name, facts, backend)
        _git(repository, "add", "calc.py", "util.py")
        _git(repository, "commit", "-m", "Both agents' work in one commit")
        oid = _git(repository, "rev-parse", "HEAD")

        changes = await read_commit_changes(str(repository), oid)
        assert {change.path for change in changes} >= {"calc.py", "util.py"}
        candidates = candidate_writes(
            changes,
            facts,
            worktree_root=str(repository),
            # The daemon knows each session's checkout, and a harness that writes a
            # relative target (omp) is placeable only through it. Withholding it
            # here would test a session mux cannot identify, not this one.
            session_roots={"one": str(repository), "two": str(repository)},
        )
        digests = {
            candidate.path: await read_blob_digest(str(repository), candidate.blob or "")
            for candidate in candidates
            if candidate.blob
        }
        contributors = {
            item.session_id: item for item in resolve_contributors(candidates, digests)
        }

        assert set(contributors) == {"one", "two"}, (
            f"{backend} commit attributed to {sorted(contributors)}, expected both runs"
        )
        assert "calc.py" in contributors["one"].paths
        assert "util.py" in contributors["two"].paths
        assert contributors["one"].agent_run_id == "run-one"
        assert contributors["two"].agent_run_id == "run-two"
        expected_content = WRITE_MATCH_STRENGTH.get(backend, "path") == "content"
        for session_id, item in contributors.items():
            assert item.content_matched is expected_content, (
                f"{backend}/{session_id} matched by "
                f"{'content' if item.content_matched else 'path'}, and the declared "
                f"strength is {WRITE_MATCH_STRENGTH.get(backend, 'path')}"
            )
            assert item.confidence == ("exact" if expected_content else "correlated")
    finally:
        store.close()
