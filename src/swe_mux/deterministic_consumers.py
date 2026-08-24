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
import shlex
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .background_tasks import background
from .tier0_store import MAX_TARGET_CHARS
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
#
# The copula is **required** in the it/this/that alternative. Optional, it matched
# ordinary English rather than a claim — "this working tree", "is it working,
# awaiting input", "leave it fixed and unexposed" — and that one alternative
# produced 27 of 42 lifetime findings, every sampled one of them false (measured
# 2026-08-21).
CLAIM_PATTERN = re.compile(
    r"\b("
    r"all (?:the )?tests? (?:now )?pass(?:es|ing)?"
    r"|tests? (?:are |now )?(?:all )?green"
    r"|(?:it|this|that|everything)(?:'s| is| are) (?:now )?(?:working|fixed|done)"
    r"|(?:i|we) (?:have )?(?:fixed|completed|finished|resolved) (?:it|this|that|the)"
    r"|(?:the )?(?:fix|change|implementation) is (?:complete|done)"
    r"|should (?:now )?be (?:fixed|working)"
    r")\b",
    re.IGNORECASE,
)

# A failure word immediately before the claim inverts it: "once shipped a failing
# test green" is a report of a defect, not a completion claim, and it fired the
# `tests … green` alternative verbatim. The window is deliberately short — the
# preceding few words, not the preceding sentence — because "I fixed the failing
# tests and all tests pass" is a real claim whose sentence also contains
# "failing".
_CLAIM_NEGATION_WINDOW = 16
_CLAIM_NEGATION = re.compile(
    r"\b(?:not|never|no|failing|failed|fails|red|broken|unless|until|without"
    r"|isn't|aren't|wasn't|don't|doesn't)\b\W*$",
    re.IGNORECASE,
)

# Code spans and fenced blocks are quotation, not assertion. Both anti-overclaim
# findings in the lifetime corpus fired on a message *quoting the requirement*
# ("Anti-overclaim (`all tests pass`) can fire when the model is quoting your
# requirement") — the pattern read the quoted rule as a claim of its own.
_FENCED_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_CODE_SPAN = re.compile(r"`[^`\n]*`")

# A completion claim is made in the closing summary. Searching an entire
# multi-thousand-word report reads its body — an audit quoting patterns, a plan
# describing a future state — as a verdict about the work just done.
CLAIM_SCOPE_CHARS = 1000

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

#: Kinds whose target is a shell command line rather than a path.
_SHELL_KINDS = frozenset({"command", "command_result", "test", "test_result"})

# Shell verbs that only *look*. A `command` fact is change-attempting by kind, but
# `grep`, `ls` and `git status` attempt nothing, so the no-progress gate is
# vacuously true for them exactly as it is for the `tool` and `file_read` kinds
# already excluded — and an agent polling a background task's output five times
# was flagged as looping (observed live 2026-08-21). This extends that same
# exclusion to the shell.
_READ_ONLY_VERBS = frozenset(
    {
        "grep", "rg", "ls", "dir", "find", "cat", "head", "tail", "wc", "less",
        "file", "stat", "du", "df", "netstat", "ps", "whoami", "hostname", "date",
        "echo", "which", "where", "sqlite3", "jq", "diff", "tree", "printenv", "env",
    }
)
#: `git` subcommands that only read. Anything else on `git` is not read-only.
_READ_ONLY_GIT = frozenset(
    {"status", "log", "diff", "show", "branch", "rev-parse", "blame", "describe",
     "ls-files", "worktree", "remote", "config", "stash"}
)
#: `curl` flags that make a request a write, either of the server's state or of a
#: local file. Case matters: `-F` is a form upload while `-f` is `--fail`.
_CURL_WRITE_FLAGS = ("-X", "-d", "--data", "-T", "--upload-file", "-F", "--form", "-o", "--output")
#: Shell operators that can turn a reading command into a writing one, matched as
#: whole tokens so a `|` inside a quoted regex is not read as a pipeline.
_PIPE_TOKENS = frozenset({"|"})
_EFFECT_TOKENS = frozenset({";", "&", "&&", "||", ">", ">>", "2>", "&>"})
#: Substitution, which can run anything at all inside an otherwise-reading command.
_SUBSTITUTION_MARKERS = ("$(", "`")


def _stage_is_read_only(tokens: list[str]) -> bool:
    verb = tokens[0].casefold().rsplit("/", 1)[-1].removesuffix(".exe")
    if verb == "git":
        # `git -C <dir> status` puts a flag *and its value* before the subcommand,
        # so the subcommand is not at a fixed position; accept it at either of the
        # first two non-flag tokens and refuse anything else.
        rest = [token for token in tokens[1:] if not token.startswith("-")]
        return any(token in _READ_ONLY_GIT for token in rest[:2])
    if verb == "curl":
        return not any(
            token.split("=", 1)[0] in _CURL_WRITE_FLAGS for token in tokens[1:]
        )
    return verb in _READ_ONLY_VERBS


