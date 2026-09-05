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
   it requires ``swemuxd --shutdown`` first, which reaps sessions).
   ``--from-archive`` replaces this step and nothing else: a downloaded release
   archive is verified and staged into the same staging tree, and every step
   below runs identically. That is the developer-machine form of the frozen-app
   updater's install; on an installed copy with no checkout the same steps run
   from the frozen console client (`swemux update-apply`), and both are one
   implementation in `swe_mux/bundle_apply.py`. Staging an archive is a
   **delta** where the archive carries the per-file manifest to support one
   (`swe_mux/bundle_stage.py`): files already installed byte-for-byte are
   hard-linked rather than rewritten, which is what keeps their antivirus scan
   verdict and most of the minutes an update used to cost.
3. Stop — ask the desktop-managed daemon to shut down with detach intent
   (sessions stay up), then terminate remaining ``swe-mux.exe`` processes
   (the WebView shell). ``swe-mux-supervisor.exe`` is never touched - unless
   ``--replace-supervisor`` was given, which stops with quit intent (every
   session ends) and swaps the supervisor bundle the archive carried as well.
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

Whoever starts a run claims ``<data_dir>/redeploy.lock`` before any work: the
endpoint does it and passes ``--lock-held``, and a terminal-launched run does it
here. That makes single-flight and client visibility identical either way - a
CLI redeploy used to take no lock at all, so two of them could race the same
staging tree and the swap, and ``GET /api/daemon/redeploy`` reported nothing in
flight while the UI was minutes from losing its daemon. A terminal-launched run
also asks the daemon to broadcast the start, best-effort, so every client can
show progress rather than discovering the redeploy as failed requests.

Every run records ``<data_dir>/redeploy-result.json``, which the successor
daemon serves back to the UI. A rollback is what that is for: the app comes back
looking entirely normal, so without it nobody learns their change never shipped.

    uv run python packaging/redeploy_desktop.py [--hidden|--restore-visibility] [--no-launch]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_desktop  # noqa: E402 - sibling packaging module

from swe_mux import bundle_apply  # noqa: E402
from swe_mux.bundle_apply import (  # noqa: E402 - re-exported for the script's callers
    APP_HEALTH_TIMEOUT_SECONDS,
    MODE_REPLACE,
    MODE_SWAP,
    OUTCOME_BUILD_FAILED,
    OUTCOME_FAILED,
    OUTCOME_REFUSED,
    OUTCOME_ROLLED_BACK,
    OUTCOME_SUCCEEDED,
    OUTCOME_SWAP_FAILED,
    OUTCOME_UNHEALTHY,
    Layout,
    Outcome,
    announce_start,
    claim_lock,
    live_lock_pid,
    log,
)
from swe_mux.config import load_config  # noqa: E402

#: The checkout's own layout: `dist/` beside `packaging/`. The module constants
#: are kept for the readers that grep them (`tests/test_bundle_contents.py`
#: asserts the `--skip-cli` line below verbatim).
LAYOUT = Layout.for_checkout(ROOT)
APP_DIST = LAYOUT.app
APP_EXE = LAYOUT.app_exe
STAGING_ROOT = LAYOUT.staging_root
STAGED_APP = LAYOUT.staged(bundle_apply.APP_BUNDLE)
PREV_APP = LAYOUT.prev(bundle_apply.APP_BUNDLE)
FAILED_APP = LAYOUT.failed(bundle_apply.APP_BUNDLE)

