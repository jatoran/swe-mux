from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, assert_never

from .config import default_data_dir
from .git_monitor import BLOB_DIGEST_MAX_BYTES, GitCommitChange, parse_raw_changes
from .git_provenance import (
    COMMITTER_EXACT_RANK,
    CONTRIBUTOR_LOOKBACK_SECONDS,
    candidate_writes,
    resolve_contributors,
)
from .harness import transcript_dialect
from .history import HistoryIndex

log = logging.getLogger(__name__)

_COMMAND_TOOL = re.compile(r"bash|shell|exec|command|powershell|terminal", re.IGNORECASE)
_GIT_COMMIT = re.compile(
    r'(?:^|[;&|()]|\r?\n)\s*(?:git|git\.exe|"[^"]*[\\/]git(?:\.exe)?")\s+'
    r"(?:(?:-c\s+\S+|--no-pager)\s+)*commit(?:\s|$)",
    re.IGNORECASE,
)
_HASH = re.compile(r"(?<![0-9a-f])([0-9a-f]{7,64})(?![0-9a-f])", re.IGNORECASE)
_MESSAGE_ARG = re.compile(
    r"(?is)\bgit\s+(?:(?:-c\s+\S+|--no-pager)\s+)*commit\b.*?"
    r"\s-m\s+(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
)
_HEREDOC = re.compile(
    r"(?is)\bgit\s+(?:(?:-c\s+\S+|--no-pager)\s+)*commit\b[^\r\n]*"
    r"<<[\"']?([A-Za-z0-9_]+)[\"']?\r?\n([^\r\n]+)"
)
_CD_PREFIX = re.compile(
    r"(?is)^\s*(?:cd|Set-Location)(?:\s+-LiteralPath)?\s+"
    r"(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|]+))\s*(?:&&|;)"
)
_JS_COMMAND = re.compile(r'\bcommand\s*:\s*("(?:\\.|[^"\\])*")', re.DOTALL)
_FAILED_OUTPUT = re.compile(r"(?im)^(?:script failed|process exited with code [1-9]\d*)\b")
_DIRECT_TOLERANCE_SECONDS = 15 * 60
_TIMESTAMP_ONLY_TOLERANCE_SECONDS = 10
#: Commits whose contributors one pass resolves, newest first. Contributor
#: attribution reads Tier 0 write facts, which are retained for weeks, so an
#: unbounded sweep would read thousands of commits to learn nothing about the old
#: ones.
CONTRIBUTOR_COMMIT_LIMIT = 500
#: Objects hashed for an exact content match across one pass.
CONTRIBUTOR_BLOB_BUDGET = 400
#: Ancestry steps walked when re-checking that a recorded previous HEAD really is
#: the base of the commit a session was credited with.
ANCESTRY_WALK_LIMIT = 200
#: Counts every per-Project report carries, so a skipped Project still sums.
_EMPTY_REPORT_COUNTS = (
    "commands",
    "records_planned",
    "records_written",
    "committer_records",
    "contributor_records",
    "exact_records",
    "correlated_records",
    "ambiguous_records",
)


@dataclass(slots=True, frozen=True)
class HistoryOwner:
    id: str
    native_id: str
    backend: str
    name: str
    cwd: str
    project_id: str
    project_root: str
    transcript_path: Path


@dataclass(slots=True, frozen=True)
class CommitCall:
    owner: HistoryOwner
    call_id: str
    command: str
    cwd: str
    observed_at: float | None
    result: str | None
    success: bool | None


@dataclass(slots=True, frozen=True)
class CommitObject:
    oid: str
    committed_at: float
    parents: tuple[str, ...]
    subject: str


@dataclass(slots=True)
class PlannedRecord:
    call: CommitCall
    commit: CommitObject
    method: str
    session_id: str
    agent_run_id: str
    worktree_root: str
    ambiguous: bool = False

    @property
    def evidence_rank(self) -> int:
        return {"output_hash": 40, "subject_time": 25, "timestamp_only": 10}[self.method]


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 1000 if value > 100_000_000_000 else float(value)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(
            _text(value.get(key))
            for key in ("text", "content", "output", "result", "message", "stdout", "stderr")
            if key in value
        )
    return "" if value is None else str(value)


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {"_raw": value}
        return decoded if isinstance(decoded, dict) else {"_raw": value}
    return {}


def _command(arguments: dict[str, Any]) -> str | None:
    for key in ("command", "cmd"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raw = arguments.get("_raw")
    if not isinstance(raw, str):
        return None
    embedded: list[str] = []
    for match in _JS_COMMAND.finditer(raw):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, str):
            embedded.append(value)
    return "\n".join(embedded) if embedded else raw