def is_read_only_command(command: str) -> bool:
    """Whether a shell command line only reads.

    Conservative by construction, in the direction that preserves the detector: an
    unrecognised verb, a redirection, a substitution, an unparseable line, or a
    command the stored target had to truncate is **not** read-only, so it can
    still seed a loop. Only a command whose every pipeline stage begins with a
    known reading verb is excluded.

    Tokenised with `shlex` rather than split on characters, because the live case
    this exists for — `grep -nE '^(=== |verification passed)' …` — carries a `|`
    *inside a quoted regex*, and a character split reads its second half as a
    pipeline stage running a command named `verification`.
    """
    text = (command or "").strip()
    if not text or len(text) >= MAX_TARGET_CHARS:
        # Truncated: what was cut off is unknown, and a `> out.txt` past the bound
        # would make this a write. Unknown is not read-only.
        return False
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    stage: list[str] = []
    for token in tokens:
        if token in _EFFECT_TOKENS or token.startswith((">", "1>", "2>")):
            return False
        if any(marker in token for marker in _SUBSTITUTION_MARKERS):
            return False
        if token in _PIPE_TOKENS:
            if not stage or not _stage_is_read_only(stage):
                return False
            stage = []
            continue
        stage.append(token)
    return bool(stage) and _stage_is_read_only(stage)


def has_loop_discriminator(fact: dict[str, Any]) -> bool:
    """Whether a fact says *which* action it was.

    A fingerprint over an empty target, an empty content hash and an empty state
    is one constant for every call of that tool, so a window of it counts distinct
    actions as repeats of one. This is fail-closed on purpose: 25,362 Bash facts
    in one day shared the fingerprint of `{"scope":"root","tool":"Bash"}`, and 390
    of 397 lifetime loop findings rested on six such fingerprints (measured
    2026-08-21). A fact that cannot name its action does not seed a loop, and the
    capture fix that gives it a target is the other half of the same repair.
    """
    if fact.get("target") or fact.get("content_hash"):
        return True
    # A test result names itself by its failing set, which the fingerprint carries.
    return isinstance(_detail(fact).get("test_outcome"), dict)


def can_seed_loop(fact: dict[str, Any]) -> bool:
    """Whether one fact may *start* a loop finding. Every fact still feeds the gate."""
    kind = str(fact.get("kind") or "")
    if kind not in _LOOP_CANDIDATE_KINDS:
        return False
    if not has_loop_discriminator(fact):
        return False
    if kind in _SHELL_KINDS and is_read_only_command(str(fact.get("target") or "")):
        return False
    return True

# A source file claimed by more than this many docs is infrastructure — a
# composition root like `server.py` (15 claimants) or `App.tsx` (8) — not a
# subject any single doc owns, and it carries no ownership signal: touching it
# would mark every claimant dirty regardless of what changed. Calibrated against
# this repo's `.docs` tree (2026-07-28): 83/20/8/4 files carry 1–4 owners and
# every one of those has a genuine subject doc among its claimants
# (`tier0_store.py`, `ProviderAccounts.tsx`), then a clean break to the ≥5 tail
# which is exactly the composition roots and cross-cutting infra modules.
DOC_HUB_OWNER_LIMIT = 4

# The same rule one level out, for the Phase 7.9 dependency-reach refinement: a
# changed file reaching more than this many dependents is a hub by reach, and the
# docs owning those dependents are no more "the owners" of the change than the 15
# claimants of `server.py` are. `server.py` reaches 19-20 files at ≤2 hops, which
# is exactly how a 3-file edit produced 21 dirty docs.
DOC_REACH_DEPENDENT_LIMIT = 8


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


#: Directory names that make everything under them a test, by convention rather
#: than by spelling. Deliberately a closed set of exact segments: a substring
#: match is what let `attestation/` and `latest.py` read as tests.
_TEST_DIR_SEGMENTS = frozenset({"test", "tests", "__tests__", "spec", "__mocks__"})
#: Extensions that take the `<name>.test.<ext>` / `<name>.spec.<ext>` convention.
_TEST_SUFFIX_EXTENSIONS = ("js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts")
#: Whole basenames that are test infrastructure wherever they sit.
_TEST_BASENAMES = frozenset({"conftest.py"})


