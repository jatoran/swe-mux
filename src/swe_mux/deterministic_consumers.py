"""Model-free control-plane detectors over Tier 0 facts (roadmap Phase 3.7).

Control-plane build-order step 3 (`CONTROL_PLANE_ROADMAP.md` §6.1, §6.3–6.5).
Every detector here is a *query over deterministic facts*: no model call, no
token spend, and no path that writes toward a session. Output is annotations
only, anchored to the agent run (or the project, for project-scoped findings)
and carrying the exact facts the finding rests on.

Three properties are load-bearing and easy to lose:

- **Deterministic detector, never a narrator.** A finding states what the facts
  say. The cheap-model "why" is a separate, later layer (CP §14).
- **Evidence is a set.** A loop's case is "this fingerprint repeated three
  times and nothing moved"; recording one event pointer would not let anyone
  check it. Every annotation carries the contributing fact ids.
- **Per-project opt-in.** Nothing runs for a project that did not enable the
  consumer *and* its substrate; the enablement DAG resolves that, not this file.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .background_tasks import background
from .transcript_view import conversation_is_readable, parse_transcript_cached


def _read_claim_tail(
    path: Path | None, backend: str, native_id: str | None
) -> list[dict[str, Any]]:
    """Positional `parse_transcript_cached`, since `asyncio.to_thread` takes no keywords."""
    return parse_transcript_cached(
        path, backend, max_bytes=CLAIM_TRANSCRIPT_BYTES, native_id=native_id
    )

# A finding fires only after this many identical canonical actions. Matches the
# production precedent (Wink, ~43k traces) that calibrates a loop at three or
# more repeated/near-identical actions.
LOOP_REPEAT_THRESHOLD = 3

# Claim language that asserts completion. Deliberately narrow and literal: this
# is the *declared* half of declared-vs-verified, and a loose pattern turns every
# ordinary summary into a claim.
CLAIM_PATTERN = re.compile(
    r"\b("
    r"all (?:the )?tests? (?:now )?pass(?:es|ing)?"
    r"|tests? (?:are |now )?(?:all )?green"
    r"|(?:it|this|that|everything)(?:'s| is| are)? (?:now )?(?:working|fixed|done)"
    r"|(?:i|we) (?:have )?(?:fixed|completed|finished|resolved) (?:it|this|that|the)"
    r"|(?:the )?(?:fix|change|implementation) is (?:complete|done)"
    r"|should (?:now )?be (?:fixed|working)"
    r")\b",
    re.IGNORECASE,
)

_WRITE_KINDS = frozenset({"file_write", "file_write_result"})
_READ_KINDS = frozenset({"file_read", "file_read_result"})
_TEST_KINDS = frozenset({"test_result"})

# Only actions that *attempt change* can loop. A read-only action (kinds "tool"
# and "file_read" — Grep, Glob, file reads — and their results) produces no test
# outcome, no content hash and no commit, so the no-progress gate is vacuously
# true for it by construction: any agent that searches the same directory three
# times would be flagged. Repeated looking is not repeated failing — the Wink
# precedent behind LOOP_REPEAT_THRESHOLD measures repeated ineffective
# *attempts*. Non-test result kinds are also excluded: a result fingerprint
# collapses onto one value when the payload carries no content hash, so four
# distinct successful edits can share a single `file_write_result` fingerprint
# (observed live). `test_result` stays: its fingerprint carries the failing-set
# state, so identical repeats mean the same failures kept happening.
_LOOP_CANDIDATE_KINDS = frozenset({"command", "file_write", "test", "test_result"})

# A source file claimed by more than this many docs is infrastructure — a
# composition root like `server.py` (15 claimants) or `App.tsx` (8) — not a
# subject any single doc owns, and it carries no ownership signal: touching it
# would mark every claimant dirty regardless of what changed. Calibrated against
# this repo's `.docs` tree (2026-07-28): 83/20/8/4 files carry 1–4 owners and
# every one of those has a genuine subject doc among its claimants
# (`tier0_store.py`, `ProviderAccounts.tsx`), then a clean break to the ≥5 tail
# which is exactly the composition roots and cross-cutting infra modules.
DOC_HUB_OWNER_LIMIT = 4


def normalize_target(target: str | None, project_root: str | None = None) -> str | None:
    """Canonical form of a tool's target path for cross-fact comparison.

    Case-folded, forward-slashed, and made project-relative when it sits under the
    root, because the same file legitimately appears as an absolute path from one
    tool and a relative one from another.
    """
    if not target:
        return None
    text = target.strip().replace("\\", "/")
    if not text:
        return None
    if project_root:
        root = str(Path(project_root)).replace("\\", "/").rstrip("/")
        if root and text.casefold().startswith(root.casefold() + "/"):
            text = text[len(root) + 1 :]
    # Strip an explicit `./` prefix only. `lstrip("./")` strips *characters*, so
    # it would turn `.docs/design/x.md` into `docs/design/x.md` and quietly break
    # every dotfile path.
    while text.startswith("./"):
        text = text[2:]
    # casefold, not os.path.normcase: normcase rewrites separators on Windows, so
    # the canonical form would differ per platform and every comparison against a
    # posix-shaped path (a doc's "Key files" entry) would silently miss.
    return text.casefold()


def _detail(fact: dict[str, Any]) -> dict[str, Any]:
    raw = fact.get("detail_json")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _failing_set(fact: dict[str, Any]) -> frozenset[str] | None:
    outcome = _detail(fact).get("test_outcome")
    if not isinstance(outcome, dict):
        return None
    failing = outcome.get("failing_tests")
    if isinstance(failing, list):
        return frozenset(str(item) for item in failing)
    # A structured outcome with no named failures still carries its counts.
    failed = outcome.get("failed") or 0
    errors = outcome.get("errors") or 0
    return frozenset() if not failed and not errors else None


def _evidence(facts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fact references an annotation carries so a reader can re-check it."""
    return [
        {
            "fact_id": fact.get("id"),
            "kind": fact.get("kind"),
            "target": fact.get("target"),
            "fingerprint": fact.get("fingerprint"),
            "source_seq": fact.get("source_seq"),
            "created_at": fact.get("created_at"),
        }
        for fact in facts
    ]