def _command_cwd(arguments: dict[str, Any], command: str, fallback: str) -> str:
    for key in ("workdir", "cwd"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    match = _CD_PREFIX.search(command)
    if match:
        value = next((item for item in match.groups() if item), fallback)
        return os.path.abspath(os.path.join(fallback, value)) if not os.path.isabs(value) else value
    return fallback


def _message_subject(command: str) -> str | None:
    match = _MESSAGE_ARG.search(command)
    if match:
        try:
            value = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError):
            value = match.group(1)[1:-1]
        return str(value).strip() or None
    match = _HEREDOC.search(command)
    return match.group(2).strip() if match else None


def _is_success(payload: dict[str, Any], output: str) -> bool:
    exit_code = payload.get("exit_code")
    return (
        not bool(payload.get("is_error"))
        and exit_code in {None, 0, "0"}
        and not bool(_FAILED_OUTPUT.search(output))
    )


@dataclass(slots=True, frozen=True)
class ProjectInputs:
    """Everything one Project's re-attribution pass reads, in one snapshot."""

    project: dict[str, str]
    owners: list[HistoryOwner]
    command_owners: dict[tuple[str, str], str]
    existing: list[dict[str, Any]]
    write_facts: list[dict[str, Any]]
    session_roots: dict[str, str | None]
    session_names: dict[str, str]


def _select_projects(db: sqlite3.Connection, project_selector: str | None) -> list[dict[str, str]]:
    """One Project by selector, or every registered Project when none is given.

    The sweep skips removed Projects. A tombstoned Project usually has no checkout
    left to read, so including it turned an ordinary sweep into a list of errors
    about repositories that are gone on purpose. Naming one explicitly still works,
    because importing the history of a Project that was removed is a real request.
    """
    if project_selector is None:
        rows = db.execute(
            "SELECT id,name,root FROM projects WHERE deleted_at IS NULL ORDER BY name,id"
        ).fetchall()
        return [{key: str(row[key]) for key in ("id", "name", "root")} for row in rows]
    rows = db.execute(
        "SELECT id,name,root FROM projects WHERE id=? OR lower(name)=lower(?) "
        "OR lower(root)=lower(?)",
        (project_selector, project_selector, project_selector),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            "project selector did not identify exactly one Project"
            if not rows
            else "project selector is ambiguous"
        )
    return [{key: str(rows[0][key]) for key in ("id", "name", "root")}]


def _read_inputs(
    database: Path, project_selector: str | None, *, since: float
) -> list[ProjectInputs]:
    uri = database.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        return [
            _read_project_inputs(db, project, since=since)
            for project in _select_projects(db, project_selector)
        ]
    finally:
        db.close()