def is_test_path(path: str | None) -> bool:
    """True when a path is a test file by an explicit naming convention.

    Conventions, not spelling: a path segment that *is* a test directory
    (`tests/`, `test/`, `__tests__/`, `spec/`), or a basename matching
    `test_*.py`, `*_test.py`, `*_test.go`, `conftest.py`, or
    `<name>.test|spec.<js/ts ext>`.

    The predecessor was `"test" in path`, which classified `latest.py`,
    `contest.py`, `attestation.ts`, and every file under a `protest/` directory
    as tests. That fails in the unsafe direction twice over: `test_gap`
    *suppresses* a finding for anything it thinks is a test (so real untested
    code goes unreported), and `blast_radius` reports the same file as its own
    covering test (so a change looks covered when nothing exercises it).
    """
    if not path:
        return False
    text = path.strip().replace("\\", "/").casefold()
    if not text:
        return False
    segments = [segment for segment in text.split("/") if segment not in ("", ".")]
    if not segments:
        return False
    if any(segment in _TEST_DIR_SEGMENTS for segment in segments[:-1]):
        return True
    name = segments[-1]
    if name in _TEST_BASENAMES:
        return True
    if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
        return True
    if name.endswith("_test.go"):
        return True
    stem, _, extension = name.rpartition(".")
    if extension in _TEST_SUFFIX_EXTENSIONS and (
        stem.endswith(".test") or stem.endswith(".spec")
    ):
        return True
    return False


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
    """Fact references an annotation carries so a reader can re-check it.

    `content_hash` is part of the reference, not decoration: it is half of the
    discriminator that says a repeat was the *same* action, and a reader holding
    only targets cannot tell a finding that rests on evidence from one that rests
    on nothing (`loop_finding_unsupported`).
    """
    return [
        {
            "fact_id": fact.get("id"),
            "kind": fact.get("kind"),
            "target": fact.get("target"),
            "content_hash": fact.get("content_hash"),
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
        if not can_seed_loop(fact):
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


#: Why a stored loop finding does not stand when it is read back.
LOOP_UNSUPPORTED_REASON = (
    "Every fact behind this finding was recorded without a target or a content "
    "hash, so the repeat it rests on cannot be told apart from any other call of "
    "the same tool. Recorded before the capture fix; withheld rather than deleted."
)


def loop_finding_unsupported(evidence: Any) -> bool:
    """Whether a stored `loop-detected` finding still stands, judged at read time.

    The capture and detection fixes stop new findings like this from being
    written, but 390 of 397 already-stored ones rest on target-less facts and
    would go on being read as real. They are invalidated **here**, by the same
    rule the detector now applies, rather than by rewriting or deleting the rows:
    a stored finding is a record of what was concluded and stays exactly as it was
    concluded. Retracting it is the reader's job, and it is done in the open.

    Evidence recorded before this change carries no `content_hash` key at all; an
    absent key reads as absent evidence, which is the honest reading — nothing in
    the row asserts a discriminator was ever seen.
    """
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence or "[]")
        except (TypeError, ValueError):
            return False
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, dict):
            return False
        if item.get("target") or item.get("content_hash"):
            return False
    return True


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


def claim_scope(text: str, *, limit: int = CLAIM_SCOPE_CHARS) -> str:
    """The closing part of a message, with quotation removed.

    Two reductions, both aimed at the same error — reading text *about* work as a
    verdict *on* it. Fenced blocks and code spans are replaced by whitespace of
    the same shape, because a message quoting `all tests pass` is discussing the
    phrase; and the search is bounded to a whole number of trailing paragraphs
    within `limit`, because a claim is made in a summary, not on page four of a
    report.
    """
    stripped = _FENCED_BLOCK.sub(lambda m: " " * len(m.group(0)), text or "")
    stripped = _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), stripped)
    if len(stripped) <= limit:
        return stripped
    cut = len(stripped) - limit
    boundary = stripped.find("\n\n", cut)
    return stripped[boundary + 2 :] if boundary != -1 else stripped[cut:]


def _claim_negated(text: str, start: int) -> bool:
    """Whether a failure word sits immediately before the claim."""
    window = text[max(0, start - _CLAIM_NEGATION_WINDOW) : start]
    return _CLAIM_NEGATION.search(window) is not None


def claim_match(text: str) -> tuple[str, re.Match[str]] | None:
    """The completion claim in a message, with its scoped text, or None.

    One place, so every reader of "did this text claim done" applies the same
    three reductions: quotation removed, closing paragraphs only, and a claim
    whose immediately preceding words invert it is not a claim.
    """
    scoped = claim_scope(text or "")
    match = CLAIM_PATTERN.search(scoped)
    while match is not None and _claim_negated(scoped, match.start()):
        match = CLAIM_PATTERN.search(scoped, match.end())
    return (scoped, match) if match is not None else None


