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

    uv run python packaging/redeploy_desktop.py [--hidden] [--no-launch]
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

from swe_mux.config import load_config  # noqa: E402
from swe_mux.spawn_contract import scrub_claude_session_markers  # noqa: E402
from swe_mux.supervisor import discovery_path  # noqa: E402

APP_DIST = ROOT / "dist" / "swe-mux"
APP_EXE = APP_DIST / "swe-mux.exe"
APP_IMAGE_NAMES = {"swe-mux.exe"}
ACTION_IMAGE_NAME = "swe-mux-action.exe"
SUPERVISOR_IMAGE_NAME = "swe-mux-supervisor.exe"
# Staged-build locations: the new bundle lands in .staging while the old app
# keeps running; the previous bundle is retained for rollback.
STAGING_ROOT = ROOT / "dist" / ".staging"
STAGED_APP = STAGING_ROOT / "swe-mux"
PREV_APP = ROOT / "dist" / "swe-mux.prev"
FAILED_APP = ROOT / "dist" / "swe-mux.failed"
# How long a directory rename retries while the just-stopped exe releases its
# locks (the old WinError 5/32 straggler, now confined to a cheap rename).
SWAP_RETRY_SECONDS = 20.0


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


def stop_app_processes(config) -> None:  # noqa: ANN001
    if health(config) is not None:
        log("asking the daemon to shut down with detach intent (sessions stay up)")
        if request_detach_shutdown(config):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and health(config, timeout=0.5) is not None:
                time.sleep(0.25)
        else:
            log("daemon did not accept desktop shutdown (not desktop-managed?); continuing")
    remaining = processes_by_image(APP_IMAGE_NAMES)
    if remaining:
        log(f"terminating {len(remaining)} swe-mux.exe process(es) (shell/daemon)")
        subprocess.run(
            ["taskkill", "/F", "/IM", "swe-mux.exe"],
            capture_output=True,
            check=False,
        )
        time.sleep(1.0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild and relaunch the frozen desktop app while live sessions survive"
    )
    parser.add_argument("--config", type=Path, help="config path (default: ~/.mux/config.toml)")
    parser.add_argument("--hidden", action="store_true", help="relaunch minimized to tray")
    parser.add_argument("--no-launch", action="store_true", help="rebuild but do not relaunch")
    parser.add_argument("--skip-build", action="store_true", help="bounce processes only")
    parser.add_argument(
        "--force",
        action="store_true",
        help="proceed even when live sessions would be killed (no usable supervisor)",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)

    # -- preflight ---------------------------------------------------------
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
    action_terminals = processes_by_image({ACTION_IMAGE_NAME})
    if action_terminals and not args.force:
        log(
            f"ABORT: {len(action_terminals)} live task terminal(s) run "
            f"{ACTION_IMAGE_NAME} from dist/swe-mux and would lock or die in a "
            "rebuild. Let them finish or kill those sessions, or re-run with --force."
        )
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
        log("rebuilding frontend + app bundle into dist/.staging (old app stays up)")
        shutil.rmtree(STAGING_ROOT, ignore_errors=True)
        build_arguments = ["--app-distpath", str(STAGING_ROOT)]
        if skip_supervisor:
            build_arguments.append("--skip-supervisor")
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
    stop_app_processes(config)

    # -- swap ---------------------------------------------------------------
    if not args.skip_build:
        clear_slot(PREV_APP)
        if APP_DIST.exists() and not replace_dir(APP_DIST, PREV_APP):
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
    launch_app(config, hidden=args.hidden)
    payload = wait_healthy(config)
    if payload is not None:
        log(
            f"daemon healthy: supervisor={payload.get('supervisor')} "
            f"live_sessions={payload.get('live_sessions')}"
        )
        return 0
    # -- rollback: the new build launched but never became healthy ----------
    if not args.skip_build and PREV_APP.is_dir():
        log(
            "new app did not report healthy within 60s; rolling back to the previous "
            f"build (failed bundle kept at {FAILED_APP})"
        )
        stop_app_processes(config)
        clear_slot(FAILED_APP)
        if not replace_dir(APP_DIST, FAILED_APP) or not replace_dir(PREV_APP, APP_DIST):
            log("ABORT: rollback swap failed; check dist/ by hand")
            return 1
        return relaunch_and_report(config, args, note="rolled-back previous build")
    log("daemon did not report healthy within 60s; check <data_dir>/desktop-daemon.log")
    return 1


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


def launch_app(config, *, hidden: bool) -> None:  # noqa: ANN001 - Config
    log(f"launching {APP_EXE}")
    command = [str(APP_EXE)] + (["--hidden"] if hidden else [])
    # cwd must stay OUT of dist/: the shell's cwd is inherited down the spawn
    # chain, and any process anchored inside dist/ locks it against the next
    # rebuild (Windows directory locking via process cwd). Likewise the env is
    # scrubbed of parent-Claude session markers: this script is designed to run
    # from an agent session, and leaked markers would make every `claude`
    # inside swe-mux think it is a nested child session (transcripts off).
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(config.data_dir),
        env=scrub_claude_session_markers(os.environ),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def wait_healthy(config, seconds: float = 60.0):  # noqa: ANN001 - Config
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        payload = health(config, timeout=1.0)
        if payload is not None:
            return payload
        time.sleep(0.5)
    return None


def relaunch_and_report(config, args, *, note: str) -> int:  # noqa: ANN001
    """Bring an app back after a failed swap/health check; always exits nonzero."""
    if args.no_launch or not APP_EXE.is_file():
        log(f"{note}: not relaunched (missing exe or --no-launch); check dist/ by hand")
        return 1
    launch_app(config, hidden=args.hidden)
    payload = wait_healthy(config)
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