def _dedupe_key(*parts: object) -> str:
    basis = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------- loop / stall


@dataclass(frozen=True, slots=True)
class LoopFinding:
    fingerprint: str
    repeats: int
    target: str | None
    kind: str
    evidence: list[dict[str, Any]]

    @property
    def content(self) -> str:
        where = f" on {self.target}" if self.target else ""
        return (
            f"The same {self.kind} action{where} ran {self.repeats} times with no "
            "objective progress in between (no shrinking failing-test set, no new "
            "file content, no new commit)."
        )


def detect_loop(facts: Sequence[dict[str, Any]]) -> LoopFinding | None:
    """Repeated canonical action with a no-progress gate.

    The gate is what keeps this from crying wolf on legitimate repeats: running
    the same test command four times while fixing things is *work*, not a loop.
    Only a repeat window in which nothing measurable moved is a finding, and even
    then it is evidence for the ranking layer rather than an interrupt.

    Only `_LOOP_CANDIDATE_KINDS` facts can seed a loop; every fact still feeds
    the progress gate.
    """
    ordered = sorted(facts, key=lambda fact: (fact.get("created_at") or 0.0))
    groups: dict[str, list[dict[str, Any]]] = {}
    for fact in ordered:
        if str(fact.get("kind") or "") not in _LOOP_CANDIDATE_KINDS:
            continue
        fingerprint = fact.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            groups.setdefault(fingerprint, []).append(fact)
    best: LoopFinding | None = None
    for fingerprint, group in groups.items():
        if len(group) < LOOP_REPEAT_THRESHOLD:
            continue
        window_start = group[0].get("created_at") or 0.0
        window_end = group[-1].get("created_at") or 0.0
        if _progress_in_window(ordered, window_start, window_end):
            continue
        if best is None or len(group) > best.repeats:
            best = LoopFinding(
                fingerprint=fingerprint,
                repeats=len(group),
                target=group[-1].get("target"),
                kind=str(group[-1].get("kind") or "tool"),
                evidence=_evidence(group),
            )
    return best