def detect_declared_vs_verified(
    claim_text: str,
    facts: Sequence[dict[str, Any]],
    *,
    claim_evidence: dict[str, Any] | None = None,
) -> VerificationFinding | None:
    """Keep "declared done", "tests passed", and "correct" strictly apart.

    Only a *claim without matching verification* is a finding: an agent that says
    it is done after a green run is reporting accurately and deserves no
    annotation. Passing tests are still never reported as correctness.

    **A run with no test facts at all produces nothing.** With zero `test_result`
    facts the detector cannot tell "this agent verified nothing" from "this
    install captured nothing", and the two mean opposite things about the agent —
    one `test_result` fact stood against 4,485 `command_result` facts in a 24-hour
    window, so the second reading was almost always the true one and every finding
    said "nothing verified" about a substrate rather than about a claim (measured
    2026-08-21). What remains is the checkable case: tests ran, they did not all
    pass, and the agent said it was done anyway.
    """
    found = claim_match(claim_text or "")
    if found is None:
        return None
    scoped, match = found
    tests = [fact for fact in facts if str(fact.get("kind") or "") in _TEST_KINDS]
    if not tests:
        return None
    latest = max(tests, key=lambda fact: fact.get("created_at") or 0.0)
    failing = _failing_set(latest)
    tests_passed = bool(failing is not None and not failing)
    if tests_passed:
        return None
    excerpt = scoped[max(0, match.start() - 80) : match.end() + 80].strip()
    return VerificationFinding(
        declared=True,
        tests_ran=True,
        tests_passed=False,
        claim=excerpt[:240],
        # The claim's own pointer first, so a reader can open the turn that made
        # it. Without it every finding in the lifetime corpus carried an empty
        # evidence set and broke the "evidence is a set" contract outright.
        evidence=[*( [claim_evidence] if claim_evidence else [] ), *_evidence(tests)],
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


#: Docs trees kept in the shared ownership cache. One entry per project root
#: that any consumer has asked about; the bound exists because the cache is
#: module-level and a daemon runs for weeks.
_OWNERSHIP_CACHE_MAX_ROOTS = 32
#: `docs_root -> (fingerprint, ownership map)`. Module-level so the MCP tools and
#: the deterministic-consumer loop share one build instead of each paying for a
#: full docs-tree parse (F22: `mcp.py` built it uncached on every `blast_radius`).
_OWNERSHIP_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, tuple[str, ...]]]] = {}


def docs_fingerprint(docs_root: Path) -> tuple[Any, ...]:
    """Identity of a docs tree: every markdown path with its `mtime_ns` and size.

    The path set is part of the fingerprint, not just a count, because a delete
    and a rename both leave the newest mtime untouched - keying on
    `max(mtime)` alone (the previous scheme) made them invisible and served a
    map that still owned a file no doc mentions.

    Size is carried for a second reason: Windows freezes a file's reported mtime
    while a handle is open, so a doc being written right now can grow without
    its mtime moving. The size moves.
    """
    entries: list[tuple[str, int, int]] = []
    try:
        candidates = sorted(docs_root.rglob("*.md"))
    except OSError:
        return ()
    for path in candidates:
        try:
            info = path.stat()
        except OSError:
            # A file that vanished between the walk and the stat is simply not
            # in this fingerprint; the next call will agree with itself.
            continue
        entries.append((path.as_posix(), int(info.st_mtime_ns), int(info.st_size)))
    return (len(entries), tuple(entries))


def cached_doc_ownership(
    docs_root: Path, *, hub_owner_limit: int = DOC_HUB_OWNER_LIMIT
) -> dict[str, tuple[str, ...]]:
    """`build_doc_ownership` behind a fingerprint cache shared by every consumer.

    Blocking (it stats and may parse the whole docs tree); call it off the event
    loop. The cache is deliberately lock-free: a concurrent miss costs a
    duplicate build and the last writer wins, which is exactly what the
    uncached callers did every time.
    """
    # The limit is part of the key: two callers asking with different hub limits
    # want different maps, and sharing one would serve whichever asked first.
    key = f"{docs_root.as_posix()}#{hub_owner_limit}"
    stamp = docs_fingerprint(docs_root)
    cached = _OWNERSHIP_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    ownership = build_doc_ownership(docs_root, hub_owner_limit=hub_owner_limit)
    if key not in _OWNERSHIP_CACHE and len(_OWNERSHIP_CACHE) >= _OWNERSHIP_CACHE_MAX_ROOTS:
        _OWNERSHIP_CACHE.pop(next(iter(_OWNERSHIP_CACHE)), None)
    _OWNERSHIP_CACHE[key] = (stamp, ownership)
    return ownership


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


