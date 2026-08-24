"""One `<cli> --version` probe, for every caller that needs one.

There were two, and they disagreed about everything that matters: a 6s timeout
against 2s, a 5-minute cache against an hour, `which_real` resolution against none
at all, and a returncode the harness registry ignored while the environment
inventory required it to be zero. Two probes of the same binary also meant two
subprocesses and two answers that could differ within the same request.

What is unified here is the *mechanism*: resolution, the subprocess, and the cache.
What deliberately stays with each caller is the **presentation**, because the two
consume different things from the same bytes - the harness registry wants the
version token (`1.2.3`) so it can be compared against a tested bound, and the agent
environment wants the CLI's own line (`claude 1.2.3 (Claude Code)`) because it is
shown to a person and used as part of an MCP catalog's cache key. Collapsing those
would have changed a displayed string and a cache key for no gain.

Resolution goes through `which_real` and never `shutil.which`: the daemon prepends
`~/.mux/bin` to PATH and writes a shim for every harness, so a plain `which` finds
a shim on a machine with no CLI installed - and probing the shim would invoke the
shim, which invokes the agent launcher.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass

from .subprocess_flags import background_creation_flags

log = logging.getLogger(__name__)

#: The generous of the two former timeouts. Both call sites run off the event loop
#: (`asyncio.to_thread`), so the cost of waiting is one thread, while the cost of
#: giving up early is a cached `None` that reads as "no CLI installed" - and a cold
#: `claude --version` behind Defender has been measured past two seconds.
PROBE_TIMEOUT_SECONDS = 6.0
#: The shorter of the two former TTLs. A five-minute-stale catalog fingerprint is
#: harmless; a registry that keeps reporting the version a user just upgraded away
#: from is the thing an operator notices.
CACHE_TTL_SECONDS = 300.0
#: A CLI banner, not a document. Anything past this is not a version string.
MAX_OUTPUT_CHARS = 4096

_VERSION_TOKEN = re.compile(r"\d+(?:\.\d+)*")

_lock = threading.Lock()
_cache: dict[str, tuple[float, CliVersion]] = {}


@dataclass(frozen=True, slots=True)
class CliVersion:
    """What `<cli> --version` said, with no judgement about what it means."""

    #: The resolved executable the probe actually ran, never the name asked for.
    executable: str
    #: `None` when the probe could not run it at all (spawn failure or timeout).
    exit_code: int | None
    #: stdout and stderr merged and stripped. Merged because CLIs disagree about
    #: which one a version belongs on, and the caller only ever wants the first
    #: thing that looks like a version.
    output: str

    @property
    def first_line(self) -> str:
        return self.output.splitlines()[0].strip() if self.output else ""

    @property
    def token(self) -> str | None:
        """The `1.2.3` inside the banner, if there is one."""
        match = _VERSION_TOKEN.search(self.output)
        return match.group() if match else None


def clear_cache() -> None:
    """Drop every cached probe. For tests, and for a deliberate re-probe."""
    with _lock:
        _cache.clear()


def probe(executable: str, *, refresh: bool = False) -> CliVersion | None:
    """Run `<executable> --version` at most once per TTL. Never raises.

    Returns `None` when the name does not resolve to a real, launchable CLI - which
    is a different answer from "it ran and said nothing", and the two must not be
    collapsed: the first means no such CLI is installed here.
    """
    from .shim_paths import which_real

    candidate = executable.strip()
    if not candidate:
        return None
    resolved = which_real(candidate)
    if not resolved:
        return None
    now = time.monotonic()
    if not refresh:
        with _lock:
            cached = _cache.get(resolved)
        if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    result = _run(resolved)
    with _lock:
        _cache[resolved] = (now, result)
    return result


def _run(resolved: str) -> CliVersion:
    try:
        completed = subprocess.run(  # noqa: S603 - resolved real executable, fixed arg
            [resolved, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # Explicit rather than the locale default: a CLI that prints a byte
            # cp1252 cannot decode would otherwise raise `UnicodeDecodeError`, which
            # is a `ValueError` and escapes the handler below.
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            creationflags=background_creation_flags(),
        )
    except subprocess.TimeoutExpired:
        log.warning("cli_version_probe_timed_out executable=%s", resolved)
        return CliVersion(resolved, None, "")
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug(
            "cli_version_probe_failed executable=%s error_type=%s", resolved, type(exc).__name__
        )
        return CliVersion(resolved, None, "")
    output = (completed.stdout or "")[:MAX_OUTPUT_CHARS].strip()
    return CliVersion(resolved, completed.returncode, output)
