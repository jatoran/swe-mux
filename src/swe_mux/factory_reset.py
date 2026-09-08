"""Return this install to its first-run state, without touching anyone's repositories.

A factory reset is the other end of onboarding: it wipes what swe-mux itself
stores - configuration, Projects, history, the database, notes, credentials it
holds copies of - so the next start comes up as a fresh install with nothing
carried over.

**It cannot run in the daemon that was asked for it**, and that is the whole
shape of this module. A running daemon holds `mux.db`, its stores hold every
JSON file beside it, and the PTY supervisor holds live sessions in a separate
process the daemon cannot speak for. So the reset is a durable *request*, in
exactly the pattern `db_maintenance` established for `VACUUM`: the route writes
the request, reaps the sessions, and restarts; the successor honours it in a
named startup phase *before any store opens a file*, which is the one moment a
daemon owns its own data directory outright.

Four rules, and each is a way this could have destroyed something it should not:

**Nothing is deleted; everything is moved.** Each top-level entry is renamed
into `.trash/factory-reset-<timestamp>/`, which makes the reset a handful of
renames rather than a recursive delete of a multi-gigabyte tree, and leaves the
whole of the old install recoverable by hand. `storage_usage` already reports
`.trash` as its own bucket, so the space it costs is visible rather than hidden,
and the result says how much it is.

**What survives is a closed list, not a guess.** `KEEP_ENTRIES` is a keep-list
rather than a delete-list on purpose: residue is what defeats this feature, and
a data-dir entry added later is far more likely to be state (which must go) than
to be load-bearing (which must not). A keep-list fails towards a *clean* reset
and a wrongly-deleted regenerable file; a delete-list fails towards a reset that
silently leaves the last install's fingerprints behind. `tests/test_factory_reset.py`
pins the list, so adding something load-bearing to the data directory forces the
decision instead of discovering it in the field.

**A repository is never touched.** `.swe-mux/config.toml`, `actions.toml` and
`project-context.md` are committed content in someone else's repository, and
`.swe-mux/notes/` is the most valuable and least recoverable thing near this
code. Forgetting a Project is what a reset does; editing that Project's working
tree is not, and would show up as an unexplained dirty `git status` in a
repository the user never pointed this at. For the same reason `worktrees/`
stays: those are real checkouts registered in the user's repositories, and
uncommitted work inside one has no other copy. They are reported instead, so
the result says what was left and where.

**A failure is recorded, never fatal.** Every step is best-effort: the daemon
that cannot move a file must still start, because a swe-mux that will not launch
because a reset half-failed is strictly worse than one that starts and says what
it could not do. The two files this process holds open itself - its own logs -
cannot be renamed on Windows, so they are truncated in place instead and
reported as such.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, load_config

log = logging.getLogger(__name__)

#: The pending request, written by the route and consumed by the next start.
REQUEST_NAME = "factory-reset.json"

#: What the last reset did, kept for the UI to show once it comes back up. Named
#: like `redeploy-result.json` because it answers the same question: the client
#: that asked for this was disconnected while it happened.
RESULT_NAME = "factory-reset-result.json"

#: Where moved entries go. Inside the data directory so the move is a rename on
#: one volume - a cross-volume "rename" is a copy, and copying a 30 GB database
#: is not something to do on the startup path.
TRASH_DIRNAME = ".trash"

#: What the operator must type. Stated by the daemon and echoed by the client,
#: so there is one copy of the phrase rather than two that can drift.
CONFIRMATION_PHRASE = "factory reset"

#: Top-level names in the data directory that a reset leaves alone. Every one
#: has a reason, and the reason is the test's subject:
#:
#: * `bin` - the shim directory on the user's PATH. Rewritten from scratch on
#:   every daemon start (`launchers.session_launch_environment`), so moving it
#:   would break `claude` on PATH for the seconds until the successor is up, in
#:   exchange for nothing.
#: * `webview` - the desktop shell's WebView2 profile, held open by the shell
#:   process that is still running. Its origin state (localStorage, IndexedDB,
#:   caches, service worker) is cleared by the client as part of the reset, so
#:   what stays is browser cache rather than anything about this install.
#: * `voice-models`, `voice-runtime` - hundreds of megabytes of downloaded model
#:   weights and a wheel closure. Cached assets, not configuration: the reset
#:   config re-offers voice setup and the download is already there.
#: * `frontend-overlay` - build payload pinned to this backend, not settings.
#:   Removing it silently reverts the UI to a possibly-stale bundled copy.
#: * `worktrees` - the user's own git checkouts. Reported, never removed.
#: * `desktop-control.token` - authenticates the shell process that is still
#:   running against the daemon it manages. A fresh one is written by the next
#:   full app start.
#: * `.trash` - where this reset is moving everything else.
#: * `daemon-recovery.json` / `daemon-recovery.lock` - current generation and
#:   the kernel fence shared with the desktop process that survives this reset.
KEEP_ENTRIES: frozenset[str] = frozenset(
    {
        "bin",
        "webview",
        "voice-models",
        "voice-runtime",
        "frontend-overlay",
        "worktrees",
        "desktop-control.token",
        "daemon-recovery.json",
        "daemon-recovery.lock",
        TRASH_DIRNAME,
    }
)

#: Names this module owns, which must survive the sweep that reads them.
_OWN_FILES: frozenset[str] = frozenset({REQUEST_NAME, RESULT_NAME})

#: Parts of swe-mux's footprint that live outside the data directory and have no
#: removal path this process can run unattended. Reported so the result names
#: them rather than implying a reset took them.
EXTERNAL_LEFT: tuple[tuple[str, str], ...] = (
    (
        "windows-firewall",
        "The inbound firewall rule for the daemon's port needs an elevated prompt to "
        "remove; delete the swe-mux rule from Windows Defender Firewall by hand.",
    ),
    (
        "tailscale-serve",
        "Tailscale Serve keeps its own configuration; run `tailscale serve reset` to "
        "stop publishing this machine's swe-mux.",
    ),
)


@dataclass(frozen=True, slots=True)
class ResetRequest:
    """What the operator asked for, read back off disk."""

    requested_at: float = 0.0
    #: Also undo the footprint outside the data directory that *can* be undone
    #: unattended: marker-tagged agent skills, and Windows shortcuts. Off unless
    #: the operator ticked it, because each of those was a separate disclosed
    #: act rather than part of installing swe-mux.
    external: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"requested_at": self.requested_at, "external": self.external}


@dataclass(frozen=True, slots=True)
class EntryOutcome:
    """What happened to one top-level entry."""

    name: str
    #: "moved" (renamed into the trash), "truncated" (held open by this process,
    #: emptied in place), or "failed" (left alone, with the reason).
    action: str
    error: str = ""


@dataclass
class ResetResult:
    """What one reset did, for the log, the result file, and the UI."""

    performed_at: float = 0.0
    seconds: float = 0.0
    trash_path: str = ""
    moved: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    #: Worktree directories left in place, absolute. Reported because leaving
    #: them is a decision, and a reset that says nothing about them reads as one
    #: that missed them.
    worktrees: list[str] = field(default_factory=list)
    #: One row per external item the opt-in group touched or declined to.
    external: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def request_path(data_dir: Path) -> Path:
    return Path(data_dir) / REQUEST_NAME


def result_path(data_dir: Path) -> Path:
    return Path(data_dir) / RESULT_NAME


def read_request(data_dir: Path) -> ResetRequest | None:
    """The pending request, or None. Anything unreadable is *not* a request.

    Failing towards "do nothing" for the same reason `db_maintenance` does, only
    more so: this one moves the operator's entire install, and a file this
    process could not parse is not authority to do that.
    """
    try:
        raw = json.loads(request_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        log.warning("ignoring a malformed factory-reset request in %s", data_dir)
        return None
    requested_at = raw.get("requested_at")
    return ResetRequest(
        requested_at=float(requested_at) if isinstance(requested_at, (int, float)) else 0.0,
        external=bool(raw.get("external")),
    )


def write_request(data_dir: Path, *, external: bool = False) -> Path:
    """Record a reset for the next daemon start to honour."""
    path = request_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".tmp")
    request = ResetRequest(requested_at=time.time(), external=external)
    staged.write_text(json.dumps(request.as_dict(), indent=2) + "\n", encoding="utf-8")
    staged.replace(path)
    return path


def clear_request(data_dir: Path) -> None:
    try:
        request_path(data_dir).unlink()
    except OSError:
        pass


def read_result(data_dir: Path) -> dict[str, Any] | None:
    """The last reset's record, for a UI that reconnected after one."""
    try:
        raw = json.loads(result_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def write_result(data_dir: Path, result: ResetResult) -> None:
    """Record what happened, after the sweep that would have moved this file."""
    path = result_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8")
    except OSError:
        log.warning("could not record the factory-reset result at %s", path, exc_info=True)


def worktree_directories(data_dir: Path) -> list[str]:
    """Worktree checkouts this reset is leaving in place, absolute paths."""
    root = Path(data_dir) / "worktrees"
    try:
        return sorted(str(entry) for entry in root.iterdir() if entry.is_dir())
    except OSError:
        return []


def planned_entries(data_dir: Path) -> list[str]:
    """Top-level names this reset would move, sorted. The preview's subject."""
    try:
        names = [entry.name for entry in Path(data_dir).iterdir()]
    except OSError:
        return []
    return sorted(name for name in names if name not in KEEP_ENTRIES and name not in _OWN_FILES)


def _sweep_entry(entry: Path, destination: Path) -> EntryOutcome:
    """Move one entry aside, or empty it when this process holds it open.

    The rename is the fast path and the whole point. Windows refuses to rename a
    file another handle has open, and this daemon has exactly that: its own
    `daemon.log` and `access.log`, the successor-spawn log it was launched
    through, and the shell's redirect of its stdout. Those are ours and they are
    all logs, so emptying one in place gets the same result as moving it - a
    fresh install has empty logs - without a handle to close first.
    """
    try:
        os.replace(entry, destination / entry.name)
        return EntryOutcome(entry.name, "moved")
    except OSError as move_error:
        if not entry.is_file():
            return EntryOutcome(entry.name, "failed", str(move_error))
    try:
        with entry.open("r+b") as handle:
            handle.truncate(0)
        return EntryOutcome(entry.name, "truncated")
    except OSError as truncate_error:
        return EntryOutcome(entry.name, "failed", str(truncate_error))


def _sweep(data_dir: Path, result: ResetResult) -> None:
    """Move everything not on the keep-list into a timestamped trash directory."""
    destination = Path(data_dir) / TRASH_DIRNAME / f"factory-reset-{int(time.time())}"
    result.trash_path = str(destination)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        entries = sorted(Path(data_dir).iterdir(), key=lambda item: item.name)
    except OSError:
        log.exception("factory reset could not read %s; nothing was moved", data_dir)
        return
    for entry in entries:
        if entry.name in KEEP_ENTRIES:
            result.kept.append(entry.name)
            continue
        if entry.name in _OWN_FILES:
            continue
        outcome = _sweep_entry(entry, destination)
        if outcome.action == "moved":
            result.moved.append(outcome.name)
        elif outcome.action == "truncated":
            result.truncated.append(outcome.name)
        else:
            result.failed.append({"name": outcome.name, "error": outcome.error})


def restore_defaults(config: Config) -> None:
    """Put this process's live `Config` back to a fresh install's values.

    Without this the daemon would finish starting on the settings it loaded
    before the sweep - and, worse, write them back out the first time anything
    saved, quietly reinstating the install that was just reset. The values come
    from `load_config` against the now-absent file rather than from a second
    list of defaults here, because a hand-maintained copy of the defaults beside
    the loader is the copy that drifts.
    """
    fresh = load_config(config.config_path or config.data_dir / "config.toml")
    for key in Config.__dataclass_fields__:
        if key in {"data_dir", "config_path"}:
            continue
        setattr(config, key, getattr(fresh, key))


def _remove_skills(result: ResetResult) -> None:
    """Take back the agent skills swe-mux installed, and only those.

    `skill_install.remove` refuses anything without the `managed-by: swe-mux`
    marker, so a user-authored skill sharing the directory is reported and left
    - which is the removal working, not failing.
    """
    from . import skill_install

    for write in skill_install.remove(skill_install.global_targets()):
        result.external.append(
            {
                "item": "agent-skill",
                "path": write.path,
                "action": write.action,
                "detail": write.reason,
            }
        )


def _remove_shortcuts(config: Config, result: ResetResult) -> None:
    """Take back the Start Menu, startup and desktop entries.

    Removal addresses every slot regardless of what was installed, which is
    `apply_shortcuts`'s own rule and exactly what a reset wants.
    """
    from .shortcuts import apply_shortcuts

    report = apply_shortcuts(config=config, remove=True)
    if not report.supported:
        result.external.append(
            {"item": "shortcut", "path": "", "action": "unsupported", "detail": report.reason}
        )
        return
    for outcome in report.outcomes:
        result.external.append(
            {
                "item": "shortcut",
                "path": str(outcome.path),
                "action": outcome.action,
                "detail": outcome.detail,
            }
        )


def _run_external(config: Config, result: ResetResult) -> None:
    """The opt-in group: only what has a removal path that runs unattended."""
    undos: tuple[tuple[str, Callable[[], None]], ...] = (
        ("agent skills", lambda: _remove_skills(result)),
        ("shortcuts", lambda: _remove_shortcuts(config, result)),
    )
    for label, undo in undos:
        try:
            undo()
        except Exception as exc:  # noqa: BLE001 - an external undo must not stop a start
            log.warning("factory reset could not undo %s: %s", label, exc, exc_info=True)
            result.external.append(
                {"item": label, "path": "", "action": "failed", "detail": str(exc)}
            )
    for item, detail in EXTERNAL_LEFT:
        result.external.append({"item": item, "path": "", "action": "left", "detail": detail})


def perform_reset(config: Config, request: ResetRequest) -> ResetResult:
    """Do the reset. Never raises; what it could not do is in the result.

    Ordering is the only subtle part. The worktrees are read *before* the sweep
    because the sweep is what makes the data directory stop describing this
    install, and the result has to be able to say where they are. The result is
    written *after* it, because the sweep would have moved it.
    """
    started = time.monotonic()
    result = ResetResult(performed_at=time.time())
    result.worktrees = worktree_directories(config.data_dir)
    _sweep(config.data_dir, result)
    restore_defaults(config)
    if request.external:
        _run_external(config, result)
    result.seconds = time.monotonic() - started
    write_result(config.data_dir, result)
    return result


def describe(result: ResetResult) -> str:
    """One line for the log: what moved, what did not, and where it went."""
    parts = [
        f"factory reset moved {len(result.moved)} entr{'y' if len(result.moved) == 1 else 'ies'}",
        f"kept {len(result.kept)}",
    ]
    if result.truncated:
        parts.append(f"emptied {len(result.truncated)} open log(s)")
    if result.failed:
        names = ", ".join(sorted(item["name"] for item in result.failed))
        parts.append(f"could not move {len(result.failed)} ({names})")
    if result.worktrees:
        parts.append(f"left {len(result.worktrees)} worktree(s) in place")
    parts.append(f"into {result.trash_path} in {result.seconds:.1f}s")
    return "; ".join(parts)


__all__ = [
    "CONFIRMATION_PHRASE",
    "EXTERNAL_LEFT",
    "KEEP_ENTRIES",
    "REQUEST_NAME",
    "RESULT_NAME",
    "ResetRequest",
    "ResetResult",
    "clear_request",
    "describe",
    "perform_reset",
    "planned_entries",
    "read_request",
    "read_result",
    "request_path",
    "restore_defaults",
    "result_path",
    "worktree_directories",
    "write_request",
    "write_result",
]