__all__ = [
    "APP_HEALTH_TIMEOUT_SECONDS",
    "MODE_REPLACE",
    "MODE_SWAP",
    "OUTCOME_BUILD_FAILED",
    "OUTCOME_FAILED",
    "OUTCOME_REFUSED",
    "OUTCOME_ROLLED_BACK",
    "OUTCOME_SUCCEEDED",
    "OUTCOME_SWAP_FAILED",
    "OUTCOME_UNHEALTHY",
    "Outcome",
    "announce_start",
    "claim_lock",
    "live_lock_pid",
    "log",
    "main",
    "parse_args",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
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
        "--from-archive",
        type=Path,
        default=None,
        help=(
            "install a downloaded release archive instead of building: extract it "
            "into dist/.staging and run the ordinary staged swap (the frozen-app "
            "updater's path)"
        ),
    )
    parser.add_argument(
        "--archive-sha256",
        default="",
        help=(
            "expected SHA-256 of --from-archive; verified again here so this script "
            "carries its own guarantee rather than inheriting its caller's"
        ),
    )
    parser.add_argument(
        "--replace-supervisor",
        action="store_true",
        help=(
            "with --from-archive: stop with quit intent (EVERY live session ends) and "
            "replace the PTY supervisor bundle the archive carries as well as the app"
        ),
    )
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
    parser.add_argument(
        "--lock-held",
        action="store_true",
        help=(
            "redeploy.lock is already claimed for this process and clients have already "
            "been told (set by the daemon's POST /api/daemon/redeploy)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    started_at = time.time()
    # Single-flight and client notification happen before any work, and cover the
    # terminal-launched run too: the daemon's endpoint does both for a UI redeploy
    # and passes --lock-held, but a redeploy started straight from a shell used to
    # take no lock and tell nobody.
    if not claim_lock(config, already_held=args.lock_held):
        # Deliberately no outcome record: the redeploy that owns the lock is still
        # running, and overwriting its result would misreport it as finished.
        return 2
    if not args.lock_held:
        announce_start(config)
    outcome = Outcome(config, started_at)
    try:
        code = _run(args, config, outcome)
    except BaseException:
        outcome.record(
            OUTCOME_FAILED,
            "The redeploy script exited unexpectedly. See redeploy.log.",
            code=1,
        )
        raise
    return outcome.finish(code)


def _run(args: argparse.Namespace, config, outcome: Outcome) -> int:  # noqa: ANN001 - Config
    mode = MODE_REPLACE if args.replace_supervisor else MODE_SWAP
    if args.from_archive is not None and not args.skip_build:
        # The whole install, preflights included, is the shared implementation:
        # the same code the frozen console client runs on a machine with no
        # checkout, so the two cannot drift apart.
        return bundle_apply.apply_archive(
            config,
            LAYOUT,
            outcome,
            archive=args.from_archive,
            expected_sha256=args.archive_sha256,
            mode=mode,
            hidden=args.hidden,
            restore_visibility=args.restore_visibility,
            no_launch=args.no_launch,
            force=args.force,
        )
    # -- preflight ---------------------------------------------------------
    # Cheapest check first, and the only one that costs nothing: the build
    # environment must carry every distributed extra. `voice-local` is optional
    # to install and mandatory to build from, because `num2words` is LGPL and its
    # relink condition is met by the spec collecting it as readable source -
    # which silently collects nothing when the package is absent. Without this,
    # the failure surfaces minutes later inside `verify_bundle_licenses`, and a
    # redeploy started from the UI reports it only as a generic build failure.
    if not args.skip_build:
        missing = build_desktop.missing_extra_distributions()
        if missing:
            extras = " ".join(
                [f"--extra {extra}" for extra in build_desktop.REQUIRED_BUILD_EXTRAS]
                + [f"--group {group}" for group in build_desktop.REQUIRED_BUILD_GROUPS]
            )
            log(
                "ABORT: the build environment is missing "
                + ", ".join(missing)
                + f". Run `uv sync {extras}` and redeploy again; the bundle cannot "
                "satisfy its LGPL relink obligation without them."
            )
            outcome.record(
                OUTCOME_REFUSED,
                "Dependencies for the voice extra are missing, so the bundle could "
                "not be built license-compliant. Run `uv sync "
                f"{extras}` and redeploy. Nothing was changed.",
                code=2,
            )
            return 2
    if bundle_apply.preflight_supervisor(config, LAYOUT, mode=mode, force=args.force):
        return 2
    if args.skip_build:
        return bundle_apply.bounce(
            config,
            LAYOUT,
            outcome,
            hidden=args.hidden,
            restore_visibility=args.restore_visibility,
            no_launch=args.no_launch,
        )
    supervisor = bundle_apply.supervisor_process(config)
    # Legacy only: task steps are spawned as ordinary shells and no longer run any
    # swe-mux binary, so nothing new can hold this lock. Terminals started by a
    # pre-removal bundle still can, until they are closed.
    action_terminals = bundle_apply.processes_by_image({bundle_apply.ACTION_IMAGE_NAME})
    if action_terminals and not args.force:
        log(
            f"ABORT: {len(action_terminals)} task terminal(s) predating the action-runner "
            f"removal still run {bundle_apply.ACTION_IMAGE_NAME} from dist/swe-mux and would "
            "lock the swap. Close those sessions (relaunching them after this redeploy is "
            "enough), or re-run with --force."
        )
        return 2
    # Anything foreign anchoring dist/swe-mux (a dev server behind a Preview tab,
    # a terminal cd'd into the bundle) survives every process this script may
    # stop — sessions descend from the supervisor, which outlives the app — so
    # the swap is doomed no matter what. Say who is holding it BEFORE spending
    # minutes on a build (measured live 2026-08-02: two redeploys built, stopped
    # the app, and then died at this exact rename).
    if bundle_apply.abort_if_bundle_held(LAYOUT, force=args.force, when="the swap would fail"):
        return 2

    # -- build (staged; the old app keeps running and serving) --------------
    skip_supervisor = False
    if not build_desktop.supervisor_bundle_current() and supervisor is not None:
        log(
            "WARNING: supervisor sources changed but a supervisor is running with "
            "live sessions; keeping the OLD supervisor bundle. To refresh it: "
            "`swemuxd --shutdown` (reaps sessions), then "
            "`uv run python packaging/build_desktop.py --supervisor-only`."
        )
        skip_supervisor = True
    built = "app bundle only" if args.skip_frontend else "frontend + app bundle"
    log(f"rebuilding {built} into dist/.staging (old app stays up)")
    shutil.rmtree(STAGING_ROOT, ignore_errors=True)
    # `--skip-cli` unconditionally. `dist/swe-mux-cli` is an installer input,
    # not part of the running app: the daemon never launches it, the swap
    # never renames it, and nothing here would be stale without it. Building
    # it would put a fresh write into `dist/` during the one operation whose
    # whole design is to touch nothing there until the swap - and if a
    # `swemux` from that bundle happened to be sitting in a terminal, the
    # build would fail on a locked exe minutes in. Refresh it deliberately
    # with `packaging/build_desktop.py --cli-only`.
    build_arguments = ["--app-distpath", str(STAGING_ROOT), "--skip-cli"]
    if skip_supervisor:
        build_arguments.append("--skip-supervisor")
    if args.skip_frontend:
        build_arguments.append("--skip-frontend")
    try:
        build_desktop.main(build_arguments)
    except (SystemExit, subprocess.CalledProcessError) as exc:
        log(f"ABORT: build failed; the running app was never touched ({exc})")
        outcome.record(
            OUTCOME_BUILD_FAILED,
            "The build failed. The current app is untouched.",
            code=1,
        )
        return 1
    # Both staging paths answer to these, and the wording stays "staged" rather
    # than "built": an archive that extracted without an exe is exactly as unusable
    # as a build that produced none, and it must fail here, before anything stops.
    return bundle_apply.apply_staged(
        config,
        LAYOUT,
        outcome,
        mode=mode,
        hidden=args.hidden,
        restore_visibility=args.restore_visibility,
        no_launch=args.no_launch,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
