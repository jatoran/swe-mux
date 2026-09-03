"""The gate that keeps short-lived helpers out of the bundle-swap window.

`packaging/redeploy_desktop.py` replaces the running app with two directory
renames: `dist/swe-mux` becomes `dist/swe-mux.prev`, then the staged tree becomes
`dist/swe-mux`. Both are cheap, and the app's own processes are stopped first, so
for years the window between them looked like it belonged to nobody.

It does not. Every instrumented Claude session runs
`dist/swe-mux/swe-mux.exe -m swe_mux.hook_client <event>` on **every** hook, and
those helpers are deliberately spared by the stop (`redeploy_desktop.is_session_helper`)
because killing one reaches into a live session. So agent CLIs keep launching
processes out of that directory straight through the rename, and two things can
happen to one that is unlucky:

- It is launched in the gap between the renames, when `dist/swe-mux` does not
  exist at all, and never starts.
- It is launched just *before* the first rename and is still inside the
  PyInstaller bootloader when the rename lands. `sys._MEIPASS` is an absolute
  path string, so every later extension import resolves against a directory that
  is now called something else. Observed 2026-09-02 as a modal Windows dialog -
  "Failed to execute script 'pyi_rth_multiprocessing' ... No module named
  '_socket'" - because that runtime hook is the first thing in the startup
  sequence to import an extension module (`multiprocessing` → `socket` →
  `_socket.pyd`). The bundle was intact; the path to it was not.

Neither is cosmetic. `hook_client` spools its durable events when the daemon is
down, which is exactly what makes a redeploy safe for a live fleet - the events
are replayed on the other side. A helper that dies in the bootloader never
reaches that code, so the event is *lost* rather than deferred, and a session
whose `Stop` or `PermissionRequest` went missing sits displayed as "working"
until the 900 s no-evidence alarm.

The fix cannot live in the bundle. By the time any of our code runs - including a
PyInstaller runtime hook - the process is already committed to a `_MEIPASS` that
the rename is about to invalidate. It has to sit *outside*, in something the swap
never touches, and decide before the executable is launched at all. That is the
shim directory the data dir already carries for agent launches (`~/.mux/bin`), so
the gate is a few lines at the top of those shims plus a hold file:

1. `hold_bundle_swap` writes `<data_dir>/bundle-swap.hold` and settles briefly,
   which lets bootloaders that started before the hold finish starting.
2. Shims written by `launchers` wait while that file exists, then exec.
3. The hold is removed in a `finally`, and a hold left behind by a killed
   redeploy is bounded twice over: the shims give up waiting after
   `WAIT_SECONDS`, and `clear_stale_hold` deletes one older than `STALE_SECONDS`.

Source installs (`uv`/`pip`) are deliberately untouched by the launcher half:
their hook command names a real interpreter in a virtualenv that no swap renames,
so there is no window to gate and no reason to pay a `cmd.exe` per hook. The hold
file costs nothing there because nothing ever writes one.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Iterator, Mapping
from pathlib import Path

from .host_platform import IS_WINDOWS

#: The hold file, in the data dir root rather than in `bin/` so that a shim can
#: name it relative to its own directory and a reader of the data dir can see it.
HOLD_FILENAME = "bundle-swap.hold"

#: How long the swap waits after raising the hold before it renames anything.
#: It only has to cover a PyInstaller bootloader's startup, because a helper that
#: got further than that has already loaded every extension module it will need
#: and survives the rename; measured cold-start for this bundle is well under
#: 250 ms once the image scan is warm.
SETTLE_SECONDS = 0.5

#: Ceiling on how long a shim waits for a swap to finish. Generous next to a
#: swap (two renames plus the settle) and short next to the CLI's own 600 s hook
#: timeout, so a wedged hold degrades to today's behaviour rather than to a
#: session parked on a shim.
WAIT_SECONDS = 30

#: A hold older than this was left by a redeploy that died between raising it and
#: its `finally`. Comfortably longer than any real swap.
STALE_SECONDS = 300.0

#: Polling interval the generated shims use. One second on Windows because
#: `cmd.exe` has no sub-second sleep that survives redirected stdin (`timeout`
#: refuses it, which is precisely the shape a hook runs in), and `ping -n 2` is
#: the portable one-second idiom. The POSIX shims can and do poll faster.
_CMD_POLL_SECONDS = 1
_SH_POLL_SECONDS = 0.1

#: Name of the gated launcher inside the data dir's `bin/`. It is a drop-in for
#: `sys.executable` rather than a hook-specific entrypoint, so every caller that
#: already builds `<python> -m swe_mux.<helper> ...` keeps its argv and only the
#: program at the front changes.
EXEC_LAUNCHER_STEM = "swemux-exec"


def hold_path(data_dir: Path) -> Path:
    return data_dir / HOLD_FILENAME


def exec_launcher_path(data_dir: Path, *, windows: bool | None = None) -> Path:
    if windows is None:
        windows = IS_WINDOWS
    suffix = ".cmd" if windows else ""
    return data_dir / "bin" / f"{EXEC_LAUNCHER_STEM}{suffix}"


def clear_stale_hold(data_dir: Path, *, max_age: float = STALE_SECONDS) -> bool:
    """Delete a hold file left behind by a redeploy that died. True if removed."""
    path = hold_path(data_dir)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age < max_age:
        return False
    with contextlib.suppress(OSError):
        path.unlink()
        return True
    return False


@contextlib.contextmanager
def hold_bundle_swap(data_dir: Path, *, settle: float = SETTLE_SECONDS) -> Iterator[Path]:
    """Hold every gated shim outside the bundle for the duration of the block.

    The settle is inside the contextmanager rather than left to the caller
    because it is not a courtesy delay - it is the half of the gate that covers
    processes already in flight when the hold went up, and a caller that
    forgot it would leave exactly the window this exists to close.
    """
    path = hold_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{os.getpid()} {time.time():.3f}\n", encoding="utf-8")
    except OSError:
        # A data dir that cannot be written is not a reason to refuse a redeploy;
        # it only means this run is exposed to the window the way every run was
        # before the gate existed.
        yield path
        return
    try:
        if settle > 0:
            deadline = time.monotonic() + settle
            remaining = settle
            while remaining > 0:
                time.sleep(remaining)
                remaining = deadline - time.monotonic()
        yield path
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def cmd_gate(hold: Path) -> str:
    """The `cmd.exe` prologue that waits out a swap. Ends at a `:mux_swap_ready`."""
    tries = max(1, int(WAIT_SECONDS / _CMD_POLL_SECONDS))
    return (
        f'set "MUX_SWAP_HOLD={hold}"\r\n'
        'set "MUX_SWAP_TRIES=0"\r\n'
        ":mux_swap_wait\r\n"
        'if not exist "%MUX_SWAP_HOLD%" goto mux_swap_ready\r\n'
        f"if %MUX_SWAP_TRIES% GEQ {tries} goto mux_swap_ready\r\n"
        "set /a MUX_SWAP_TRIES+=1\r\n"
        # Two pings to localhost is one second, and is the only sleep `cmd.exe`
        # offers that does not read stdin.
        "ping -n 2 127.0.0.1 >nul 2>&1\r\n"
        "goto mux_swap_wait\r\n"
        ":mux_swap_ready\r\n"
    )


def sh_gate(hold: Path) -> str:
    """The POSIX-shell prologue that waits out a swap."""
    tries = max(1, int(WAIT_SECONDS / _SH_POLL_SECONDS))
    return (
        f"mux_swap_hold='{hold}'\n"
        "mux_swap_tries=0\n"
        f'while [ -e "$mux_swap_hold" ] && [ "$mux_swap_tries" -lt {tries} ]; do\n'
        f"  sleep {_SH_POLL_SECONDS}\n"
        "  mux_swap_tries=$((mux_swap_tries + 1))\n"
        "done\n"
    )


def ensure_exec_launcher(
    data_dir: Path,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
    windows: bool | None = None,
) -> Path | None:
    """Write the gated launcher; None when this install has no swap to gate.

    `frozen` and `windows` are parameters rather than reads of the running
    interpreter so the written artifact can be asserted on every CI leg instead
    of only on the one whose host happens to match.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        # A virtualenv interpreter is not renamed by anything, so a source install
        # has no window to gate and gating it would cost a shell process on every
        # PreToolUse for nothing. Callers fall back to `sys.executable`.
        return None
    if windows is None:
        windows = IS_WINDOWS
    target = executable or sys.executable
    path = exec_launcher_path(data_dir, windows=windows)
    path.parent.mkdir(parents=True, exist_ok=True)
    hold = hold_path(data_dir)
    if windows:
        write_script(
            path,
            "@echo off\r\n"
            "rem swe-mux gated exec. Regenerated by the daemon; edits are lost.\r\n"
            "setlocal\r\n"
            f"{cmd_gate(hold)}"
            f'"{target}" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n",
        )
        return path
    write_script(
        path,
        "#!/bin/sh\n"
        "# swe-mux gated exec. Regenerated by the daemon; edits are lost.\n"
        f"{sh_gate(hold)}"
        f'exec "{target}" "$@"\n',
    )
    path.chmod(0o755)
    return path


