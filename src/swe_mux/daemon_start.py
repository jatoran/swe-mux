"""Start the daemon without holding a terminal open for it.

The gap this fills is narrow and specific. On Windows the desktop shell already
solves it: `swe-mux` is a `[project.gui-scripts]` launcher, so it opens no
console, and it spawns the daemon with `CREATE_NO_WINDOW`. Nothing about that
helps three other people:

- someone on Linux or macOS, where there is no tray and no desktop app by design
  (`design/features/desktop-shell.md`), whose only entry point is `muxd`;
- someone on Windows who wants the browser UI and not a native window;
- anyone iterating on the daemon from a checkout, where `uv run swemuxd` is the fast
  path and a held console is the price of it.

For all three the answer is the same shape, and it is the one herdr's
`server/autodetect.rs` uses: spawn a detached child, wait for it to serve, then
return. What is deliberately *not* copied is herdr's implicit spawn on any
invocation - `swemux ls` against a stopped daemon should keep saying so rather than
starting one behind the user's back. This is a command you type.

Two properties do the work.

**The child outlives this process.** POSIX gets `start_new_session=True`
(`setsid`), so it leads its own session and no terminal hangup reaches it.
Windows gets `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` and, through
`popen_outside_job`, `CREATE_BREAKAWAY_FROM_JOB` - the last because a `swemux start`
typed *inside* a swe-mux session would otherwise put the daemon in that session's
kill-on-close Job and have it reaped when the session is removed, which is the
same hazard `desktop.ensure_daemon` and `__main__._warn_if_inside_job` already
guard.

**Success means "answered", not "spawned".** A pid proves nothing: a daemon that
exits during startup has a pid too. This polls `/api/health` until the child
serves or dies, and reports which - so a script can branch on the exit code and a
person is not left with a cheerful message and no daemon.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .lifecycle import ledger
from .subprocess_flags import popen_outside_job

#: How long a daemon may take to answer before this gives up waiting. Matches the
#: reasoning in `desktop.DAEMON_HEALTH_TIMEOUT_SECONDS` but not its value: the
#: tray can afford 300s because it has a window to put up meanwhile, while a
#: person watching a command wants an answer. A daemon still starting at the
#: deadline is reported as such, not killed.
START_TIMEOUT_SECONDS = 90.0
POLL_SECONDS = 0.25

#: Where a detached daemon's stdio goes. It has no console to inherit, and
#: `subprocess.DEVNULL` on all three streams would discard a traceback from a
#: daemon that dies before its own logging is set up - which is exactly the
#: failure this command has to be able to explain.
START_LOG_NAME = "daemon-start.log"
START_LOG_MAX_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """What happened, in the vocabulary the CLI prints and a script branches on."""

    #: `already-running` | `started` | `starting` | `failed`
    status: str
    url: str
    pid: int | None = None
    detail: str = ""
    log_path: str = ""

    @property
    def ok(self) -> bool:
        """`starting` is not a failure: the daemon is alive and has not finished.

        Calling it one would make a slow first start - a cold page cache after an
        update, a large `mux.db` - indistinguishable from a daemon that cannot
        run at all, and the two need opposite responses.
        """
        return self.status != "failed"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "url": self.url,
            "pid": self.pid,
            "detail": self.detail,
            "log_path": self.log_path,
        }


def health(url: str, *, timeout: float = 1.0) -> dict[str, object] | None:
    """The daemon's health payload, or None when nothing answers at `url`."""
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout) as response:
            if int(response.status) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def daemon_command(config_path: Path, *, executable: str | None = None) -> list[str]:
    """The argv for a detached daemon child of this exact install.

    Mirrors `desktop.daemon_command`, including the frozen branch: a frozen
    executable dispatches its own daemon through `--daemon-child` rather than
    `-m swe_mux`, because there is no interpreter to hand a module to.
    """
    executable = executable or sys.executable
    if bool(getattr(sys, "frozen", False)):
        return [executable, "--daemon-child", "--config", str(config_path)]
    return [executable, "-m", "swe_mux", "--config", str(config_path)]


def creation_flags() -> int:
    """Windows flags that keep the child off this console and out of this group.

    `DETACHED_PROCESS` rather than `CREATE_NO_WINDOW`: the tray uses the latter
    because it has no console to detach *from*, while this command usually runs
    in one and the child must not inherit it - a daemon sharing the console would
    take Ctrl-C with the shell that started it, which is the whole thing being
    avoided. `CREATE_NEW_PROCESS_GROUP` is the same point for the group.
    """
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008)) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    )


def _rotate(path: Path) -> None:
    try:
        if path.stat().st_size > START_LOG_MAX_BYTES:
            os.replace(path, path.with_suffix(".log.1"))
    except OSError:
        pass


