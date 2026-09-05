"""Swapping the installed desktop bundles for staged ones, from any process.

This is the staged swap `packaging/redeploy_desktop.py` has always performed -
stage while the old app serves, stop it only once the staging tree is good,
rename, relaunch, health-check, roll back - lifted out of that script so that
a process with **no source checkout and no `uv`** can perform it. That process
is the frozen console client (`swemux update-apply`, `cli.py`), and the reason
it exists is the one property no other process on the machine has: it runs from
a *copy* in the data directory, outside every tree this module renames.

The script keeps what is genuinely a developer's concern - argument parsing,
the build-environment preflight, and the PyInstaller build into the staging
tree - and calls `apply_staged` for everything after the staging tree exists.
The frozen applier calls `apply_archive`, which stages a verified release
archive and then calls the same `apply_staged`. There is one swap.

#### What a run guarantees, and where each guarantee comes from

**A failure before the stop leaves the running app untouched.** Staging happens
into `<install root>/.staging` while the old bundles keep serving, and every
refusal in `stage_from_archive`, `preflight_supervisor` and
`abort_if_bundle_held` happens before anything is stopped.

**The stop matches the mode, and the mode is the operator's decision.**
`MODE_SWAP` asks the daemon to shut down with detach intent, so the PTY
supervisor - a separate process in its own bundle - keeps every live session
and the relaunched daemon reattaches them. `MODE_REPLACE` asks for quit intent,
which reaps every session and stops the supervisor, and then replaces the
supervisor bundle as well. Nothing here chooses between them: the updater's
consent gate (`update_install.py`) and the script's `--replace-supervisor` flag
do, and both say what it costs before it runs.

**Renames are the only mutation, and the previous bundle is kept.** Each bundle
moves to `<name>.prev` before its staged replacement moves in, under the
bundle-swap hold that keeps hook clients from launching into a directory that
is mid-rename (`bundle_swap.py`). A bundle that never turns healthy is rolled
back from those slots, in reverse order, and the failed tree is kept at
`<name>.failed` for inspection.

**The console client is best effort.** A `swemux` sitting in a terminal holds
`swe-mux-cli/` open, and killing a person's terminal command to refresh a
client is the wrong trade: the old client keeps working against the new daemon
(it is an HTTP client of the daemon's own routes), the failure is logged and
recorded, and the next update tries again. The app bundle and, in replace mode,
the supervisor bundle are *required*: a failure there rolls the run back.

**Every run records what it did.** `<data_dir>/redeploy-result.json` carries the
outcome, the mode, the version, and which bundles were swapped, written at the
moment of decision because the very next thing a rollback does is relaunch the
old app and the browser starts asking for the record as soon as any daemon
answers. Progress goes to whatever stdout the caller redirected - the daemon
points it at `<data_dir>/redeploy.log`, which the UI's progress chip tails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bundle_archive import (
    ARCHIVE_ROOT,
    CLI_ROOT,
    SUPERVISOR_ROOT,
    ArchiveError,
    file_digest,
    read_archive_metadata,
)
from .bundle_locks import (
    REDEPLOY_LOCK_NAME,
    bundle_lock_holders,
    live_redeploy_lock_pid,
    write_redeploy_lock,
)
from .bundle_metadata import BundleMetadata
from .bundle_stage import StageResult, stage_bundle
from .bundle_swap import hold_bundle_swap
from .spawn_contract import scrub_claude_session_markers
from .subprocess_flags import popen_outside_job
from .supervisor import discovery_path

#: The three bundle directories, under the names both `dist/` and the installer's
#: `{app}` use. The app bundle is the one every install has and the one every
#: run swaps; the other two ride along when the archive carries them.
APP_BUNDLE = ARCHIVE_ROOT
SUPERVISOR_BUNDLE = SUPERVISOR_ROOT
CLI_BUNDLE = CLI_ROOT
ALL_BUNDLES = (APP_BUNDLE, SUPERVISOR_BUNDLE, CLI_BUNDLE)

#: Where a run stages under the install root, and the suffixes the retired and
#: failed trees are kept under beside the live one.
STAGING_DIRNAME = ".staging"
PREV_SUFFIX = ".prev"
FAILED_SUFFIX = ".failed"

APP_IMAGE_NAMES = {"swe-mux.exe", "swe-mux"}
ACTION_IMAGE_NAME = "swe-mux-action.exe"
SUPERVISOR_IMAGE_NAME = "swe-mux-supervisor.exe"
# `swe-mux.exe -m swe_mux.<module>` is a short-lived helper an agent session
# spawned inside its OWN process tree -- hook_client is the one that matters, it
# runs on every PreToolUse/PostToolUse. It shares the app's image name but is not
# the shell or the daemon, and killing it reaches into a live session. A redeploy
# once did exactly that (`taskkill /F /IM swe-mux.exe`, no filter) and took down
# the only session that happened to be mid-tool-call. Helpers are therefore spared
# by the ordinary stop and only swept if a lock actually blocks the swap.
HELPER_MODULE_FLAG = "-m"
HELPER_MODULE_PREFIX = "swe_mux."
# How long a directory rename retries while the just-stopped exe releases its
# locks (the old WinError 5/32 straggler, now confined to a cheap rename).
SWAP_RETRY_SECONDS = 20.0
# First launch of a freshly written PyInstaller tree can spend several minutes
# in Windows image scanning before the tray reaches daemon startup. Rolling back
# while that process is still alive converts a slow-but-valid deploy into an
# outage, so give cold starts a realistic budget and fail early only when the
# launched shell actually exits.
# 600 rather than 300: measured 2026-08-21, an already-scanned build took 225s
# to "runtime ready" with 30 live sessions, so a fresh bundle paying its
# first-launch scan on top of that legitimately exceeds 300s - the rollback
# fired on a healthy-but-slow deploy. Overridable per run for slower fleets.
APP_HEALTH_TIMEOUT_SECONDS = float(os.environ.get("MUX_REDEPLOY_HEALTH_TIMEOUT", "600"))
#: How long the quit-intent stop waits for the daemon to reap its sessions and
#: for the supervisor to exit after it, before terminating what is left.
QUIT_GRACE_SECONDS = 30.0
# Outcomes recorded in `<data_dir>/redeploy-result.json`. The successor daemon
# serves this so the reconnecting UI can say what actually happened: a rollback
# used to be visible only as English in redeploy.log, which meant the app came
# back as the OLD build and nothing said so.
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_ROLLED_BACK = "rolled_back"
OUTCOME_BUILD_FAILED = "build_failed"
OUTCOME_SWAP_FAILED = "swap_failed"
OUTCOME_UNHEALTHY = "unhealthy"
OUTCOME_REFUSED = "refused"
OUTCOME_FAILED = "failed"

#: The two things a run can be asked to do, and the whole difference between
#: them is whether the operator's live sessions survive.
MODE_SWAP = "swap"
MODE_REPLACE = "replace"
MODES = (MODE_SWAP, MODE_REPLACE)


def log(message: str) -> None:
    print(f"[redeploy] {message}", flush=True)


def app_exe_name() -> str:
    """The app launcher's filename on this host."""
    return "swe-mux.exe" if os.name == "nt" else "swe-mux"


