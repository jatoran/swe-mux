from __future__ import annotations

import functools
import logging
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .harness import agent_harnesses
from .host_platform import IS_WINDOWS, is_windows_interop_path

log = logging.getLogger(__name__)

# Written into every ~/.mux/bin/{claude,codex} shim; the marker both routes
# the launch and identifies a file as one of our shims during resolution.
SHIM_MARKER = "swe_mux.agent_launcher"
# Windows needs the `.cmd` extension to be executable through PATHEXT. POSIX needs
# the *absence* of one, because `claude` is what the user types and what every
# harness-detection call looks for; a `claude.sh` would simply never be found.
SHIM_SUFFIX = ".cmd" if IS_WINDOWS else ""
SHIM_NAMES = tuple(f"{name}{SHIM_SUFFIX}" for name in agent_harnesses())
# A shim is two lines. Reading more than this would mean the candidate is not one,
# and the cap is what keeps `is_mux_shim` from slurping an arbitrary binary that
# happens to sit at an agent's name on PATH.
_SHIM_READ_LIMIT = 4096


def is_mux_shim(path: str | os.PathLike[str]) -> bool:
    """True when *path* is one of swe-mux's own agent shims.

    A daemon relaunched from inside a session inherits that session's PATH with
    the shim directory prepended, so name-based resolution ("codex") can land on
    the shim itself. Anything that resolves an agent executable for launching
    must reject these, or the shim ends up invoking itself in a loop.

    The extension gate is per-platform and had to become one. It previously
    accepted only `.cmd`/`.bat`, which is correct on Windows and wrong everywhere
    else: a POSIX shim is deliberately extensionless, so every shim would have
    read as a real CLI. `harness.detect_installation` calls `which_real`, so the
    visible symptom would have been every harness reported as installed on Linux,
    and every launch recursing into the shim - the exact trap this guard exists
    to prevent, reintroduced by the port.
    """
    candidate = Path(path)
    suffix = candidate.suffix.casefold()
    if IS_WINDOWS:
        if suffix not in {".cmd", ".bat"}:
            return False
    elif suffix not in {"", ".sh"}:
        # A POSIX shim carries no extension. `.sh` is accepted because a user or a
        # migration could reasonably have named one that way; anything else (a
        # `.py`, a binary with a suffix) is not ours.
        return False
    # Stat before reading, and memoize on the result. On Windows the extension gate
    # above already rejects almost every candidate for free; on POSIX it cannot,
    # because a shim is deliberately extensionless - so every probe of a directory
    # that has no shim used to cost a real open(). Measured on a WSL PATH (106
    # entries, 95 of them DrvFs paths under /mnt) that put `path_without_shim_dirs`
    # at ~0.7s per call, and `which_real` calls it up to twice per lookup.
    try:
        info = candidate.stat()
    except OSError:
        return False
    return _read_marker(str(candidate), info.st_mtime_ns, info.st_size)


@functools.lru_cache(maxsize=1024)
def _read_marker(path: str, mtime_ns: int, size: int) -> bool:
    """Whether the file at ``path`` carries the shim marker, keyed by its identity.

    `mtime_ns` and `size` are part of the key rather than decoration: a rewritten
    shim (a data-dir change, an upgrade) must not keep answering from the cache.
    """
    try:
        with open(path, "rb") as handle:
            return SHIM_MARKER.encode("utf-8") in handle.read(_SHIM_READ_LIMIT)
    except OSError:
        return False


def path_without_shim_dirs(environ_path: str | None = None) -> str:
    """The search PATH with every directory that holds mux agent shims removed."""
    raw = environ_path if environ_path is not None else os.environ.get("PATH", "")
    return _filtered_path(raw, os.environ.get("MUX_SHIM_DIR") or "")


