"""Rebuild and relaunch the frozen desktop app while live sessions survive.

The agent/user-facing frozen redeploy (SESSION_PRESERVING_RELOAD.md). Live
sessions are owned by the dedicated PTY supervisor (`swe-mux-supervisor.exe`,
its own bundle outside `dist/swe-mux`), so the app tree can be stopped,
rebuilt, and relaunched around them. The build is **staged**: the new bundle
is built into `dist/.staging` while the old app keeps running, and the old
app is only stopped once the build succeeded — a failed build leaves the
running app completely untouched (critical when redeploying from a phone,
where a dead daemon means no way back in).

1. Preflight — a supervisor must be running and must not have its process
   image inside `dist/swe-mux` (a `--supervisor-child` fallback would be
   killed by the rebuild). Aborts otherwise unless ``--force``.
2. Rebuild — frontend + app bundle into `dist/.staging` (old app still up).
   The supervisor bundle is rebuilt only if its sources changed AND no
   supervisor is running; otherwise it is skipped with a warning (refreshing
   it requires ``muxd --shutdown`` first, which reaps sessions).
3. Stop — ask the desktop-managed daemon to shut down with detach intent
   (sessions stay up), then terminate remaining ``swe-mux.exe`` processes
   (the WebView shell). ``swe-mux-supervisor.exe`` is never touched.
4. Swap — the previous bundle moves to `dist/swe-mux.prev` (kept as the
   rollback artifact), the staged bundle moves into `dist/swe-mux`. Renames
   retry briefly while the just-stopped exe releases its locks.
5. Relaunch — start the new ``swe-mux.exe``; the fresh daemon reattaches to
   every live session. If it fails its health check, the previous bundle is
   rolled back in and relaunched (the failed one is kept at
   `dist/swe-mux.failed` for inspection).

Run from an ordinary terminal, from an agent session inside swe-mux itself,
or via ``POST /api/daemon/redeploy`` (the UI menu entry): the agent's own
session survives step 3 because its PTY lives in the supervisor, and the
relaunched daemon reattaches it.

    uv run python packaging/redeploy_desktop.py [--hidden|--restore-visibility] [--no-launch]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_desktop  # noqa: E402 - sibling packaging module

from swe_mux.bundle_locks import bundle_lock_holders  # noqa: E402
from swe_mux.config import load_config  # noqa: E402
from swe_mux.spawn_contract import scrub_claude_session_markers  # noqa: E402
from swe_mux.subprocess_flags import popen_outside_job  # noqa: E402
from swe_mux.supervisor import discovery_path  # noqa: E402

APP_DIST = ROOT / "dist" / "swe-mux"
APP_EXE = APP_DIST / "swe-mux.exe"
APP_IMAGE_NAMES = {"swe-mux.exe"}
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
# Staged-build locations: the new bundle lands in .staging while the old app
# keeps running; the previous bundle is retained for rollback.
STAGING_ROOT = ROOT / "dist" / ".staging"
STAGED_APP = STAGING_ROOT / "swe-mux"
PREV_APP = ROOT / "dist" / "swe-mux.prev"
FAILED_APP = ROOT / "dist" / "swe-mux.failed"
# How long a directory rename retries while the just-stopped exe releases its
# locks (the old WinError 5/32 straggler, now confined to a cheap rename).
SWAP_RETRY_SECONDS = 20.0
# First launch of a freshly written PyInstaller tree can spend several minutes
# in Windows image scanning before the tray reaches daemon startup. Rolling back
# while that process is still alive converts a slow-but-valid deploy into an
# outage, so give cold starts a realistic budget and fail early only when the
# launched shell actually exits.
APP_HEALTH_TIMEOUT_SECONDS = 300.0


def log(message: str) -> None:
    print(f"[redeploy] {message}", flush=True)


def base_url(config) -> str:  # noqa: ANN001 - Config
    return f"http://127.0.0.1:{config.port}"


def health(config, timeout: float = 1.5) -> dict | None:  # noqa: ANN001
    try:
        with urllib.request.urlopen(f"{base_url(config)}/api/health", timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) and payload.get("ok") else None


def supervisor_process(config):  # noqa: ANN001
    """(pid, exe_path) of the live supervisor for this config, or None."""
    import psutil

    try:
        info = json.loads(discovery_path(config.data_dir).read_text(encoding="utf-8"))
        pid = int(info["pid"])
    except (OSError, ValueError, KeyError):
        return None
    try:
        process = psutil.Process(pid)
        return pid, Path(process.exe())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def request_detach_shutdown(config) -> bool:  # noqa: ANN001
    token_path = config.data_dir / "desktop-control.token"
    try:
        token = token_path.read_text(encoding="ascii").strip()
    except OSError:
        return False
    if not token:
        return False
    request = urllib.request.Request(
        f"{base_url(config)}/api/desktop/shutdown",
        data=json.dumps({"mode": "restart"}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status) == 202
    except (OSError, urllib.error.URLError):
        return False


def processes_by_image(names: set[str]) -> list[tuple[int, str]]:
    import psutil

    found: list[tuple[int, str]] = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = (process.info["name"] or "").casefold()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name in {value.casefold() for value in names}:
            found.append((int(process.info["pid"]), name))
    return found


def is_session_helper(process) -> bool:  # noqa: ANN001 - psutil.Process
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

    @enum_callback
    def inspect_window(handle, _parameter) -> bool:  # noqa: ANN001 - Win32 callback
        nonlocal found
        if not user32.IsWindowVisible(handle):
            return True
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if process_id.value in shell_pids:
            found = True
            return False
        return True

    user32.EnumWindows(inspect_window, 0)
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
    subprocess.run(["taskkill", "/F", "/IM", "swe-mux.exe"], capture_output=True, check=False)
    time.sleep(1.0)


def stop_app_processes(config) -> None:  # noqa: ANN001
    if health(config) is not None:
        log("asking the daemon to shut down with detach intent (sessions stay up)")
        if request_detach_shutdown(config):
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild and relaunch the frozen desktop app while live sessions survive"
    )
    parser.add_argument("--config", type=Path, help="config path (default: ~/.mux/config.toml)")
    presentation = parser.add_mutually_exclusive_group()
    presentation.add_argument("--hidden", action="store_true", help="relaunch minimized to tray")
    presentation.add_argument(
        "--restore-visibility",
        action="store_true",
        help="restore whether the desktop window is visible when the app stops",
    )
    parser.add_argument("--no-launch", action="store_true", help="rebuild but do not relaunch")
    parser.add_argument("--skip-build", action="store_true", help="bounce processes only")
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="backend-only redeploy: bundle the already-built src/swe_mux/static as-is",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="proceed even when live sessions would be killed (no usable supervisor)",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)

    # -- preflight ---------------------------------------------------------
    supervisor = supervisor_process(config)
    supervisor = supervisor_process(config)
    if supervisor is None:
        message = (
            "no PTY supervisor is running for this config; a redeploy will kill any "
            "in-process sessions"
        )
        if not args.force:
            log(f"ABORT: {message}. Re-run with --force to proceed anyway.")
            return 2
        log(f"WARNING: {message} (continuing due to --force)")
    else:
        pid, exe = supervisor
        log(f"supervisor pid {pid} running from {exe}")
        try:
            inside_app_dist = exe.resolve().is_relative_to(APP_DIST.resolve())
        except OSError:
            inside_app_dist = False
        if inside_app_dist and not args.force:
            log(
                "ABORT: the supervisor is running from dist/swe-mux (the "
                "--supervisor-child fallback), so rebuilding would kill it and every "
                "session. Run `muxd --shutdown`, rebuild once (this creates the "
                "dedicated swe-mux-supervisor bundle), and relaunch; future redeploys "
                "will then preserve sessions."
            )
            return 2
    # Legacy only: task steps are spawned as ordinary shells and no longer run any
    # swe-mux binary, so nothing new can hold this lock. Terminals started by a
    # pre-removal bundle still can, until they are closed.
    action_terminals = processes_by_image({ACTION_IMAGE_NAME})
    if action_terminals and not args.force:
        log(
            f"ABORT: {len(action_terminals)} task terminal(s) predating the action-runner "
            f"removal still run {ACTION_IMAGE_NAME} from dist/swe-mux and would lock the "
            "swap. Close those sessions (relaunching them after this redeploy is enough), "
            "or re-run with --force."
        )
        return 2
    # Anything foreign anchoring dist/swe-mux (a dev server behind a Preview tab,
    # a terminal cd'd into the bundle) survives every process this script may
    # stop — sessions descend from the supervisor, which outlives the app — so
    # the swap is doomed no matter what. Say who is holding it BEFORE spending
    # minutes on a build (measured live 2026-08-02: two redeploys built, stopped
    # the app, and then died at this exact rename).
    if not args.skip_build and abort_if_bundle_held(args, when="the swap would fail"):
        return 2

    # -- rebuild (staged; the old app keeps running and serving) ------------
    if not args.skip_build:
        skip_supervisor = False
        if not build_desktop.supervisor_bundle_current() and supervisor is not None:
            log(
                "WARNING: supervisor sources changed but a supervisor is running with "
                "live sessions; keeping the OLD supervisor bundle. To refresh it: "
                "`muxd --shutdown` (reaps sessions), then "
                "`uv run python packaging/build_desktop.py --supervisor-only`."
            )
            skip_supervisor = True
        built = "app bundle only" if args.skip_frontend else "frontend + app bundle"
        log(f"rebuilding {built} into dist/.staging (old app stays up)")
        shutil.rmtree(STAGING_ROOT, ignore_errors=True)
        build_arguments = ["--app-distpath", str(STAGING_ROOT)]
        if skip_supervisor:
            build_arguments.append("--skip-supervisor")
        if args.skip_frontend:
            build_arguments.append("--skip-frontend")
        try:
            build_desktop.main(build_arguments)
        except (SystemExit, subprocess.CalledProcessError) as exc:
            log(f"ABORT: build failed; the running app was never touched ({exc})")
            return 1
        if not (STAGED_APP / "swe-mux.exe").is_file():
            log("ABORT: staged build produced no swe-mux.exe; the running app was never touched")
            return 1
        # Free the rollback slot BEFORE the app is stopped. The swap renames
        # dist/swe-mux onto it, and a Windows rename cannot land on an existing
        # directory — so a `.prev` that a previous run only partially removed
        # (an exe image still mapped at the time) would otherwise abort the
        # swap after the daemon was already down.
        if not clear_slot(PREV_APP):
            log("ABORT: dist/swe-mux.prev is not removable; the running app was never touched")
            return 1

    # -- stop (only after a successful build) -------------------------------
    # Re-checked here because the build takes minutes: a holder that appeared
    # during it would still doom the swap, and aborting now leaves the running
    # app completely untouched (the staged build is kept for the retry).
    if not args.skip_build and abort_if_bundle_held(
        args, when="the swap would fail; the running app was never touched"
    ):
        return 2
    args.hidden = resolve_relaunch_hidden(
        hidden=args.hidden, restore_visibility=args.restore_visibility
    )
    if args.restore_visibility:
        presentation = "hidden in the tray" if args.hidden else "with its window visible"
        log(f"desktop presentation captured; relaunching {presentation}")
    stop_app_processes(config)

    # -- swap ---------------------------------------------------------------
    if not args.skip_build:
        clear_slot(PREV_APP)
        if APP_DIST.exists() and not replace_dir(APP_DIST, PREV_APP):
            # A lock straggler outlived the targeted stop. Only now is the blunt
            # image-wide kill worth its cost: the alternative is a redeploy that
            # fails outright. Sparing helpers first means the common path never
            # pays it, and this path retries the rename once afterwards.
            force_stop_app_images()
            if not replace_dir(APP_DIST, PREV_APP):
                log("ABORT: could not retire the old bundle; relaunching it unchanged")
                return relaunch_and_report(config, args, note="old build (swap failed)")
        if not replace_dir(STAGED_APP, APP_DIST):
            log("ABORT: could not move the staged bundle into dist; restoring the old app")
            if PREV_APP.exists():
                replace_dir(PREV_APP, APP_DIST)
            return relaunch_and_report(config, args, note="old build (swap failed)")
        shutil.rmtree(STAGING_ROOT, ignore_errors=True)

    # -- relaunch ------------------------------------------------------------
    if args.no_launch:
        log("done (relaunch skipped)")
        return 0
    if not APP_EXE.is_file():
        log(f"ABORT: {APP_EXE} does not exist after build")
        return 1
    launched = launch_app(config, hidden=args.hidden)
    payload = wait_healthy(config, process=launched)
    if payload is not None:
        log(
            f"daemon healthy: supervisor={payload.get('supervisor')} "
            f"live_sessions={payload.get('live_sessions')}"
        )
        return 0
    # -- rollback: the new build launched but never became healthy ----------
    if not args.skip_build and PREV_APP.is_dir():
        log(
            f"new app did not report healthy within {APP_HEALTH_TIMEOUT_SECONDS:.0f}s; "
            "rolling back to the previous "
            f"build (failed bundle kept at {FAILED_APP})"
        )
        stop_app_processes(config)
        clear_slot(FAILED_APP)
        if not replace_dir(APP_DIST, FAILED_APP) or not replace_dir(PREV_APP, APP_DIST):
            log("ABORT: rollback swap failed; check dist/ by hand")
            return 1
        return relaunch_and_report(config, args, note="rolled-back previous build")
    log(
        f"daemon did not report healthy within {APP_HEALTH_TIMEOUT_SECONDS:.0f}s; "
        "check <data_dir>/desktop-daemon.log"
    )
    return 1


def abort_if_bundle_held(args, *, when: str) -> bool:  # noqa: ANN001 - argparse.Namespace
    """Report foreign processes anchoring dist/swe-mux. True means abort.

    Only processes the stop machinery cannot release count (the app's own
    image and its descendants are excluded by the scan), so a report here is a
    swap that WILL fail. ``--force`` downgrades it to a warning for the case
    where the holder is expected to exit during the build.
    """
    holders = bundle_lock_holders(APP_DIST)
    if not holders:
        return False
    verdict = "WARNING" if args.force else "ABORT"
    log(f"{verdict}: dist/swe-mux is held open by processes a redeploy cannot stop ({when}):")
    for holder in holders:
        log(f"  pid {holder['pid']} {holder['name']} ({holder['via']}: {holder['path']})")
    if args.force:
        log("continuing due to --force; the swap may still fail on these locks")
        return False
    log(
        "These are usually a dev server/preview process or a terminal whose working "
        "directory is inside dist/swe-mux. Stop those processes (or close their "
        "tabs/sessions) and re-run, or re-run with --force to attempt anyway."
    )
    return True


def clear_slot(path: Path, *, retry_seconds: float = SWAP_RETRY_SECONDS) -> bool:
    """Free `path` so a later rename can land on it. False if it survives.

    `shutil.rmtree(..., ignore_errors=True)` can leave a *partially* deleted
    tree behind: Windows refuses to unlink an exe/DLL whose image is still
    mapped by a process killed moments earlier, and the errors are swallowed.
    The surviving directory then blocks every future rename onto it (WinError
    183), which is how a stale `dist/swe-mux.prev` aborts a redeploy long after
    the run that created it. So retry the removal, and if it still will not go,
    move it aside under a unique name instead of leaving the slot poisoned.
    Stale leftovers are swept opportunistically once their locks are gone.
    """
    for stale in path.parent.glob(f"{path.name}.stale-*"):
        shutil.rmtree(stale, ignore_errors=True)
    if not path.exists():
        return True
    deadline = time.monotonic() + retry_seconds
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
    """Rename source → target, retrying while a just-stopped exe releases locks."""
    deadline = time.monotonic() + retry_seconds
    while True:
        try:
            source.rename(target)
            return True
        except OSError as exc:
            if time.monotonic() >= deadline:
                log(f"could not move {source} -> {target}: {exc}")
                return False
            time.sleep(0.5)


def launch_app(config, *, hidden: bool) -> subprocess.Popen[bytes]:  # noqa: ANN001 - Config
    log(f"launching {APP_EXE}")
    command = [str(APP_EXE)] + (["--hidden"] if hidden else [])
    # cwd must stay OUT of dist/: the shell's cwd is inherited down the spawn
    # chain, and any process anchored inside dist/ locks it against the next
    # rebuild (Windows directory locking via process cwd). Likewise the env is
    # scrubbed of parent-Claude session markers: this script is designed to run
    # from an agent session, and leaked markers would make every `claude`
    # inside swe-mux think it is a nested child session (transcripts off).
    # Breakaway spawn for the same reason: run from inside a session, this
    # script sits in that session's kill-on-close Job, and a relaunched app
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
    config,
    seconds: float = APP_HEALTH_TIMEOUT_SECONDS,
    *,
    process: subprocess.Popen[bytes] | None = None,
):  # noqa: ANN001 - Config
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        payload = health(config, timeout=1.0)
        if payload is not None:
            return payload
        if process is not None and process.poll() is not None:
            log(f"launched app process exited with code {process.returncode} before health")
            return None
        time.sleep(0.5)
    return None


def relaunch_and_report(config, args, *, note: str) -> int:  # noqa: ANN001
    """Bring an app back after a failed swap/health check; always exits nonzero."""
    if args.no_launch or not APP_EXE.is_file():
        log(f"{note}: not relaunched (missing exe or --no-launch); check dist/ by hand")
        return 1
    launched = launch_app(config, hidden=args.hidden)
    payload = wait_healthy(config, process=launched)
    if payload is not None:
        log(
            f"{note} healthy again: supervisor={payload.get('supervisor')} "
            f"live_sessions={payload.get('live_sessions')}; the redeploy itself FAILED"
        )
    else:
        log(f"{note} did not report healthy; check <data_dir>/desktop-daemon.log")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