def start_daemon(
    config: Config,
    *,
    url: str,
    timeout_seconds: float = START_TIMEOUT_SECONDS,
) -> StartOutcome:
    """Start a detached daemon and wait for it to serve. Idempotent.

    An already-serving daemon is reported and left alone, which is what makes
    this safe to put in a login script or run twice by hand. The port is the
    interlock rather than a lock file: two daemons cannot both hold 8765, so
    checking health first is checking the thing that actually matters.
    """
    assert config.config_path is not None
    if health(url) is not None:
        return StartOutcome(status="already-running", url=url)

    log_path = config.data_dir / START_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate(log_path)
    command = daemon_command(config.config_path)
    with log_path.open("ab", buffering=0) as log:
        child = popen_outside_job(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            # The data dir, matching the tray: a long-lived process anchored in
            # an installation directory locks that tree against an in-place
            # update (`desktop.ensure_daemon` carries the measurement).
            cwd=str(config.data_dir),
            creationflags=creation_flags(),
            start_new_session=os.name != "nt",
        )
    ledger(config.data_dir, f"swemux start spawned daemon pid {child.pid}")

    started = time.monotonic()
    deadline = started + timeout_seconds
    while time.monotonic() < deadline:
        if health(url, timeout=0.5) is not None:
            elapsed = time.monotonic() - started
            ledger(
                config.data_dir,
                f"daemon pid {child.pid} answered health after {elapsed:.1f}s",
            )
            return StartOutcome(
                status="started", url=url, pid=child.pid, log_path=str(log_path)
            )
        code = child.poll()
        if code is not None:
            return StartOutcome(
                status="failed",
                url=url,
                pid=child.pid,
                detail=f"the daemon exited with code {code} during startup",
                log_path=str(log_path),
            )
        time.sleep(POLL_SECONDS)
    return StartOutcome(
        status="starting",
        url=url,
        pid=child.pid,
        detail=(
            f"the daemon has not answered {url}/api/health after "
            f"{timeout_seconds:.0f}s and is still running; it may still be "
            "opening databases or reattaching sessions"
        ),
        log_path=str(log_path),
    )


#: Set to anything non-empty to stop a daemon opening a browser. The escape
#: hatch for the case a flag cannot reach: a login task, a service wrapper, or a
#: `swemuxd` somebody else's script starts on this machine.
NO_BROWSER_ENV = "SWE_MUX_NO_BROWSER"


def should_open_browser(
    *,
    requested: bool,
    stdout_isatty: bool,
    environ: Mapping[str, str],
) -> bool:
    """Whether a starting daemon should put the UI in front of somebody.

    The question is not "is this the first run" but **"is a person watching this
    process right now"**, and a TTY is the honest test for that. Every way
    swe-mux starts without one is a way where a browser window would be wrong:
    the tray spawns its daemon with stdio to a log file and shows a window of its
    own, `swemux start`'s detached child writes to `daemon-start.log`, a
    self-restarting successor is nobody's foreground, and a login task has no
    console at all. All four are non-TTY, so one rule covers them without any of
    them having to know about this.

    Pure, and every input is passed: the branch that decides whether a window
    appears on somebody's screen is exactly the kind that must be assertable from
    a host that has no browser.
    """
    if not requested:
        return False
    if environ.get(NO_BROWSER_ENV, "").strip():
        return False
    return stdout_isatty


def open_browser(url: str) -> bool:
    """Open `url`, reporting whether the attempt was made without error.

    Never raises. A browser that will not open is a worse outcome than no
    browser only if it also takes the daemon with it, and the daemon is the part
    that matters - the URL has already been printed by the banner either way.
    """
    import webbrowser

    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001 - a convenience must not fail a daemon start
        return False


def startup_banner(url: str, *, windows: bool, opened_browser: bool) -> str:
    """What a person who just typed `swemuxd` needs to read.

    Until 2026-08-30 this moment printed aiohttp's `======== Running on ...`
    line and nothing else, which was doing the job by accident: it happens to
    carry the URL, and it happens to be the only thing a new user sees. It is the
    *only* moment swe-mux gets, because nothing runs after `pip`/`uv` install a
    wheel - `uv tool install` prints its own "Installed 3 executables" and there
    is no hook to add a word to it.

    So it says the four things that are otherwise a documentation hunt: where the
    UI is, how to stop it (which is genuinely non-obvious - Ctrl-C detaches and
    leaves supervised sessions running for the next daemon), what the desktop
    shell is called on the platform that has one, and the command that tells
    installed from working.
    """
    lines = [
        "",
        f"  swe-mux is serving  {url}",
    ]
    if opened_browser:
        lines.append("  opening it in your browser")
    lines += [
        "",
        "  swemuxd --shutdown   stop the daemon, the supervisor and every session",
        "                       (Ctrl-C only detaches; sessions keep running)",
    ]
    if windows:
        lines.append("  swe-mux              the same thing in a window, with a tray icon")
        lines.append(
            "  swemux install-shortcut --startup   add Start Menu, desktop and login shortcuts"
        )
    lines.append("  Setup opens in the UI. Resume it any time from Getting started or Help.")
    lines += [
        "  swemux doctor        read-only health report",
        "",
    ]
    return "\n".join(lines)


def render(outcome: StartOutcome) -> str:
    """The human rendering: what happened, where to point a browser, where to look."""
    if outcome.status == "already-running":
        return f"A swe-mux daemon is already serving {outcome.url}."
    lines = []
    if outcome.status == "started":
        lines.append(f"swe-mux is serving {outcome.url}  (pid {outcome.pid})")
        lines.append("")
        lines.append("It is detached: closing this terminal will not stop it.")
        lines.append("Stop everything with `swemuxd --shutdown`.")
    elif outcome.status == "starting":
        lines.append(f"Started (pid {outcome.pid}), still coming up.")
        lines.append(outcome.detail)
        lines.append(f"It will serve {outcome.url} when it is ready.")
    else:
        lines.append("The swe-mux daemon did not start.")
        lines.append(outcome.detail)
    if outcome.log_path:
        lines.append(f"log  {outcome.log_path}")
    return "\n".join(lines)
