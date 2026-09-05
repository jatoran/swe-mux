"""Starting the staged swap in a detached process, for the two callers that do it.

The staged swap has exactly one implementation (`bundle_apply.py`) and two
processes that run it: `packaging/redeploy_desktop.py` in a source checkout
(`POST /api/daemon/redeploy`, the rebuild-from-source path) and the frozen
console client's `swemux update-apply` (the in-app updater, `update_install.py`,
which downloads a verified archive and hands it to a copy of that client in the
data directory). Both spawns need the identical three things, and getting any
of them subtly different is how two of the redeploy's recorded incidents
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
    from its checkout has neither, and the rebuild-from-source redeploy is
    refused - which is the honest answer, because the build needs a checkout.
    The in-app updater no longer needs one: it installs a release through the
    frozen console client (`spawn_applier`), which carries the swap itself.
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
    return _spawn_detached(
        command,
        cwd=root,
        lock_path=lock_path,
        log_path=log_path,
        what="redeploy script",
        detail={"redeploy_root": str(root), "redeploy_extra_args": " ".join(extra_args)},
    )


def spawn_applier(
    config: Config,
    *,
    applier: Path,
    archive: Path,
    sha256: str,
    install_root: Path,
    mode: str,
    lock_path: Path,
    log_path: Path,
) -> subprocess.Popen[bytes]:
    """Start the frozen console client's `update-apply` detached, lock recorded.

    `applier` is a `swemux` executable in a copy of the client bundle under the
    data directory (`update_install.UpdateInstaller._prepare_applier`), which is
    the whole reason this works without a checkout: it runs from outside every
    tree it renames. The working directory is the data directory for the same
    reason the script's is the checkout - never inside the install root, whose
    bundles are about to be renamed and which a cwd would lock.
    """
    command = [
        str(applier),
        "update-apply",
        "--archive",
        str(archive),
        "--archive-sha256",
        sha256,
        "--install-root",
        str(install_root),
        "--mode",
        mode,
        "--restore-visibility",
        "--lock-held",
    ]
    if (config_path := getattr(config, "config_path", None)) is not None:
        command += ["--config", str(config_path)]
    return _spawn_detached(
        command,
        cwd=Path(config.data_dir),
        lock_path=lock_path,
        log_path=log_path,
        what="update applier",
        detail={
            "update_applier": str(applier),
            "update_archive": str(archive),
            "update_install_root": str(install_root),
            "update_mode": mode,
        },
    )


def _spawn_detached(
    command: list[str],
    *,
    cwd: Path,
    lock_path: Path,
    log_path: Path,
    what: str,
    detail: dict[str, str],
) -> subprocess.Popen[bytes]:
    """The one spawn: detached from this daemon and its Job, output into `log_path`."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb", buffering=0) as log_file:
            process = popen_outside_job(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(cwd),
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
        "%s spawned",
        what,
        extra={"redeploy_pid": process.pid, "redeploy_log": str(log_path), **detail},
    )
    return process