def _read_project_inputs(
    db: sqlite3.Connection, project: dict[str, str], *, since: float
) -> ProjectInputs:
    rows = db.execute(
        "SELECT id,native_id,backend,name,cwd,project_id,project_root,transcript_path,"
        "external,spawned_at FROM history WHERE project_id=? AND agent_visible=1 "
        "AND transcript_path IS NOT NULL AND transcript_path!='' "
        "ORDER BY external ASC,spawned_at ASC,id ASC",
        (project["id"],),
    ).fetchall()
    owners: dict[tuple[str, str], HistoryOwner] = {}
    for row in rows:
        path = Path(str(row["transcript_path"]))
        if not path.is_file():
            continue
        owner = HistoryOwner(
            id=str(row["id"]),
            native_id=str(row["native_id"]),
            backend=str(row["backend"]),
            name=str(row["name"]),
            cwd=str(row["cwd"]),
            project_id=str(row["project_id"]),
            project_root=str(row["project_root"] or project["root"]),
            transcript_path=path,
        )
        owners.setdefault((owner.backend, owner.native_id), owner)
    # Identity for a Tier 0 fact's session: `history.id` is one agent run, while
    # `note_id` is the persistent session a fact is stamped with, so both maps are
    # needed to put a name and a checkout on a contributor.
    session_roots: dict[str, str | None] = {}
    session_names: dict[str, str] = {}
    for row in db.execute(
        "SELECT id,note_id,name,cwd FROM history WHERE project_id=? "
        "ORDER BY spawned_at DESC,id DESC",
        (project["id"],),
    ).fetchall():
        for key in (str(row["note_id"] or ""), str(row["id"])):
            if not key:
                continue
            session_roots.setdefault(key, str(row["cwd"] or "") or None)
            session_names.setdefault(key, str(row["name"] or ""))
    command_owners: dict[tuple[str, str], str] = {}
    write_facts: list[dict[str, Any]] = []
    has_tier0 = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tier0_facts'"
    ).fetchone()
    if has_tier0:
        facts = db.execute(
            "SELECT session_id,agent_run_id,detail_json FROM tier0_facts WHERE project_id=? "
            "AND kind='command' AND lower(COALESCE(target,'')) LIKE '%git%commit%'",
            (project["id"],),
        ).fetchall()
        for row in facts:
            try:
                detail = json.loads(str(row["detail_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            call_id = detail.get("call_id") if isinstance(detail, dict) else None
            run_id = str(row["agent_run_id"] or "")
            if isinstance(call_id, str) and call_id:
                command_owners[(run_id, call_id)] = str(row["session_id"])
        write_facts = [
            dict(row)
            for row in db.execute(
                "SELECT id,session_id,agent_run_id,kind,target,content_hash,created_at "
                "FROM tier0_facts WHERE project_id=? "
                "AND kind IN ('file_write','file_write_result') "
                "AND target IS NOT NULL AND target!='' AND created_at>=? "
                "ORDER BY created_at ASC",
                (project["id"], since),
            ).fetchall()
        ]
    existing = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM git_provenance WHERE project_id=? ORDER BY observed_at DESC",
            (project["id"],),
        ).fetchall()
    ]
    return ProjectInputs(
        project=project,
        owners=list(owners.values()),
        command_owners=command_owners,
        existing=existing,
        write_facts=write_facts,
        session_roots=session_roots,
        session_names=session_names,
    )


def _scan_transcript(owner: HistoryOwner) -> list[CommitCall]:
    dialect = transcript_dialect(owner.backend)
    if dialect is None:
        return []
    uses: dict[str, tuple[str, str, float | None]] = {}
    results: dict[str, tuple[str, bool, float | None]] = {}

    def add_use(
        call_id: str, tool: str, raw_arguments: Any, observed_at: float | None, fallback_cwd: str
    ) -> None:
        arguments = _arguments(raw_arguments)
        command = _command(arguments)
        if (
            not call_id
            or not command
            or not _COMMAND_TOOL.search(tool)
            or not _GIT_COMMIT.search(command)
        ):
            return
        uses.setdefault(
            call_id, (command, _command_cwd(arguments, command, fallback_cwd), observed_at)
        )

    try:
        source = owner.transcript_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with source:
        for line in source:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            observed_at = _timestamp(
                event.get("timestamp") or event.get("time") or event.get("created_at")
            )
            if dialect == "claude":
                message = event.get("message")
                message = message if isinstance(message, dict) else {}
                content = message.get("content")
                if (
                    event.get("type") == "assistant"
                    and event.get("isSidechain") is not True
                    and isinstance(content, list)
                ):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            add_use(
                                str(block.get("id") or ""),
                                str(block.get("name") or ""),
                                block.get("input"),
                                observed_at,
                                str(event.get("cwd") or owner.cwd),
                            )
                elif event.get("type") == "user" and isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            results[str(block.get("tool_use_id") or "")] = (
                                _text(block.get("content")),
                                not bool(block.get("is_error")),
                                observed_at,
                            )
            elif dialect == "codex":
                payload = event.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                payload_type = payload.get("type")
                if payload_type in {"function_call", "custom_tool_call"}:
                    add_use(
                        str(payload.get("call_id") or payload.get("id") or ""),
                        str(payload.get("name") or ""),
                        payload.get("arguments")
                        if payload.get("arguments") is not None
                        else payload.get("input"),
                        observed_at,
                        owner.cwd,
                    )
                elif payload_type in {
                    "function_call_output",
                    "custom_tool_call_output",
                    "exec_command_end",
                }:
                    output = _text(
                        payload.get("output")
                        or payload.get("content")
                        or payload.get("result")
                        or payload.get("message")
                    )
                    results[str(payload.get("call_id") or payload.get("id") or "")] = (
                        output,
                        _is_success(payload, output),
                        observed_at,
                    )
            elif dialect == "pi":
                if event.get("type") == "custom" and event.get("customType") == (
                    "tool_execution_start"
                ):
                    data = event.get("data")
                    data = data if isinstance(data, dict) else {}
                    add_use(
                        str(data.get("toolCallId") or ""),
                        str(data.get("toolName") or ""),
                        data.get("args"),
                        observed_at,
                        owner.cwd,
                    )
                if event.get("type") != "message" or not isinstance(
                    event.get("message"), dict
                ):
                    continue
                message = event["message"]
                if message.get("role") == "assistant" and isinstance(
                    message.get("content"), list
                ):
                    for block in message["content"]:
                        if isinstance(block, dict) and block.get("type") == "toolCall":
                            add_use(
                                str(block.get("id") or block.get("toolCallId") or ""),
                                str(block.get("name") or ""),
                                block.get("arguments"),
                                observed_at,
                                owner.cwd,
                            )
                elif message.get("role") == "toolResult":
                    results[str(message.get("toolCallId") or "")] = (
                        _text(message.get("content")),
                        not bool(message.get("isError")),
                        observed_at,
                    )
            elif dialect == "opencode":
                continue
            else:
                assert_never(dialect)
    calls: list[CommitCall] = []
    for call_id, (command, cwd, observed_at) in uses.items():
        result = results.get(call_id)
        calls.append(
            CommitCall(
                owner=owner,
                call_id=call_id,
                command=command,
                cwd=cwd,
                observed_at=observed_at or (result[2] if result else None),
                result=result[0] if result else None,
                success=result[1] if result else None,
            )
        )
    return calls


def _git(root: Path, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _commit_catalog(root: Path) -> dict[str, CommitObject]:
    result = _git(root, "log", "--all", "--reflog", "--format=%H%x00%ct%x00%P%x00%s")
    if result.returncode:
        raise RuntimeError("unable to read the Project Git object catalog")
    commits: dict[str, CommitObject] = {}

    def add(line: str) -> None:
        fields = line.split("\0", 3)
        if len(fields) != 4:
            return
        oid, committed_at, parents, subject = fields
        try:
            stamp = float(committed_at)
        except ValueError:
            return
        commits[oid] = CommitObject(oid, stamp, tuple(parents.split()), subject)

    for line in result.stdout.splitlines():
        add(line)
    try:
        unreachable = _git(root, "fsck", "--no-reflogs", "--unreachable", "--no-progress")
    except subprocess.TimeoutExpired:
        # `fsck` walks the whole object database and takes minutes on a large
        # repository. It only adds commits nothing references — a rewritten or
        # abandoned one — so a timeout costs a little coverage and must not cost
        # the Project its entire plan.
        log.warning("git object fsck timed out; continuing with reachable commits only")
        return commits
    dangling = [
        match.group(1)
        for line in unreachable.stdout.splitlines()
        if (match := re.fullmatch(r"unreachable commit ([0-9a-f]{40,64})", line))
    ][:2000]
    for oid in dangling:
        if oid in commits:
            continue
        metadata = _git(root, "show", "-s", "--format=%H%x00%ct%x00%P%x00%s", oid, timeout=5)
        if metadata.returncode == 0:
            add(metadata.stdout.rstrip("\r\n"))
    return commits


def _resolved_worktree_root(cwd: str, project_root: Path) -> str:
    candidate = Path(cwd)
    if candidate.is_dir():
        result = _git(candidate, "rev-parse", "--show-toplevel", timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return str(Path(result.stdout.strip()).resolve())
    try:
        if os.path.commonpath((project_root.resolve(), candidate.resolve())) == str(
            project_root.resolve()
        ):
            return str(project_root.resolve())
    except ValueError:
        pass
    return str(candidate)


def _git_bytes(root: Path, *args: str, timeout: float = 30) -> bytes:
    """Undecoded stdout for one read-only query. Blob bytes cannot be decoded."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return result.stdout if result.returncode == 0 else b""


def _blob_digest(root: Path, blob: str) -> str | None:
    """SHA-256 of a blob's exact bytes, comparable with a whole-file write hash."""
    size = _git(root, "cat-file", "-s", blob, timeout=10)
    if size.returncode:
        return None
    try:
        blob_size = int(size.stdout.strip())
    except ValueError:
        return None
    if blob_size > BLOB_DIGEST_MAX_BYTES:
        return None
    if blob_size == 0:
        return hashlib.sha256(b"").hexdigest()
    data = _git_bytes(root, "cat-file", "blob", blob, timeout=15)
    return hashlib.sha256(data).hexdigest() if data else None


def _commit_changes(root: Path, oid: str) -> tuple[GitCommitChange, ...]:
    result = _git(
        root,
        "diff-tree",
        "--no-commit-id",
        "-r",
        "-z",
        "--root",
        "--find-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--raw",
        oid,
        timeout=20,
    )
    return parse_raw_changes(result.stdout) if result.returncode == 0 else ()


def _is_ancestor(catalog: dict[str, CommitObject], ancestor: str, commit: str) -> bool:
    """Walk first parents back from `commit` looking for `ancestor`.

    The catalog is already in memory, so this costs no subprocess. It answers the
    one question the shared-checkout rule should have been asking: did this commit
    grow from the head the session started on.
    """
    if not ancestor or not commit:
        return False
    current = commit
    for _ in range(ANCESTRY_WALK_LIMIT):
        if current == ancestor:
            return True
        node = catalog.get(current)
        if node is None or not node.parents:
            return False
        current = node.parents[0]
    return False


def _reattribute_committers(
    project: dict[str, str],
    existing: list[dict[str, Any]],
    catalog: dict[str, CommitObject],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Promote rows the retired shared-checkout rule stamped ambiguous.

    A row written from an observed commit command already names the commit. What
    the old rule discarded was the confidence, purely because other sessions
    happened to share the checkout. The commit is re-checked here against the head
    the session started from, and a commit no other session's command claims is
    that session's, however many terminals were open in the directory.
    """
    stats: Counter[str] = Counter()
    claims: dict[str, set[str]] = defaultdict(set)
    for row in existing:
        if str(row.get("source") or "").startswith(("session_tool", "transcript_backfill")):
            claims[str(row.get("commit_oid") or "")].add(str(row.get("session_id") or ""))
    records: list[dict[str, Any]] = []
    for row in existing:
        oid = str(row.get("commit_oid") or "")
        source = str(row.get("source") or "")
        # Live command evidence only. A transcript match's confidence expresses how
        # certainly it identified the commit *object*, which ancestry cannot
        # improve; only the shared-checkout downgrade is being undone here.
        if source != "session_tool" or not oid:
            continue
        if oid not in catalog:
            stats["reattribution_commit_missing"] += 1
            continue
        if len(claims.get(oid, set())) > 1:
            # Two sessions ran a commit command that resolved to one object. This
            # is the undecidable case ambiguity is reserved for.
            stats["reattribution_contested"] += 1
            continue
        previous_head = str(row.get("previous_head") or "")
        relationship = str(row.get("relationship") or "created")
        rooted = (
            _is_ancestor(catalog, previous_head, oid)
            if previous_head and relationship != "rewrote"
            else True
        )
        if not rooted:
            stats["reattribution_unrooted"] += 1
            continue
        already = (
            str(row.get("role") or "") == "committer"
            and str(row.get("confidence") or "") == "exact"
            and not row.get("ambiguous")
        )
        if already:
            stats["reattribution_current"] += 1
            continue
        commit = catalog[oid]
        records.append(
            {
                "session_id": str(row.get("session_id") or ""),
                "session_name": str(row.get("session_name") or ""),
                "agent_run_id": str(row.get("agent_run_id") or ""),
                "project_id": project["id"],
                "worktree_root": str(row.get("worktree_root") or project["root"]),
                "commit_oid": oid,
                "parent_oids": commit.parents,
                "subject": commit.subject,
                "committed_at": commit.committed_at,
                "previous_head": previous_head or (
                    commit.parents[0] if commit.parents else None
                ),
                "relationship": relationship,
                "confidence": "exact",
                "ambiguous": False,
                "source": source,
                "source_event_seq": row.get("source_event_seq"),
                "tool_call_id": row.get("tool_call_id"),
                "evidence_rank": COMMITTER_EXACT_RANK,
                "observed_at": row.get("observed_at") or commit.committed_at,
                "role": "committer",
                "match_method": "reattributed_ancestry",
                "contributed_paths": row.get("contributed_paths") or (),
            }
        )
        stats["reattributed"] += 1
    return records, stats


def _plan_contributors(
    project: dict[str, str],
    inputs: ProjectInputs,
    commits: list[tuple[str, CommitObject]],
    catalog: dict[str, CommitObject],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Match each commit's changed files against recorded Tier 0 writes.

    Identical in substance to the live path — the same `candidate_writes` and
    `resolve_contributors` decide it, so a historical answer and a live one cannot
    drift apart — with the budgets a sweep needs and a live event does not.
    """
    stats: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    if not inputs.write_facts:
        return records, stats
    budget = CONTRIBUTOR_BLOB_BUDGET
    for worktree_root, commit in commits[:CONTRIBUTOR_COMMIT_LIMIT]:
        root = Path(worktree_root)
        if not root.is_dir():
            root = Path(project["root"])
            if not root.is_dir():
                stats["contributor_root_missing"] += 1
                continue
        changes = _commit_changes(root, commit.oid)
        if not changes:
            stats["contributor_no_changes"] += 1
            continue
        floor = commit.committed_at - CONTRIBUTOR_LOOKBACK_SECONDS
        # The parent commit's time is the real floor: work committed before it is
        # in that commit, not this one. The lookback bounds the case where the
        # parent is unreadable (a root commit, a shallow clone).
        parent = catalog.get(commit.parents[0]) if commit.parents else None
        since = max(floor, parent.committed_at) if parent else floor
        window = [
            fact
            for fact in inputs.write_facts
            if since <= float(fact.get("created_at") or 0.0) <= commit.committed_at + 90.0
        ]
        if not window:
            continue
        candidates = candidate_writes(
            changes,
            window,
            worktree_root=str(root),
            session_roots=inputs.session_roots,
        )
        digests: dict[str, str | None] = {}
        for candidate in candidates:
            if budget <= 0 or not candidate.blob:
                continue
            if not any(
                fact.get("content_hash")
                for fact in (*candidate.positional, *candidate.confirmable)
            ):
                continue
            digests[candidate.path] = _blob_digest(root, candidate.blob)
            budget -= 1
        for contributor in resolve_contributors(candidates, digests):
            if not contributor.paths:
                continue
            session_id = contributor.session_id
            records.append(
                {
                    "session_id": session_id,
                    "session_name": (
                        inputs.session_names.get(session_id)
                        or inputs.session_names.get(contributor.agent_run_id or "")
                        or session_id
                    ),
                    "agent_run_id": contributor.agent_run_id or "",
                    "project_id": project["id"],
                    "worktree_root": str(root),
                    "commit_oid": commit.oid,
                    "parent_oids": commit.parents,
                    "subject": commit.subject,
                    "committed_at": commit.committed_at,
                    "previous_head": commit.parents[0] if commit.parents else None,
                    "relationship": "contributed",
                    "confidence": contributor.confidence,
                    "ambiguous": False,
                    "source": "tier0_write",
                    "evidence_rank": contributor.evidence_rank,
                    "observed_at": commit.committed_at,
                    "role": "contributor",
                    "match_method": contributor.method,
                    "contributed_paths": contributor.paths,
                }
            )
            stats[f"contributor_{contributor.method}"] += 1
    return records, stats


def _plan(inputs: ProjectInputs) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    project = inputs.project
    owners = inputs.owners
    command_owners = inputs.command_owners
    calls = [call for owner in owners for call in _scan_transcript(owner)]
    catalog = _commit_catalog(Path(project["root"]))
    subjects: dict[str, set[str]] = defaultdict(set)
    for commit in catalog.values():
        subjects[commit.subject].add(commit.oid)

    def near(oid: str, observed_at: float | None, tolerance: int) -> bool:
        return observed_at is None or abs(catalog[oid].committed_at - observed_at) <= tolerance

    planned: list[PlannedRecord] = []
    stats: Counter[str] = Counter(commands=len(calls))
    for call in calls:
        if call.success is None:
            stats["unpaired"] += 1
            continue
        if not call.success:
            stats["failed"] += 1
            continue
        stats["successful"] += 1
        output = call.result or ""
        direct: set[str] = set()
        for prefix in _HASH.findall(output):
            matches = [oid for oid in catalog if oid.startswith(prefix.casefold())]
            if len(matches) == 1 and near(
                matches[0], call.observed_at, _DIRECT_TOLERANCE_SECONDS
            ):
                direct.add(matches[0])
        subject = _message_subject(call.command)
        subject_matches = {
            oid
            for oid in subjects.get(subject or "", set())
            if near(oid, call.observed_at, _DIRECT_TOLERANCE_SECONDS)
        }
        if not subject_matches:
            for candidate_subject, oids in subjects.items():
                if len(candidate_subject) >= 8 and candidate_subject in output:
                    subject_matches.update(
                        oid
                        for oid in oids
                        if near(oid, call.observed_at, _DIRECT_TOLERANCE_SECONDS)
                    )
        if len(direct) == 1 and (not subject_matches or direct == subject_matches):
            oid = next(iter(direct))
            method = "output_hash"
        elif len(subject_matches) == 1:
            oid = next(iter(subject_matches))
            method = "subject_time"
        else:
            timestamp_matches = {
                oid
                for oid in catalog
                if call.observed_at is not None
                and near(oid, call.observed_at, _TIMESTAMP_ONLY_TOLERANCE_SECONDS)
            }
            if len(timestamp_matches) == 1:
                oid = next(iter(timestamp_matches))
                method = "timestamp_only"
            else:
                outcome = (
                    "ambiguous" if direct or subject_matches or timestamp_matches else "no_match"
                )
                stats[outcome] += 1
                continue
        run_id = call.owner.id
        session_id = command_owners.get((run_id, call.call_id), run_id)
        planned.append(
            PlannedRecord(
                call=call,
                commit=catalog[oid],
                method=method,
                session_id=session_id,
                agent_run_id=run_id,
                worktree_root=_resolved_worktree_root(call.cwd, Path(project["root"])),
                ambiguous=method == "timestamp_only",
            )
        )
        stats[method] += 1

    commit_sessions: dict[str, set[str]] = defaultdict(set)
    for item in planned:
        commit_sessions[item.commit.oid].add(item.session_id)
    conflicting = {oid for oid, sessions in commit_sessions.items() if len(sessions) > 1}
    for item in planned:
        if item.commit.oid in conflicting:
            item.ambiguous = True

    deduped: dict[tuple[str, str, str, str], PlannedRecord] = {}
    for item in planned:
        key = (item.session_id, item.agent_run_id, item.worktree_root, item.commit.oid)
        previous = deduped.get(key)
        if previous is None or item.evidence_rank > previous.evidence_rank:
            deduped[key] = item
    records: list[dict[str, Any]] = []
    for item in deduped.values():
        ambiguous = item.ambiguous
        relationship = "rewrote" if re.search(
            r"(?:^|\s)--amend(?:\s|$)", item.call.command
        ) else "created"
        records.append(
            {
                "session_id": item.session_id,
                "session_name": item.call.owner.name,
                "agent_run_id": item.agent_run_id,
                "project_id": project["id"],
                "worktree_root": item.worktree_root,
                "commit_oid": item.commit.oid,
                "parent_oids": item.commit.parents,
                "subject": item.commit.subject,
                "committed_at": item.commit.committed_at,
                "previous_head": (
                    item.commit.parents[0]
                    if relationship == "created" and item.commit.parents
                    else None
                ),
                "relationship": relationship,
                "confidence": (
                    "ambiguous"
                    if ambiguous
                    else "exact"
                    if item.method == "output_hash"
                    else "correlated"
                ),
                "ambiguous": ambiguous,
                "source": f"transcript_backfill:{item.method}",
                "tool_call_id": item.call.call_id,
                "evidence_rank": item.evidence_rank,
                "observed_at": item.call.observed_at or item.commit.committed_at,
                "role": "committer",
                "match_method": f"transcript_{item.method}",
            }
        )

    # Pass two: promote rows the retired shared-checkout rule downgraded, including
    # the ones this pass just planned — a transcript match and a live observation
    # of the same commit both name it, and neither is ambiguous for being in a
    # busy checkout.
    reattributed, reattribution_stats = _reattribute_committers(
        project, [*inputs.existing, *records], catalog
    )
    records.extend(reattributed)
    stats.update(reattribution_stats)

    # Pass three: whose writes are in each commit. Newest first, because Tier 0
    # facts are retained for weeks and an older commit has nothing left to match.
    known: dict[str, tuple[str, CommitObject]] = {}
    for row in (*inputs.existing, *records):
        oid = str(row.get("commit_oid") or "")
        known_commit = catalog.get(oid)
        if known_commit is None or oid in known:
            continue
        known[oid] = (str(row.get("worktree_root") or project["root"]), known_commit)
    ordered = sorted(known.values(), key=lambda item: item[1].committed_at, reverse=True)
    contributor_records, contributor_stats = _plan_contributors(
        project, inputs, ordered, catalog
    )
    records.extend(contributor_records)
    stats.update(contributor_stats)

    report: dict[str, Any] = {
        "project_id": project["id"],
        "project_name": project["name"],
        "transcripts_scanned": len(owners),
        "repository_commit_objects": len(catalog),
        "existing_rows": len(inputs.existing),
        "write_facts_available": len(inputs.write_facts),
        "commits_examined_for_contributors": min(len(ordered), CONTRIBUTOR_COMMIT_LIMIT),
        **stats,
        "records_planned": len(records),
        "distinct_commits": len({record["commit_oid"] for record in records}),
        "cross_session_commit_conflicts": len(conflicting),
        "exact_records": sum(record["confidence"] == "exact" for record in records),
        "correlated_records": sum(record["confidence"] == "correlated" for record in records),
        "ambiguous_records": sum(record["confidence"] == "ambiguous" for record in records),
        "committer_records": sum(record["role"] == "committer" for record in records),
        "contributor_records": sum(record["role"] == "contributor" for record in records),
    }
    return records, report


async def _backfill_project(
    database: Path, inputs: ProjectInputs, *, apply: bool
) -> dict[str, Any]:
    try:
        records, report = await asyncio.to_thread(_plan, inputs)
    except Exception as error:
        # One Project whose root is gone, is not a repository, or times out must
        # not abort a sweep over every other Project. It is reported, not hidden.
        log.warning(
            "git provenance backfill skipped project_id=%s reason=%s",
            inputs.project["id"],
            type(error).__name__,
            exc_info=True,
        )
        return {
            "project_id": inputs.project["id"],
            "project_name": inputs.project["name"],
            "error": f"{type(error).__name__}: {error}",
            "dry_run": not apply,
            **dict.fromkeys(_EMPTY_REPORT_COUNTS, 0),
        }
    report["dry_run"] = not apply
    report["records_written"] = 0
    if apply:
        history = HistoryIndex(database)
        try:
            for start in range(0, len(records), 1000):
                report["records_written"] += await history.record_git_provenance_batch(
                    records[start : start + 1000]
                )
        finally:
            history.close()
    log.info(
        "git provenance backfill project project_id=%s dry_run=%s commands=%d planned=%d "
        "written=%d committer=%d contributor=%d exact=%d correlated=%d ambiguous=%d",
        inputs.project["id"],
        not apply,
        report["commands"],
        report["records_planned"],
        report["records_written"],
        report["committer_records"],
        report["contributor_records"],
        report["exact_records"],
        report["correlated_records"],
        report["ambiguous_records"],
    )
    return report


async def backfill_git_provenance(
    database: Path,
    project_selector: str | None,
    *,
    apply: bool = False,
    since_days: int = 30,
) -> dict[str, Any]:
    """Re-derive committer and contributor attribution for recorded commits.

    Read-only unless `apply`. Idempotent by construction: every write goes through
    the ranked upsert, so a second run re-plans the same rows and replaces nothing
    with anything weaker. `project_selector` of None sweeps every registered
    Project, which is what re-attributing history after Phase 7.8 needs.
    """
    operation_id = uuid.uuid4().hex
    started = time.monotonic()
    since = time.time() - max(1, since_days) * 86400
    projects = await asyncio.to_thread(_read_inputs, database, project_selector, since=since)
    reports = [
        await _backfill_project(database, inputs, apply=apply) for inputs in projects
    ]
    if len(reports) == 1:
        report = dict(reports[0])
    else:
        report = {
            "projects": reports,
            "records_planned": sum(item["records_planned"] for item in reports),
            "records_written": sum(item["records_written"] for item in reports),
            "committer_records": sum(item["committer_records"] for item in reports),
            "contributor_records": sum(item["contributor_records"] for item in reports),
            "exact_records": sum(item["exact_records"] for item in reports),
            "correlated_records": sum(item["correlated_records"] for item in reports),
            "ambiguous_records": sum(item["ambiguous_records"] for item in reports),
        }
    report["operation_id"] = operation_id
    report["dry_run"] = not apply
    report["projects_scanned"] = len(reports)
    report["duration_ms"] = round((time.monotonic() - started) * 1000)
    log.info(
        "git provenance backfill completed operation_id=%s projects=%d dry_run=%s "
        "planned=%d written=%d",
        operation_id,
        len(reports),
        not apply,
        report["records_planned"],
        report["records_written"],
    )
    return report


def _configure_logging(data_dir: Path) -> None:
    handler = RotatingFileHandler(
        data_dir / "git-provenance-backfill.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger(__name__).addHandler(handler)
    logging.getLogger(__name__).setLevel(logging.INFO)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m swe_mux.git_provenance_backfill",
        description=(
            "Re-derive committer and contributor attribution for recorded commits, "
            "and import historical commits from native transcripts."
        ),
    )
    parser.add_argument(
        "project",
        nargs="?",
        help="registered Project id, exact name, or exact root; omit with --all-projects",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="sweep every registered Project instead of one",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=default_data_dir() / "mux.db",
        help="swe-mux SQLite database (default: ~/.mux/mux.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the planned rows; omission is a read-only dry run",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="age of the oldest write fact considered for contributors (default: 30)",
    )
    args = parser.parse_args()
    if bool(args.project) == bool(args.all_projects):
        parser.error("name one Project or pass --all-projects, not both and not neither")
    _configure_logging(args.database.resolve().parent)
    try:
        report = asyncio.run(
            backfill_git_provenance(
                args.database,
                None if args.all_projects else args.project,
                apply=args.apply,
                since_days=args.since_days,
            )
        )
    except Exception:
        log.exception(
            "git provenance backfill failed project_selector=%s apply=%s",
            args.project,
            args.apply,
        )
        raise
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
