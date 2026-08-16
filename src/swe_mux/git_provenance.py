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
    read_git_position,
)
from .history import HistoryIndex
from .models import MuxEvent
from .session import Session, SessionManager
from .tier0_store import Tier0Store

log = logging.getLogger(__name__)

_COMMAND_TOOL_MARKERS = ("bash", "shell", "exec", "command", "powershell", "terminal")
_GIT_COMMIT = re.compile(
    r"(?:^|[;&|()]|\r?\n)\s*"
    r"(?:git|git\.exe|\"[^\"]*[\\/]git(?:\.exe)?\")\s+"
    r"(?:(?:-c\s+\S+|--no-pager)\s+)*commit(?:\s|$)",
    re.IGNORECASE,
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

#: Evidence ranks. Every attribution path introduced by Phase 7.8 outranks every
#: rank written before it (50 was the highest), so re-attribution promotes a
#: historical row in place instead of being refused by the ranked upsert.
COMMITTER_EXACT_RANK = 70
CONTRIBUTOR_CONTENT_RANK = 65
CONTRIBUTOR_PATH_RANK = 60
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


@dataclass(slots=True, frozen=True)
class GitCommitCommand:
    relationship: str


@dataclass(slots=True)
class PendingCommit:
    session_id: str
    session_name: str
    agent_run_id: str | None
    project_id: str
    call_id: str
    position: GitPosition | None
    relationship: str
    subject: str | None
    started_at: float


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
    """Recognize only an explicit ordinary `git commit` invocation.

    Repository-redirection flags are rejected because resolving their shell quoting and
    environment safely would turn a provenance observer into another command interpreter.
    The checkout poller still records the resulting HEAD as an observation.
    """
    if not command or not any(marker in tool.casefold() for marker in _COMMAND_TOOL_MARKERS):
        return None
    if _UNSAFE_REPOSITORY_REDIRECT.search(command) or not _GIT_COMMIT.search(command):
        return None
    relationship = "rewrote" if re.search(r"(?:^|\s)--amend(?:\s|$)", command) else "created"
    return GitCommitCommand(relationship=relationship)


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
    relative target (codex writes one) needs its session's checkout to say so. A
    fact that satisfies neither is kept only as `confirmable`: it counts if, and
    only if, its content turns out to be the bytes the commit stored.
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
        placed = (absolute and normalized != normalize_target(target)) or _under(
            worktree_root, session_roots.get(str(fact.get("session_id") or "")) or ""
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

    `attribution` is `exact` when a committer was isolated, `correlated` when only
    contributions or occupancy are known, and `ambiguous` when the commit is work
    mux never observed.
    """
    commits: dict[str, dict[str, Any]] = {}
    for row in rows:
        oid = str(row.get("commit_oid") or "")
        if not oid:
            continue
        commit = commits.setdefault(
            oid,
            {
                "commit_oid": oid,
                "subject": row.get("subject") or "",
                "committed_at": row.get("committed_at"),
                "worktree_root": row.get("worktree_root") or "",
                "committer": None,
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
        role = str(row.get("role") or "observer")
        if role == "committer" and not row.get("ambiguous"):
            commit["committer"] = entry
            commit["attribution"] = "exact"
        elif role == "committer" and commit["committer"] is None:
            commit["committer"] = entry
        if entry["paths"]:
            commit["contributors"].append(entry)
    for commit in commits.values():
        if commit["attribution"] != "exact" and (commit["contributors"] or commit["committer"]):
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
        self._captured = 0
        self._contributors = 0
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

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._task and not self._task.done()),
            "captured": self._captured,
            "contributors": self._contributors,
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
            subject=commit_message_subject(raw_command),
            started_at=now,
        )

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
                return
        candidates = await read_commit_range(
            current.root,
            started_head,
            current.head,
            # With no starting head there is no range, only the commit now on top.
            # Listing history back from it would offer fifty candidates for a
            # question that has one honest answer.
            limit=COMMIT_RANGE_LIMIT if started_head else 1,
        )
        if not candidates:
            # The command reported success and HEAD moved, but the new head is not
            # a descendant of where the session started, so no commit object here
            # can be called this session's. The monitor still records the move.
            return
        selection = select_commit(
            candidates,
            subject=pending.subject,
            window_start=pending.started_at - COMMIT_TIME_SLACK_SECONDS,
            window_end=event.ts + COMMIT_TIME_SLACK_SECONDS,
        )
        commit = selection.commit
        if commit is None:
            return
        contributors = (
            await self._resolve_contributors(current.root, pending.project_id, commit)
            if self._claim_attribution(current.root, commit.oid)
            else []
        )
        own = next(
            (item for item in contributors if item.session_id == pending.session_id), None
        )
        await self._record(
            session,
            worktree_root=current.root,
            commit=commit,
            previous_head=(commit.parents[0] if commit.parents else started_head),
            relationship=pending.relationship,
            confidence="ambiguous" if selection.ambiguous else "exact",
            ambiguous=selection.ambiguous,
            source="session_tool",
            source_event_seq=event.seq or None,
            tool_call_id=call_id,
            evidence_rank=(
                COMMITTER_AMBIGUOUS_RANK if selection.ambiguous else COMMITTER_EXACT_RANK
            ),
            observed_at=event.ts,
            role="committer",
            match_method=selection.method,
            contributed_paths=own.paths if own else (),
            session_name=pending.session_name,
            agent_run_id=pending.agent_run_id,
            project_id=pending.project_id,
        )
        await self._record_contributors(
            worktree_root=current.root,
            project_id=pending.project_id,
            commit=commit,
            contributors=contributors,
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
        moved = await read_commit_range(root, previous_head, head)
        # A merge or a rebase moves many commits at once. Which one this session's
        # occupancy relates to is genuinely undecidable, and that is one of the two
        # cases `ambiguous` is reserved for.
        bulk = len(moved) > 1
        metadata = moved[0] if moved else await read_commit_metadata(root, head)
        fresh = self._claim_attribution(root, head)
        contributors = (
            await self._resolve_contributors(root, session.record.project_id, metadata)
            if fresh and metadata is not None and not bulk
            else []
        )
        await self._record(
            session,
            worktree_root=root,
            commit=metadata,
            commit_oid=head,
            previous_head=previous_head,
            relationship="observed",
            confidence="ambiguous" if bulk else "correlated",
            ambiguous=bulk,
            source="git_monitor",
            source_event_seq=event.seq or None,
            tool_call_id=None,
            evidence_rank=MONITOR_RANGE_RANK if bulk else MONITOR_OBSERVED_RANK,
            observed_at=event.ts,
            role="observer",
            match_method="monitor_range" if bulk else "monitor_head",
        )
        if metadata is not None:
            await self._record_contributors(
                worktree_root=root,
                project_id=session.record.project_id,
                commit=metadata,
                contributors=contributors,
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
        changes = await read_commit_changes(worktree_root, commit.oid)
        if not changes:
            return []
        parent = (
            await read_commit_metadata(worktree_root, commit.parents[0])
            if commit.parents
            else None
        )
        floor = commit.committed_at - CONTRIBUTOR_LOOKBACK_SECONDS
        since = max(floor, parent.committed_at) if parent else floor
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
                role="contributor",
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
                role="contributor",
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