def _progress_in_window(
    facts: Sequence[dict[str, Any]], start: float, end: float
) -> bool:
    """True when something objectively moved between `start` and `end`."""
    window = [
        fact for fact in facts if start <= (fact.get("created_at") or 0.0) <= end
    ]
    # A failing-test set that got smaller is progress even if the command repeats.
    failing: list[frozenset[str]] = [
        found
        for fact in window
        if str(fact.get("kind") or "") in _TEST_KINDS
        and (found := _failing_set(fact)) is not None
    ]
    for earlier, later in zip(failing, failing[1:], strict=False):
        if later < earlier or (not later and earlier):
            return True
    # New file content, or a new commit, is progress.
    seen_before = {
        fact.get("content_hash")
        for fact in facts
        if (fact.get("created_at") or 0.0) < start and fact.get("content_hash")
    }
    for fact in window:
        digest = fact.get("content_hash")
        if (
            str(fact.get("kind") or "") in _WRITE_KINDS
            and isinstance(digest, str)
            and digest
            and digest not in seen_before
        ):
            seen_before.add(digest)
            return True
    heads = {
        str(_detail(fact).get("head") or "")
        for fact in window
        if str(fact.get("kind") or "") == "git"
    }
    return len({head for head in heads if head}) > 1


# ----------------------------------------------------------- declared/verified


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    declared: bool
    tests_ran: bool
    tests_passed: bool
    claim: str
    evidence: list[dict[str, Any]]

    @property
    def content(self) -> str:
        # Three facts, three clauses, never collapsed into one ✓. "Verified" is
        # the strongest word available here, and it still is not "correct".
        ran = "tests ran" if self.tests_ran else "tests not run"
        passed = (
            "tests passed"
            if self.tests_passed
            else ("tests failed" if self.tests_ran else "nothing verified")
        )
        return f"claims done · {ran} · {passed} — claim: {self.claim}"


def detect_declared_vs_verified(
    claim_text: str, facts: Sequence[dict[str, Any]]
) -> VerificationFinding | None:
    """Keep "declared done", "tests passed", and "correct" strictly apart.

    Only a *claim without matching verification* is a finding: an agent that says
    it is done after a green run is reporting accurately and deserves no
    annotation. Passing tests are still never reported as correctness.
    """
    match = CLAIM_PATTERN.search(claim_text or "")
    if not match:
        return None
    tests = [fact for fact in facts if str(fact.get("kind") or "") in _TEST_KINDS]
    tests_ran = bool(tests)
    latest = max(tests, key=lambda fact: fact.get("created_at") or 0.0) if tests else None
    failing = _failing_set(latest) if latest is not None else None
    tests_passed = bool(latest is not None and failing is not None and not failing)
    if tests_ran and tests_passed:
        return None
    excerpt = claim_text[max(0, match.start() - 80) : match.end() + 80].strip()
    return VerificationFinding(
        declared=True,
        tests_ran=tests_ran,
        tests_passed=tests_passed,
        claim=excerpt[:240],
        evidence=_evidence(tests),
    )


# ------------------------------------------------------------------ doc debt


_KEY_FILES_HEADING = re.compile(r"^#{2,3}\s+key files\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s")
_BACKTICKED = re.compile(r"`([^`]+)`")
_SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".css", ".toml", ".json")


def build_doc_ownership(
    docs_root: Path, *, hub_owner_limit: int = DOC_HUB_OWNER_LIMIT
) -> dict[str, tuple[str, ...]]:
    """Invert every doc's "Key files" section into `source path -> owning docs`.

    The routing table in `.docs/CLAUDE.md` is keyed by *change type*, which a
    machine cannot match against a file path. The per-doc "Key files" sections are
    the same routing information already written as literal paths, so they give a
    deterministic lookup with no heuristics and no second list to maintain: a doc
    that adopts a module by listing it is immediately covered.

    A file claimed by more than `hub_owner_limit` docs is dropped from the map
    entirely: it is infrastructure everyone touches, and keeping it would turn
    every edit to a composition root into debt against a page of unrelated docs.
    """
    ownership: dict[str, set[str]] = {}
    if not docs_root.is_dir():
        return {}
    for doc in sorted(docs_root.rglob("*.md")):
        try:
            lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        inside = False
        for line in lines:
            if _KEY_FILES_HEADING.match(line.strip()):
                inside = True
                continue
            if inside and _HEADING.match(line):
                inside = False
            if not inside:
                continue
            for candidate in _BACKTICKED.findall(line):
                path = candidate.strip()
                if not path.endswith(_SOURCE_SUFFIXES):
                    continue
                normalized = normalize_target(path)
                if normalized:
                    relative = doc.relative_to(docs_root).as_posix()
                    ownership.setdefault(normalized, set()).add(relative)
    return {
        path: tuple(sorted(docs))
        for path, docs in ownership.items()
        if len(docs) <= hub_owner_limit
    }


