"""Starting `packaging/redeploy_desktop.py`, for the two callers that do it.

The staged swap has exactly one implementation and this module is how it is
reached: `POST /api/daemon/redeploy` (rebuild locally) and the frozen-app updater
(`update_install.py`, which downloads a verified archive and hands it to the same
script with `--from-archive`). Both need the identical three things, and getting
any of them subtly different is how two of the redeploy's recorded incidents
happened:

- **The single-flight lock is claimed before the spawn, atomically.** Writing it
  afterwards let a double-submit start two staged redeploys racing the same
  `dist/.staging` tree and the same swap. The lock names the *script* process and
  is never removed on exit, so a crash releases it for free.
- **The child is detached from this daemon's lifetime and any Job it inherited**,
  because the script's third step is stopping this very daemon. A child that dies
  with its parent would leave the app stopped and never swapped.
- **The environment is scrubbed of parent-Claude session markers and the cwd
  stays out of `dist/`**: an inherited marker makes every `claude` inside swe-mux
  believe it is a nested child session, and a process anchored inside the bundle
  locks it against the rename the swap depends on.

Nothing here decides *whether* a redeploy may run. The preconditions differ
between the two callers - the updater has a supervisor-protocol gate the local
rebuild does not need, and the local rebuild has a bundle-holder scan the
updater inherits from the script itself - so each caller owns its own refusals
and this module owns only the launch.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from .bundle_locks import REDEPLOY_LOCK_NAME, live_redeploy_lock_pid, write_redeploy_lock
from .config import Config
from .spawn_contract import scrub_claude_session_markers
from .subprocess_flags import background_creation_flags, popen_outside_job

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent

#: The script both callers run, relative to a source checkout root.
REDEPLOY_SCRIPT = Path("packaging") / "redeploy_desktop.py"


class RedeployInFlight(RuntimeError):
    """Another redeploy already holds the lock. Carries its pid when known."""

    def __init__(self, pid: int | None = None) -> None:
        super().__init__(
            f"a redeploy is already running (pid {pid})"
            if pid
            else "a redeploy is already starting"
        )
        self.pid = pid


def redeploy_source_root() -> Path | None:
    """The source checkout this daemon can rebuild itself from, if any.

    Frozen builds live at ``<root>/dist/swe-mux/swe-mux.exe`` inside the
    checkout; source runs resolve from this file. A frozen app deployed away
    from its checkout has neither, and both redeploy and the updater's handoff
    are refused - which is the honest answer, because the swap script is not
    carried in the bundle.
    """
    import sys

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        with suppress(OSError, IndexError):
            candidates.append(Path(sys.executable).resolve().parents[2])
    with suppress(OSError, IndexError):
        # Anchored on the package directory rather than counted from this file's
        # own depth, so moving this module cannot silently repoint "the checkout".
        candidates.append(PACKAGE_DIR.parents[1])
    for root in candidates:
        if (root / REDEPLOY_SCRIPT).is_file() and (root / "pyproject.toml").is_file():
            return root
    return None


def redeploy_lock_pid(config: Config) -> int | None:
    """PID of a live in-flight redeploy, or None (missing/stale lock).

    "Live" means the process is still *this redeploy*, not merely that the number
    exists: a completed run's lock read as live forever once Windows recycled its
    pid, silently refusing every redeploy for the next twenty hours. One rule,
    shared with the script (`bundle_locks.REDEPLOY_LOCK_NAME`), so the two
    readers cannot disagree about whether a redeploy is happening.
    """
    return live_redeploy_lock_pid(config.data_dir / REDEPLOY_LOCK_NAME)


def claim_redeploy_lock(config: Config) -> Path:
    """Claim `redeploy.lock` for a spawn that is about to happen.

    Raises `RedeployInFlight` rather than returning a sentinel, because every
    caller has to stop, and a boolean invites one of them not to.
    """
    lock_path = config.data_dir / REDEPLOY_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    live = redeploy_lock_pid(config)
    if live is not None:
        raise RedeployInFlight(live)
    # No live redeploy, so a file still here is stale (a crash between claiming
    # the lock and writing the pid). Leaving it makes O_EXCL refuse forever.
    with suppress(OSError):
        lock_path.unlink(missing_ok=True)
    try:
        handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RedeployInFlight() from exc
    os.close(handle)
    return lock_path


def spawn_redeploy(
    config: Config,
    *,
    root: Path,
    uv: str,
    lock_path: Path,
    log_path: Path,
    extra_args: Sequence[str] = (),
) -> subprocess.Popen[bytes]:
    """Start the redeploy script detached, and record its pid in the lock.

    `--lock-held` is always passed: the caller claimed the lock above, and
    without it the script would refuse itself.
    """
    command = [
        uv,
        "run",
        "--project",
        str(root),
        "python",
        str(root / REDEPLOY_SCRIPT),
        "--restore-visibility",
        "--lock-held",
        *extra_args,
    ]
    # Without this the script targets ~/.mux, so a daemon on an alternate config
    # reads the wrong supervisor discovery file and aborts - or worse,
    # detach-stops a *different* instance while swapping the shared bundle.
    if (config_path := getattr(config, "config_path", None)) is not None:
        command += ["--config", str(config_path)]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb", buffering=0) as log_file:
            process = popen_outside_job(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(root),
                env=scrub_claude_session_markers(os.environ),
                creationflags=background_creation_flags()
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
    except OSError:
        # The placeholder lock must not outlive a spawn that never happened.
        with suppress(OSError):
            lock_path.unlink(missing_ok=True)
        raise
    write_redeploy_lock(lock_path, process.pid)
    log.info(
        "redeploy script spawned",
        extra={
            "redeploy_pid": process.pid,
            "redeploy_root": str(root),
            "redeploy_log": str(log_path),
            "redeploy_extra_args": " ".join(str(part) for part in extra_args),
        },
    )
    return process