def _reach_owners(
    target: str,
    ownership: dict[str, tuple[str, ...]],
    dependents: dict[str, tuple[str, ...]] | None,
    *,
    hub_owner_limit: int = DOC_HUB_OWNER_LIMIT,
    dependent_limit: int = DOC_REACH_DEPENDENT_LIMIT,
) -> tuple[str, ...]:
    """Docs owning a *dependent* of ``target`` — the Phase 7.9 reach refinement.

    The hub rule applies to reach exactly as it applies to direct ownership, and
    for the same reason. `build_doc_ownership` drops a file more than four docs
    claim because it carries no ownership signal; reach re-admitted that
    explosion through the back door by unioning the owners of *every* dependent,
    and one window's finding read "21 doc(s) owe an update for 3 changed source
    file(s)" — very nearly the whole `.docs` tree (measured 2026-08-21).

    So a file whose reverse reach exceeds `dependent_limit` is a hub by reach and
    contributes no owners, and a reach set resolving to more than
    `hub_owner_limit` docs is dropped whole. Both are the same statement: a
    signal that points at everything points at nothing. Truncating to the first N
    instead would report an arbitrary subset as *the* owners.
    """
    if not dependents:
        return ()
    reached = dependents.get(target, ())
    if len(reached) > dependent_limit:
        return ()
    owners: dict[str, None] = {}
    for dependent in reached:
        for owner in ownership.get(dependent, ()):
            owners.setdefault(owner, None)
    if len(owners) > hub_owner_limit:
        return ()
    return tuple(owners)


def detect_doc_debt(
    facts: Sequence[dict[str, Any]],
    ownership: dict[str, tuple[str, ...]],
    *,
    project_root: str | None = None,
    dependents: dict[str, tuple[str, ...]] | None = None,
) -> DocDebtFinding | None:
    """Accumulate doc debt from changed files; never nag per turn.

    A doc the same window already edited is not dirty — the debt was paid as it
    was incurred. The output is a ledger entry with a count, deliberately not an
    interrupt: one expensive pass at a stopping point beats forty interruptions.

    ``dependents`` is the Phase 7.9 dependency-reach refinement (optional, default
    off so behaviour is unchanged): a map from a changed file to the files that
    depend on it (its reverse callers/importers). When present, a doc that owns a
    *dependent* of a changed file is also dirty — changing a file can invalidate
    the documentation of the code that calls it, not only the file's own doc. It
    is a lower bound over the static graph, like every reach signal.
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
        reach_owners = _reach_owners(target, ownership, dependents)
        if not owners and not reach_owners:
            continue
        changed.setdefault(target, None)
        contributing.append(fact)
        for owner in (owners or ()):
            dirty.setdefault(owner, None)
        for owner in reach_owners:
            dirty.setdefault(owner, None)
    remaining = tuple(doc for doc in dirty if doc.casefold() not in edited_docs)
    if not remaining:
        return None
    return DocDebtFinding(
        dirty=remaining,
        changed=tuple(changed),
        evidence=_evidence(contributing),
    )


def doc_debt_content(doc: str, changed: Sequence[str], *, shown: int = 8) -> str:
    """One doc's debt, stated as the files it owes an update for."""
    listed = ", ".join(changed[:shown])
    more = f" (+{len(changed) - shown} more)" if len(changed) > shown else ""
    return (
        f"{doc} owes an update for {len(changed)} changed source file(s): {listed}{more}"
    )