@dataclass(frozen=True, slots=True)
class DocDebtFinding:
    dirty: tuple[str, ...]
    changed: tuple[str, ...]
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def content(self) -> str:
        return (
            f"{len(self.dirty)} doc(s) owe an update for {len(self.changed)} changed "
            f"source file(s): {', '.join(self.dirty)}"
        )


def detect_doc_debt(
    facts: Sequence[dict[str, Any]],
    ownership: dict[str, tuple[str, ...]],
    *,
    project_root: str | None = None,
) -> DocDebtFinding | None:
    """Accumulate doc debt from changed files; never nag per turn.

    A doc the same window already edited is not dirty — the debt was paid as it
    was incurred. The output is a ledger entry with a count, deliberately not an
    interrupt: one expensive pass at a stopping point beats forty interruptions.
    """
    dirty: dict[str, None] = {}
    changed: dict[str, None] = {}
    contributing: list[dict[str, Any]] = []
    edited_docs: set[str] = set()
    for fact in facts:
        if str(fact.get("kind") or "") not in _WRITE_KINDS:
            continue
        target = normalize_target(fact.get("target"), project_root)
        if not target:
            continue
        if ".docs/" in target or target.startswith(".docs"):
            edited_docs.add(target.split(".docs/", 1)[-1])
            continue
        owners = ownership.get(target)
        if not owners:
            continue
        changed.setdefault(target, None)
        contributing.append(fact)
        for owner in owners:
            dirty.setdefault(owner, None)
    remaining = tuple(doc for doc in dirty if doc.casefold() not in edited_docs)
    if not remaining:
        return None
    return DocDebtFinding(
        dirty=remaining,
        changed=tuple(changed),
        evidence=_evidence(contributing),
    )


def build_doc_debt_map(
    facts: Sequence[dict[str, Any]],
    ownership: dict[str, tuple[str, ...]],
    *,
    project_root: str | None = None,
) -> dict[str, tuple[str, ...]]:
    """Invert doc debt to ``owning doc -> the changed source files that owe it``.

    The same rules `detect_doc_debt` applies — only write-kind facts, the
    hub-owner-limited ownership map, and a doc edited in the same window is not
    dirty — but it keeps the per-doc mapping the finding discards. The finding
    flattens debt to two parallel lists (`dirty` docs, `changed` files) for a
    human sentence; an agent needs to know *which* files each doc owes an update
    for, so the `doc_debt` MCP tool re-derives from this rather than scraping the
    finding.
    """
    per_doc: dict[str, dict[str, None]] = {}
    edited_docs: set[str] = set()
    for fact in facts:
        if str(fact.get("kind") or "") not in _WRITE_KINDS:
            continue
        target = normalize_target(fact.get("target"), project_root)
        if not target:
            continue
        if ".docs/" in target or target.startswith(".docs"):
            edited_docs.add(target.split(".docs/", 1)[-1])
            continue
        owners = ownership.get(target)
        if not owners:
            continue
        for owner in owners:
            per_doc.setdefault(owner, {}).setdefault(target, None)
    return {
        doc: tuple(files)
        for doc, files in per_doc.items()
        if doc.casefold() not in edited_docs
    }


# ------------------------------------------------------------ provenance graph


@dataclass(frozen=True, slots=True)
class ProvenanceEdge:
    """One factual read-after-write edge. Never a causal blame label.

    The write-side and read-side content hashes are not joinable by equality — a
    read result hashes the CLI's *rendering* of a file, not the file — so the edge
    is stated as `target` plus time order, carrying the writer's hash as the
    thing that was written. `ambiguous` marks the case where another write to the
    same target falls in between, which is exactly when "the reader saw this
    write" stops being a fact.
    """

    target: str
    writer_session_id: str
    writer_fact_id: str
    writer_content_hash: str | None
    written_at: float
    reader_session_id: str
    reader_fact_id: str
    read_at: float
    ambiguous: bool

    def snapshot(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "writer_session_id": self.writer_session_id,
            "writer_fact_id": self.writer_fact_id,
            "writer_content_hash": self.writer_content_hash,
            "written_at": self.written_at,
            "reader_session_id": self.reader_session_id,
            "reader_fact_id": self.reader_fact_id,
            "read_at": self.read_at,
            "ambiguous": self.ambiguous,
        }


