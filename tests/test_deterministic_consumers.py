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
    facts = [fact("command", at=index, fingerprint="same") for index in range(1, 5)]
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
        fact("command", at=index, fingerprint="same")
        for index in range(LOOP_REPEAT_THRESHOLD - 1)
    ]
    assert detect_loop(facts) is None


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
        fact("command", at=1, fingerprint="same"),
        fact("file_read", at=2, fingerprint="r", target="a.py"),
        fact("command", at=3, fingerprint="same"),
        fact("command", at=4, fingerprint="same"),
    ]
    finding = detect_loop(facts)
    assert finding is not None
    assert finding.repeats == 3


def test_new_file_content_in_the_window_is_progress() -> None:
    facts = [
        fact("command", at=1, fingerprint="same"),
        fact("file_write", at=2, fingerprint="w", target="a.py", content_hash="h1"),
        fact("command", at=3, fingerprint="same"),
        fact("command", at=4, fingerprint="same"),
    ]
    assert detect_loop(facts) is None


# ------------------------------------------------------- declared vs verified


def test_a_completion_claim_with_no_test_run_is_reported_as_three_facts() -> None:
    finding = detect_declared_vs_verified("Everything is fixed now.", [])
    assert finding is not None
    assert (finding.declared, finding.tests_ran, finding.tests_passed) == (True, False, False)
    # Declared, verified, and correct stay strictly apart — never one ✓.
    assert "claims done" in finding.content
    assert "tests not run" in finding.content
    assert "✓" not in finding.content


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
    for text in (
        "I'll start by reading the tests.",
        "The tests are in tests/test_core.py.",
        "This might be why it fails.",
    ):
        assert detect_declared_vs_verified(text, []) is None, text


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


def test_doc_debt_dedupe_key_is_stable_for_the_same_dirty_set() -> None:
    ownership = {normalize_target("a.py"): ("one.md",), normalize_target("b.py"): ("two.md",)}
    first = detect_doc_debt(
        [fact("file_write", at=1, target="a.py"), fact("file_write", at=2, target="b.py")],
        ownership,
    )
    second = detect_doc_debt(
        [fact("file_write", at=3, target="b.py"), fact("file_write", at=4, target="a.py")],
        ownership,
    )
    assert first is not None and second is not None
    # Order of discovery must not create a second ledger entry for one debt.
    assert doc_debt_dedupe_key("p1", first) == doc_debt_dedupe_key("p1", second)


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
    for automation_id in ("scan_timeline", "project_card", "attention_ranking"):
        assert REGISTRY[automation_id].implemented is False, automation_id
    # Ranking reads every other signal; a one-dependency tree would be a lie.
    assert set(REGISTRY["attention_ranking"].requires) >= {
        "loop_detection",
        "declared_vs_verified",
        "doc_debt",
        "scan_timeline",
    }


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