def build_doc_debt_map(
    facts: Sequence[dict[str, Any]],
    ownership: dict[str, tuple[str, ...]],
    *,
    project_root: str | None = None,
    dependents: dict[str, tuple[str, ...]] | None = None,
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
        reach_owners = _reach_owners(target, ownership, dependents)
        if not owners and not reach_owners:
            continue
        for owner in (owners or ()):
            per_doc.setdefault(owner, {}).setdefault(target, None)
        # A doc owning a dependent of `target` owes an update *for* `target`: the
        # changed file it lists depends on the one that changed.
        for owner in reach_owners:
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


def doc_debt_dedupe_key(project_id: str, doc: str) -> str:
    # Per doc, not per dirty *set* — the same correction provenance made below,
    # for the same reason. A set hash changed whenever one more doc went dirty, so
    # every evaluation minted a new row restating all the others: 137 rows carried
    # 137 distinct keys, and one window's 8-doc set was a strict subset of the
    # 9-doc set beside it (measured 2026-08-21). Keyed on the doc, one dirty doc
    # is one row forever and its changed-file list lives in the content.
    return _dedupe_key("doc-debt", project_id, doc)


def provenance_dedupe_key(project_id: str, edge: ProvenanceEdge) -> str:
    # Per edge, not per edge *set*: a set-hash key changes whenever the graph
    # grows, so every evaluation of a growing window minted a new annotation
    # restating every prior edge — quadratic storage, and each edge counted
    # once per restatement by anything ranking annotations. Keyed on the two
    # fact ids, one real-world write→read event is exactly one row forever.
    return _dedupe_key(
        "provenance", project_id, f"{edge.writer_fact_id}>{edge.reader_fact_id}"
    )


def blast_radius_dedupe_key(agent_run_id: str, path: str) -> str:
    # One finding per edited file per run: the reach count lives in the content,
    # not the key, so a growing blast radius does not mint a second row for the
    # same edit the way a set-hash key would.
    return _dedupe_key("blast-radius", agent_run_id, path)


def unexamined_callers_dedupe_key(agent_run_id: str, path: str) -> str:
    return _dedupe_key("unexamined-callers", agent_run_id, path)


def code_structure_dedupe_key(project_id: str, kind: str, target: str) -> str:
    # Structural findings (dead code, god node, import cycle) are properties of
    # the project, keyed on the finding kind and its target so each is one row.
    return _dedupe_key("code-structure", project_id, kind, target)


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
# The same bound for doc debt, now that it writes one row per dirty doc rather
# than one row per dirty *set*. Not truncation: a doc past the cap keeps its debt
# and lands on the next turn boundary, because the per-doc key makes an
# already-recorded doc a no-op.
DOC_DEBT_MAX_NEW_PER_PASS = 20
# Code-graph (Phase 7.9) thresholds. A blast-radius finding fires only when an
# edit reaches at least this many dependents — a change with a handful of callers
# is not worth a human's attention, and the noise floor is what keeps the signal
# usable. Structural findings are bounded per pass and each dedupes to one row.
BLAST_MIN_REACH = 5
GOD_NODE_MIN_FAN_IN = 12
CODE_STRUCTURE_MAX_PER_PASS = 8


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
        code_graph: Any = None,
    ) -> None:
        self.tier0 = tier0
        self.store = store
        self.sessions = sessions
        self.events = events
        self._resolve_context = resolve_context
        self._docs_root_name = docs_root_name
        #: The Phase 7.9 structural graph store, or None when the graph substrate
        #: is not constructed. When present, the `code_graph` consumer maintains it
        #: off this same turn-boundary stream and reads it for blast-radius findings.
        self.code_graph = code_graph
        #: Projects whose source tree has had its one-time index. The consumer loop
        #: is serial, so a plain set needs no lock.
        self._graph_indexed: set[str] = set()
        self._queue: asyncio.Queue[Any] | None = None
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

        Fingerprinted rather than given a TTL: the map is derived from files in
        the repository the agent is editing, so a stale copy would report debt
        against a doc that was just updated. The fingerprint and the cache both
        live in `cached_doc_ownership`, which the MCP `blast_radius` tool shares
        - it used to rebuild the same map, uncached, on every call.
        """
        return cached_doc_ownership(Path(project_root) / self._docs_root_name)

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
        if context.wants("code_graph") and self.code_graph is not None:
            written.extend(await self._code_graph(context, session_id, run_facts))
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
        text, claim_evidence = await self._last_assistant_text(session_id)
        if not text:
            return []
        finding = detect_declared_vs_verified(text, facts, claim_evidence=claim_evidence)
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
        # Phase 7.9 precision upgrade: when the code graph is enabled, a doc owning
        # a *dependent* of a changed file also owes an update (dependency reach, not
        # only direct ownership). Off when the graph is absent — behaviour unchanged.
        dependents = await self._doc_debt_dependents(context, facts)
        debt = build_doc_debt_map(
            facts, ownership, project_root=context.project_root, dependents=dependents
        )
        if not debt:
            return []
        # One row per dirty doc. The finding used to be one row listing the whole
        # dirty set, which is the shape whose key could not be stable.
        writes: dict[str, list[dict[str, Any]]] = {}
        for fact in facts:
            if str(fact.get("kind") or "") not in _WRITE_KINDS:
                continue
            target = normalize_target(fact.get("target"), context.project_root)
            if target:
                writes.setdefault(target, []).append(fact)
        written: list[dict[str, Any]] = []
        for doc, changed in sorted(debt.items()):
            if len(written) >= DOC_DEBT_MAX_NEW_PER_PASS:
                break
            contributing = [fact for path in changed for fact in writes.get(path, ())]
            annotation = await self._annotate(
                context,
                tag="doc-debt",
                content=doc_debt_content(doc, changed),
                evidence=_evidence(contributing),
                dedupe_key=doc_debt_dedupe_key(context.project_id, doc),
                # Project-scoped: doc debt is a property of the repository, not of
                # the run that happened to incur the latest slice of it.
                run_scoped=False,
            )
            if annotation:
                written.append(annotation)
        return written

    async def _doc_debt_dependents(
        self, context: ConsumerContext, facts: Sequence[dict[str, Any]]
    ) -> dict[str, tuple[str, ...]] | None:
        """For each changed source file, the files that depend on it — so a doc
        owning a dependent is dirtied by dependency reach. None (no refinement)
        when the graph is absent or the project has not opted `code_graph` in."""
        if self.code_graph is None or not context.wants("code_graph"):
            return None
        from . import code_graph as cg

        changed: list[str] = []
        for fact in facts:
            if str(fact.get("kind") or "") not in _WRITE_KINDS:
                continue
            target = normalize_target(fact.get("target"), context.project_root)
            if target and cg.spec_for_path(target) is not None and target not in changed:
                changed.append(target)
        dependents: dict[str, tuple[str, ...]] = {}
        for target in changed:
            deps = await self.code_graph.reverse_dependents(context.project_id, target, hops=1)
            if deps:
                dependents[target] = tuple(dep.path for dep in deps)
        return dependents or None

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

    async def _code_graph(
        self, context: ConsumerContext, session_id: str, run_facts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Maintain the structural graph off this turn's writes, then emit the
        human-passive structural findings. Pull-only agent tools read the same
        graph on their own initiative; nothing here writes toward a session."""
        from . import code_graph as cg  # lazy: breaks the normalize_target cycle

        pid = context.project_id
        root = context.project_root
        # One-time index so reverse-dependency edges exist for importers this
        # session never edited. Runs at most once per project per process.
        if pid not in self._graph_indexed:
            self._graph_indexed.add(pid)
            try:
                await cg.index_project(self.code_graph, pid, root)
            except Exception as exc:  # noqa: BLE001 - graph upkeep never kills the loop
                self.last_error = f"code_graph index: {exc}"[:200]

        edited = self._graph_source_targets(run_facts, root, cg)
        if edited:
            try:
                await cg.maintain_files(self.code_graph, pid, root, edited)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"code_graph maintain: {exc}"[:200]

        written: list[dict[str, Any]] = []
        if context.agent_run_id:
            written.extend(await self._blast_radius(context, session_id, edited))
            written.extend(
                await self._unexamined_callers(context, session_id, run_facts, edited, root)
            )
        written.extend(await self._code_structure(context))
        return written

    @staticmethod
    def _graph_source_targets(
        facts: list[dict[str, Any]], root: str, cg: Any
    ) -> list[str]:
        """Normalized identities of the parseable source files written in the
        window, in first-seen order."""
        out: list[str] = []
        for fact in facts:
            if fact.get("kind") not in ("file_write", "file_write_result"):
                continue
            identity = normalize_target(fact.get("target"), root)
            # Same rule the graph itself uses: a worktree copy or generated file is
            # not part of the canonical tree, so it neither maintains the graph nor
            # earns a blast-radius finding.
            if identity is None or not cg.is_indexable_path(identity):
                continue
            if identity not in out:
                out.append(identity)
        return out

    async def _blast_radius(
        self, context: ConsumerContext, session_id: str, edited: list[str]
    ) -> list[dict[str, Any]]:
        written: list[dict[str, Any]] = []
        for path in edited:
            deps = await self.code_graph.reverse_dependents(context.project_id, path, hops=2)
            if len(deps) < BLAST_MIN_REACH:
                continue
            content = (
                f"Edit to {path} reaches {len(deps)} dependent file(s) within 2 hops. "
                "Static reverse-callers are a lower bound; dynamic dispatch (getattr, "
                "dict dispatch, decorators, DI, dynamic import) is not shown."
            )
            evidence = [
                {"path": dep.path, "hop": dep.hop, "via": dep.via} for dep in deps[:50]
            ]
            annotation = await self._annotate(
                context,
                tag="blast-radius",
                content=content,
                evidence=evidence,
                dedupe_key=blast_radius_dedupe_key(context.agent_run_id or "", path),
                session_id=session_id,
            )
            if annotation:
                written.append(annotation)
        return written

    async def _unexamined_callers(
        self,
        context: ConsumerContext,
        session_id: str,
        run_facts: list[dict[str, Any]],
        edited: list[str],
        root: str,
    ) -> list[dict[str, Any]]:
        """The mux-unique signal: reverse callers of an edited file that the
        session never opened, so it may have changed their behaviour blind. No
        standalone code-graph tool can produce this — it needs the agent's reads."""
        read_files = {
            n
            for fact in run_facts
            if fact.get("kind") in ("file_read", "file_read_result")
            if (n := normalize_target(fact.get("target"), root)) is not None
        }
        edited_set = set(edited)
        written: list[dict[str, Any]] = []
        for path in edited:
            callers = await self.code_graph.callers_of_symbol(context.project_id, path)
            caller_files = {
                c["src_path"] for c in callers if c["src_path"] != path
            }
            unexamined = sorted(caller_files - read_files - edited_set)
            if not unexamined:
                continue
            shown = ", ".join(unexamined[:8])
            content = (
                f"Edited {path}; {len(unexamined)} caller file(s) were not opened this "
                f"session and may be affected: {shown}. Static lower bound."
            )
            evidence = [{"caller": caller} for caller in unexamined[:50]]
            annotation = await self._annotate(
                context,
                tag="unexamined-callers",
                content=content,
                evidence=evidence,
                dedupe_key=unexamined_callers_dedupe_key(context.agent_run_id or "", path),
                session_id=session_id,
            )
            if annotation:
                written.append(annotation)
        return written

    async def _code_structure(
        self, context: ConsumerContext
    ) -> list[dict[str, Any]]:
        """Project-scoped structural findings: dead-code candidates, god nodes,
        and import cycles. Each dedupes to one row so re-running per turn is a
        no-op, and each is bounded per pass."""
        pid = context.project_id
        written: list[dict[str, Any]] = []
        budget = CODE_STRUCTURE_MAX_PER_PASS

        async def emit(kind: str, tag: str, target: str, content: str, evidence: Any) -> None:
            nonlocal budget
            if budget <= 0:
                return
            annotation = await self._annotate(
                context,
                tag=tag,
                content=content,
                evidence=evidence,
                dedupe_key=code_structure_dedupe_key(pid, kind, target),
                run_scoped=False,
            )
            if annotation:
                written.append(annotation)
                budget -= 1

        for orphan in await self.code_graph.orphans(pid):
            await emit(
                "dead-code",
                "dead-code",
                orphan["path"],
                f"{orphan['path']} has no inbound import or call in the graph — a "
                "dead-code candidate. Paths the graph could never draw an edge to "
                "(outside the root, generated or served output, tests) are already "
                "excluded; an entry point or a dynamic caller (plugin registry, "
                "CLI dispatch) is still a false positive.",
                [orphan],
            )
        for god in await self.code_graph.god_nodes(pid, min_fan_in=GOD_NODE_MIN_FAN_IN):
            await emit(
                "god-node",
                "god-node",
                god["path"],
                f"{god['path']} is imported or called by {god['fan_in']} files — a "
                "high fan-in hub whose change reaches widely.",
                [god],
            )
        for cycle in await self.code_graph.import_cycles(pid):
            signature = " -> ".join(sorted(cycle))
            await emit(
                "import-cycle",
                "import-cycle",
                signature,
                f"Import cycle among {len(cycle)} files: {' -> '.join(cycle)} -> "
                f"{cycle[0]}.",
                [{"cycle": cycle}],
            )
        return written

    async def _last_assistant_text(
        self, session_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        """The last assistant turn's text, and a pointer back to it.

        The pointer is what makes a claim finding checkable: the transcript and
        the message's own timestamp, so a reader can open the turn the claim was
        read from rather than trusting the excerpt.
        """
        session = self.sessions.sessions.get(session_id)
        if session is None:
            return "", None
        path = getattr(session, "transcript_path", None)
        native_id = session.record.native_session_id
        if not conversation_is_readable(path, session.record.backend, native_id):
            return "", None
        try:
            messages = await asyncio.wait_for(
                asyncio.to_thread(
                    _read_claim_tail, path, session.record.backend, native_id
                ),
                timeout=2,
            )
        except (OSError, TimeoutError):
            return "", None
        assistant = next(
            (item for item in reversed(messages) if item.get("role") == "assistant"), None
        )
        if not assistant:
            return "", None
        # Blocks are joined as paragraphs, not with a space: `claim_scope` reads
        # the closing paragraphs, and a space would fuse the whole turn into one.
        text = "\n\n".join(
            str(block.get("text") or "")
            for block in assistant.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        pointer = {
            "kind": "claim",
            "session_id": session_id,
            "agent_run_id": session.record.agent_run_id,
            "transcript": str(path) if path else None,
            "message_ts": assistant.get("ts"),
        }
        return text, pointer

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