@dataclass(frozen=True, slots=True)
class Layout:
    """Where the bundles live, and where a run stages, retires, and keeps failures.

    One root rather than a handful of module constants, because the same swap
    now runs against three different roots: a checkout's `dist/`, the Windows
    installer's `{app}`, and wherever a portable archive was unpacked. Every path
    is derived from `install_root`, so the three cannot disagree about where a
    retired bundle went.
    """

    install_root: Path

    @classmethod
    def for_checkout(cls, root: Path) -> Layout:
        """A source checkout's `dist/`, where `build_desktop.py` writes."""
        return cls(Path(root) / "dist")

    @classmethod
    def for_bundle(cls, bundle_root: Path) -> Layout:
        """The install holding an app bundle: its parent directory."""
        return cls(Path(bundle_root).parent)

    @property
    def app(self) -> Path:
        return self.install_root / APP_BUNDLE

    @property
    def app_exe(self) -> Path:
        return self.app / app_exe_name()

    @property
    def supervisor(self) -> Path:
        return self.install_root / SUPERVISOR_BUNDLE

    @property
    def cli(self) -> Path:
        return self.install_root / CLI_BUNDLE

    @property
    def staging_root(self) -> Path:
        return self.install_root / STAGING_DIRNAME

    def live(self, name: str) -> Path:
        return self.install_root / name

    def staged(self, name: str) -> Path:
        return self.staging_root / name

    def prev(self, name: str) -> Path:
        return self.install_root / f"{name}{PREV_SUFFIX}"

    def failed(self, name: str) -> Path:
        return self.install_root / f"{name}{FAILED_SUFFIX}"