def build_provenance_edges(
    facts: Sequence[dict[str, Any]], *, project_root: str | None = None
) -> list[ProvenanceEdge]:
    """Read-after-write edges across sessions, stated factually.

    Cross-session only: a session reading back what it just wrote is not the
    question this answers ("why did my thing suddenly break"). The result is a
    statement of what happened, in order — the human draws the conclusion.
    """
    ordered = sorted(facts, key=lambda fact: (fact.get("created_at") or 0.0))
    writes: dict[str, list[dict[str, Any]]] = {}
    edges: list[ProvenanceEdge] = []
    for fact in ordered:
        kind = str(fact.get("kind") or "")
        target = normalize_target(fact.get("target"), project_root)
        if not target:
            continue
        if kind in _WRITE_KINDS:
            writes.setdefault(target, []).append(fact)
            continue
        if kind not in _READ_KINDS:
            continue
        prior = writes.get(target) or []
        foreign = [
            item for item in prior if item.get("session_id") != fact.get("session_id")
        ]
        if not foreign:
            continue
        writer = foreign[-1]
        # Another write landed after the one being reported: the reader saw
        # *something* here, and which write it was is no longer a fact.
        ambiguous = prior[-1] is not writer
        edges.append(
            ProvenanceEdge(
                target=target,
                writer_session_id=str(writer.get("session_id") or ""),
                writer_fact_id=str(writer.get("id") or ""),
                writer_content_hash=writer.get("content_hash"),
                written_at=float(writer.get("created_at") or 0.0),
                reader_session_id=str(fact.get("session_id") or ""),
                reader_fact_id=str(fact.get("id") or ""),
                read_at=float(fact.get("created_at") or 0.0),
                ambiguous=ambiguous,
            )
        )
    return edges


def provenance_content(edge: ProvenanceEdge) -> str:
    digest = (edge.writer_content_hash or "")[:12]
    hashed = f" (content {digest})" if digest else ""
    qualifier = " — another write to the same file falls in between" if edge.ambiguous else ""
    return (
        f"session {edge.writer_session_id[:8]} wrote {edge.target}{hashed}; "
        f"session {edge.reader_session_id[:8]} read it afterwards{qualifier}"
    )


# ------------------------------------------------------------- annotation keys


def loop_dedupe_key(agent_run_id: str, finding: LoopFinding) -> str:
    return _dedupe_key("loop", agent_run_id, finding.fingerprint, finding.repeats)


def verification_dedupe_key(agent_run_id: str, finding: VerificationFinding) -> str:
    return _dedupe_key(
        "declared-vs-verified", agent_run_id, finding.claim, finding.tests_ran, finding.tests_passed
    )


def doc_debt_dedupe_key(project_id: str, finding: DocDebtFinding) -> str:
    return _dedupe_key("doc-debt", project_id, "\x1f".join(sorted(finding.dirty)))


def provenance_dedupe_key(project_id: str, edge: ProvenanceEdge) -> str:
    # Per edge, not per edge *set*: a set-hash key changes whenever the graph
    # grows, so every evaluation of a growing window minted a new annotation
    # restating every prior edge — quadratic storage, and each edge counted
    # once per restatement by anything ranking annotations. Keyed on the two
    # fact ids, one real-world write→read event is exactly one row forever.
    return _dedupe_key(
        "provenance", project_id, f"{edge.writer_fact_id}>{edge.reader_fact_id}"
    )


# --------------------------------------------------------------------- runner


@dataclass(frozen=True, slots=True)
class ConsumerContext:
    """What the enablement gate resolved for one session."""

    project_id: str
    project_root: str
    agent_run_id: str | None
    enabled: frozenset[str]

    def wants(self, automation_id: str) -> bool:
        return automation_id in self.enabled


