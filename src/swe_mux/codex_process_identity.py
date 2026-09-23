"""Prove which Codex process emitted a conversation-start hook.

Codex uses ``source=startup`` for both its first thread and an in-process
replacement. Hook credentials alone also reach nested CLIs, so the exception
needs a live process fingerprint, its ancestry, and a root-thread transcript.
No command lines or credentials are collected.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, NamedTuple

import psutil

log = logging.getLogger(__name__)

PROCESS_FIELD = "mux_codex_process"
MAX_ANCESTORS = 32
MAX_META_BYTES = 256 * 1024


def _is_codex(process: psutil.Process) -> bool:
    return process.name().casefold() in {"codex", "codex.exe"}


def capture_codex_process() -> dict[str, int | float] | None:
    """The nearest Codex ancestor, captured while the hook helper is alive.

    Capturing the CLI rather than the short-lived helper keeps a spooled hook
    verifiable after the helper exits. Never walk beyond an inaccessible or
    recycled parent link and never substitute a more distant Codex ancestor.
    """
    try:
        process = psutil.Process(os.getpid())
        for _ in range(MAX_ANCESTORS):
            child_started = process.create_time()
            parent = process.parent()
            if parent is None or parent.create_time() > child_started:
                return None
            process = parent
            if _is_codex(process):
                return {"pid": process.pid, "started_at": process.create_time()}
    except psutil.Error as error:
        log.warning("Codex hook process identity unavailable: %s", type(error).__name__)
    return None


class ProcessProof(NamedTuple):
    verified: bool
    reason: str


def _positive_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and value > 0
        and value <= 2**53
    )


def verify_codex_root_process(
    payload: dict[str, Any], root_pid: int, root_started_at: float | None
) -> ProcessProof:
    """Require the outermost Codex in this PTY's live, birth-checked ancestry.

    A nested CLI shares the PTY's ancestry and credentials but has another Codex
    above it. A native subagent can share the process, so its rollout must also
    identify a CLI root rather than a subagent source.
    """
    identity = payload.get(PROCESS_FIELD)
    if not isinstance(identity, dict):
        return ProcessProof(False, "process_identity_missing")
    pid, started = identity.get("pid"), identity.get("started_at")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or pid > 2**32 - 1
        or not _positive_number(started)
        or not _positive_number(root_started_at)
        or root_pid <= 0
    ):
        return ProcessProof(False, "process_identity_invalid")
    try:
        process = psutil.Process(pid)
        if process.create_time() != started or not _is_codex(process):
            return ProcessProof(False, "process_identity_changed")
        for _ in range(MAX_ANCESTORS):
            if process.pid == root_pid:
                if process.create_time() != root_started_at:
                    return ProcessProof(False, "pty_process_identity_changed")
                break
            child_started = process.create_time()
            parent = process.parent()
            if parent is None or parent.create_time() > child_started:
                return ProcessProof(False, "pty_ancestry_unverified")
            process = parent
            if _is_codex(process):
                return ProcessProof(False, "nested_codex_process")
        else:
            return ProcessProof(False, "pty_ancestry_limit")
    except psutil.Error:
        return ProcessProof(False, "process_unavailable")

    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return ProcessProof(False, "root_transcript_missing")
    try:
        with Path(path).open("rb") as handle:
            line = handle.readline(MAX_META_BYTES + 1)
        if len(line) > MAX_META_BYTES:
            return ProcessProof(False, "root_transcript_invalid")
        meta = json.loads(line)
    except (OSError, ValueError):
        return ProcessProof(False, "root_transcript_unavailable")
    if not isinstance(meta, dict) or meta.get("type") != "session_meta":
        return ProcessProof(False, "root_transcript_invalid")
    data = meta.get("payload")
    native_id = payload.get("session_id") or payload.get("sessionId")
    if not isinstance(data, dict) or data.get("id") != native_id:
        return ProcessProof(False, "root_transcript_identity_mismatch")
    if data.get("source") != "cli":
        return ProcessProof(False, "not_cli_root_thread")
    return ProcessProof(True, "pty_root_codex")


def owned_rollout(root_pid: int, root_started_at: float | None, home: Path) -> Path | None:
    """The unique CLI-root rollout held open by this PTY's outer Codex process.

    Hooks can be disabled or untrusted, including after fork/rewind. An OS file
    handle is independent evidence, unlike a directory's newest filename. Native
    subagents share the process but identify themselves in their metadata; nested
    CLIs have an intervening Codex ancestor. Ambiguity and access errors fail closed.
    Runs only in a worker thread, on the observer's throttled recovery cadence.
    """
    if root_pid <= 0 or not _positive_number(root_started_at):
        return None
    from .codex_history import metadata

    try:
        root = psutil.Process(root_pid)
        if root.create_time() != root_started_at:
            return None
        pending = [root]
        codex: list[psutil.Process] = []
        visited = 0
        while pending and visited < MAX_ANCESTORS:
            process = pending.pop()
            visited += 1
            if _is_codex(process):
                codex.append(process)
                continue  # Never inspect a nested CLI or its open files.
            pending.extend(
                child
                for child in process.children()
                if child.create_time() >= process.create_time()
            )
        if pending or len(codex) != 1:
            return None
        process = codex[0]
        candidates: set[Path] = set()
        for opened in process.open_files():
            path = Path(opened.path)
            if not path.name.startswith("rollout-") or path.suffix != ".jsonl":
                continue
            if not path.resolve().is_relative_to(home.resolve()):
                continue
            data = metadata(path)
            native_id = data.get("id")
            if (
                data.get("source") != "cli"
                or data.get("parent_thread_id")
                or not isinstance(native_id, str)
                or not path.name.endswith(f"-{native_id}.jsonl")
            ):
                continue
            proof = verify_codex_root_process(
                {
                    PROCESS_FIELD: {"pid": process.pid, "started_at": process.create_time()},
                    "session_id": native_id,
                    "transcript_path": str(path),
                },
                root_pid,
                root_started_at,
            )
            if proof.verified:
                candidates.add(path)
        return next(iter(candidates)) if len(candidates) == 1 else None
    except (psutil.Error, OSError, ValueError):
        return None