class Outcome:
    """Records what a run did, for the UI that reconnects after the outage.

    `record` is called at the terminal paths whose meaning the exit code cannot
    carry (a rollback and a failed swap both exit 1, and "the app is back" means
    something very different in each). It writes **at the moment of decision**,
    not on the way out: the very next thing a rollback does is relaunch the old
    app, and the browser starts asking for this file as soon as *a* daemon
    answers, so a record written after that relaunch is one the reader can miss.

    `finish` is the backstop for every other return. It writes a record derived
    from the exit code when none was made, so a new early return can never leave
    the previous run's result standing - a stale record would tell the UI that
    *this* redeploy did whatever the last one did, which is worse than silence.

    `describe` attaches facts a reader needs to interpret the outcome - which
    mode ran, which version, which bundles moved - and every record carries the
    latest of them.
    """

    def __init__(self, config: Any, started_at: float) -> None:
        self._path = Path(config.data_dir) / "redeploy-result.json"
        self._log_path = Path(config.data_dir) / "redeploy.log"
        self._started_at = started_at
        self._recorded = False
        self._facts: dict[str, Any] = {}

    def describe(self, **facts: Any) -> None:
        self._facts.update(facts)

    def record(self, kind: str, detail: str, *, code: int) -> None:
        self._recorded = True
        self._write(kind, detail, code)

    def finish(self, code: int) -> int:
        if not self._recorded:
            if code == 0:
                kind, detail = OUTCOME_SUCCEEDED, "The redeploy completed."
            elif code == 2:
                kind, detail = (
                    OUTCOME_REFUSED,
                    "The redeploy was refused before anything was changed.",
                )
            else:
                kind, detail = OUTCOME_FAILED, "The redeploy failed. See redeploy.log."
            self._write(kind, detail, code)
        return code

    def _write(self, kind: str, detail: str, code: int) -> None:
        payload = {
            "outcome": kind,
            "detail": detail,
            "exit_code": code,
            "started_at": self._started_at,
            "finished_at": time.time(),
            "log_tail": self._tail(),
            **self._facts,
        }
        # Written whole via a temp file: the daemon that reads this is starting up
        # concurrently, and a partially written file would parse as "no record".
        temporary = self._path.with_suffix(".json.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError as exc:
            log(f"could not record the redeploy outcome: {exc}")

    def _tail(self, lines: int = 12) -> list[str]:
        """This run's log tail, or nothing.

        Only the daemon endpoint redirects this script's output into
        `redeploy.log`; a run launched from a terminal prints to its own stdout
        and never touches that file. Reading it unconditionally therefore
        stamped a *previous* redeploy's output into this run's result - observed
        live: a record whose detail said 11 live sessions carried a tail ending
        "live_sessions=2" from an unrelated earlier run. A log older than this
        run is not this run's log, and no tail beats a wrong one.
        """
        try:
            if self._log_path.stat().st_mtime < self._started_at:
                return []
            data = self._log_path.read_bytes()
        except OSError:
            return []
        return data[-8192:].decode("utf-8", "replace").splitlines()[-lines:]


# --- single-flight and announcement -------------------------------------------


def claim_lock(config: Any, *, already_held: bool) -> bool:
    """Claim `redeploy.lock` for this process. False means one is already live.

    The daemon claims it before spawning the script (and passes --lock-held),
    so this covers the terminal-launched case, which previously took no lock at
    all: `GET /api/daemon/redeploy` reported nothing in flight, two concurrent
    CLI redeploys could race the same staging tree and swap, and the UI had no
    way to know it should stop trusting the daemon.

    Never removed on exit. The lock names this process and every reader tests
    whether that process is still *this redeploy*, so a crash releases it for
    free and a half-deleted file can never make a live redeploy look finished.

    "This redeploy" rather than "a pid that exists": a completed run's lock read
    as live forever once Windows recycled its pid, and the refusal below exits 0,
    so every redeploy for the next twenty hours was silently declined
    (`bundle_locks.REDEPLOY_LOCK_NAME`).
    """
    if already_held:
        return True
    path = Path(config.data_dir) / REDEPLOY_LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    live = live_lock_pid(config)
    if live is not None:
        log(f"ABORT: a redeploy is already running (pid {live})")
        return False
    # A lock naming a dead pid is stale by definition; only O_EXCL can decide the
    # race between two scripts that both just found it stale.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        log("ABORT: a redeploy is already starting")
        return False
    except OSError as exc:
        log(f"WARNING: could not claim {path} ({exc}); continuing without single-flight")
        return True
    os.close(handle)
    write_redeploy_lock(path, os.getpid())
    return True


def live_lock_pid(config: Any) -> int | None:
    """PID named by a live `redeploy.lock`, or None (missing/stale/ours-to-take).

    One shared rule with the daemon's reader (`bundle_locks`), so the two cannot
    disagree about whether a redeploy is in flight.
    """
    pid = live_redeploy_lock_pid(Path(config.data_dir) / REDEPLOY_LOCK_NAME)
    return None if pid == os.getpid() else pid


def announce_start(config: Any) -> None:
    """Ask the daemon to tell its clients a redeploy just began.

    Best-effort by design: this only buys the UI a progress chip during the
    build, so a daemon that is not up, not desktop-managed, or too old to know
    the route costs nothing but the old behaviour.
    """
    request = urllib.request.Request(
        f"{base_url(config)}/api/daemon/redeploy/announce",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if int(response.status) == 202:
                log("announced the redeploy to connected clients")
                return
    except (OSError, urllib.error.URLError) as exc:
        log(f"could not announce the redeploy to clients ({exc}); continuing")
        return
    log("daemon did not accept the redeploy announcement; continuing")


# --- the daemon, observed over HTTP -------------------------------------------


def base_url(config: Any) -> str:
    return f"http://127.0.0.1:{config.port}"


def health_payload(config: Any, timeout: float = 1.5) -> dict[str, Any] | None:
    """Whatever `/api/health` says, ready or not.

    A daemon that is still building its runtime answers 503 with the phase it is
    in, and `urlopen` raises `HTTPError` for that - which is itself a readable
    response, so the body is parsed rather than discarded. Reading it is the
    whole reason the health wait can report progress instead of silence.
    """
    try:
        with urllib.request.urlopen(f"{base_url(config)}/api/health", timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            payload = json.load(error)
        except (OSError, ValueError):
            return None
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def health(config: Any, timeout: float = 1.5) -> dict[str, Any] | None:
    """The health payload only once the daemon is fully ready.

    Deliberately unchanged in meaning: every caller of this asks "is there a
    usable daemon on this port", and a daemon part-way through its startup is
    not one. `health_payload` is the wider read for callers that want the
    in-progress answer too.
    """
    payload = health_payload(config, timeout)
    return payload if payload is not None and payload.get("ok") else None


def startup_progress(payload: dict[str, Any] | None) -> str:
    """One line describing where a starting daemon has got to, or "".

    Only ever descriptive. It quotes the phase the daemon named and the phases
    it has already finished; nothing here estimates a remaining time, because
    the phase durations vary by two orders of magnitude across fleets and a made
    up percentage is acted on where an absent one is not.
    """
    if not payload or payload.get("status") != "starting":
        return ""
    phase = str(payload.get("phase") or "starting")
    phase_seconds = float(payload.get("phase_seconds") or 0.0)
    elapsed = float(payload.get("elapsed_seconds") or 0.0)
    done = [str(item.get("name")) for item in (payload.get("phases") or []) if item.get("name")]
    completed = f"; done: {', '.join(done)}" if done else ""
    return (
        f"starting - phase {phase} ({phase_seconds:.0f}s), {elapsed:.0f}s into startup{completed}"
    )


def supervisor_process(config: Any) -> tuple[int, Path] | None:
    """(pid, exe_path) of the live supervisor for this config, or None."""
    import psutil

    try:
        info = json.loads(discovery_path(Path(config.data_dir)).read_text(encoding="utf-8"))
        pid = int(info["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        process = psutil.Process(pid)
        return pid, Path(process.exe())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def request_shutdown(config: Any, mode: str) -> bool:
    """Ask the desktop-managed daemon to stop with `quit` or `restart` intent.

    `restart` detaches and leaves supervisor-owned sessions running for the next
    daemon; `quit` reaps every session and stops the supervisor. False when the
    daemon is not desktop-managed (no control token) or did not accept.
    """
    token_path = Path(config.data_dir) / "desktop-control.token"
    try:
        token = token_path.read_text(encoding="ascii").strip()
    except OSError:
        return False
    if not token:
        return False
    request = urllib.request.Request(
        f"{base_url(config)}/api/desktop/shutdown",
        data=json.dumps({"mode": mode}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status) == 202
    except (OSError, urllib.error.URLError):
        return False


def request_detach_shutdown(config: Any) -> bool:
    return request_shutdown(config, "restart")


# --- the app's processes --------------------------------------------------------


def processes_by_image(names: set[str]) -> list[tuple[int, str]]:
    import psutil

    found: list[tuple[int, str]] = []
    wanted = {value.casefold() for value in names}
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = (process.info["name"] or "").casefold()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name in wanted:
            found.append((int(process.info["pid"]), name))
    return found


def is_session_helper(process: Any) -> bool:
    """True for `swe-mux.exe -m swe_mux.<module>`, a helper inside a session tree."""
    import psutil

    try:
        argv = [str(part) for part in process.cmdline()]
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        # Unreadable argv cannot be proven safe to kill. Treating it as a helper
        # only risks a lock straggler, which the swap escalation already handles;
        # treating it as the shell risks killing a live session, which it does not.
        return True
    for flag, module in zip(argv, argv[1:], strict=False):
        if flag == HELPER_MODULE_FLAG and module.startswith(HELPER_MODULE_PREFIX):
            return True
    return False


def partition_app_processes() -> tuple[list[int], list[int]]:
    """Split live `swe-mux.exe` processes into (shell/daemon, session helpers)."""
    import psutil

    shell: list[int] = []
    helpers: list[int] = []
    for pid, _ in processes_by_image(APP_IMAGE_NAMES):
        if pid == os.getpid():
            continue
        try:
            process = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        (helpers if is_session_helper(process) else shell).append(pid)
    return shell, helpers


def app_window_visible() -> bool:
    """Whether a visible top-level window belongs to the desktop app."""

    if sys.platform != "win32":
        return False
    shell_pids = set(partition_app_processes()[0])
    if not shell_pids:
        return False

    import ctypes
    from ctypes import wintypes

    found = False
    user32 = ctypes.windll.user32
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    def inspect_window(handle: Any, _parameter: Any) -> bool:
        nonlocal found
        if not user32.IsWindowVisible(handle):
            return True
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if process_id.value in shell_pids:
            found = True
            return False
        return True

    user32.EnumWindows(enum_callback(inspect_window), 0)
    return found


def resolve_relaunch_hidden(*, hidden: bool, restore_visibility: bool) -> bool:
    """Choose launch presentation, probing only for UI-triggered redeploys."""

    return hidden or (restore_visibility and not app_window_visible())


def terminate_pids(pids: list[int], *, grace: float = 3.0) -> None:
    """Terminate then kill specific pids, never a whole image name."""
    import psutil

    processes = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            process.terminate()
            processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    _, alive = psutil.wait_procs(processes, timeout=grace)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    if alive:
        psutil.wait_procs(alive, timeout=grace)


def force_stop_app_images() -> None:
    """Last-resort image-wide kill, used only when a lock blocks the swap.

    This is the blunt instrument: it reaches every `swe-mux.exe`, including the
    in-session helpers deliberately spared above. It runs only when the choice is
    between that and a failed redeploy, and it says so.
    """
    _, helpers = partition_app_processes()
    if helpers:
        log(
            f"escalating to an image-wide kill; {len(helpers)} in-session helper(s) "
            "will be terminated too"
        )
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/IM", "swe-mux.exe"], capture_output=True, check=False)
    else:
        shell, helpers = partition_app_processes()
        terminate_pids(shell + helpers)
    time.sleep(1.0)


def stop_app_processes(config: Any) -> None:
    """The session-preserving stop: detach the daemon, then stop the shell."""
    if health(config) is not None:
        log("asking the daemon to shut down with detach intent (sessions stay up)")
        if request_shutdown(config, "restart"):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and health(config, timeout=0.5) is not None:
                time.sleep(0.25)
        else:
            log("daemon did not accept desktop shutdown (not desktop-managed?); continuing")
    shell, helpers = partition_app_processes()
    if helpers:
        log(f"sparing {len(helpers)} in-session swe-mux helper(s) (hook clients)")
    if shell:
        log(f"terminating {len(shell)} swe-mux.exe process(es) (shell/daemon)")
        terminate_pids(shell)
        time.sleep(1.0)


def stop_supervisor(config: Any, *, grace: float = QUIT_GRACE_SECONDS) -> None:
    """Wait for the supervisor to exit after a quit, then terminate what is left.

    The daemon's quit teardown asks the supervisor to reap every session and
    exit (`supervisor_client.reap_all_and_exit`), so the ordinary case is a wait.
    The termination is for a supervisor that did not get that message - a daemon
    that was not desktop-managed, or one that died before its teardown - and it
    is scoped to the pid the discovery file names rather than to an image name,
    so a supervisor serving a different data directory is never touched.
    """
    found = supervisor_process(config)
    if found is None:
        return
    pid, exe = found
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and supervisor_process(config) is not None:
        time.sleep(0.25)
    if supervisor_process(config) is not None:
        log(f"supervisor pid {pid} ({exe}) is still running after quit; terminating it")
        terminate_pids([pid])
    # The discovery file is the supervisor's own statement that it is serving;
    # one naming a process that is gone would send the next daemon to a socket
    # nobody answers before it decides to spawn a fresh supervisor.
    discovery = discovery_path(Path(config.data_dir))
    with suppress(OSError, ValueError, TypeError):
        info = json.loads(discovery.read_text(encoding="utf-8"))
        if int(info.get("pid", -1)) == pid:
            discovery.unlink(missing_ok=True)
    log("supervisor stopped; every supervised session was reaped")


def stop_everything(config: Any) -> None:
    """The reap-everything stop: quit intent, then the supervisor, then stragglers."""
    if health(config) is not None:
        log("asking the daemon to shut down with quit intent (this reaps every session)")
        if request_shutdown(config, "quit"):
            deadline = time.monotonic() + QUIT_GRACE_SECONDS
            while time.monotonic() < deadline and health(config, timeout=0.5) is not None:
                time.sleep(0.25)
        else:
            log("daemon did not accept desktop shutdown (not desktop-managed?); continuing")
    stop_supervisor(config)
    shell, helpers = partition_app_processes()
    if shell or helpers:
        log(f"terminating {len(shell) + len(helpers)} remaining swe-mux.exe process(es)")
        terminate_pids(shell + helpers)
        time.sleep(1.0)


def stop_for_mode(config: Any, mode: str) -> None:
    if mode == MODE_REPLACE:
        stop_everything(config)
    else:
        stop_app_processes(config)


# --- preflight ---------------------------------------------------------------------


def preflight_supervisor(config: Any, layout: Layout, *, mode: str, force: bool) -> int:
    """Refuse a swap that would kill sessions the operator expects to keep.

    Returns 0 to proceed and 2 to refuse. In replace mode nothing is refused -
    the operator has already accepted the reap - and what is about to be reaped
    is logged instead, so the record says what the consent covered.
    """
    supervisor = supervisor_process(config)
    if mode == MODE_REPLACE:
        if supervisor is None:
            log("no PTY supervisor is running; replacing every bundle (nothing to preserve)")
        else:
            pid, exe = supervisor
            log(
                f"supervisor pid {pid} running from {exe}; it will be stopped and every "
                "supervised session reaped (replace mode, consented)"
            )
        return 0
    if supervisor is None:
        message = (
            "no PTY supervisor is running for this config; a redeploy will kill any "
            "in-process sessions"
        )
        if not force:
            log(f"ABORT: {message}. Re-run with --force to proceed anyway.")
            return 2
        log(f"WARNING: {message} (continuing due to --force)")
        return 0
    pid, exe = supervisor
    log(f"supervisor pid {pid} running from {exe}")
    try:
        inside_app = exe.resolve().is_relative_to(layout.app.resolve())
    except OSError:
        inside_app = False
    if inside_app and not force:
        log(
            f"ABORT: the supervisor is running from {layout.app} (the "
            "--supervisor-child fallback), so replacing the app bundle would kill it and "
            "every session. Update in replace mode (which ends every session and installs "
            "the dedicated supervisor bundle), or run `swemuxd --shutdown`, replace the "
            "bundles by hand, and relaunch; future updates will then preserve sessions."
        )
        return 2
    return 0


def abort_if_bundle_held(layout: Layout, *, force: bool, when: str) -> bool:
    """Report foreign processes anchoring the app bundle. True means abort.

    Only processes the stop machinery cannot release count (the app's own
    image and its descendants are excluded by the scan), so a report here is a
    swap that WILL fail. ``force`` downgrades it to a warning for the case
    where the holder is expected to exit during the build.
    """
    holders = bundle_lock_holders(layout.app)
    if not holders:
        return False
    verdict = "WARNING" if force else "ABORT"
    log(f"{verdict}: {layout.app} is held open by processes a redeploy cannot stop ({when}):")
    for holder in holders:
        log(f"  pid {holder['pid']} {holder['name']} ({holder['via']}: {holder['path']})")
    if force:
        log("continuing due to --force; the swap may still fail on these locks")
        return False
    log(
        "These are usually a dev server/preview process or a terminal whose working "
        f"directory is inside {layout.app}. Stop those processes (or close their "
        "tabs/sessions) and re-run, or re-run with --force to attempt anyway."
    )
    return True


# --- staging -----------------------------------------------------------------------


def stage_from_archive(
    archive: Path,
    expected_sha256: str,
    layout: Layout,
    outcome: Any,
) -> tuple[int, StageResult | None, BundleMetadata | None]:
    """Verify and extract a release archive into the staging tree.

    Returns `(0, result, metadata)` when staged, or a non-zero code with `None`s
    after recording the refusal. Stands exactly where the PyInstaller build
    stands, and gives the same two guarantees the build gives: it happens while
    the old app is still serving, and a failure here has touched nothing.
    Everything after it - the stop, the swap, the health wait, the rollback to
    `<name>.prev` - is unchanged and unaware that a download rather than a build
    produced the tree.

    The hash is re-checked here even though the daemon's updater already verified
    it. That is not distrust of the caller; it is that this code is separately
    invocable with any path a person can type, and a guarantee that only holds
    when you were called by the right process is not a guarantee. Passing no
    hash is allowed and says so out loud, because a maintainer installing a
    locally-built archive has nothing to check against.

    Since Phase 21 the extraction is a **delta** where the archive supports one:
    `bundle_stage.stage_bundle` reads the archive's own `files.json`, hard-links
    every file whose SHA-256 already matches what is installed, and writes only
    the rest. That is the same tree either way - each reused file is proven
    byte-identical to the release before it is linked - but a linked file keeps
    the antivirus verdict the machine already has for it, which is where an
    update's minutes actually go. Anything unexpected falls back to extracting
    the whole archive and says so, because that is precisely the behaviour that
    shipped before, and a delta may never turn a slow install into no install.
    """
    archive = Path(archive)
    if not archive.is_file():
        log(f"ABORT: {archive} does not exist; nothing was touched")
        outcome.record(
            OUTCOME_REFUSED,
            f"The release archive {archive.name} was not found. Nothing was changed.",
            code=2,
        )
        return 2, None, None
    expected = str(expected_sha256 or "").strip().lower()
    if expected:
        actual = file_digest(archive)
        if actual != expected:
            log(f"ABORT: {archive.name} does not match the expected SHA-256; nothing was touched")
            outcome.record(
                OUTCOME_REFUSED,
                f"{archive.name} does not match the SHA-256 it was supposed to have, "
                "so it was not staged. Nothing was changed.",
                code=2,
            )
            return 2, None, None
        log(f"{archive.name} matches the expected SHA-256")
    else:
        log(f"WARNING: no SHA-256 given for {archive.name}; extracting unverified")
    try:
        metadata = read_archive_metadata(archive)
        log(
            f"staging swe-mux {metadata.version} ({metadata.platform}, supervisor "
            f"protocol {metadata.supervisor_protocol}) from {archive.name}"
        )
        # The installed bundle is offered as the reuse source only when it is
        # actually there. A first install, or an install root somebody has
        # cleared, is a full extraction rather than a failure.
        current = layout.app if layout.app.is_dir() else None
        result = stage_bundle(archive, layout.staging_root, current_root=current, say=log)
        log(result.summary())
    except ArchiveError as exc:
        log(f"ABORT: {exc.message}; nothing was touched")
        outcome.record(
            OUTCOME_REFUSED,
            f"{exc.message} Nothing was changed.",
            code=2,
        )
        return 2, None, None
    except OSError as exc:
        log(f"ABORT: could not extract {archive.name} ({exc}); nothing was touched")
        outcome.record(
            OUTCOME_BUILD_FAILED,
            f"The release archive could not be extracted ({exc}). The current app is untouched.",
            code=1,
        )
        return 1, None, None
    return 0, result, metadata


# --- the swap ----------------------------------------------------------------------


def clear_slot(path: Path, *, retry_seconds: float = SWAP_RETRY_SECONDS) -> bool:
    """Free `path` so a later rename can land on it. False if it survives.

    `shutil.rmtree(..., ignore_errors=True)` can leave a *partially* deleted
    tree behind: Windows refuses to unlink an exe/DLL whose image is still
    mapped by a process killed moments earlier, and the errors are swallowed.
    The surviving directory then blocks every future rename onto it (WinError
    183), which is how a stale `swe-mux.prev` aborts a redeploy long after the
    run that created it. So retry the removal, and if it still will not go,
    move it aside under a unique name instead of leaving the slot poisoned.
    Stale leftovers are swept opportunistically once their locks are gone.
    """
    for stale in path.parent.glob(f"{path.name}.stale-*"):
        shutil.rmtree(stale, ignore_errors=True)
    if not path.exists():
        return True
    deadline = time.monotonic() + retry_seconds
    # unsupervised-loop-ok: a synchronous retry bounded by `retry_seconds`, in the
    # applier process rather than the daemon.
    while True:
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    aside = path.with_name(f"{path.name}.stale-{int(time.time())}")
    try:
        path.rename(aside)
    except OSError as exc:
        log(f"could not clear {path}: {exc}")
        return False
    log(f"{path} had undeletable leftovers (locked images); moved aside to {aside}")
    return True


def replace_dir(source: Path, target: Path, *, retry_seconds: float = SWAP_RETRY_SECONDS) -> bool:
    """Rename source -> target, retrying while a just-stopped exe releases locks."""
    deadline = time.monotonic() + retry_seconds
    # unsupervised-loop-ok: a synchronous retry bounded by `retry_seconds`, in the
    # applier process rather than the daemon.
    while True:
        try:
            source.rename(target)
            return True
        except OSError as exc:
            if time.monotonic() >= deadline:
                log(f"could not move {source} -> {target}: {exc}")
                return False
            time.sleep(0.5)


def bundles_for_mode(layout: Layout, mode: str) -> list[str]:
    """Which staged bundles a run of `mode` moves into place, app first.

    The supervisor bundle is included only in replace mode, whatever the archive
    carried: a staged supervisor tree in swap mode is left where it is and
    deleted with the rest of the staging root, because moving it would replace
    the process that holds every live session.
    """
    names = [APP_BUNDLE]
    if mode == MODE_REPLACE and layout.staged(SUPERVISOR_BUNDLE).is_dir():
        names.append(SUPERVISOR_BUNDLE)
    if layout.staged(CLI_BUNDLE).is_dir():
        names.append(CLI_BUNDLE)
    return names


def required_bundles(mode: str) -> frozenset[str]:
    """The bundles whose swap failure is a failed run rather than a warning."""
    if mode == MODE_REPLACE:
        return frozenset({APP_BUNDLE, SUPERVISOR_BUNDLE})
    return frozenset({APP_BUNDLE})


def _swap_one(layout: Layout, name: str) -> bool:
    """Retire the live `name` to its `.prev` slot and move the staged tree in."""
    live = layout.live(name)
    staged = layout.staged(name)
    prev = layout.prev(name)
    clear_slot(prev)
    if live.exists() and not replace_dir(live, prev):
        if name != APP_BUNDLE:
            return False
        # A lock straggler outlived the targeted stop. Only now is the blunt
        # image-wide kill worth its cost: the alternative is a redeploy that
        # fails outright. Sparing helpers first means the common path never
        # pays it, and this path retries the rename once afterwards.
        force_stop_app_images()
        if not replace_dir(live, prev):
            return False
    if not replace_dir(staged, live):
        if prev.exists():
            replace_dir(prev, live)
        return False
    return True


def _restore_one(layout: Layout, name: str) -> bool:
    """Put the retired `name` back, keeping the failed tree at `.failed`."""
    live = layout.live(name)
    prev = layout.prev(name)
    failed = layout.failed(name)
    clear_slot(failed)
    if live.exists() and not replace_dir(live, failed):
        return False
    return replace_dir(prev, live) if prev.exists() else True


def swap_bundles(
    layout: Layout, names: Sequence[str], *, required: frozenset[str], data_dir: Path
) -> tuple[bool, list[str]]:
    """Move every staged bundle in `names` into place. `(ok, swapped)`.

    Held under the bundle-swap hold for the renames only: the app bundle stops
    existing between them, and every gated shim in the data dir waits that out
    rather than launching a process whose `_MEIPASS` is about to name a directory
    called something else (`bundle_swap.py` has the failure it closes). A
    required bundle that will not swap rolls back what already moved and answers
    False; an optional one is logged and skipped, and the run continues.
    """
    swapped: list[str] = []
    with hold_bundle_swap(data_dir):
        for name in names:
            if not layout.staged(name).is_dir():
                continue
            if _swap_one(layout, name):
                swapped.append(name)
                log(f"swapped {name} (previous kept at {layout.prev(name).name})")
                continue
            if name in required:
                log(f"ABORT: could not swap {name}; restoring what already moved")
                for done in reversed(swapped):
                    _restore_one(layout, done)
                return False, []
            log(
                f"WARNING: {name} could not be replaced (a process is holding it open); "
                "the previous copy stays in place and the next update will retry"
            )
    return True, swapped


def rollback_bundles(layout: Layout, swapped: Sequence[str], *, data_dir: Path) -> bool:
    """Undo `swap_bundles` after an unhealthy relaunch. True when every one went back."""
    ok = True
    with hold_bundle_swap(data_dir):
        for name in reversed(list(swapped)):
            if not _restore_one(layout, name):
                log(f"rollback of {name} failed; check {layout.install_root} by hand")
                ok = False
    return ok


# --- relaunch --------------------------------------------------------------------


def launch_app(config: Any, layout: Layout, *, hidden: bool) -> subprocess.Popen[bytes]:
    log(f"launching {layout.app_exe}")
    command = [str(layout.app_exe)] + (["--hidden"] if hidden else [])
    # cwd must stay OUT of the install root: the shell's cwd is inherited down
    # the spawn chain, and any process anchored inside it locks it against the
    # next swap (Windows directory locking via process cwd). Likewise the env is
    # scrubbed of parent-Claude session markers: this code is designed to run
    # from an agent session, and leaked markers would make every `claude`
    # inside swe-mux think it is a nested child session (transcripts off).
    # Breakaway spawn for the same reason: run from inside a session, this
    # process sits in that session's kill-on-close Job, and a relaunched app
    # that inherits it is silently terminated when the session is removed.
    return popen_outside_job(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(config.data_dir),
        env=scrub_claude_session_markers(os.environ),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def wait_healthy(
    config: Any,
    seconds: float = APP_HEALTH_TIMEOUT_SECONDS,
    *,
    process: subprocess.Popen[bytes] | None = None,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + seconds
    reported_phase: object = None
    progress = ""
    while time.monotonic() < deadline:
        payload = health_payload(config, timeout=1.0)
        if payload is not None and payload.get("ok"):
            return payload
        # The daemon binds its listeners before it builds its runtime, so this
        # wait is no longer blind. Logged on each phase *change* rather than each
        # poll - the elapsed seconds in the line move every time, so comparing
        # rendered text would put a line in the log twice a second. This is what
        # turns a 5-15 minute wait from an indistinguishable-from-hung silence
        # into a record of progress, which is the ambiguity that once made a
        # 300s ceiling roll back a perfectly good bundle.
        if payload is not None and payload.get("status") == "starting":
            progress = startup_progress(payload)
            if payload.get("phase") != reported_phase:
                reported_phase = payload.get("phase")
                log(f"daemon {progress}")
        if process is not None and process.poll() is not None:
            log(f"launched app process exited with code {process.returncode} before health")
            return None
        time.sleep(0.5)
    if progress:
        log(f"health budget expired while the daemon was {progress}")
    return None


def relaunch_and_report(
    config: Any, layout: Layout, *, note: str, no_launch: bool, hidden: bool
) -> int:
    """Bring an app back after a failed swap/health check; always exits nonzero."""
    if no_launch or not layout.app_exe.is_file():
        log(f"{note}: not relaunched (missing exe or --no-launch); check the install by hand")
        return 1
    launched = launch_app(config, layout, hidden=hidden)
    payload = wait_healthy(config, process=launched)
    if payload is not None:
        log(
            f"{note} healthy again: supervisor={payload.get('supervisor')} "
            f"live_sessions={payload.get('live_sessions')}; the redeploy itself FAILED"
        )
    else:
        log(f"{note} did not report healthy; check <data_dir>/desktop-daemon.log")
    return 1


# --- the run -------------------------------------------------------------------------


def apply_staged(
    config: Any,
    layout: Layout,
    outcome: Outcome,
    *,
    mode: str = MODE_SWAP,
    hidden: bool = False,
    restore_visibility: bool = False,
    no_launch: bool = False,
    force: bool = False,
    on_success: Callable[[], None] | None = None,
) -> int:
    """Stop, swap, relaunch, and roll back if needed. The staging tree exists.

    The tail every caller shares. It re-checks the bundle holders because
    staging takes minutes (a holder that appeared during it would still doom the
    swap, and aborting now leaves the running app completely untouched), stops
    the processes the mode allows, swaps every bundle the mode names, and judges
    the result by the successor's health rather than by the renames succeeding.

    `on_success` runs once the new app has reported healthy and before the
    success record is written: the frozen applier uses it to bring the Windows
    installer's Add/Remove Programs entry up to the version that is now running.
    A failure inside it is logged and does not fail the run - the update did
    ship, and a stale registry line is not a reason to say it did not.
    """
    if mode not in MODES:
        raise ValueError(f"unknown apply mode {mode!r}")
    names = bundles_for_mode(layout, mode)
    required = required_bundles(mode)
    outcome.describe(mode=mode, bundles=names)
    if not layout.staged(APP_BUNDLE).joinpath(app_exe_name()).is_file():
        log("ABORT: nothing staged a swe-mux executable; the running app was never touched")
        outcome.record(
            OUTCOME_BUILD_FAILED,
            "The staged bundle carries no executable. The current app is untouched.",
            code=1,
        )
        return 1
    # Free the rollback slots BEFORE the app is stopped. The swap renames each
    # live bundle onto its slot, and a Windows rename cannot land on an existing
    # directory - so a `.prev` that a previous run only partially removed (an
    # exe image still mapped at the time) would otherwise abort the swap after
    # the daemon was already down.
    for name in names:
        if not clear_slot(layout.prev(name)):
            log(f"ABORT: {layout.prev(name)} is not removable; the running app was never touched")
            return 1
    if abort_if_bundle_held(
        layout, force=force, when="the swap would fail; the running app was never touched"
    ):
        return 2
    hidden = resolve_relaunch_hidden(hidden=hidden, restore_visibility=restore_visibility)
    if restore_visibility:
        presentation = "hidden in the tray" if hidden else "with its window visible"
        log(f"desktop presentation captured; relaunching {presentation}")
    stop_for_mode(config, mode)

    ok, swapped = swap_bundles(layout, names, required=required, data_dir=Path(config.data_dir))
    if not ok:
        outcome.record(
            OUTCOME_SWAP_FAILED,
            "A bundle could not be moved into place, so the previous build was restored. "
            "Your change did NOT ship.",
            code=1,
        )
        return relaunch_and_report(
            config, layout, note="old build (swap failed)", no_launch=no_launch, hidden=hidden
        )
    outcome.describe(swapped=swapped)
    shutil.rmtree(layout.staging_root, ignore_errors=True)

    if no_launch:
        log("done (relaunch skipped)")
        return 0
    if not layout.app_exe.is_file():
        log(f"ABORT: {layout.app_exe} does not exist after the swap")
        return 1
    launched = launch_app(config, layout, hidden=hidden)
    payload = wait_healthy(config, process=launched)
    if payload is not None:
        log(
            f"daemon healthy: supervisor={payload.get('supervisor')} "
            f"live_sessions={payload.get('live_sessions')}"
        )
        if on_success is not None:
            try:
                on_success()
            except Exception as exc:  # noqa: BLE001 - the update shipped; say so and go on
                log(f"post-install step failed ({type(exc).__name__}: {exc}); continuing")
        outcome.record(
            OUTCOME_SUCCEEDED,
            f"The new app is running with {payload.get('live_sessions', 0)} live session(s).",
            code=0,
        )
        return 0
    # -- rollback: the new build launched but never became healthy ----------
    if not layout.prev(APP_BUNDLE).is_dir():
        # A first install, or an install root somebody cleared: there is nothing
        # to go back to, and moving the only app bundle aside would leave no app.
        log(
            f"daemon did not report healthy within {APP_HEALTH_TIMEOUT_SECONDS:.0f}s and "
            "there is no previous build to roll back to; check <data_dir>/desktop-daemon.log"
        )
        outcome.record(
            OUTCOME_UNHEALTHY,
            "The app did not report healthy and there was no previous build to roll back to. "
            "Check desktop-daemon.log in the data directory.",
            code=1,
        )
        return 1
    log(
        f"new app did not report healthy within {APP_HEALTH_TIMEOUT_SECONDS:.0f}s; "
        f"rolling back to the previous build (failed bundles kept at *{FAILED_SUFFIX})"
    )
    stop_for_mode(config, mode)
    if not rollback_bundles(layout, swapped, data_dir=Path(config.data_dir)):
        outcome.record(
            OUTCOME_SWAP_FAILED,
            "The new build was unhealthy and the rollback swap also failed. "
            f"{layout.install_root} needs checking by hand.",
            code=1,
        )
        return 1
    # Written before the relaunch below, not after: the browser asks for this
    # file as soon as any daemon answers health, which that relaunch causes.
    outcome.record(
        OUTCOME_ROLLED_BACK,
        "The new build never became healthy, so the previous app was restored. "
        f"Your change did NOT ship; the failed bundle is kept at {layout.failed(APP_BUNDLE)}.",
        code=1,
    )
    return relaunch_and_report(
        config, layout, note="rolled-back previous build", no_launch=no_launch, hidden=hidden
    )


def bounce(
    config: Any,
    layout: Layout,
    outcome: Outcome,
    *,
    hidden: bool = False,
    restore_visibility: bool = False,
    no_launch: bool = False,
) -> int:
    """Stop and relaunch the installed app without swapping anything.

    The script's `--skip-build`: a session-preserving restart of the frozen app
    from outside it, for the case where the bundle is already the one wanted.
    """
    hidden = resolve_relaunch_hidden(hidden=hidden, restore_visibility=restore_visibility)
    stop_app_processes(config)
    if no_launch:
        log("done (relaunch skipped)")
        return 0
    if not layout.app_exe.is_file():
        log(f"ABORT: {layout.app_exe} does not exist")
        return 1
    launched = launch_app(config, layout, hidden=hidden)
    payload = wait_healthy(config, process=launched)
    if payload is not None:
        log(
            f"daemon healthy: supervisor={payload.get('supervisor')} "
            f"live_sessions={payload.get('live_sessions')}"
        )
        outcome.record(
            OUTCOME_SUCCEEDED,
            f"The app was restarted with {payload.get('live_sessions', 0)} live session(s).",
            code=0,
        )
        return 0
    outcome.record(
        OUTCOME_UNHEALTHY,
        "The app did not report healthy after the restart. Check desktop-daemon.log in "
        "the data directory.",
        code=1,
    )
    return 1


def apply_archive(
    config: Any,
    layout: Layout,
    outcome: Outcome,
    *,
    archive: Path,
    expected_sha256: str = "",
    mode: str = MODE_SWAP,
    hidden: bool = False,
    restore_visibility: bool = False,
    no_launch: bool = False,
    force: bool = False,
    on_success: Callable[[BundleMetadata], None] | None = None,
) -> int:
    """Install a release archive: preflight, stage, then `apply_staged`.

    The whole of the frozen applier's job, and the `--from-archive` half of the
    script's. Every refusal before `apply_staged` has touched nothing.
    """
    if mode not in MODES:
        raise ValueError(f"unknown apply mode {mode!r}")
    log(f"install root {layout.install_root}; mode {mode}")
    outcome.describe(mode=mode, install_root=str(layout.install_root))
    if preflight_supervisor(config, layout, mode=mode, force=force):
        outcome.record(
            OUTCOME_REFUSED,
            "The PTY supervisor's state would not let the app be replaced without ending "
            "sessions. Nothing was changed.",
            code=2,
        )
        return 2
    # Legacy only: task steps are spawned as ordinary shells and no longer run any
    # swe-mux binary, so nothing new can hold this lock. Terminals started by a
    # pre-removal bundle still can, until they are closed.
    action_terminals = processes_by_image({ACTION_IMAGE_NAME})
    if action_terminals and not force:
        log(
            f"ABORT: {len(action_terminals)} task terminal(s) predating the action-runner "
            f"removal still run {ACTION_IMAGE_NAME} from the app bundle and would lock the "
            "swap. Close those sessions (relaunching them after this update is enough), "
            "or re-run with --force."
        )
        return 2
    # Anything foreign anchoring the app bundle survives every process this run
    # may stop (sessions descend from the supervisor, which outlives the app),
    # so the swap is doomed no matter what. Say who is holding it BEFORE
    # spending minutes on the extraction.
    if abort_if_bundle_held(layout, force=force, when="the swap would fail"):
        return 2
    code, result, metadata = stage_from_archive(archive, expected_sha256, layout, outcome)
    if code or result is None or metadata is None:
        return code
    outcome.describe(version=metadata.version, staged=result.as_dict())

    def after_success() -> None:
        if on_success is not None:
            on_success(metadata)

    return apply_staged(
        config,
        layout,
        outcome,
        mode=mode,
        hidden=hidden,
        restore_visibility=restore_visibility,
        no_launch=no_launch,
        force=force,
        on_success=after_success,
    )


__all__ = [
    "ALL_BUNDLES",
    "APP_BUNDLE",
    "APP_HEALTH_TIMEOUT_SECONDS",
    "CLI_BUNDLE",
    "MODES",
    "MODE_REPLACE",
    "MODE_SWAP",
    "OUTCOME_BUILD_FAILED",
    "OUTCOME_FAILED",
    "OUTCOME_REFUSED",
    "OUTCOME_ROLLED_BACK",
    "OUTCOME_SUCCEEDED",
    "OUTCOME_SWAP_FAILED",
    "OUTCOME_UNHEALTHY",
    "SUPERVISOR_BUNDLE",
    "Layout",
    "Outcome",
    "abort_if_bundle_held",
    "announce_start",
    "app_exe_name",
    "apply_archive",
    "apply_staged",
    "bounce",
    "bundles_for_mode",
    "claim_lock",
    "clear_slot",
    "health",
    "health_payload",
    "live_lock_pid",
    "preflight_supervisor",
    "replace_dir",
    "required_bundles",
    "resolve_relaunch_hidden",
    "rollback_bundles",
    "stage_from_archive",
    "stop_app_processes",
    "stop_everything",
    "stop_for_mode",
    "supervisor_process",
    "swap_bundles",
    "wait_healthy",
]
