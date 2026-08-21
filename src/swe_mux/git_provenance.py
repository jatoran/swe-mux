"""Which session made a commit, and whose work is in it (roadmap Phase 7.8).

Two questions live here, and keeping them apart is the whole design:

- The **committer** is the one session whose process ran `git commit`. It is
  identified by isolating the commit *object* the command produced, never by
  reading `HEAD` back afterwards — a sibling session committing into the same
  checkout in between makes the read-back name the wrong commit.
- The **contributors** are the sessions whose file writes the commit contains.
  They are found by matching the commit's changed files against Tier 0
  `file_write` facts, and there can legitimately be more than one: git records a
  single author, so this is an answer no git tool holds.

A shared checkout is deliberately *not* a reason to give up on either. A shared
`HEAD` is a fact about the starting point, not about the commit event, and
treating it as ambiguity stamped nearly every commit in a multi-session checkout
`ambiguous` even on the path that watched the exact session run the command.

A third question used to be muddled into the first two and is now separate: **a
reference moved**. That is a fact about a *checkout*, not about a session. Every
session attached to a checkout watches the same move, so recording it per session
wrote one row each for sessions that had nothing to do with the commits — and a
landing fast-forward, which authors nothing at all, wrote the most of them. Ref
moves live in `git_ref_moves`, keyed by checkout, and a session row is written
only for commits the move actually *authored*.

Authored-versus-arrived is the distinction the whole module now turns on. A ref
that moves forward either gained commits that were written just now (a commit, a
merge, a rebase replay) or gained commits that already existed somewhere else (a
fast-forward, a pull, a landing). The first is attributable; the second is not,
and never was.

The observer stance holds throughout: nothing here writes a trailer, a hook, a
ref, a note, or a git identity. Attribution is derived from what mux already
observed, or it is not claimed at all.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .background_tasks import background
from .deterministic_consumers import normalize_target
from .event_bus import EventBus
from .git_monitor import (
    COMMIT_RANGE_LIMIT,
    GitCommitChange,
    GitCommitMetadata,
    GitPosition,
    read_blob_digest,
    read_commit_changes,
    read_commit_metadata,
    read_commit_range,
    read_excluded_range,
    read_git_position,
    read_is_ancestor,
    read_merge_resolution_changes,
)
from .history import (
    AUTHORING_ROLES,
    ROLE_BRANCH_AUTHOR,
    ROLE_COMMITTER,
    ROLE_CONTRIBUTOR,
    ROLE_INTEGRATOR,
    ROLE_OBSERVER,
    HistoryIndex,
)
from .models import MuxEvent
from .session import Session, SessionManager
from .tier0_store import Tier0Store

log = logging.getLogger(__name__)

_COMMAND_TOOL_MARKERS = ("bash", "shell", "exec", "command", "powershell", "terminal")
#: Every git subcommand that can leave a new commit object behind, mapped to the
#: shape of the evidence it produces. `git commit` was the only one recognized
#: before, which meant the single most common commit-creating command in a
#: worktree workflow — `git merge` — produced no committer evidence at all, and
#: the session that ran it was recorded exactly like the bystanders whose HEAD it
#: dragged along.
#:
#: `merge` is in this list even though a `--ff-only` merge creates nothing. Argv
#: cannot tell the two apart (a plain `git merge` fast-forwards whenever it can),
#: so the *outcome* decides: what the reference actually gained is classified
#: after the command, and a move that authored nothing yields no committer row.
_GIT_COMMIT_SUBCOMMANDS: dict[str, str] = {
    "commit": "commit",
    "merge": "merge",
    "cherry-pick": "cherry_pick",
    "revert": "revert",
    "rebase": "rebase",
    "am": "am",
}
#: Kinds that replay or rewrite a run of commits rather than adding one. All of
#: the commits such a command authors belong to the session that ran it, so they
#: are recorded together instead of being reduced to one answer.
_MULTI_COMMIT_KINDS = frozenset({"cherry_pick", "revert", "rebase", "am"})
_GIT_SUBCOMMAND = re.compile(
    r"(?:^|[;&|()]|\r?\n)\s*"
    r"(?:git|git\.exe|\"[^\"]*[\\/]git(?:\.exe)?\")\s+"
    r"(?:(?:-c\s+\S+|--no-pager)\s+)*"
    r"(commit|merge|cherry-pick|revert|rebase|am)(?:\s|$)",
    re.IGNORECASE,
)
#: Forms of the listed subcommands that resolve or abandon an operation rather
#: than creating anything. `--continue` does create commits, so it is absent.
_NON_CREATING_ARG = re.compile(
    r"(?:^|\s)--(?:abort|quit|skip|no-commit|show-current-patch)(?:\s|$)", re.IGNORECASE
)
_UNSAFE_REPOSITORY_REDIRECT = re.compile(
    r"(?:^|\s)(?:-C|--git-dir|--work-tree)(?:\s|=)", re.IGNORECASE
)
#: `-m` subject on a commit command. The strongest discriminator available when a
#: sibling session committed into the same checkout inside the same window: two
#: concurrent commits share a range and a minute, never a message.
_MESSAGE_ARG = re.compile(
    r"(?is)\bgit\s+(?:(?:-c\s+\S+|--no-pager)\s+)*commit\b.*?"
    r"\s-m\s+(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
)
_HEREDOC = re.compile(
    r"(?is)\bgit\s+(?:(?:-c\s+\S+|--no-pager)\s+)*commit\b[^\r\n]*"
    r"<<[\"']?([A-Za-z0-9_]+)[\"']?\r?\n([^\r\n]+)"
)
_PENDING_LIMIT = 512
_PENDING_MAX_AGE_SECONDS = 3600.0
GIT_PROVENANCE_LOOP = "git-provenance"

#: How a reference moved. These are the answers git can actually give, and they
#: replace the old non-answer "a merge or a rebase" — which described two
#: structurally distinguishable events as if they were one, on a code path that
#: never asked which had happened. Measured against this repository's own ledger,
#: every single move previously recorded as undecidable classifies here.
REF_MOVE_CREATED = "created"
REF_MOVE_MERGED = "merged"
REF_MOVE_FAST_FORWARD = "fast_forward"
REF_MOVE_REBASED = "rebased"
REF_MOVE_RESET = "reset"
REF_MOVE_UNKNOWN = "unknown"
#: Moves that added commits already written elsewhere. Nothing about the checkout
#: they landed in says who wrote them.
ARRIVAL_KINDS = frozenset({REF_MOVE_FAST_FORWARD, REF_MOVE_RESET, REF_MOVE_UNKNOWN})

#: Evidence ranks. Every attribution path introduced by Phase 7.8 outranks every
#: rank written before it (50 was the highest), so re-attribution promotes a
#: historical row in place instead of being refused by the ranked upsert.
COMMITTER_EXACT_RANK = 70
#: An integrator is the same strength of evidence as a committer — mux watched the
#: session run the command across a successful tool boundary — asked about a
#: different act. Deliberately equal, so the ranked upsert (which updates on `>=`)
#: reclassifies a merge row already recorded as a committer's.
INTEGRATOR_EXACT_RANK = COMMITTER_EXACT_RANK
CONTRIBUTOR_CONTENT_RANK = 65
CONTRIBUTOR_PATH_RANK = 60
#: Derived from the ledger's own answers about other commits rather than from an
#: observation of this one, so it sits below every direct match and above
#: occupancy: "wrote the branch this merge carries" is a better answer than "had
#: the directory open", and a worse one than "wrote these bytes".
BRANCH_AUTHOR_RANK = 40
COMMITTER_AMBIGUOUS_RANK = 35
MONITOR_OBSERVED_RANK = 20
MONITOR_RANGE_RANK = 15

#: Clock slack between a commit's own timestamp and the tool-call boundary that
#: produced it. Both come from this machine, so this covers a slow commit hook and
#: a second-resolution timestamp, not a genuine clock difference.
COMMIT_TIME_SLACK_SECONDS = 90.0
#: Oldest write a commit is allowed to claim. The parent commit's time is the real
#: floor whenever it is readable; this bounds the case where it is not.
CONTRIBUTOR_LOOKBACK_SECONDS = 7 * 86400.0
#: Files whose committed bytes are hashed for an exact content match, per commit.
CONTRIBUTOR_BLOB_READS = 25
#: Commits whose contributors have already been resolved. Every session in a
#: checkout observes the same HEAD move, so without this each one would re-read
#: the same commit.
_ATTRIBUTED_LIMIT = 512
#: How far back of a commit's own timestamp the monitor will still call it freshly
#: authored. The monitor polls attached checkouts every 5 s and sweeps detached
#: ones every 60 s, so this covers the widest gap in which a commit can first
#: appear on a HEAD it was written on, with room for a slow commit hook. A commit
#: older than this that turns up on a reference *arrived* there.
MONITOR_AUTHORSHIP_WINDOW_SECONDS = 300.0
#: Commits a single rewriting command (a rebase replay, a run of cherry-picks) is
#: credited with. Past this the run is recorded as a rewrite of that many commits
#: without enumerating each one, which keeps one command from writing fifty rows.
AUTHORED_COMMIT_LIMIT = 20
#: Authored commits whose contributors are resolved on one event. Contributor
#: resolution reads objects, so a fifty-commit rebase must not turn one event into
#: fifty file reads; the rest are picked up by the backfill's contributor pass.
CONTRIBUTOR_COMMITS_PER_EVENT = 3
#: Commits of a merge's own side examined when naming the branch it unified. A
#: branch longer than this is named from its newest commits; the answer is a set
#: of sessions, and a session that wrote fifty commits wrote one of the last ones.
BRANCH_LINE_LIMIT = COMMIT_RANGE_LIMIT
#: Parents whose metadata is read to floor a merge's contributor window. Octopus
#: merges are rare and bounded; this stops a pathological one from reading fifty.
MERGE_PARENT_READS = 8


def is_merge(commit: GitCommitMetadata | None) -> bool:
    """Whether this commit unified two lines rather than continuing one.

    The parent count is the whole test, and it is a property of the object rather
    than of the command that produced it: `git merge` fast-forwards whenever it
    can and then leaves no merge commit at all, while a merge commit reached by
    any other route is still a merge.
    """
    return commit is not None and len(commit.parents) > 1


@dataclass(slots=True, frozen=True)
class GitCommitCommand:
    relationship: str
    #: Which subcommand it was, which decides how many commits the outcome may
    #: legitimately be credited with. A `commit` produces one; a `rebase` produces
    #: as many as it replayed, and all of them belong to the session that ran it.
    kind: str = "commit"

    @property
    def multi(self) -> bool:
        return self.kind in _MULTI_COMMIT_KINDS


@dataclass(slots=True)
class PendingCommit:
    session_id: str
    session_name: str
    agent_run_id: str | None
    project_id: str
    call_id: str
    position: GitPosition | None
    relationship: str
    kind: str
    subject: str | None
    #: Wall clock at capture, used only to expire a pending call.
    started_at: float
    #: The tool call's own timestamp. Commit times and event times are both wall
    #: clock on this machine, so the authorship window is built from these rather
    #: than from `started_at` — which keeps the window correct when a transcript
    #: is read some time after the call it describes.
    event_ts: float


@dataclass(slots=True, frozen=True)
class CommitSelection:
    """Which commit object a commit command produced, and how sure that is."""

    commit: GitCommitMetadata | None
    method: str
    ambiguous: bool
    reason: str | None = None
    candidates: int = 0


@dataclass(slots=True, frozen=True)
class ContributorMatch:
    """One session whose observed writes appear in a commit."""

    session_id: str
    agent_run_id: str | None
    paths: tuple[str, ...]
    content_matched: bool

    @property
    def method(self) -> str:
        return "write_content" if self.content_matched else "write_path"

    @property
    def confidence(self) -> str:
        return "exact" if self.content_matched else "correlated"

    @property
    def evidence_rank(self) -> int:
        return CONTRIBUTOR_CONTENT_RANK if self.content_matched else CONTRIBUTOR_PATH_RANK


@dataclass(slots=True)
class PathCandidates:
    """Write facts that could account for one file the commit changed."""

    path: str
    blob: str | None
    confirmable: list[dict[str, Any]] = field(default_factory=list)
    positional: list[dict[str, Any]] = field(default_factory=list)


def classify_git_commit_command(tool: str, command: str | None) -> GitCommitCommand | None:
    """Recognize an explicit git invocation that can leave a new commit behind.

    Repository-redirection flags are rejected because resolving their shell quoting and
    environment safely would turn a provenance observer into another command interpreter.
    The checkout poller still records the resulting HEAD as an observation.

    Recognition stays narrow in *form* while being complete in *subcommand*. The
    previous version matched the literal token `commit` only, so `git merge` — the
    command a worktree workflow uses to reconcile and to land — created commits
    that no committer path ever saw. Recognizing it here does not by itself claim
    anything: a recognized command that turns out to have authored nothing (every
    `--ff-only` land) records a ref move and no committer row at all.
    """
    if not command or not any(marker in tool.casefold() for marker in _COMMAND_TOOL_MARKERS):
        return None
    if _UNSAFE_REPOSITORY_REDIRECT.search(command):
        return None
    match = _GIT_SUBCOMMAND.search(command)
    if match is None or _NON_CREATING_ARG.search(command):
        return None
    kind = _GIT_COMMIT_SUBCOMMANDS[match.group(1).casefold()]
    amended = bool(re.search(r"(?:^|\s)--amend(?:\s|$)", command))
    # A rebase rewrites by definition; an amend replaces the commit it names.
    rewrite = amended or kind == "rebase"
    return GitCommitCommand(relationship="rewrote" if rewrite else "created", kind=kind)


def commit_message_subject(command: str | None) -> str | None:
    """The subject line a commit command asked for, from `-m` or a heredoc.

    Read from the command text mux already observed, never by running anything.
    The value is compared with the commit object's own subject, so a quoting form
    this does not recognize costs one discriminator and never a wrong answer.
    """
    if not command:
        return None
    match = _MESSAGE_ARG.search(command)
    if match:
        try:
            value = str(ast.literal_eval(match.group(1)))
        except (SyntaxError, ValueError):
            value = match.group(1)[1:-1]
    else:
        heredoc = _HEREDOC.search(command)
        if heredoc is None:
            return None
        value = heredoc.group(2)
    subject = value.strip().splitlines()[0].strip() if value.strip() else ""
    return subject or None


@dataclass(slots=True, frozen=True)
class RefMove:
    """What a reference did, and which of the commits it gained were written now.

    `authored` is the commits this move brought into existence; `arrived` is the
    commits it merely started pointing at. Keeping them apart is what separates a
    session that ran `git merge` from three sessions that had the resulting commit
    fast-forwarded under them a few minutes later.
    """

    kind: str
    authored: tuple[GitCommitMetadata, ...]
    arrived: tuple[GitCommitMetadata, ...]
    total: int

    @property
    def is_arrival(self) -> bool:
        return not self.authored


def classify_ref_move(
    line: tuple[GitCommitMetadata, ...],
    *,
    head_oid: str,
    head_parents: tuple[str, ...],
    forward: bool | None,
    backward: bool | None,
    window_start: float,
    window_end: float,
    known_elsewhere: frozenset[str] = frozenset(),
) -> RefMove:
    """Decide what a HEAD move was, from facts rather than from a count.

    `line` is the **first-parent** range `previous..head`, newest first: the
    reference's own line of development, not the side branches it absorbed. That
    choice is the whole point. Full ancestry counts the commits a merge pulled in,
    so `git merge master` creating exactly one commit and a two-commit
    fast-forward both reported "two commits arrived at once" and both were given
    up on — including on the path that had watched a session run the merge.

    Two independent ancestry answers place the move: whether the old position is
    still reachable from the new one (forward), and whether the new one is
    reachable from the old (a rewind). A rebase is neither; a reset is the second.
    Nothing here is inferred from how many commits appeared.

    A commit counts as authored by this move when its own timestamp falls in the
    window *and* the ledger does not already hold it under another checkout.
    `known_elsewhere` is what makes a landing honest without any extra git work:
    when `worktree-x` merges into master, mux recorded those commits in the
    worktree minutes earlier, so the checkout they land in claims none of them.
    """
    if forward is None:
        # Git declined to place the move. Claiming authorship on a guess is the
        # one outcome worse than claiming nothing.
        return RefMove(REF_MOVE_UNKNOWN, (), line, len(line))
    if not forward:
        kind = REF_MOVE_RESET if backward else REF_MOVE_REBASED
        if kind == REF_MOVE_RESET:
            # The reference moved back onto commits that already existed. A rewind
            # authors nothing, whatever it makes the working tree look like.
            return RefMove(kind, (), line, len(line))
        authored = tuple(
            item
            for item in line
            if window_start <= item.committed_at <= window_end
            and item.oid not in known_elsewhere
        )
        return RefMove(
            kind, authored, tuple(item for item in line if item not in authored), len(line)
        )
    authored = tuple(
        item
        for item in line
        if window_start <= item.committed_at <= window_end and item.oid not in known_elsewhere
    )
    arrived = tuple(item for item in line if item not in authored)
    if not authored:
        return RefMove(REF_MOVE_FAST_FORWARD, (), arrived, len(line))
    merged = (
        len(head_parents) > 1
        and len(authored) == 1
        and authored[0].oid == head_oid
    )
    return RefMove(REF_MOVE_MERGED if merged else REF_MOVE_CREATED, authored, arrived, len(line))


def select_commit(
    candidates: tuple[GitCommitMetadata, ...],
    *,
    subject: str | None,
    window_start: float,
    window_end: float,
) -> CommitSelection:
    """Pick the commit a session's command produced out of the range it moved.

    `candidates` is `started_head..current_head`, newest first. One candidate is
    the ordinary case and settles it. More than one means another writer landed a
    commit in the same range, which is exactly where reading `HEAD` back gets the
    answer wrong, so the message subject decides, then the command's own time
    window. Nothing left to decide with is reported as ambiguous rather than
    guessed — a wrong committer is worse than a named unknown.
    """
    if not candidates:
        return CommitSelection(None, "none", False, "no_new_commit", 0)
    total = len(candidates)
    if total == 1:
        return CommitSelection(candidates[0], "command_range", False, None, total)
    if subject:
        matched = [item for item in candidates if item.subject.strip() == subject.strip()]
        if len(matched) == 1:
            return CommitSelection(matched[0], "command_subject", False, None, total)
    in_window = [
        item for item in candidates if window_start <= item.committed_at <= window_end
    ]
    if len(in_window) == 1:
        return CommitSelection(in_window[0], "command_window", False, None, total)
    return CommitSelection(
        candidates[0], "command_ambiguous", True, "concurrent_commits", total
    )


def _path_key(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _under(root: str, path: str) -> bool:
    """True when `path` names something inside `root` (or the root itself)."""
    if not root or not path:
        return False
    root_key = _path_key(root)
    path_key = _path_key(path)
    return path_key == root_key or path_key.startswith(root_key + os.sep)


def candidate_writes(
    changes: tuple[GitCommitChange, ...],
    facts: list[dict[str, Any]],
    *,
    worktree_root: str,
    session_roots: dict[str, str | None],
) -> list[PathCandidates]:
    """Group write facts under the commit paths they could account for.

    A fact qualifies for a path when its normalized target is that path *and* the
    write can be placed in this checkout. An absolute target that normalizes to a
    repository-relative path was written inside this worktree by construction; a
    relative target needs its session's checkout to say so. A fact that satisfies
    neither is kept only as `confirmable`: it counts if, and only if, its content
    turns out to be the bytes the commit stored.

    A *result* fact is never positional, whatever its path. A result hash is the
    CLI's rendering of what happened for most harnesses and the file's real bytes
    for a codex `patch_apply_end`, and nothing in the fact says which — so it is
    admitted as content evidence alone, and the hash equality is what decides. That
    keeps codex attributable (its call classifies as a command, so the result is
    its only targeted fact) without letting any harness's success message stand in
    for a write.
    """
    by_path: dict[str, PathCandidates] = {}
    for change in changes:
        normalized = normalize_target(change.path)
        if normalized:
            by_path.setdefault(
                normalized, PathCandidates(path=change.path, blob=change.blob)
            )
    for fact in facts:
        target = fact.get("target")
        if not isinstance(target, str) or not target:
            continue
        normalized = normalize_target(target, worktree_root)
        entry = by_path.get(normalized or "")
        if entry is None:
            continue
        absolute = os.path.isabs(target)
        result = str(fact.get("kind") or "").endswith("_result")
        placed = not result and (
            (absolute and normalized != normalize_target(target))
            or _under(worktree_root, session_roots.get(str(fact.get("session_id") or "")) or "")
        )
        if placed:
            entry.positional.append(fact)
        elif fact.get("content_hash"):
            entry.confirmable.append(fact)
    return [entry for entry in by_path.values() if entry.positional or entry.confirmable]


def resolve_contributors(
    candidates: list[PathCandidates], digests: dict[str, str | None]
) -> list[ContributorMatch]:
    """Turn per-path candidates into per-session contributions.

    Content equality is the strong answer: the digest of the bytes the commit
    stored against the digest the adapter took of the bytes the agent wrote. It is
    only available for a whole-file write — an edit tool hashes the replacement
    fragment, and a patch envelope hashes the patch — so path-and-time evidence
    remains the ordinary case, recorded as `correlated` rather than dressed up.

    Without a content match only the *last* write to a path counts. An earlier
    write that another session then replaced is not in the commit, and saying it
    is would invent a contributor.
    """
    contributions: dict[str, dict[str, Any]] = {}

    def add(fact: dict[str, Any], path: str, *, content_matched: bool) -> None:
        session_id = str(fact.get("session_id") or "")
        if not session_id:
            return
        entry = contributions.setdefault(
            session_id,
            {
                "agent_run_id": fact.get("agent_run_id") or None,
                "paths": [],
                "content_matched": False,
            },
        )
        if path not in entry["paths"]:
            entry["paths"].append(path)
        entry["content_matched"] = entry["content_matched"] or content_matched
        if content_matched or not entry["agent_run_id"]:
            entry["agent_run_id"] = fact.get("agent_run_id") or entry["agent_run_id"]

    for candidate in candidates:
        digest = digests.get(candidate.path)
        matched = False
        if digest:
            for fact in (*candidate.positional, *candidate.confirmable):
                if fact.get("content_hash") == digest:
                    add(fact, candidate.path, content_matched=True)
                    matched = True
        if matched or not candidate.positional:
            continue
        latest = max(candidate.positional, key=lambda fact: float(fact.get("created_at") or 0.0))
        add(latest, candidate.path, content_matched=False)
    return [
        ContributorMatch(
            session_id=session_id,
            agent_run_id=entry["agent_run_id"],
            paths=tuple(entry["paths"][:200]),
            content_matched=bool(entry["content_matched"]),
        )
        for session_id, entry in contributions.items()
    ]


def summarize_git_provenance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll durable rows up into one attribution per commit.

    The table stores one row per session per commit, because that is what each
    piece of evidence is about. A reader asks a different question — who made this
    commit, and whose work is in it — so the set is assembled here rather than
    denormalized into every row, where a later discovery would have to rewrite all
    of them.

    `attribution` is `exact` when a committer or integrator was isolated,
    `correlated` when only contributions or occupancy are known, and `ambiguous`
    when the commit is work mux never observed.

    A merge commit answers to *three* slots rather than one, because a landing
    merge has three true answers and collapsing them onto `committer` is what made
    the session that ran a land read as the author of somebody else's branch. The
    `integrator` made the merge; the `branch_authors` wrote the side it unified;
    the `contributors` wrote bytes it holds, which for a merge is the conflict
    resolution and nothing else.

    A retracted row is skipped rather than summarized. Retraction is the ledger's
    only weakening operation and it exists precisely so a row that turned out to
    record occupancy stops being read as an answer.
    """
    commits: dict[str, dict[str, Any]] = {}
    for row in rows:
        oid = str(row.get("commit_oid") or "")
        if not oid or row.get("retracted_at"):
            continue
        commit = commits.setdefault(
            oid,
            {
                "commit_oid": oid,
                "subject": row.get("subject") or "",
                "committed_at": row.get("committed_at"),
                "worktree_root": row.get("worktree_root") or "",
                "committer": None,
                "integrator": None,
                "branch_authors": [],
                "contributors": [],
                "attribution": "ambiguous",
            },
        )
        entry = {
            "session_id": row.get("session_id"),
            "session_name": row.get("session_name"),
            "agent_run_id": row.get("agent_run_id"),
            "confidence": row.get("confidence"),
            "match_method": row.get("match_method"),
            "paths": row.get("contributed_paths") or [],
        }
        role = str(row.get("role") or ROLE_OBSERVER)
        slot = "committer" if role == ROLE_COMMITTER else "integrator"
        if role in AUTHORING_ROLES:
            if not row.get("ambiguous"):
                commit[slot] = entry
                commit["attribution"] = "exact"
            elif commit[slot] is None:
                commit[slot] = entry
        elif role == ROLE_BRANCH_AUTHOR:
            commit["branch_authors"].append(entry)
        if entry["paths"]:
            commit["contributors"].append(entry)
    for commit in commits.values():
        named = (
            commit["contributors"]
            or commit["branch_authors"]
            or commit["committer"]
            or commit["integrator"]
        )
        if commit["attribution"] != "exact" and named:
            commit["attribution"] = "correlated"
    return list(commits.values())


