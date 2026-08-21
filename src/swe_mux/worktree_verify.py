"""Phase 14: the repository-declared verification gate a land must pass.

Resolution and execution are the ordinary worktree-command seam (`worktree_exec.py`);
what this module adds is **authority**. Worktree bootstrap runs once, for a tree a
human just asked to create, before anything starts in it. Verification runs
repeatedly, unattended, on an agent's request - so it carries the exact-content
approval model Project Actions established: a machine-local SHA-256 over the bytes
that will actually run, keyed by canonical Project root, and any edit un-approves it.

That is the whole reason an agent cannot author the command its own land runs. Writing
a verification script is a proposal; a human turns it into an authority, which is the
same sentence `project-actions.md` already ends on.

The gate's exit status is reported exactly as the process gave it. Nothing here pipes,
filters, or re-derives it: a gate command trimmed inside its own pipeline has already
shipped a failing suite green in this repository once.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .verify_progress import VerifyProgress
from .worktree_exec import (
    WorktreeCommand,
    resolve_worktree_command,
    run_bounded_command,
)

log = logging.getLogger(__name__)

VERIFY_TIMEOUT_SECONDS = 60 * 60
MAX_VERIFY_OUTPUT_BYTES = 1024 * 1024
#: What a handback may carry into an agent's prompt. The full capture stays in the
#: land record; this is the tail a reader can act on.
MAX_HANDBACK_OUTPUT_BYTES = 4 * 1024
#: An approved command's own bytes, retained so the approval prompt can show what
#: changed. "The verify script changed" cannot separate a new test target from a new
#: `curl | sh`.
MAX_APPROVED_SNAPSHOT_BYTES = 128 * 1024
#: How many distinct approved digests one Project root retains, newest last.
#:
#: The store is digest-scoped rather than one slot per root, so approving one copy of
#: the gate never un-approves another (`land-queue.md`). That has to be bounded anyway:
#: an approval is authority, and authority that only ever accumulates is authority
#: nobody can account for. Sixteen is far more copies of one gate than a repository has
#: worktrees editing it at once, and the oldest is what falls off - which is a genuine
#: un-approval and is why the cap is not smaller.
MAX_APPROVED_DIGESTS = 16

CONFIG_KEY = "verify_command"
SCRIPT_NAME = ".worktree-verify"
#: The same bound `parse_project_config` enforces on `[worktree]` command strings, named
#: here so the editor can refuse before writing rather than after the parse rejects it.
MAX_VERIFY_COMMAND_CHARS = 4096

VerifyStatus = Literal[
    "not_configured",
    "unapproved",
    "passed",
    "failed",
    "timed_out",
    "error",
]


@dataclass(frozen=True, slots=True)
class VerifyCommandInfo:
    """What would run, and whether a human has approved these exact bytes."""

    configured: bool
    source: str | None = None
    display: str | None = None
    digest: str | None = None
    approved: bool = False
    #: The bytes standing approved, when any were retained. `None` covers both "never
    #: approved" and "approved but too large to retain", which `previously_approved`
    #: tells apart.
    approved_snapshot: str | None = None
    previously_approved: bool = False
    #: The bytes that would run now, when they are a script small enough to show.
    current_source: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "source": self.source,
            "display": self.display,
            "digest": self.digest,
            "approved": self.approved,
            "previously_approved": self.previously_approved,
        }


@dataclass(frozen=True, slots=True)
class WorktreeVerifyResult:
    status: VerifyStatus
    source: str | None = None
    command: str | None = None
    digest: str | None = None
    exit_code: int | None = None
    duration_ms: float = 0.0
    output: bytes = b""
    output_truncated: bool = False
    error: str | None = None
    #: The step names this run announced, in order, when it announced any. A *passing*
    #: run's list is what a later run is measured against; a failing run stopped early
    #: under `set -e`, so its list is a prefix and is never recorded as a plan.
    steps: tuple[str, ...] = ()
    #: Lines the gate emitted. Not progress toward an end - the honest fallback for a
    #: gate that declares no steps of its own.
    output_lines: int = 0

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "command": self.command,
            "digest": self.digest,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 1),
            "output_truncated": self.output_truncated,
            "error": self.error,
            "steps": list(self.steps),
            "output_lines": self.output_lines,
        }

    def failure_summary(self) -> str:
        """One line naming why the gate did not pass, for a handback or a land row."""
        if self.status == "timed_out":
            return f"verification timed out after {VERIFY_TIMEOUT_SECONDS}s"
        if self.status == "error":
            return f"verification could not run: {self.error or 'unknown error'}"
        if self.status == "unapproved":
            return "the verification command is not approved"
        if self.status == "not_configured":
            return "no verification command is configured"
        return f"verification failed with exit code {self.exit_code}"


def _digest(source: str, payload: bytes) -> str:
    """Digest the source kind alongside the bytes.

    A config string and a script file with identical bytes are two different
    authorities, and a digest that cannot tell them apart would let approving one
    silently approve the other.
    """
    digest = hashlib.sha256()
    digest.update(source.encode())
    digest.update(b"\0")
    digest.update(payload)
    return digest.hexdigest()


def _command_payload(command: WorktreeCommand, worktree: Path) -> tuple[bytes, str | None]:
    """The bytes that define this command, and their text when it is readable.

    For a config override the definition is the command string itself. For the script
    convention it is the script's own bytes, read from the worktree, because that is
    the copy that will run - a branch that edits its verification script must present
    for approval again, which is exactly the property the phase needs.
    """
    if command.source == "project_config":
        payload = command.display.encode("utf-8")
        return payload, command.display
    script = worktree / SCRIPT_NAME
    try:
        payload = script.read_bytes()
    except OSError:
        return b"", None
    if len(payload) > MAX_APPROVED_SNAPSHOT_BYTES:
        return payload, None
    return payload, payload.decode("utf-8", "replace")


class VerifyApprovalStore:
    """Machine-local approval of verification-command **bytes**, per Project root.

    Machine-local rather than repository state for the same reason Project Action
    trust is: an approval committed to a repository would grant itself in every clone
    of it, which is the opposite of what approving means.

    **Digest-scoped, not one slot per root.** It was one slot, and one slot is wrong
    for the thing being approved. A Project's worktrees each carry their own copy of
    `.worktree-verify`, so a branch that edits the gate resolves to different bytes
    from the primary's - and with a single slot, approving the branch's copy silently
    un-approved the primary's and vice versa. Observed 2026-08-21: two landings took
    turns un-approving each other and neither could be cleared. Approving bytes now
    says only what it says - *these* bytes may run - and says nothing about any other
    copy. Retained approvals are capped (`MAX_APPROVED_DIGESTS`); the oldest falling
    off is a real un-approval, which is why the cap is generous.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "worktree-verify-trust.json"

    def _store(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _approvals(self, project_root: str) -> list[dict[str, Any]]:
        """This root's approvals, oldest first, in the one shape the reader uses.

        The single-slot shape is read as a one-element list rather than migrated on
        read: a store written by an older daemon must keep granting exactly what it
        granted, and rewriting a trust file as a side effect of *reading* it is not
        something an approval store should ever do. The next `approve` writes the new
        shape, carrying the old grant forward.
        """
        stored = self._store().get(str(Path(project_root).resolve()))
        if not isinstance(stored, dict):
            return []
        entries = stored.get("approvals")
        if isinstance(entries, list):
            return [
                item
                for item in entries
                if isinstance(item, dict) and isinstance(item.get("digest"), str)
            ]
        legacy = stored.get("digest")
        if isinstance(legacy, str):
            carried: dict[str, Any] = {"digest": legacy}
            if isinstance(stored.get("snapshot"), str):
                carried["snapshot"] = stored["snapshot"]
            return [carried]
        return []

    def approved_digest(self, project_root: str) -> str | None:
        """The most recently approved digest, or `None`.

        Kept for the callers that ask "what stands approved here" as a single fact -
        a diagnostic reading. Deciding whether a particular command may run is
        `is_approved`, which is the question the pipeline actually asks.
        """
        approvals = self._approvals(project_root)
        return str(approvals[-1]["digest"]) if approvals else None

    def is_approved(self, project_root: str, digest: str) -> bool:
        return bool(digest) and any(
            item["digest"] == digest for item in self._approvals(project_root)
        )

    def approved_snapshot(self, project_root: str) -> str | None:
        """The bytes standing approved, for the prompt's diff.

        The most recent approval's, because that is the copy a reader last said yes
        to and therefore the one an unapproved copy is meaningfully different *from*.
        """
        for item in reversed(self._approvals(project_root)):
            value = item.get("snapshot")
            if isinstance(value, str):
                return value
        return None

    def ever_approved(self, project_root: str) -> bool:
        return bool(self._approvals(project_root))

    def _write(self, store: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def approve(self, project_root: str, digest: str, *, snapshot: str | None) -> None:
        root = str(Path(project_root).resolve())
        approvals = [item for item in self._approvals(project_root) if item["digest"] != digest]
        entry: dict[str, Any] = {"digest": digest}
        if snapshot is not None and len(snapshot.encode("utf-8")) <= MAX_APPROVED_SNAPSHOT_BYTES:
            entry["snapshot"] = snapshot
        approvals.append(entry)
        # Only the newest approval keeps its bytes. The snapshot exists to answer "what
        # changed since you approved", which is asked against the last thing approved;
        # retaining sixteen 128 KiB scripts per root would make the trust file - read on
        # every gate resolution - the expensive thing about resolving a gate.
        for older in approvals[:-1]:
            older.pop("snapshot", None)
        store = self._store()
        store[root] = {"approvals": approvals[-MAX_APPROVED_DIGESTS:]}
        self._write(store)

    def revoke(self, project_root: str, digest: str | None = None) -> None:
        """Withdraw one approval, or every approval for this root.

        A digest withdraws exactly those bytes, mirroring `approve`. No digest
        withdraws the root entirely, which is the only shape that can answer "revoke
        everything approved here" without the caller enumerating what that is.
        """
        root = str(Path(project_root).resolve())
        store = self._store()
        if root not in store:
            return
        if digest is None:
            del store[root]
        else:
            remaining = [
                item for item in self._approvals(project_root) if item["digest"] != digest
            ]
            if len(remaining) == len(self._approvals(project_root)):
                return
            if remaining:
                store[root] = {"approvals": remaining}
            else:
                del store[root]
        self._write(store)


def describe_verify_command(
    worktree: Path,
    project_values: dict[str, Any],
    store: VerifyApprovalStore,
    *,
    project_root: str,
) -> VerifyCommandInfo:
    """What would run in this worktree, and its approval standing.

    `project_values` comes from the **Project root**, matching the rule that every
    worktree shares one committed Project configuration (`git.md`); only the script
    convention is read from the worktree itself.
    """
    command = resolve_worktree_command(
        worktree,
        project_values,
        config_key=CONFIG_KEY,
        script_name=SCRIPT_NAME,
    )
    previously = store.ever_approved(project_root)
    if command is None:
        return VerifyCommandInfo(False, previously_approved=previously)
    payload, text = _command_payload(command, worktree)
    digest = _digest(command.source, payload)
    return VerifyCommandInfo(
        True,
        command.source,
        command.display,
        digest,
        store.is_approved(project_root, digest),
        store.approved_snapshot(project_root),
        previously,
        text,
    )


async def run_worktree_verify(
    worktree: Path,
    project_values: dict[str, Any],
    store: VerifyApprovalStore,
    *,
    project_root: str,
    request_id: str,
    progress: VerifyProgress | None = None,
) -> WorktreeVerifyResult:
    """Run the approved verification command, or refuse without running anything.

    `progress` observes the gate's output while it runs, so a caller can report which
    step it is on instead of an opaque "verifying". It reads the stream and never
    touches it: the bytes captured and the exit status reported are exactly what the
    process produced, which is the one property this module refuses to compromise.
    """
    info = describe_verify_command(worktree, project_values, store, project_root=project_root)
    if not info.configured:
        return WorktreeVerifyResult("not_configured")
    if not info.approved:
        log.info(
            "worktree_verify_unapproved request_id=%s path=%s source=%s",
            request_id,
            worktree,
            info.source,
        )
        return WorktreeVerifyResult(
            "unapproved",
            info.source,
            info.display,
            info.digest,
            error=(
                "the verification command changed since it was approved"
                if info.previously_approved
                else "the verification command has never been approved"
            ),
        )
    command = resolve_worktree_command(
        worktree,
        project_values,
        config_key=CONFIG_KEY,
        script_name=SCRIPT_NAME,
    )
    # Re-resolution cannot fail here: `describe_verify_command` just resolved the same
    # inputs. The guard keeps the type honest rather than covering a real branch.
    if command is None:  # pragma: no cover - defensive
        return WorktreeVerifyResult("not_configured")
    log.info(
        "worktree_verify_started request_id=%s path=%s source=%s",
        request_id,
        worktree,
        command.source,
    )
    outcome = await run_bounded_command(
        command,
        worktree,
        timeout_seconds=VERIFY_TIMEOUT_SECONDS,
        output_limit=MAX_VERIFY_OUTPUT_BYTES,
        label="verification",
        on_chunk=progress.feed if progress is not None else None,
    )
    steps: tuple[str, ...] = ()
    output_lines = 0
    if progress is not None:
        progress.finish()
        steps = progress.observed_steps()
        output_lines = progress.lines
    if outcome.timed_out:
        log.warning(
            "worktree_verify_timed_out request_id=%s path=%s duration_ms=%.1f",
            request_id,
            worktree,
            outcome.duration_ms,
        )
        return WorktreeVerifyResult(
            "timed_out",
            command.source,
            command.display,
            info.digest,
            None,
            outcome.duration_ms,
            outcome.output,
            outcome.truncated,
            outcome.error,
            steps,
            output_lines,
        )
    if outcome.exit_code is None:
        return WorktreeVerifyResult(
            "error",
            command.source,
            command.display,
            info.digest,
            None,
            outcome.duration_ms,
            outcome.output,
            outcome.truncated,
            outcome.error,
            steps,
            output_lines,
        )
    status: VerifyStatus = "passed" if outcome.exit_code == 0 else "failed"
    log.log(
        logging.INFO if outcome.exit_code == 0 else logging.WARNING,
        "worktree_verify_completed request_id=%s path=%s exit_code=%d duration_ms=%.1f "
        "output_bytes=%d truncated=%s",
        request_id,
        worktree,
        outcome.exit_code,
        outcome.duration_ms,
        len(outcome.output),
        outcome.truncated,
    )
    return WorktreeVerifyResult(
        status,
        command.source,
        command.display,
        info.digest,
        outcome.exit_code,
        outcome.duration_ms,
        outcome.output,
        outcome.truncated,
        None,
        steps,
        output_lines,
    )