@functools.lru_cache(maxsize=32)
def _filtered_path(raw: str, shim_dir: str) -> str:
    """The filtered PATH, cached on the inputs that determine it.

    A PATH scan is O(entries x harnesses) filesystem probes and the answer only
    changes when PATH or the shim directory does, yet it is asked on every
    executable resolution. Keying the cache on both inputs keeps it correct across
    a data-dir change without anyone having to remember to invalidate it.
    """
    kept: list[str] = []
    for entry in raw.split(os.pathsep):
        if not entry:
            continue
        if shim_dir and Path(entry) == Path(shim_dir):
            continue
        if any(is_mux_shim(Path(entry) / name) for name in SHIM_NAMES):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def clear_caches() -> None:
    """Drop both memoizations and the log de-duplication. For tests, and for a
    deliberate re-scan.

    Ordinary correctness does not depend on the memoizations: the PATH scan is
    keyed on the PATH and shim directory, and the marker read on the file's mtime
    and size, so a shim that is rewritten or a PATH that changes invalidates
    itself. `_reported_refusals` is dropped here too, so a test that asserts on a
    logged refusal is not silenced by a neighbour having provoked the same one.
    """
    _filtered_path.cache_clear()
    _read_marker.cache_clear()
    _reported_refusals.clear()


#: Why an executable resolution ended the way it did.
#:
#: Three distinct refusals rather than one bare ``None``, because they call for
#: three different actions from whoever reads them, and collapsing them is what
#: cost an operator an hour on 2026-08-28: told "No such file or directory:
#: 'codex.exe'", they went looking for a missing install, when the truth was that
#: a perfectly good Windows codex had been *found* at ``/mnt/c/...`` and
#: deliberately refused. One of those is actionable and the other sends you
#: digging.
#:
#: - ``found`` - a launchable executable on this host.
#: - ``not_found`` - nothing of that name exists on PATH. Install the CLI.
#: - ``mux_shim`` - the only thing of that name is one of swe-mux's own
#:   ``~/.mux/bin`` agent shims, which would invoke itself in a loop. The real
#:   CLI is not installed, or the daemon's PATH needs repairing.
#: - ``windows_interop`` - a Windows binary reached through WSL interop. It
#:   *runs*, which is exactly the danger, so it is refused
#:   (:func:`host_platform.is_windows_interop_path` says why). Install the Linux
#:   build inside the distribution.
ResolutionReason = Literal["found", "not_found", "mux_shim", "windows_interop"]

#: How informative each reason is. Resolution makes several attempts and they can
#: disagree ("nothing on the filtered PATH" versus "a Windows binary on the full
#: one"); the ranking picks the answer that tells the operator the most, rather
#: than whichever attempt happened to run last.
_REASON_RANK: dict[ResolutionReason, int] = {
    "found": 3,
    "windows_interop": 2,
    "mux_shim": 1,
    "not_found": 0,
}

#: Refusals already written to the log, as (command, reason, rejected path).
#: `detect_installations` re-resolves every registered harness on every registry
#: read, so an un-deduplicated WARNING for a host that will never have a Linux
#: codex would be the whole of `daemon.log`. Bounded so a pathological caller
#: cannot grow it without limit; clearing it merely re-reports.
_reported_refusals: set[tuple[str, str, str]] = set()
_REPORTED_REFUSAL_LIMIT = 512


@dataclass(frozen=True, slots=True)
class ExecutableResolution:
    """What resolving one command name found, and - when nothing usable - why not.

    ``command`` is what was asked for, ``path`` the launchable executable or
    ``None``, ``rejected`` the path that was found and refused (only ever set for
    a refusal), and ``also_tried`` the other names the same logical resolution
    attempted - the suffix-stripped form of a stale ``codex.exe``, say - so the
    message can say what the search actually covered.
    """

    command: str
    path: str | None
    reason: ResolutionReason
    rejected: str | None = None
    also_tried: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.path is not None

    def describe(self) -> str:
        """One sentence naming what was searched for, what was found, and why not.

        Written for a human who is blocked: it always names the configured value
        in quotes, and for a refusal it always names the path that was refused,
        because "not found" and "found and refused" are the two answers that look
        identical from the outside and mean opposite things.
        """
        also = ""
        if self.also_tried:
            names = ", ".join(f'"{name}"' for name in self.also_tried)
            also = f" (also tried {names})"
        if self.reason == "found":
            return f'"{self.command}" resolves to {self.path}{also}'
        if self.reason == "not_found":
            return f'"{self.command}" was not found on PATH{also}'
        name = Path(self.command).stem or self.command
        if self.reason == "mux_shim":
            return (
                f'"{self.command}" is swe-mux\'s own agent shim ({self.rejected}) and '
                f"would invoke itself; no other {name} CLI was found on PATH{also}"
            )
        return (
            f'"{self.command}" resolves to a Windows binary reached through WSL interop '
            f"({self.rejected}) and was refused: a Windows agent CLI driven from a Linux "
            f"daemon writes its transcript into the Windows home, reports a wsl.localhost "
            f"working directory, and joins no Linux process group. Install the Linux build "
            f"of {name} inside this distribution{also}"
        )