CONSUMER_LOOP = "deterministic-consumers"
# Findings are attached to the run/project, so a window wider than the turn only
# re-reads facts an earlier pass already judged. Bounded for cost, not for truth.
RUN_FACT_WINDOW_SECONDS = 6 * 3600
PROJECT_FACT_WINDOW_SECONDS = 24 * 3600
# Bounded transcript read for the completion claim. Only the last assistant turn
# matters, and an unbounded read on the event path is the one heavy operation the
# design flags.
CLAIM_TRANSCRIPT_BYTES = 256 * 1024
# New provenance rows one evaluation may write. Bounds the first pass over a
# busy window; later passes pick up the remainder because per-edge dedupe skips
# everything already recorded.
PROVENANCE_MAX_NEW_PER_PASS = 50


class DeterministicConsumerService:
    """Runs the Phase 3.7 detectors on turn boundaries, per project opt-in.

    Deliberately event-driven rather than polled: every detector is a query over
    facts that only change when a turn produces them. It writes annotations and
    nothing else — no PTY write, no spawn, no model call, no spend.
    """

    def __init__(
        self,
        tier0: Any,
        store: Any,
        sessions: Any,
        events: Any,
        *,
        resolve_context: Callable[[str], Awaitable[ConsumerContext | None]],
        docs_root_name: str = ".docs",
    ) -> None:
        self.tier0 = tier0
        self.store = store
        self.sessions = sessions
        self.events = events
        self._resolve_context = resolve_context
        self._docs_root_name = docs_root_name
        self._queue: asyncio.Queue[Any] | None = None
        self._ownership: dict[str, tuple[dict[str, tuple[str, ...]], float]] = {}
        self.findings = 0
        self.last_error: str | None = None

    def start(self) -> None:
        if self._queue is not None:
            return
        self._queue = self.events.subscribe(name="deterministic-consumers")
        background.start(CONSUMER_LOOP, self._consume)

    async def stop(self) -> None:
        await background.stop(CONSUMER_LOOP)
        if self._queue is not None:
            self.events.unsubscribe(self._queue)
            self._queue = None

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            with background.iteration(CONSUMER_LOOP):
                if event.type == "turn_ended" and event.session_id:
                    await self.evaluate(str(event.session_id))

    def _ownership_for(self, project_root: str) -> dict[str, tuple[str, ...]]:
        """Cached `source path -> owning docs` map, rebuilt when the docs change.

        Keyed on the newest mtime in the docs tree rather than a TTL: the map is
        derived from files in the repository the agent is editing, so a stale copy
        would report debt against a doc that was just updated.
        """
        docs_root = Path(project_root) / self._docs_root_name
        try:
            stamp = max(
                (path.stat().st_mtime for path in docs_root.rglob("*.md")), default=0.0
            )
        except OSError:
            stamp = 0.0
        cached = self._ownership.get(project_root)
        if cached is not None and cached[1] == stamp:
            return cached[0]
        ownership = build_doc_ownership(docs_root)
        self._ownership[project_root] = (ownership, stamp)
        return ownership

    async def evaluate(self, session_id: str) -> list[dict[str, Any]]:
        """Run every enabled detector for one session's just-finished turn."""
        try:
            context = await self._resolve_context(session_id)
        except Exception as exc:  # noqa: BLE001 - a gate failure must not kill the loop
            self.last_error = str(exc)[:200]
            return []
        if context is None or not context.enabled:
            return []
        written: list[dict[str, Any]] = []
        now = time.time()
        run_facts: list[dict[str, Any]] = []
        if context.agent_run_id:
            run_facts = await self.tier0.facts_for_run(
                context.agent_run_id, since=now - RUN_FACT_WINDOW_SECONDS
            )
        if context.wants("loop_detection") and context.agent_run_id and run_facts:
            written.extend(await self._loop(context, run_facts))
        if context.wants("declared_vs_verified") and context.agent_run_id:
            written.extend(await self._declared(context, session_id, run_facts))
        if context.wants("doc_debt"):
            written.extend(await self._doc_debt(context, now))
        if context.wants("provenance_graph"):
            written.extend(await self._provenance(context, now))
        self.findings += len(written)
        return written

    async def _annotate(
        self,
        context: ConsumerContext,
        *,
        tag: str,
        content: str,
        evidence: list[dict[str, Any]],
        dedupe_key: str,
        session_id: str | None = None,
        run_scoped: bool = True,
    ) -> dict[str, Any] | None:
        annotation: dict[str, Any] = await self.store.create_annotation(
            agent_run_id=context.agent_run_id if run_scoped else None,
            project_id=context.project_id,
            session_id=session_id,
            tag=tag,
            content=content[:4000],
            evidence=evidence[:200],
            dedupe_key=dedupe_key,
            provenance="deterministic_consumer",
        )
        if annotation.get("duplicate"):
            return None
        await self.events.emit(
            "annotation_created",
            session_id=session_id,
            source="automation",
            annotation_id=annotation["id"],
            tag=tag,
            rule_id=None,
        )
        return annotation

    async def _loop(
        self, context: ConsumerContext, facts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        finding = detect_loop(facts)
        if finding is None:
            return []
        written = await self._annotate(
            context,
            tag="loop-detected",
            content=finding.content,
            evidence=finding.evidence,
            dedupe_key=loop_dedupe_key(context.agent_run_id or "", finding),
        )
        return [written] if written else []

    async def _declared(
        self, context: ConsumerContext, session_id: str, facts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        text = await self._last_assistant_text(session_id)
        if not text:
            return []
        finding = detect_declared_vs_verified(text, facts)
        if finding is None:
            return []
        written = await self._annotate(
            context,
            tag="declared-vs-verified",
            content=finding.content,
            evidence=finding.evidence,
            dedupe_key=verification_dedupe_key(context.agent_run_id or "", finding),
            session_id=session_id,
        )
        return [written] if written else []

    async def _doc_debt(
        self, context: ConsumerContext, now: float
    ) -> list[dict[str, Any]]:
        facts = await self.tier0.facts_for_project(
            context.project_id, since=now - PROJECT_FACT_WINDOW_SECONDS
        )
        ownership = await asyncio.to_thread(self._ownership_for, context.project_root)
        finding = detect_doc_debt(facts, ownership, project_root=context.project_root)
        if finding is None:
            return []
        written = await self._annotate(
            context,
            tag="doc-debt",
            content=finding.content,
            evidence=finding.evidence,
            dedupe_key=doc_debt_dedupe_key(context.project_id, finding),
            # Project-scoped: doc debt is a property of the repository, not of the
            # run that happened to incur the latest slice of it.
            run_scoped=False,
        )
        return [written] if written else []

    async def _provenance(
        self, context: ConsumerContext, now: float
    ) -> list[dict[str, Any]]:
        facts = await self.tier0.facts_for_project(
            context.project_id, since=now - PROJECT_FACT_WINDOW_SECONDS
        )
        edges = build_provenance_edges(facts, project_root=context.project_root)
        if not edges:
            return []
        # One annotation per edge: the per-edge dedupe key makes an edge that
        # was already recorded a no-op, so re-deriving the whole window on every
        # turn writes only what is new. The cap bounds one pass's writes (and
        # `annotation_created` fan-out) on a first evaluation of a busy window;
        # it is not truncation — the loop walks every edge, skipping duplicates,
        # and anything past the cap lands on the next turn boundary.
        written: list[dict[str, Any]] = []
        for edge in edges:
            if len(written) >= PROVENANCE_MAX_NEW_PER_PASS:
                break
            annotation = await self._annotate(
                context,
                tag="provenance",
                content=provenance_content(edge),
                evidence=[edge.snapshot()],
                dedupe_key=provenance_dedupe_key(context.project_id, edge),
                run_scoped=False,
            )
            if annotation:
                written.append(annotation)
        return written

    async def _last_assistant_text(self, session_id: str) -> str:
        session = self.sessions.sessions.get(session_id)
        if session is None:
            return ""
        path = getattr(session, "transcript_path", None)
        native_id = session.record.native_session_id
        if not conversation_is_readable(path, session.record.backend, native_id):
            return ""
        try:
            messages = await asyncio.wait_for(
                asyncio.to_thread(
                    _read_claim_tail, path, session.record.backend, native_id
                ),
                timeout=2,
            )
        except (OSError, TimeoutError):
            return ""
        assistant = next(
            (item for item in reversed(messages) if item.get("role") == "assistant"), None
        )
        if not assistant:
            return ""
        return " ".join(
            str(block.get("text") or "")
            for block in assistant.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        )

    def status(self) -> dict[str, Any]:
        loops = background.health().get("loops", [])
        running = any(
            item.get("name") == CONSUMER_LOOP and item.get("running") for item in loops
        )
        return {
            "findings": self.findings,
            "last_error": self.last_error,
            "running": running,
        }
