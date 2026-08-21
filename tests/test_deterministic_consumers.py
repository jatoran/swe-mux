"""Phase 3.7 / control-plane step 3: the model-free detectors over Tier 0 facts.

Each detector is a pure query, so these are pure-function tests. What they pin is
not the wording but the *discipline*: the no-progress gate, the three separate
verification facts, evidence as a set of facts rather than one pointer, and a
provenance edge that never asserts more than time order supports.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from swe_mux.automation_registry import REGISTRY, resolve
from swe_mux.automation_store import AutomationStore
from swe_mux.deterministic_consumers import (
    LOOP_REPEAT_THRESHOLD,
    build_doc_ownership,
    build_provenance_edges,
    detect_declared_vs_verified,
    detect_doc_debt,
    detect_loop,
    doc_debt_dedupe_key,
    normalize_target,
)
from swe_mux.tier0_store import Tier0Store


def fact(
    kind: str,
    *,
    at: float,
    fingerprint: str = "fp",
    target: str | None = None,
    content_hash: str | None = None,
    session_id: str = "s1",
    detail: dict[str, Any] | None = None,
    identity: str | None = None,
) -> dict[str, Any]:
    return {
        "id": identity or f"{kind}-{at}",
        "session_id": session_id,
        "kind": kind,
        "target": target,
        "content_hash": content_hash,
        "fingerprint": fingerprint,
        "detail_json": json.dumps(detail or {}),
        "source_seq": int(at),
        "created_at": at,
    }


# --------------------------------------------------------------------- loops


def test_a_repeated_action_with_no_progress_is_a_loop() -> None:
    facts = [
        fact("command", at=index, fingerprint="same", target="pytest tests/test_a.py")
        for index in range(1, 5)
    ]
    finding = detect_loop(facts)
    assert finding is not None
    assert finding.repeats == 4
    assert finding.fingerprint == "same"
    # Evidence is the whole set: a loop's case is "these N facts", and one event
    # pointer would leave a reader unable to check it.
    assert len(finding.evidence) == 4
    assert {item["fact_id"] for item in finding.evidence} == {item["id"] for item in facts}


def test_two_repeats_are_not_a_loop() -> None:
    facts = [
        fact("command", at=index, fingerprint="same", target="pytest -q")
        for index in range(LOOP_REPEAT_THRESHOLD - 1)
    ]
    assert detect_loop(facts) is None


def test_a_fact_with_no_discriminator_never_seeds_a_loop() -> None:
    # The single largest source of false loops (measured 2026-08-21): a `PreToolUse`
    # hook emitted `tool_use` with only a tool name, so 25,362 unrelated Bash calls
    # shared the fingerprint of `{"scope":"root","tool":"Bash"}` and 390 of 397
    # lifetime findings rested on six such fingerprints. Capture now carries the
    # target; this is the fail-closed half, which does not depend on it.
    from swe_mux.deterministic_consumers import has_loop_discriminator

    blind = [fact("command", at=index, fingerprint="same") for index in range(1, 6)]
    assert detect_loop(blind) is None
    assert has_loop_discriminator(blind[0]) is False
    # Either half of the discriminator is enough: a write names itself by what it
    # wrote even with no target key, and a test result by its failing set.
    assert has_loop_discriminator(fact("file_write", at=1, content_hash="h1")) is True
    outcome = {"test_outcome": {"failed": 2, "failing_tests": ["a", "b"]}}
    tested = [
        fact("test_result", at=index, fingerprint="same", detail=outcome)
        for index in range(1, 6)
    ]
    assert has_loop_discriminator(tested[0]) is True
    assert detect_loop(tested) is not None


def test_a_repeated_read_only_shell_command_is_not_a_loop() -> None:
    # Live calibration case (2026-08-21): `grep -nE ... tasks/bpberjtk2.output` five
    # times was flagged — an agent polling a background task's output. Every Bash
    # call classifies as `command`, a change-attempting kind, so the exclusion that
    # already protects `tool`/`file_read` did not reach the shell.
    for command in (
        "grep -nE '^(=== |verification passed)' /tmp/tasks/x.output",
        "git -C D:/PROJECTS/swe-mux status --porcelain",
        "netstat -ano",
        "curl -s http://127.0.0.1:8765/api/health",
        "ls -la src/swe_mux",
    ):
        facts = [
            fact("command", at=index, fingerprint="same", target=command)
            for index in range(1, 6)
        ]
        assert detect_loop(facts) is None, command


def test_a_shell_command_that_writes_still_seeds_a_loop() -> None:
    # The predicate fails toward keeping the detector: a redirection, a
    # substitution, an unknown verb, or a truncated command is not read-only.
    for command in (
        "grep -r foo src > out.txt",
        "cat template.py && python build.py",
        "npm run build",
        "grep foo src | tee out.txt",
    ):
        facts = [
            fact("command", at=index, fingerprint="same", target=command)
            for index in range(1, 6)
        ]
        assert detect_loop(facts) is not None, command


def test_a_shrinking_failing_test_set_is_progress_not_a_loop() -> None:
    # Running the same test command repeatedly while fixing things is work. The
    # no-progress gate is the entire difference between a useful signal and a
    # detector that cries wolf on every red-green cycle.
    facts = [
        fact(
            "test_result",
            at=1,
            fingerprint="same",
            detail={"test_outcome": {"failed": 3, "failing_tests": ["a", "b", "c"]}},
        ),
        fact(
            "test_result",
            at=2,
            fingerprint="same",
            detail={"test_outcome": {"failed": 2, "failing_tests": ["a", "b"]}},
        ),
        fact(
            "test_result",
            at=3,
            fingerprint="same",
            detail={"test_outcome": {"failed": 1, "failing_tests": ["a"]}},
        ),
    ]
    assert detect_loop(facts) is None


def test_repeated_read_only_actions_are_never_a_loop() -> None:
    # Live calibration case (run 603e5833, 2026-07-28): four identical Greps on
    # `frontend/src` were flagged. A read-only action produces no test outcome,
    # no content hash and no commit, so the no-progress gate is vacuously true
    # for it *by construction* — repeated looking is not repeated failing, and
    # the Wink precedent behind the threshold measures ineffective *attempts*.
    for kind in ("tool", "tool_result", "file_read", "file_read_result"):
        facts = [
            fact(kind, at=index, fingerprint="same", target="frontend/src")
            for index in range(1, 7)
        ]
        assert detect_loop(facts) is None, kind


def test_a_collapsed_write_result_fingerprint_is_not_a_loop_seed() -> None:
    # Observed live: four *distinct* successful edits shared one
    # `file_write_result` fingerprint because the result payload carries no
    # content hash. Result kinds (other than test_result) must not seed a loop.
    facts = [
        fact("file_write_result", at=index, fingerprint="same", target="a.tsx")
        for index in range(1, 5)
    ]
    assert detect_loop(facts) is None


def test_read_only_facts_still_feed_the_progress_gate() -> None:
    # Excluding read-only kinds from *seeding* must not exclude the window's
    # other facts from the gate: a repeated command interleaved with reads is
    # still judged on whether anything measurable moved.
    facts = [
        fact("command", at=1, fingerprint="same", target="pytest -q"),
        fact("file_read", at=2, fingerprint="r", target="a.py"),
        fact("command", at=3, fingerprint="same", target="pytest -q"),
        fact("command", at=4, fingerprint="same", target="pytest -q"),
    ]
    finding = detect_loop(facts)
    assert finding is not None
    assert finding.repeats == 3


def test_new_file_content_in_the_window_is_progress() -> None:
    facts = [
        fact("command", at=1, fingerprint="same", target="pytest -q"),
        fact("file_write", at=2, fingerprint="w", target="a.py", content_hash="h1"),
        fact("command", at=3, fingerprint="same", target="pytest -q"),
        fact("command", at=4, fingerprint="same", target="pytest -q"),
    ]
    assert detect_loop(facts) is None


def test_a_stored_loop_finding_with_no_discriminator_is_retracted_at_read_time() -> None:
    # The stored row is never rewritten: it is a record of what was concluded. What
    # changes is what a reader is told about it, by the same rule the detector now
    # applies before seeding.
    from swe_mux.deterministic_consumers import loop_finding_unsupported

    legacy = json.dumps(
        [{"fact_id": "f1", "kind": "command", "target": None, "fingerprint": "ae90"}] * 4
    )
    assert loop_finding_unsupported(legacy) is True
    # A finding resting on real evidence still stands, by target or by hash.
    assert loop_finding_unsupported(json.dumps([{"target": "pytest -q"}])) is False
    assert loop_finding_unsupported(json.dumps([{"content_hash": "h1"}])) is False
    # An empty or unreadable evidence set is not a positive claim either way.
    assert loop_finding_unsupported("[]") is False
    assert loop_finding_unsupported("not json") is False


# ------------------------------------------------------- declared vs verified


def test_a_run_with_no_test_facts_produces_no_finding() -> None:
    # Measured 2026-08-21: one `test_result` fact stood against 4,485
    # `command_result` facts in a 24-hour window, because the land queue's gate
    # runs out-of-band and never became a fact. With nothing captured the detector
    # cannot tell "this agent verified nothing" from "this install captured
    # nothing", and it was saying the first about every run while only the second
    # was known. Silence is the honest answer; the gate fact is the repair.
    assert detect_declared_vs_verified("Everything is fixed now.", []) is None


def test_a_completion_claim_against_a_red_run_reports_three_separate_facts() -> None:
    facts = [
        fact("test_result", at=5, detail={"test_outcome": {"failed": 2, "failing_tests": ["x"]}})
    ]
    finding = detect_declared_vs_verified("Everything is fixed now.", facts)
    assert finding is not None
    assert (finding.declared, finding.tests_ran, finding.tests_passed) == (True, True, False)
    # Declared, verified, and correct stay strictly apart — never one ✓.
    assert "claims done" in finding.content
    assert "✓" not in finding.content


def test_a_claim_finding_carries_a_pointer_back_to_the_turn_that_made_it() -> None:
    # Every one of the 42 lifetime findings carried an empty evidence set, which
    # breaks the "evidence is a set" contract outright: the reader had nothing to
    # check the claim against, not even the message it was read from.
    facts = [fact("test_result", at=5, detail={"test_outcome": {"failed": 1}})]
    pointer = {"kind": "claim", "session_id": "s1", "message_ts": 1700.0}
    finding = detect_declared_vs_verified(
        "Everything is fixed now.", facts, claim_evidence=pointer
    )
    assert finding is not None
    assert finding.evidence[0] == pointer
    assert finding.evidence[1]["fact_id"] == facts[0]["id"]


def test_a_claim_backed_by_a_green_run_is_not_a_finding() -> None:
    facts = [
        fact(
            "test_result",
            at=5,
            detail={"test_outcome": {"failed": 0, "errors": 0, "failing_tests": []}},
        )
    ]
    assert detect_declared_vs_verified("All tests pass.", facts) is None


def test_a_claim_contradicted_by_a_red_run_is_a_finding() -> None:
    facts = [
        fact(
            "test_result",
            at=5,
            detail={"test_outcome": {"failed": 2, "failing_tests": ["x", "y"]}},
        )
    ]
    finding = detect_declared_vs_verified("This should now be working.", facts)
    assert finding is not None
    assert (finding.tests_ran, finding.tests_passed) == (True, False)
    assert "tests failed" in finding.content


def test_ordinary_prose_is_not_a_completion_claim() -> None:
    red = [fact("test_result", at=5, detail={"test_outcome": {"failed": 1}})]
    for text in (
        "I'll start by reading the tests.",
        "The tests are in tests/test_core.py.",
        "This might be why it fails.",
        # The optional copula made these claims. 27 of 42 lifetime findings came
        # from this one alternative, and every sampled one was English rather than
        # an assertion (measured 2026-08-21).
        "Land it from this working tree, not the primary checkout.",
        "Is it working, or awaiting input?",
        "Leave it fixed and unexposed for now.",
        # A failure word immediately before the claim inverts it.
        "The pipe once shipped a failing test green.",
        "Not all tests pass yet.",
        # Quotation is not assertion: both anti-overclaim findings in the lifetime
        # corpus fired on a message quoting the requirement.
        "Anti-overclaim (`all tests pass`) can fire when the model is quoting you.",
        "```\nall tests pass\n```\nThat pattern is the one to avoid.",
    ):
        assert detect_declared_vs_verified(text, red) is None, text


def test_a_claim_is_read_from_the_closing_summary_not_the_body() -> None:
    # A multi-thousand-word report describing what a *future* state would look
    # like is not a verdict on the work just done.
    from swe_mux.deterministic_consumers import CLAIM_SCOPE_CHARS

    red = [fact("test_result", at=5, detail={"test_outcome": {"failed": 1}})]
    buried = "Everything is fixed now.\n\n" + ("Body prose about the audit. " * 200)
    assert len(buried) > CLAIM_SCOPE_CHARS
    assert detect_declared_vs_verified(buried, red) is None
    # The same sentence in the closing paragraph is a claim.
    closing = ("Body prose about the audit. " * 200) + "\n\nEverything is fixed now."
    assert detect_declared_vs_verified(closing, red) is not None


# ------------------------------------------------------------------ doc debt


def test_doc_ownership_is_built_from_each_doc_s_key_files_section(tmp_path: Path) -> None:
    # The routing table is keyed by change *type*, which no machine can match to
    # a path. The per-doc "Key files" sections are the same routing information
    # already written as literal paths, so the map needs no second list.
    docs = tmp_path / ".docs" / "design" / "features"
    docs.mkdir(parents=True)
    (docs / "sessions.md").write_text(
        "# Sessions\n\nProse mentioning `src/swe_mux/decoy.py` outside the section.\n"
        "\n## Key files\n\n- `src/swe_mux/session.py`\n- `frontend/src/TerminalPane.tsx`\n"
        "\n## Relates to\n\n- `src/swe_mux/other.py`\n",
        encoding="utf-8",
    )
    ownership = build_doc_ownership(tmp_path / ".docs")
    assert ownership[normalize_target("src/swe_mux/session.py")] == (
        "design/features/sessions.md",
    )
    assert normalize_target("frontend/src/TerminalPane.tsx") in ownership
    # Paths outside the Key files section are not ownership claims.
    assert normalize_target("src/swe_mux/decoy.py") not in ownership
    assert normalize_target("src/swe_mux/other.py") not in ownership


def test_a_hub_file_claimed_by_many_docs_carries_no_ownership(tmp_path: Path) -> None:
    # Live calibration case (2026-07-28): one edit to `App.tsx` — claimed by 8
    # feature docs because it is the browser composition root — marked 8
    # unrelated docs dirty. A file claimed by more than DOC_HUB_OWNER_LIMIT docs
    # is infrastructure, not a subject any single doc owns, and must carry no
    # ownership signal. Files at or under the limit keep theirs.
    docs = tmp_path / ".docs" / "design" / "features"
    docs.mkdir(parents=True)
    for name in ("git", "sessions", "projects", "ui", "workspace-layout"):
        lines = ["## Key files", "", "- `frontend/src/App.tsx`"]
        if name != "git":
            lines.append("- `frontend/src/shared.tsx`")
        lines.append(f"- `src/swe_mux/{name}.py`")
        (docs / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ownership = build_doc_ownership(tmp_path / ".docs")
    # 5 claimants > limit: dropped entirely.
    assert normalize_target("frontend/src/App.tsx") not in ownership
    # Exactly at the limit (4 claimants): still a real owner.
    assert len(ownership[normalize_target("frontend/src/shared.tsx")]) == 4
    # Single-owner files are untouched.
    assert ownership[normalize_target("src/swe_mux/git.py")] == (
        "design/features/git.md",
    )
    # And debt from touching only the hub is no finding at all.
    assert (
        detect_doc_debt([fact("file_write", at=1, target="frontend/src/App.tsx")], ownership)
        is None
    )


def test_doc_debt_accumulates_and_clears_when_the_doc_is_edited() -> None:
    ownership = {normalize_target("src/swe_mux/session.py"): ("design/features/sessions.md",)}
    changed = [
        fact("file_write", at=1, target="src/swe_mux/session.py", content_hash="h1"),
    ]
    finding = detect_doc_debt(changed, ownership)
    assert finding is not None
    assert finding.dirty == ("design/features/sessions.md",)
    assert "1 doc(s) owe an update" in finding.content

    paid = [*changed, fact("file_write", at=2, target=".docs/design/features/sessions.md")]
    assert detect_doc_debt(paid, ownership) is None


def test_doc_debt_ignores_files_no_doc_claims() -> None:
    assert detect_doc_debt([fact("file_write", at=1, target="scratch/notes.py")], {}) is None


def test_doc_debt_dedupe_key_is_per_doc_so_a_growing_set_never_restates() -> None:
    # The old key hashed the whole dirty *set* — the identical pattern removed
    # from provenance for being quadratic-restating. One more dirty doc minted a
    # new row restating all the others: 137 rows carried 137 distinct keys, and
    # one window's 8-doc set was a strict subset of the 9-doc set beside it
    # (measured 2026-08-21). Per doc, one dirty doc is one row forever.
    assert doc_debt_dedupe_key("p1", "one.md") == doc_debt_dedupe_key("p1", "one.md")
    assert doc_debt_dedupe_key("p1", "one.md") != doc_debt_dedupe_key("p1", "two.md")
    assert doc_debt_dedupe_key("p1", "one.md") != doc_debt_dedupe_key("p2", "one.md")


def test_doc_debt_reach_drops_a_hub_rather_than_naming_every_doc() -> None:
    # The reach refinement re-admitted through the back door exactly the explosion
    # DOC_HUB_OWNER_LIMIT was calibrated to prevent: one window's finding read "21
    # doc(s) owe an update for 3 changed source file(s)" — very nearly the whole
    # `.docs` tree — from edits to three composition roots (measured 2026-08-21).
    from swe_mux.deterministic_consumers import (
        DOC_REACH_DEPENDENT_LIMIT,
        build_doc_debt_map,
    )

    ownership = {
        normalize_target(f"dep{index}.py"): (f"doc{index}.md",) for index in range(12)
    }
    changed = [fact("file_write", at=1, target="hub.py")]
    wide = {
        normalize_target("hub.py"): tuple(
            normalize_target(f"dep{index}.py") or "" for index in range(12)
        )
    }
    assert build_doc_debt_map(changed, ownership, dependents=wide) == {}
    # A reach small enough to mean something is kept.
    narrow = {
        normalize_target("hub.py"): tuple(
            normalize_target(f"dep{index}.py") or ""
            for index in range(DOC_REACH_DEPENDENT_LIMIT - 6)
        )
    }
    assert build_doc_debt_map(changed, ownership, dependents=narrow) != {}


# ------------------------------------------------------------- provenance


def test_provenance_states_time_order_not_hash_equality() -> None:
    # Write-side and read-side hashes are not joinable by equality — a read result
    # hashes the CLI's rendering, not the file — so the edge is `target` plus time
    # order, carrying the writer's hash as the thing that was written.
    facts = [
        fact(
            "file_write",
            at=1,
            target="src/a.py",
            content_hash="written-hash",
            session_id="writer",
        ),
        fact(
            "file_read_result",
            at=2,
            target="SRC/A.PY",
            content_hash="rendered-hash",
            session_id="reader",
        ),
    ]
    edges = build_provenance_edges(facts)
    assert len(edges) == 1
    edge = edges[0]
    assert (edge.writer_session_id, edge.reader_session_id) == ("writer", "reader")
    assert edge.writer_content_hash == "written-hash"
    assert edge.ambiguous is False


def test_a_second_write_between_makes_the_edge_ambiguous() -> None:
    facts = [
        fact("file_write", at=1, target="a.py", content_hash="h1", session_id="writer"),
        fact("file_write", at=2, target="a.py", content_hash="h2", session_id="other"),
        fact("file_read", at=3, target="a.py", session_id="reader"),
    ]
    edges = build_provenance_edges(facts)
    # The most recent foreign write is reported, and the fact that another write
    # intervened is stated rather than papered over.
    assert [edge.writer_session_id for edge in edges] == ["other"]
    assert edges[0].ambiguous is False

    same_writer = [
        fact("file_write", at=1, target="a.py", content_hash="h1", session_id="writer"),
        fact("file_write", at=2, target="a.py", content_hash="h2", session_id="reader"),
        fact("file_read", at=3, target="a.py", session_id="reader"),
    ]
    ambiguous = build_provenance_edges(same_writer)
    assert len(ambiguous) == 1
    assert ambiguous[0].writer_session_id == "writer"
    assert ambiguous[0].ambiguous is True


def test_a_session_reading_back_its_own_write_is_not_an_edge() -> None:
    facts = [
        fact("file_write", at=1, target="a.py", content_hash="h1", session_id="s1"),
        fact("file_read", at=2, target="a.py", session_id="s1"),
    ]
    assert build_provenance_edges(facts) == []


def test_provenance_dedupe_is_per_edge_so_a_growing_graph_never_restates() -> None:
    # The old key hashed the whole edge *set*, so every evaluation of a growing
    # window minted a new annotation restating every prior edge — quadratic
    # storage, and one write→read event counted once per restatement by anything
    # ranking annotations (observed live 2026-07-28: a 2-edge row then a 6-edge
    # row repeating the same edges). Per-edge keys make an edge one row forever.
    from swe_mux.deterministic_consumers import provenance_dedupe_key

    facts = [
        fact("file_write", at=1, target="a.py", content_hash="h1", session_id="writer"),
        fact("file_read", at=2, target="a.py", session_id="reader"),
    ]
    grown = [*facts, fact("file_read", at=3, target="a.py", session_id="reader")]
    first = build_provenance_edges(facts)
    second = build_provenance_edges(grown)
    assert len(first) == 1 and len(second) == 2
    # The pre-existing edge keeps its key when the graph grows around it.
    assert provenance_dedupe_key("p1", first[0]) == provenance_dedupe_key("p1", second[0])
    # Distinct edges get distinct keys.
    assert provenance_dedupe_key("p1", second[0]) != provenance_dedupe_key("p1", second[1])


@pytest.mark.asyncio
async def test_reevaluating_the_same_window_writes_zero_new_provenance_rows(
    tmp_path: Path,
) -> None:
    from swe_mux.deterministic_consumers import ConsumerContext, DeterministicConsumerService

    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    store = AutomationStore(database)

    class Events:
        async def emit(self, kind: str, **payload: Any) -> None:
            pass

    async def context(_session_id: str) -> ConsumerContext:
        return ConsumerContext(
            project_id="p1",
            project_root=str(tmp_path),
            agent_run_id=None,
            enabled=frozenset({"provenance_graph"}),
        )

    try:
        now = time.time()
        await tier0.record_fact(
            session_id="writer",
            project_id="p1",
            kind="file_write",
            target="src/a.py",
            content_hash="h1",
            created_at=now - 30,
        )
        await tier0.record_fact(
            session_id="reader",
            project_id="p1",
            kind="file_read",
            target="src/a.py",
            created_at=now - 20,
        )
        service = DeterministicConsumerService(
            tier0,
            store,
            type("Sessions", (), {"sessions": {}})(),
            Events(),
            resolve_context=context,
        )
        first = await service.evaluate("reader")
        assert [item["tag"] for item in first] == ["provenance"]
        # Same window, next turn boundary: the edge is already recorded.
        assert await service.evaluate("reader") == []
        # A new edge writes exactly one new row, never a restatement.
        await tier0.record_fact(
            session_id="reader",
            project_id="p1",
            kind="file_read",
            target="src/a.py",
            created_at=now - 10,
        )
        third = await service.evaluate("reader")
        assert len(third) == 1
        assert json.loads(third[0]["evidence_json"])[0]["target"] == "src/a.py"
    finally:
        store.close()
        tier0.close()


# ------------------------------------------------------------- enablement DAG


def test_every_deterministic_consumer_is_implemented_and_needs_only_tier0() -> None:
    # The four that ship in step 3 are model-free queries over Tier 0. If one
    # grows a dependency on an unbuilt layer, its toggle silently stops working.
    for consumer_id in ("loop_detection", "declared_vs_verified", "doc_debt", "provenance_graph"):
        automation = REGISTRY[consumer_id]
        assert automation.implemented, consumer_id
        assert set(automation.requires) == {"tier0"}, consumer_id
        resolution = resolve({consumer_id, "tier0", "raw_store"})
        assert resolution.is_enabled(consumer_id), consumer_id


def test_unimplemented_automations_are_marked_so_the_toggle_cannot_mislead() -> None:
    # The toggle surface renders dependencies straight from this registry, so a
    # reserved id with a placeholder edge must not present as ready to enable.
    for automation_id in ("cross_session_interlocks",):
        assert REGISTRY[automation_id].implemented is False, automation_id
    # Phase 7.7 implemented the adaptive titler and its near-term consumers.
    for automation_id in (
        "continuous_title",
        "phase_transitions",
        "timeline_handoff",
        "catch_me_up",
        "live_blockers",
        "semantic_history_search",
        # Phase 7.11: whether agents may read this Project's timeline at all.
        # Its own consumer id, not the `scan_timeline` substrate, so a Project
        # can keep its timeline and still withhold it from sibling agents.
        "scan_reads",
    ):
        assert REGISTRY[automation_id].implemented is True, automation_id
        assert set(REGISTRY[automation_id].requires) == {"scan_timeline"}, automation_id
    # Project context is user-owned data, not an automation toggle.
    assert "project_card" not in REGISTRY
    assert REGISTRY["scan_timeline"].implemented is True
    # Phase 6.5 shipped: ranking, the digest, and the model tier over them.
    for automation_id in ("attention_ranking", "absence_report", "model_narration"):
        assert REGISTRY[automation_id].implemented is True, automation_id
    # Ranking reads every other signal; a one-dependency tree would be a lie.
    assert set(REGISTRY["attention_ranking"].requires) >= {
        "loop_detection",
        "declared_vs_verified",
        "doc_debt",
        "scan_timeline",
    }
    # Narration sits over ranked items, so with ranking off there is nothing to
    # narrate and no way to spend tokens on one.
    assert set(REGISTRY["model_narration"].requires) == {"attention_ranking"}


# --------------------------------------------------------------- store wiring


@pytest.mark.asyncio
async def test_findings_are_written_as_annotations_with_fact_evidence(tmp_path: Path) -> None:
    from swe_mux.deterministic_consumers import ConsumerContext, DeterministicConsumerService

    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    store = AutomationStore(database)
    emitted: list[dict[str, Any]] = []

    class Events:
        async def emit(self, kind: str, **payload: Any) -> None:
            emitted.append({"type": kind, **payload})

    async def context(_session_id: str) -> ConsumerContext:
        return ConsumerContext(
            project_id="p1",
            project_root=str(tmp_path),
            agent_run_id="run-1",
            enabled=frozenset({"loop_detection"}),
        )

    try:
        now = time.time()
        for index in range(4):
            await tier0.record_fact(
                session_id="s1",
                agent_run_id="run-1",
                project_id="p1",
                kind="command",
                target="pytest tests/test_a.py",
                fingerprint="stuck",
                created_at=now + index,
            )
        service = DeterministicConsumerService(
            tier0,
            store,
            type("Sessions", (), {"sessions": {}})(),
            Events(),
            resolve_context=context,
        )

        written = await service.evaluate("s1")
        assert [item["tag"] for item in written] == ["loop-detected"]
        evidence = json.loads(written[0]["evidence_json"])
        assert len(evidence) == 4
        assert all(item["fact_id"] for item in evidence)
        assert written[0]["agent_run_id"] == "run-1"
        assert written[0]["project_id"] == "p1"
        assert [item["type"] for item in emitted] == ["annotation_created"]

        # Idempotent: the same finding on a later turn does not duplicate.
        assert await service.evaluate("s1") == []
    finally:
        store.close()
        tier0.close()