def combine_resolutions(
    first: ExecutableResolution, second: ExecutableResolution
) -> ExecutableResolution:
    """The more informative of two attempts at the same logical command.

    Ties go to *first*, so a caller's preferred spelling stays the one named in
    the message. Every name either attempt covered is carried on the winner's
    ``also_tried``, which is what lets a message say `"codex.exe" was not found on
    PATH (also tried "codex")` rather than reporting half the search.
    """
    winner = first if _REASON_RANK[first.reason] >= _REASON_RANK[second.reason] else second
    covered = (
        *first.also_tried,
        first.command,
        *second.also_tried,
        second.command,
    )
    also_tried = tuple(
        name for name in dict.fromkeys(covered) if name and name != winner.command
    )
    return replace(winner, also_tried=also_tried)


def _resolve_once(command: str, path: str | None) -> ExecutableResolution:
    """One `shutil.which` lookup, classified.

    Two rejections, and both exist because the alternative is a session that looks
    healthy and is not. A mux shim would invoke itself in a loop. A Windows binary
    reached through WSL interop *runs* - and then writes its transcript into the
    Windows home, reports a `wsl.localhost` working directory, and sits outside
    every Linux process group, so mux observes none of it and cannot clean it up.
    """
    resolved = shutil.which(command) if path is None else shutil.which(command, path=path)
    if not resolved:
        return ExecutableResolution(command, None, "not_found")
    if is_mux_shim(resolved):
        return ExecutableResolution(command, None, "mux_shim", resolved)
    if is_windows_interop_path(resolved):
        return ExecutableResolution(command, None, "windows_interop", resolved)
    return ExecutableResolution(command, resolved, "found")


def resolve_executable(command: str) -> ExecutableResolution:
    """Resolve *command* to something launchable, or say why nothing was.

    The reason-carrying form of :func:`which_real`, which is now a thin wrapper
    over it so there is exactly one resolver and no second implementation to
    disagree with this one.

    Two passes, as before: the plain PATH first, then the PATH with mux's own shim
    directories stripped, because a daemon relaunched from inside a session
    carries a shim-first PATH and the real CLI is behind it.
    """
    outcome = _resolve_once(command, None)
    if not outcome.usable:
        outcome = combine_resolutions(outcome, _resolve_once(command, path_without_shim_dirs()))
    _report(outcome)
    return outcome


def _report(outcome: ExecutableResolution) -> None:
    """Put a resolution into `daemon.log` at the level its outcome deserves.

    A refusal is a WARNING and is reported once per distinct (command, reason,
    rejected path): something *is* installed under that name and mux will not run
    it, which is precisely the failure that used to exist only in an HTTP response
    body. Success and plain absence are DEBUG - detection re-resolves every
    registered harness on every registry read, and a host with two CLIs installed
    would otherwise write several lines per read forever.
    """
    if outcome.reason in {"found", "not_found"}:
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "executable_resolved",
                extra={
                    "command": outcome.command,
                    "reason": outcome.reason,
                    "resolved": outcome.path,
                },
            )
        return
    key = (outcome.command, outcome.reason, outcome.rejected or "")
    if key in _reported_refusals:
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "executable_refused_again",
                extra={"command": outcome.command, "reason": outcome.reason},
            )
        return
    if len(_reported_refusals) >= _REPORTED_REFUSAL_LIMIT:
        _reported_refusals.clear()
    _reported_refusals.add(key)
    log.warning(
        "executable_refused %s",
        outcome.describe(),
        extra={
            "command": outcome.command,
            "reason": outcome.reason,
            "rejected": outcome.rejected,
        },
    )


def which_real(command: str) -> str | None:
    """``shutil.which`` that never resolves to a shim or a foreign-platform binary.

    The launchable path from :func:`resolve_executable`, with the reason dropped.
    Kept because most callers only need to know whether *anything* is launchable;
    anything that has to tell an operator what went wrong should call
    `resolve_executable` instead, so the three ways this returns ``None`` do not
    reach them as one.
    """
    return resolve_executable(command).path