class GitProvenanceService:
    """Attribute commits to the sessions that made them and wrote into them."""

    def __init__(
        self,
        history: HistoryIndex,
        sessions: SessionManager,
        events: EventBus,
        tier0: Tier0Store | None = None,
    ) -> None:
        self.history = history
        self.sessions = sessions
        self.events = events
        #: Contributor attribution reads Tier 0 write facts. Without the store (or
        #: with Tier 0 off for the project) committer attribution still works and
        #: the contributor set is simply empty — never guessed.
        self.tier0 = tier0
        self._queue: asyncio.Queue[MuxEvent] | None = None
        self._task: asyncio.Task[None] | None = None
        self._pending: dict[tuple[str, str], PendingCommit] = {}
        self._attributed: OrderedDict[tuple[str, str], float] = OrderedDict()
        #: Checkout of a session that has ended, from its durable History row. A
        #: contributor is often a session that finished before the commit landed.
        self._ended_roots: OrderedDict[str, str | None] = OrderedDict()
        #: Reference moves already written for a checkout. Same reason as
        #: `_attributed`: every session in a checkout reports the same move.
        self._moved: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._captured = 0
        self._contributors = 0
        self._branch_authors = 0
        self._moves = 0
        self._arrivals = 0
        self._suppressed = 0
        self._dropped = 0
        self._last_error: str | None = None
        self._last_error_ts: float | None = None

    def start(self) -> None:
        self._queue = self.events.subscribe(name="git-provenance")
        self._task = background.start(GIT_PROVENANCE_LOOP, self._consume)

    async def stop(self) -> None:
        if self._queue is not None:
            self.events.unsubscribe(self._queue)
        if self._task is not None:
            await background.stop(GIT_PROVENANCE_LOOP)
        self._queue = None
        self._task = None
        self._pending.clear()
        self._attributed.clear()
        self._ended_roots.clear()
        self._moved.clear()

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._task and not self._task.done()),
            "captured": self._captured,
            "contributors": self._contributors,
            "branch_authors": self._branch_authors,
            "ref_moves": self._moves,
            # Moves that brought in commits written elsewhere, and bystander rows
            # not written because another session is known to have made the
            # commit. Both are answers, not failures, and are counted so a reader
            # can see how much of the fleet's git traffic is arrival rather than
            # authorship.
            "arrivals": self._arrivals,
            "suppressed": self._suppressed,
            "dropped": self._dropped,
            "pending": len(self._pending),
            "last_error": self._last_error,
            "last_error_ts": self._last_error_ts,
        }

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            with background.iteration(GIT_PROVENANCE_LOOP):
                try:
                    await self.handle_event(event)
                except Exception as exc:
                    self._dropped += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._last_error_ts = time.time()
                    raise

    async def handle_event(self, event: MuxEvent) -> None:
        if not event.session_id:
            return
        if event.type == "tool_use":
            await self._note_tool_use(event)
        elif event.type == "tool_result":
            await self._note_tool_result(event)
        elif event.type == "git_changed":
            await self._note_git_change(event)

    def _session(self, session_id: str) -> Session | None:
        return self.sessions.sessions.get(session_id)

    async def _position(self, session: Session) -> GitPosition | None:
        git = session.record.git
        if git.root and git.head:
            return GitPosition(git.root, git.head)
        return await read_git_position(session.record.git_cwd)

    def _trim_pending(self, now: float) -> None:
        for key, pending in tuple(self._pending.items()):
            if now - pending.started_at > _PENDING_MAX_AGE_SECONDS:
                self._pending.pop(key, None)
        while len(self._pending) >= _PENDING_LIMIT:
            self._pending.pop(next(iter(self._pending)))

    def _claim_attribution(self, root: str, commit_oid: str) -> bool:
        """True the first time a commit's contributors are worth resolving.

        Every session attached to a checkout sees the same HEAD move, so the
        monitor path would otherwise re-read one commit once per session.
        """
        key = (_path_key(root), commit_oid)
        if key in self._attributed:
            return False
        self._attributed[key] = time.time()
        while len(self._attributed) > _ATTRIBUTED_LIMIT:
            self._attributed.popitem(last=False)
        return True

    def _claim_move(self, root: str, previous_head: str, head: str) -> bool:
        """True the first time one checkout's reference move is worth recording."""
        key = (_path_key(root), previous_head.lower(), head.lower())
        if key in self._moved:
            return False
        self._moved[key] = time.time()
        while len(self._moved) > _ATTRIBUTED_LIMIT:
            self._moved.popitem(last=False)
        return True

    async def _note_tool_use(self, event: MuxEvent) -> None:
        payload = event.payload or {}
        call_id = str(payload.get("call_id") or "")
        raw_command = payload.get("target") if isinstance(payload.get("target"), str) else None
        command = classify_git_commit_command(str(payload.get("tool") or ""), raw_command)
        session = self._session(event.session_id or "")
        if not call_id or command is None or session is None:
            return
        now = time.time()
        self._trim_pending(now)
        self._pending[(session.record.id, call_id)] = PendingCommit(
            session_id=session.record.id,
            session_name=session.record.name,
            agent_run_id=session.record.agent_run_id or None,
            project_id=session.record.project_id,
            call_id=call_id,
            position=await self._position(session),
            relationship=command.relationship,
            kind=command.kind,
            subject=commit_message_subject(raw_command),
            started_at=now,
            event_ts=event.ts,
        )

    async def _analyze_move(
        self,
        root: str,
        project_id: str,
        previous_head: str | None,
        head: str,
        *,
        window_start: float,
        window_end: float,
        observed_at: float,
    ) -> tuple[RefMove, GitCommitMetadata | None]:
        """Read what a reference did, and the metadata of the commit now on top.

        Bounded to three git reads plus one indexed ledger query, regardless of how
        far the reference moved: two ancestry answers and one first-parent range.
        """
        tip = await read_commit_metadata(root, head)
        line: tuple[GitCommitMetadata, ...]
        forward: bool | None
        backward: bool | None
        if previous_head is None:
            # With no starting position there is no range, only the commit now on
            # top. Listing history back from it would offer fifty candidates for a
            # question that has one honest answer.
            line = (tip,) if tip is not None else ()
            forward, backward = True, False
        else:
            forward, backward = await asyncio.gather(
                read_is_ancestor(root, previous_head, head),
                read_is_ancestor(root, head, previous_head),
            )
            line = await read_commit_range(
                root, previous_head, head, limit=COMMIT_RANGE_LIMIT, first_parent=True
            )
        if not line:
            return RefMove(REF_MOVE_UNKNOWN, (), (), 0), tip
        known = await self._known_elsewhere(
            project_id, root, [item.oid for item in line], observed_at
        )
        move = classify_ref_move(
            line,
            head_oid=head.lower(),
            head_parents=tip.parents if tip is not None else (),
            forward=forward,
            backward=backward,
            window_start=window_start,
            window_end=window_end,
            known_elsewhere=known,
        )
        return move, tip

    async def _known_elsewhere(
        self, project_id: str, root: str, oids: list[str], before: float
    ) -> frozenset[str]:
        """Commits another checkout recorded *before* this observation.

        The arrival oracle, and it costs nothing extra: when a worktree branch
        lands, mux recorded its commits in the worktree minutes before the primary
        checkout ever saw them, so the ledger can say "these arrived" without a
        single additional git call.

        `before` is what keeps the oracle pointing one way. Without it the test is
        symmetric — after a landing *both* checkouts hold the commit — and the
        worktree that actually created it would read its own work as an arrival
        because the checkout it later landed in also has a row.
        """
        if not oids or not project_id:
            return frozenset()
        claims = await self._commit_claims(project_id, oids)
        key = _path_key(root)
        return frozenset(
            oid
            for oid, claim in claims.items()
            if any(
                _path_key(other) != key and seen < before
                for other, seen in claim["first_seen"].items()
            )
        )

    async def _commit_claims(
        self, project_id: str, oids: list[str]
    ) -> dict[str, dict[str, Any]]:
        return await self.history.git_commit_claims(
            project_id=project_id, commit_oids=oids[:COMMIT_RANGE_LIMIT]
        )

    async def _record_move(
        self,
        *,
        project_id: str,
        root: str,
        previous_head: str,
        head: str,
        move: RefMove,
        tip: GitCommitMetadata | None,
        observed_at: float,
    ) -> None:
        await self.history.record_git_ref_move(
            project_id=project_id,
            worktree_root=root,
            commit_oid=head,
            previous_head=previous_head,
            kind=move.kind,
            commit_count=move.total,
            authored_count=len(move.authored),
            subject=tip.subject if tip is not None else "",
            committed_at=tip.committed_at if tip is not None else None,
            observed_at=observed_at,
        )
        self._moves += 1

    async def _note_tool_result(self, event: MuxEvent) -> None:
        payload = event.payload or {}
        call_id = str(payload.get("call_id") or "")
        pending = self._pending.pop((event.session_id or "", call_id), None)
        if pending is None or payload.get("success") is not True:
            return
        session = self._session(pending.session_id)
        if session is None:
            return
        current = await read_git_position(session.record.git_cwd)
        if current is None:
            return
        started_head = pending.position.head if pending.position else None
        if pending.position is not None:
            if _path_key(pending.position.root) != _path_key(current.root):
                return
            if pending.position.head == current.head:
                # The command succeeded without moving HEAD. For `git merge` that
                # is the ordinary "already up to date"; for `git commit` it means
                # whatever it did, it did not commit.
                return
        window_start = min(pending.event_ts, event.ts) - COMMIT_TIME_SLACK_SECONDS
        window_end = max(pending.event_ts, event.ts) + COMMIT_TIME_SLACK_SECONDS
        move, tip = await self._analyze_move(
            current.root,
            pending.project_id,
            started_head,
            current.head,
            window_start=window_start,
            window_end=window_end,
            observed_at=event.ts,
        )
        if started_head:
            await self._record_move(
                project_id=pending.project_id,
                root=current.root,
                previous_head=started_head,
                head=current.head,
                move=move,
                tip=tip,
                observed_at=event.ts,
            )
        if move.is_arrival:
            # The command moved the reference onto commits that already existed —
            # a `--ff-only` land, a pull, a reset. It authored nothing, so it gets
            # no committer row. This is the case that used to produce an
            # `ambiguous` claim against every session in the checkout.
            return
        authored = move.authored
        if pending.kind in _MULTI_COMMIT_KINDS:
            # A replay's commits all belong to the session that ran it. There is
            # nothing to disambiguate between them.
            await self._record_authored_run(
                session, pending, authored, current.root, move, event, call_id
            )
            return
        selection = select_commit(
            authored, subject=pending.subject, window_start=window_start, window_end=window_end
        )
        if selection.commit is None:
            return
        await self._record_authored_run(
            session,
            pending,
            (selection.commit,),
            current.root,
            move,
            event,
            call_id,
            ambiguous=selection.ambiguous,
            match_method=selection.method,
        )

    async def _record_authored_run(
        self,
        session: Session,
        pending: PendingCommit,
        authored: tuple[GitCommitMetadata, ...],
        root: str,
        move: RefMove,
        event: MuxEvent,
        call_id: str,
        *,
        ambiguous: bool = False,
        match_method: str | None = None,
    ) -> None:
        """Write the committer rows for the commits one command actually created.

        A merge commit is the one shape where "the session that ran the command"
        and "the session whose work this is" are different answers, so it is
        written as an integration and the branch it unified is named beside it.
        """
        method = match_method or f"command_{move.kind}"
        for index, commit in enumerate(authored[:AUTHORED_COMMIT_LIMIT]):
            fresh = (
                index < CONTRIBUTOR_COMMITS_PER_EVENT
                and self._claim_attribution(root, commit.oid)
            )
            contributors = (
                await self._resolve_contributors(root, pending.project_id, commit)
                if fresh
                else []
            )
            own = next(
                (item for item in contributors if item.session_id == pending.session_id), None
            )
            merge = is_merge(commit)
            await self._record(
                session,
                worktree_root=root,
                commit=commit,
                previous_head=(commit.parents[0] if commit.parents else None),
                relationship=(
                    "merged"
                    if merge and pending.relationship == "created"
                    else pending.relationship
                ),
                confidence="ambiguous" if ambiguous else "exact",
                ambiguous=ambiguous,
                source="session_tool",
                source_event_seq=event.seq or None,
                tool_call_id=call_id,
                evidence_rank=(
                    COMMITTER_AMBIGUOUS_RANK if ambiguous else COMMITTER_EXACT_RANK
                ),
                observed_at=event.ts,
                role=ROLE_INTEGRATOR if merge else ROLE_COMMITTER,
                match_method=method,
                contributed_paths=own.paths if own else (),
                session_name=pending.session_name,
                agent_run_id=pending.agent_run_id,
                project_id=pending.project_id,
            )
            await self._record_contributors(
                worktree_root=root,
                project_id=pending.project_id,
                commit=commit,
                contributors=contributors,
                exclude_session_id=pending.session_id,
                observed_at=event.ts,
                source_event_seq=event.seq or None,
            )
            if merge and fresh:
                await self._record_branch_authors(
                    worktree_root=root,
                    project_id=pending.project_id,
                    commit=commit,
                    exclude_session_id=pending.session_id,
                    observed_at=event.ts,
                    source_event_seq=event.seq or None,
                )

    async def _note_git_change(self, event: MuxEvent) -> None:
        payload = event.payload or {}
        head = payload.get("head")
        previous_head = payload.get("previous_head")
        git = payload.get("git")
        root = git.get("root") if isinstance(git, dict) else None
        if not isinstance(head, str) or not head:
            return
        if not isinstance(previous_head, str) or not previous_head:
            return
        if not isinstance(root, str) or not root:
            return
        if head == previous_head:
            return
        session = self._session(event.session_id or "")
        if session is None:
            return
        project_id = session.record.project_id
        move, tip = await self._analyze_move(
            root,
            project_id,
            previous_head,
            head,
            window_start=event.ts - MONITOR_AUTHORSHIP_WINDOW_SECONDS,
            window_end=event.ts + COMMIT_TIME_SLACK_SECONDS,
            observed_at=event.ts,
        )
        # The move itself belongs to the checkout, and is written once for it
        # rather than once per attached session.
        if self._claim_move(root, previous_head, head):
            await self._record_move(
                project_id=project_id,
                root=root,
                previous_head=previous_head,
                head=head,
                move=move,
                tip=tip,
                observed_at=event.ts,
            )
        if move.is_arrival:
            # Occupancy during an arrival says nothing about the session. This is
            # the single largest source of the ledger's old noise: a landing
            # fast-forward wrote one `ambiguous` row per session in the checkout,
            # for commits none of them had touched.
            self._arrivals += 1
            return
        # Only commits this move authored can implicate the session occupying the
        # checkout, and only weakly: it ran *something* that created them.
        claims = await self._commit_claims(project_id, [item.oid for item in move.authored])
        for commit in move.authored[:AUTHORED_COMMIT_LIMIT]:
            claimed = claims.get(commit.oid, {}).get("committers") or []
            if any(other != session.record.id for other in claimed):
                # Another session is already known to have run the command that
                # created this commit. A bystander's occupancy adds nothing to an
                # answered question, and eleven of them buried it.
                self._suppressed += 1
                continue
            fresh = self._claim_attribution(root, commit.oid)
            contributors = (
                await self._resolve_contributors(root, project_id, commit) if fresh else []
            )
            await self._record(
                session,
                worktree_root=root,
                commit=commit,
                commit_oid=commit.oid,
                previous_head=(commit.parents[0] if commit.parents else previous_head),
                relationship="observed",
                confidence="correlated",
                ambiguous=False,
                source="git_monitor",
                source_event_seq=event.seq or None,
                tool_call_id=None,
                evidence_rank=MONITOR_OBSERVED_RANK,
                observed_at=event.ts,
                role=ROLE_OBSERVER,
                match_method=f"monitor_{move.kind}",
            )
            await self._record_contributors(
                worktree_root=root,
                project_id=project_id,
                commit=commit,
                contributors=contributors,
                exclude_session_id=None,
                observed_at=event.ts,
                source_event_seq=event.seq or None,
            )
            if fresh and is_merge(commit):
                # The tool path is not the only way a merge reaches the ledger: a
                # merge run outside an observed tool call is first seen here, and
                # the branch it unified is nobody's less for that.
                await self._record_branch_authors(
                    worktree_root=root,
                    project_id=project_id,
                    commit=commit,
                    exclude_session_id=None,
                    observed_at=event.ts,
                    source_event_seq=event.seq or None,
                )

    async def _resolve_contributors(
        self, worktree_root: str, project_id: str, commit: GitCommitMetadata | None
    ) -> list[ContributorMatch]:
        """Sessions whose observed writes are in this commit.

        Bounded by construction: one changed-file read, one parent-metadata read,
        one Tier 0 query, and at most `CONTRIBUTOR_BLOB_READS` object reads. A
        commit with no matching write facts returns nothing, which is the honest
        answer for work mux never observed.
        """
        if self.tier0 is None or not project_id or commit is None:
            return []
        merge = is_merge(commit)
        changes = (
            await read_merge_resolution_changes(worktree_root, commit.oid)
            if merge
            else await read_commit_changes(worktree_root, commit.oid)
        )
        if not changes:
            return []
        parents = [
            found
            for parent in commit.parents[:MERGE_PARENT_READS]
            if (found := await read_commit_metadata(worktree_root, parent)) is not None
        ]
        floor = commit.committed_at - CONTRIBUTOR_LOOKBACK_SECONDS
        # For an ordinary commit the parent's time is the floor: work committed
        # before it is in *that* commit. For a merge every parent already existed
        # when the resolution was written, so the floor is the newest of them —
        # the earlier side's commits are not what a conflict resolution is.
        since = max([floor, *(parent.committed_at for parent in parents)])
        facts = await self.tier0.write_facts_for_project(
            project_id,
            since=since,
            until=commit.committed_at + COMMIT_TIME_SLACK_SECONDS,
        )
        # Narrow to writes that name a file this commit actually changed before
        # resolving anything per session: the window holds every write in the
        # Project, and only these can contribute.
        changed = {
            normalized
            for change in changes
            if (normalized := normalize_target(change.path)) is not None
        }
        relevant = [
            fact
            for fact in facts
            if normalize_target(
                fact.get("target") if isinstance(fact.get("target"), str) else None,
                worktree_root,
            )
            in changed
        ]
        if not relevant:
            return []
        candidates = candidate_writes(
            changes,
            relevant,
            worktree_root=worktree_root,
            session_roots=await self._session_roots(
                {str(fact.get("session_id") or "") for fact in relevant}
            ),
        )
        digests: dict[str, str | None] = {}
        reads = 0
        for candidate in candidates:
            if reads >= CONTRIBUTOR_BLOB_READS or not candidate.blob:
                continue
            # An exact match is only possible against a write that hashed whole-file
            # content. Reading the object for a path where no candidate carries one
            # would be a subprocess spent on an answer that cannot come back.
            if not any(
                fact.get("content_hash")
                for fact in (*candidate.positional, *candidate.confirmable)
            ):
                continue
            digests[candidate.path] = await read_blob_digest(worktree_root, candidate.blob)
            reads += 1
        return resolve_contributors(candidates, digests)

    async def _record_branch_authors(
        self,
        *,
        worktree_root: str,
        project_id: str,
        commit: GitCommitMetadata,
        exclude_session_id: str | None,
        observed_at: float,
        source_event_seq: int | None,
    ) -> None:
        """Name the sessions whose commits a merge unified.

        Two bounded reads and no guessing. Git answers *which* commits the merge's
        own side had that the other side did not — `rev-list p0 ^p1...`, the
        symmetric half of the first-parent rule that classifies the move — and the
        ledger answers *whose* those commits already are. Nothing is inferred from
        a timestamp, a directory, or a branch name, and a merge whose side commits
        mux never attributed produces no rows rather than a guess.

        The first parent is the side deliberately: it is the merge's own line of
        development, the branch the merge belongs to, and in a worktree landing
        (`git merge master` run inside the branch's checkout) it is exactly the
        work that checkout did. The other parents are the trunk being absorbed,
        whose commits are already attributed where they were written.
        """
        if not project_id or len(commit.parents) < 2:
            return
        branch_side = await read_excluded_range(
            worktree_root,
            commit.parents[0],
            commit.parents[1:MERGE_PARENT_READS],
            limit=BRANCH_LINE_LIMIT,
        )
        if not branch_side:
            return
        authors = await self.history.git_branch_authors(
            project_id=project_id, commit_oids=list(branch_side)
        )
        for session_id, author in authors.items():
            if not session_id or session_id == exclude_session_id:
                continue
            await self.history.record_git_provenance(
                session_id=session_id,
                session_name=await self._session_label(
                    session_id, author.get("agent_run_id") or None
                ),
                agent_run_id=author.get("agent_run_id") or None,
                project_id=project_id,
                worktree_root=worktree_root,
                commit_oid=commit.oid,
                parent_oids=commit.parents,
                subject=commit.subject,
                committed_at=commit.committed_at,
                previous_head=commit.parents[0],
                relationship="authored_branch",
                confidence=str(author.get("confidence") or "correlated"),
                ambiguous=False,
                source="ledger_branch_line",
                source_event_seq=source_event_seq,
                evidence_rank=BRANCH_AUTHOR_RANK,
                observed_at=observed_at,
                role=ROLE_BRANCH_AUTHOR,
                match_method="merge_branch_line",
                # Deliberately no paths. This session's files are in its own
                # commits, and copying them onto the merge would put A's branch
                # content on a commit A did not write — the mirror image of the
                # defect this exists to fix.
                contributed_paths=(),
            )
            self._branch_authors += 1
            log.info(
                "git branch authorship recorded session=%s merge=%s commits=%d",
                session_id,
                commit.oid[:12],
                len(branch_side),
            )
            await self.events.emit(
                "git_provenance_changed",
                session_id=session_id,
                source="daemon",
                project_id=project_id,
                agent_run_id=author.get("agent_run_id") or None,
                commit_oid=commit.oid,
                relationship="authored_branch",
                confidence=str(author.get("confidence") or "correlated"),
                role=ROLE_BRANCH_AUTHOR,
            )

    async def _session_roots(self, session_ids: set[str]) -> dict[str, str | None]:
        """Each session's checkout, for placing a write that named a relative path.

        A live session answers from its own polled Git state. One that has ended
        answers from its durable History row, because a contributor is frequently
        a session that finished before the commit that carried its work.
        """
        roots: dict[str, str | None] = {}
        for session_id in session_ids:
            if not session_id:
                continue
            session = self._session(session_id)
            if session is not None:
                roots[session_id] = session.record.git.root or session.record.git_cwd
                continue
            if session_id in self._ended_roots:
                self._ended_roots.move_to_end(session_id)
                roots[session_id] = self._ended_roots[session_id]
                continue
            entry = await self.history.history_entry(session_id)
            resolved = str(entry.get("cwd") or "") if entry else None
            self._ended_roots[session_id] = resolved
            while len(self._ended_roots) > _ATTRIBUTED_LIMIT:
                self._ended_roots.popitem(last=False)
            roots[session_id] = resolved
        return roots

    async def _session_label(self, session_id: str, agent_run_id: str | None) -> str:
        session = self._session(session_id)
        if session is not None:
            return session.record.name
        for candidate in (agent_run_id, session_id):
            entry = await self.history.history_entry(candidate) if candidate else None
            if entry and entry.get("name"):
                return str(entry["name"])
        return session_id

    async def _record_contributors(
        self,
        *,
        worktree_root: str,
        project_id: str,
        commit: GitCommitMetadata,
        contributors: list[ContributorMatch],
        exclude_session_id: str | None,
        observed_at: float,
        source_event_seq: int | None,
    ) -> None:
        for contributor in contributors:
            if contributor.session_id == exclude_session_id or not contributor.paths:
                continue
            await self.history.record_git_provenance(
                session_id=contributor.session_id,
                session_name=await self._session_label(
                    contributor.session_id, contributor.agent_run_id
                ),
                agent_run_id=contributor.agent_run_id,
                project_id=project_id,
                worktree_root=worktree_root,
                commit_oid=commit.oid,
                parent_oids=commit.parents,
                subject=commit.subject,
                committed_at=commit.committed_at,
                previous_head=commit.parents[0] if commit.parents else None,
                relationship="contributed",
                confidence=contributor.confidence,
                ambiguous=False,
                source="tier0_write",
                source_event_seq=source_event_seq,
                evidence_rank=contributor.evidence_rank,
                observed_at=observed_at,
                role=ROLE_CONTRIBUTOR,
                match_method=contributor.method,
                contributed_paths=contributor.paths,
            )
            self._contributors += 1
            log.info(
                "git contribution recorded session=%s commit=%s files=%d match=%s",
                contributor.session_id,
                commit.oid[:12],
                len(contributor.paths),
                contributor.method,
            )
            # A contributor row is written after the row that emitted the change
            # event, so without its own the ledger would show the commit and not
            # yet the work in it until some later refresh.
            await self.events.emit(
                "git_provenance_changed",
                session_id=contributor.session_id,
                source="daemon",
                project_id=project_id,
                agent_run_id=contributor.agent_run_id,
                commit_oid=commit.oid,
                relationship="contributed",
                confidence=contributor.confidence,
                role=ROLE_CONTRIBUTOR,
            )

    async def _record(
        self,
        session: Session,
        *,
        worktree_root: str,
        commit: GitCommitMetadata | None,
        commit_oid: str | None = None,
        previous_head: str | None,
        relationship: str,
        confidence: str,
        ambiguous: bool,
        source: str,
        source_event_seq: int | None,
        tool_call_id: str | None,
        evidence_rank: int,
        observed_at: float,
        role: str,
        match_method: str | None,
        contributed_paths: tuple[str, ...] = (),
        session_name: str | None = None,
        agent_run_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        oid = commit.oid if commit is not None else commit_oid
        if not oid:
            return
        record = session.record
        effective_name = session_name or record.name
        effective_run_id = (
            agent_run_id if session_name is not None else record.agent_run_id or None
        )
        effective_project_id = project_id or record.project_id
        item = await self.history.record_git_provenance(
            session_id=record.id,
            session_name=effective_name,
            agent_run_id=effective_run_id,
            project_id=effective_project_id,
            worktree_root=worktree_root,
            commit_oid=oid,
            parent_oids=commit.parents if commit else (),
            subject=commit.subject if commit else "",
            committed_at=commit.committed_at if commit else None,
            previous_head=previous_head,
            relationship=relationship,
            confidence=confidence,
            ambiguous=ambiguous,
            source=source,
            source_event_seq=source_event_seq,
            tool_call_id=tool_call_id,
            evidence_rank=evidence_rank,
            observed_at=observed_at,
            role=role,
            match_method=match_method,
            contributed_paths=contributed_paths,
        )
        self._captured += 1
        log.info(
            "git provenance recorded session=%s run=%s commit=%s role=%s relationship=%s "
            "confidence=%s match=%s",
            record.id,
            effective_run_id or "shell",
            oid[:12],
            role,
            item["relationship"],
            item["confidence"],
            match_method or "none",
        )
        await self.events.emit(
            "git_provenance_changed",
            session_id=record.id,
            source="daemon",
            project_id=effective_project_id,
            agent_run_id=effective_run_id,
            commit_oid=oid,
            relationship=item["relationship"],
            confidence=item["confidence"],
            role=role,
        )
