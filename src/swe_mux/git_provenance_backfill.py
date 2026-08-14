from __future__ import annotations

import argparse
import ast
import asyncio
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


def _read_inputs(
    database: Path, project_selector: str
) -> tuple[dict[str, str], list[HistoryOwner], dict[tuple[str, str], str]]:
    uri = database.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        projects = db.execute(
            "SELECT id,name,root FROM projects WHERE id=? OR lower(name)=lower(?) "
            "OR lower(root)=lower(?)",
            (project_selector, project_selector, project_selector),
        ).fetchall()
        if len(projects) != 1:
            raise ValueError(
                "project selector did not identify exactly one Project"
                if not projects
                else "project selector is ambiguous"
            )
        project = {key: str(projects[0][key]) for key in ("id", "name", "root")}
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
        command_owners: dict[tuple[str, str], str] = {}
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
        return project, list(owners.values()), command_owners
    finally:
        db.close()


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
    unreachable = _git(root, "fsck", "--no-reflogs", "--unreachable", "--no-progress")
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


def _plan(
    project: dict[str, str],
    owners: list[HistoryOwner],
    command_owners: dict[tuple[str, str], str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
            }
        )
    report: dict[str, Any] = {
        "project_id": project["id"],
        "project_name": project["name"],
        "transcripts_scanned": len(owners),
        "repository_commit_objects": len(catalog),
        **stats,
        "records_planned": len(records),
        "distinct_commits": len({record["commit_oid"] for record in records}),
        "cross_session_commit_conflicts": len(conflicting),
        "exact_records": sum(record["confidence"] == "exact" for record in records),
        "correlated_records": sum(record["confidence"] == "correlated" for record in records),
        "ambiguous_records": sum(record["confidence"] == "ambiguous" for record in records),
    }
    return records, report


async def backfill_git_provenance(
    database: Path, project_selector: str, *, apply: bool = False
) -> dict[str, Any]:
    operation_id = uuid.uuid4().hex
    started = time.monotonic()
    project, owners, command_owners = await asyncio.to_thread(
        _read_inputs, database, project_selector
    )
    records, report = await asyncio.to_thread(_plan, project, owners, command_owners)
    report["operation_id"] = operation_id
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
    report["duration_ms"] = round((time.monotonic() - started) * 1000)
    log.info(
        "git provenance backfill completed operation_id=%s project_id=%s dry_run=%s "
        "commands=%d planned=%d written=%d exact=%d correlated=%d ambiguous=%d",
        operation_id,
        project["id"],
        not apply,
        report["commands"],
        report["records_planned"],
        report["records_written"],
        report["exact_records"],
        report["correlated_records"],
        report["ambiguous_records"],
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
        description="Backfill durable session-to-commit provenance from native transcripts.",
    )
    parser.add_argument("project", help="registered Project id, exact name, or exact root")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path.home() / ".mux" / "mux.db",
        help="swe-mux SQLite database (default: ~/.mux/mux.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the planned rows; omission is a read-only dry run",
    )
    args = parser.parse_args()
    _configure_logging(args.database.resolve().parent)
    try:
        report = asyncio.run(
            backfill_git_provenance(args.database, args.project, apply=args.apply)
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
