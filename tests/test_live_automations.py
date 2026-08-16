"""Tier A: a real agent drives the automations pipeline end to end, in process.

This is the automations half of the live suite. It runs a real authenticated CLI,
captures the Tier 0 facts that run actually produced (via `live_facts`, the same
observer-to-store path the daemon uses), and then asserts the deterministic
detectors and the mux cross-session memory tools over those *real* facts — not
fixtures, and not stubs. It binds no port and starts no daemon, so it stays
parallel-safe in any worktree exactly as `.worktree-verify` requires.

Coverage is derived, never skipped: a transcript-file harness that declares an
`automations` probe is driven here; opencode (no transcript to replay) is excluded
by that declared capability and covered by the store canary instead. The
`dead_ends`/`prior_resolutions` tools have no deterministic producer — the scan
timeline needs OpenRouter and the experience corpus is model-scored — so their tool
correctness is proven here against a real store round-trip rather than a stub, and
the real semantic pipeline is left to the manual OpenRouter-gated path documented in
scan-timeline.md.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.automation_store import AutomationStore
from swe_mux.harness import HARNESSES, live_automation_harnesses, live_canary_harnesses
from swe_mux.mcp import McpService
from swe_mux.tier0_store import Tier0Store
from tests.support.live_facts import capture_facts_from_transcript
from tests.test_live_agent_conformance import _probe_transcript
from tests.test_mcp import HistoryStub, live_session, manager_for

RUN_AUTO = os.environ.get("SWEMUX_RUN_LIVE_AUTOMATIONS_TESTS") == "1"
AUTO_HARNESSES = list(live_automation_harnesses())
# Harnesses that cannot form a same-run read-after-write edge, so the provenance
# canary excludes them by this stated limitation (never a silent skip); they still
# run the target-independent verified_status canary. codex reads a file through its
# shell/exec tool, which Tier 0 records as a `command` fact rather than a
# `file_read`, so there is no read fact to pair with a write within one codex run.
# Its writes DO now carry a target (the observer reads the path from
# patch_apply_end.changes, fixed 2026-08-16), so cross-harness lineage — a codex
# write read by another harness — works; only the codex-only pairing does not.
_PROVENANCE_EXCLUSIONS: dict[str, str] = {
    "codex": "codex reads via its shell/exec tool (a command fact, not a file_read)",
}
PROVENANCE_HARNESSES = [h for h in AUTO_HARNESSES if h not in _PROVENANCE_EXCLUSIONS]
ALL_MEMORY_AUTOMATIONS = frozenset(
    {"provenance_graph", "declared_vs_verified", "dead_end_memory", "prior_resolutions"}
)
_FILENAME = "calc.py"
# One physical line, no embedded newlines: codex and pi launch through a `.cmd`
# shim wrapped in `cmd.exe /c`, which truncates an argv value at its first newline,
# so a multi-line prompt reaches those CLIs cut off at the first break. The file
# body is therefore a single-line Python statement.
_WRITE_PROMPT = (
    "Use your file-writing or editing tool (not a shell command) to create a file "
    "named calc.py in the current directory containing exactly this one line of "
    "Python: def add(a, b): return a + b -- then reply with the single word "
    "WROTE_OK. Do not run any tests or other commands."
)
_READ_PROMPT = (
    "Use your file-reading tool to read the file named calc.py in the current "
    "directory. Then reply with the single word READ_OK. Do not modify it and do "
    "not run any commands."
)


def _projects(root: str, pid: str = "p1", name: str = "Work") -> Any:
    return SimpleNamespace(projects={pid: SimpleNamespace(id=pid, name=name, root=root)})


def _gate(enabled: frozenset[str] = ALL_MEMORY_AUTOMATIONS) -> Any:
    async def gate(_root: str) -> frozenset[str]:
        return enabled

    return gate


def _service(
    caller: Any,
    *,
    tier0: Any = None,
    store: Any = None,
    root: str = "D:/work",
    gate: Any = None,
) -> McpService:
    return McpService(
        manager_for(caller),
        HistoryStub(),
        automation_store=store,
        projects=_projects(root),
        tier0=tier0,
        automation_gate=gate or _gate(),
    )


# ---------------------------------------------------------------- coverage guard


# Transcript-file harnesses that deliberately declare no `automations` probe, each
# with the reason. Empty today: every transcript harness (claude, codex, omp, pi)
# can write a file and run a command, so all four are driven. A new transcript
# harness that genuinely cannot must be listed here with its reason, which is the
# adapter-matrix discipline — an omission fails the guard rather than passing
# silently.
_AUTOMATIONS_EXCLUSIONS: dict[str, str] = {}


def test_every_fact_producing_harness_is_covered_by_the_automations_canary() -> None:
    """A transcript harness that can write facts joins this canary, or states why not.

    The registry-derived guard that makes "add a harness" fail loudly until its
    automations coverage is declared, the sibling of the transcript and adapter
    coverage guards. A harness is driven here exactly when it is in the transcript
    canary and declares an `automations` probe; a store-backed harness (opencode) is
    excluded by having no transcript to replay, and a transcript harness with no
    automations probe must be an entry in `_AUTOMATIONS_EXCLUSIONS`, never a silent
    gap.
    """
    transcript_canary = set(live_canary_harnesses())
    for name, harness in HARNESSES.items():
        has_automations_probe = harness.headless_probes.automations is not None
        expected = name in transcript_canary and has_automations_probe
        assert (name in AUTO_HARNESSES) == expected, name
        if name not in transcript_canary:
            # No transcript file to replay: excluded here by that capability.
            assert name not in AUTO_HARNESSES, name
        elif not has_automations_probe:
            # A transcript harness that could produce facts but declares no
            # automations probe is a coverage hole unless it is a stated exclusion.
            assert name in _AUTOMATIONS_EXCLUSIONS, (
                f"{name} is a transcript harness with no automations probe and no "
                f"entry in _AUTOMATIONS_EXCLUSIONS; declare the probe or the exclusion"
            )
    # Exclusions must name a real transcript harness, so a stale entry is caught too.
    assert set(_AUTOMATIONS_EXCLUSIONS) <= transcript_canary
    assert AUTO_HARNESSES, "the automations canary covers no harness at all"
    # Provenance runs over the automation harnesses minus a stated set that cannot
    # produce a targeted write fact; every excluded name must be an automation
    # harness with a documented reason, and the partition must be exact.
    assert set(_PROVENANCE_EXCLUSIONS) <= set(AUTO_HARNESSES)
    assert all(reason for reason in _PROVENANCE_EXCLUSIONS.values())
    assert set(PROVENANCE_HARNESSES) == set(AUTO_HARNESSES) - set(_PROVENANCE_EXCLUSIONS)
    assert PROVENANCE_HARNESSES, "provenance canary covers no harness at all"


# --------------------------------------------------------- live automations tier


@pytest.mark.live_agent
@pytest.mark.live_automations
@pytest.mark.skipif(not RUN_AUTO, reason="set SWEMUX_RUN_LIVE_AUTOMATIONS_TESTS=1")
@pytest.mark.parametrize("backend", PROVENANCE_HARNESSES)
async def test_provenance_traces_a_real_cross_session_write_then_read(
    backend: str, tmp_path: Path
) -> None:
    """A real write in one run and a real read in another produce a lineage edge.

    Two real agent runs against the same working directory — one writes calc.py, one
    reads it — captured into one Tier 0 store under distinct session ids. The mux
    provenance tool must then report the cross-session read-after-write edge over
    those real facts, attributing each side to its own run rather than blaming one
    for the other.
    """
    store = Tier0Store(tmp_path / "tier0.db")
    try:
        writer_transcript = await _probe_transcript(
            backend, tmp_path, time.time(), _WRITE_PROMPT, mode="automations"
        )
        assert writer_transcript.exists()
        wrote = await capture_facts_from_transcript(
            backend,
            writer_transcript,
            store,
            session_id="writer",
            agent_run_id="writer-run",
            project_id="p1",
        )
        assert wrote > 0, f"{backend} write run produced no Tier 0 facts"

        reader_transcript = await _probe_transcript(
            backend, tmp_path, time.time(), _READ_PROMPT, mode="automations"
        )
        assert reader_transcript.exists()
        read = await capture_facts_from_transcript(
            backend,
            reader_transcript,
            store,
            session_id="reader",
            agent_run_id="reader-run",
            project_id="p1",
        )
        assert read > 0, f"{backend} read run produced no Tier 0 facts"

        caller = live_session("caller", token="tok", project_id="p1", scope_id="scope-1")
        service = _service(caller, tier0=store, root=str(tmp_path))
        result = await service.provenance(caller, {"file": _FILENAME})

        actions = {touch["action"] for touch in result["touches"]}
        assert "write" in actions, result
        assert "read" in actions, result
        assert result["cross_session_edges"], result
        edge = result["cross_session_edges"][0]
        assert edge["writer"]["run_relation"] == "sibling_run", edge
        assert edge["reader"]["run_relation"] == "sibling_run", edge
        assert edge["content_hash"], edge
    finally:
        store.close()


@pytest.mark.live_agent
@pytest.mark.live_automations
@pytest.mark.skipif(not RUN_AUTO, reason="set SWEMUX_RUN_LIVE_AUTOMATIONS_TESTS=1")
@pytest.mark.parametrize("backend", AUTO_HARNESSES)
async def test_verified_status_flags_a_real_unverified_claim(
    backend: str, tmp_path: Path
) -> None:
    """A real run that wrote code but ran no test is 'declared, not verified'.

    The run writes a file and makes no test, so a completion claim about it is
    unverified. `verified_status` over the run's real facts must say so — declared
    true, tests_ran false — rather than returning a bare check mark.
    """
    store = Tier0Store(tmp_path / "tier0.db")
    try:
        transcript = await _probe_transcript(
            backend, tmp_path, time.time(), _WRITE_PROMPT, mode="automations"
        )
        assert transcript.exists()
        captured = await capture_facts_from_transcript(
            backend,
            transcript,
            store,
            session_id="caller",
            agent_run_id="caller",
            project_id="p1",
        )
        assert captured > 0, f"{backend} run produced no Tier 0 facts"

        caller = live_session("caller", token="tok", project_id="p1", scope_id="scope-1")
        service = _service(caller, tier0=store, root=str(tmp_path))
        result = await service.verified_status(
            caller, {"claim": "the calc.py change is done and working"}
        )
        assert result["declared"] is True, result
        assert result["tests_ran"] is False, result
        assert result["verified"] is False, result
        assert "not run" in result["status"] or "nothing verified" in result["status"], result
    finally:
        store.close()


# -------------------------------------------- real-store tool correctness (offline)


async def test_dead_ends_and_prior_resolutions_read_a_real_store(tmp_path: Path) -> None:
    """The two model-fed memory tools read a real store round-trip, not a stub.

    Neither tool has a deterministic offline producer (the scan timeline needs
    OpenRouter; the experience corpus is model-scored), so their live producer is
    covered manually. This proves the read side against a real `AutomationStore`:
    a seeded dead-end scan record and a seeded experience row are returned with
    their run attribution and pass the confidence gate, while a low-confidence
    experience is withheld. Runs in the default tier — no agent, no quota.
    """
    tier0 = Tier0Store(tmp_path / "tier0.db")
    store = AutomationStore(tmp_path / "automation.db")
    try:
        await store.save_scan_record(
            session_id="s-abandon",
            agent_run_id="abandon-run",
            project_id="p1",
            t0=1.0,
            t1=2.0,
            trigger="manual",
            record={
                "approach_status": "abandoned",
                "dead_end": "tried a PTY marker; the CLI stopped emitting it",
                "intent": "detect the terminal state from a marker",
                "summary": "marker approach abandoned",
                "target": ["src/swe_mux/observation.py"],
                "confidence": 0.9,
            },
            input_hash="h",
            requested_model="m",
            resolved_model="m",
            generation_id=None,
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )
        await store.add_experience(
            project_scope_id="scope-1",
            backend="claude",
            error="ValueError: unknown backend: frob",
            resolution="register frob in the HARNESSES table",
            source_run_id="fix-run",
            confidence=0.9,
        )
        await store.add_experience(
            project_scope_id="scope-1",
            backend="claude",
            error="TypeError: object is not subscriptable",
            resolution="guessed fix, low confidence",
            source_run_id="weak-run",
            confidence=0.2,
        )

        caller = live_session("caller", token="tok", project_id="p1", scope_id="scope-1")
        service = McpService(
            manager_for(caller),
            HistoryStub(),
            automation_store=store,
            projects=_projects(str(tmp_path)),
            tier0=tier0,
            automation_gate=_gate(),
        )

        dead = await service.dead_ends(caller, {"subsystem": "observation"})
        assert len(dead["dead_ends"]) == 1, dead
        assert dead["dead_ends"][0]["approach_status"] == "abandoned", dead
        assert dead["dead_ends"][0]["run"]["run_relation"] == "sibling_run", dead

        prior = await service.prior_resolutions(
            caller, {"error": "ValueError: unknown backend: frob"}
        )
        assert len(prior["resolutions"]) == 1, prior
        assert prior["resolutions"][0]["source_run"]["run_relation"] == "sibling_run", prior

        # The low-confidence experience is withheld and only counted, never returned.
        weak = await service.prior_resolutions(
            caller, {"error": "TypeError: object is not subscriptable"}
        )
        assert weak["resolutions"] == [], weak
        assert weak["low_confidence_suppressed"] == 1, weak
    finally:
        store.close()
        tier0.close()