def hook_delivery_executable(
    data_dir: Path | None,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
    windows: bool | None = None,
) -> str:
    """What a generated hook command should name in front of `-m swe_mux...`."""
    launcher = (
        ensure_exec_launcher(data_dir, executable=executable, frozen=frozen, windows=windows)
        if data_dir is not None
        else None
    )
    return str(launcher) if launcher else (executable or sys.executable)


def hook_delivery_from_env(environ: Mapping[str, str] | None = None) -> str:
    """The same answer for code running *inside* a session rather than the daemon.

    `agent_launcher` assembles Codex's lifecycle hooks itself, in the pane, where
    there is no `Config` to read - so the daemon publishes the launcher it wrote
    as `MUX_HOOK_LAUNCHER` (`launchers.create_agent_shims`) and this reads it
    back. Unset means a source install, where `sys.executable` is already safe.
    """
    source = os.environ if environ is None else environ
    return source.get("MUX_HOOK_LAUNCHER") or sys.executable


def write_script(path: Path, body: str) -> None:
    """Write a generated shim verbatim, and only when its bytes actually change.

    Verbatim because `Path.write_text` translates `\\n` to `os.linesep`, which on
    Windows turns the CRLF a `.cmd` needs into `\\r\\r\\n`; `cmd.exe` tolerates the
    stray CR in a command line but a label is compared as written, and the gate
    below is built out of labels. Only-on-change because these files are read by
    a process the daemon does not own: rewriting one byte-identically is a real
    window in which a shim being executed right now is momentarily truncated.
    """
    data = body.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    path.write_bytes(data)


__all__ = [
    "EXEC_LAUNCHER_STEM",
    "HOLD_FILENAME",
    "SETTLE_SECONDS",
    "STALE_SECONDS",
    "WAIT_SECONDS",
    "clear_stale_hold",
    "cmd_gate",
    "ensure_exec_launcher",
    "exec_launcher_path",
    "hold_bundle_swap",
    "hold_path",
    "hook_delivery_executable",
    "hook_delivery_from_env",
    "sh_gate",
    "write_script",
]
