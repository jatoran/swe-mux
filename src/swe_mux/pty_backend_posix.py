"""POSIX pseudoterminal: `openpty`/`forkpty` with a controlling terminal.

The controlling-terminal part is not incidental. A child that merely has a pty on
its stdio, without that pty being its controlling terminal, silently loses job
control: Ctrl+C reaches nothing, SIGWINCH is never delivered on resize, and an
agent CLI that asks `isatty` gets the right answer while behaving like a pipe.
``pty.fork()`` is used precisely because it performs `setsid` and `TIOCSCTTY` in
the child in the correct order; hand-rolling that with `subprocess` plus a
`preexec_fn` gets the ordering wrong in ways that only show up interactively.

`setsid` also gives the session its own process group, which is what
``ProcessGroupReaper.assign`` requires before it will take ownership - the two
halves of POSIX lifetime management are designed against each other here rather
than separately.

**That handoff has to be waited for, not assumed.** ``setsid`` runs in the
*child*, after the fork, inside `forkpty`/`login_tty`. The parent returns from
`pty.fork()` the moment the fork syscall returns, with nothing ordering it
against the child's first instruction, so `os.getpgid(child)` read immediately
after the fork can still answer *this process's own group* - the one value
``ProcessGroupReaper.assign`` refuses. Measured 2026-08-27 in a Linux container:
on an idle 8-CPU host the parent never observed its own group across 60 spawns;
with 8 busy-loop processes pinning every core it observed it in 15 of 60, and
against the real spawn-then-assign path ownership was refused in 21, 36 and 33
of 40. Contention is the whole story, so this was never macOS-only - a
three-core runner under `pytest -n auto` is simply contended permanently, which
is why that is where it surfaced. `spawn` therefore does not return until
the child's own group is visible, so the documented contract - "one root child
that leads its own session" - is true of the object it hands back rather than
true shortly afterwards.

The child is deliberately doing almost nothing between fork and exec. This
process is multi-threaded, so any allocation in the child can deadlock against a
lock held by a thread that does not exist after the fork. `chdir` and `execvpe`
are all that runs, and the wait above is deliberately on the parent's side for
the same reason.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
import time

from .pty_backend import PtyError

log = logging.getLogger(__name__)

# One read syscall's ceiling. The shared reader coalesces across calls, so this
# only bounds a single buffer rather than a burst.
_READ_CHUNK = 65536

# How long the parent waits for the child to reach `setsid`. Generously bounded
# rather than unbounded: exceeding it means the child is starved or already gone,
# and both are better reported by the caller's own error path than by hanging a
# session start. The typical cost is a single `getpgid` that already answers.
_SESSION_WAIT_SECONDS = 5.0
_SESSION_POLL_SECONDS = 0.0005


class PosixPtyProcess:
    """A POSIX pseudoterminal with one root child that leads its own session."""

    def __init__(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows
        self._master: int = -1
        self._pid: int = -1
        self._exit_status: int | None = None
        self._reaped = False

    @property
    def pid(self) -> int:
        return self._pid

    def spawn(
        self,
        appname: str,
        argv: tuple[str, ...],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> None:
        if self._pid != -1:
            raise PtyError("pseudoterminal already has a child")
        # argv carries arguments only; the executable is named separately, exactly
        # as on the ConPTY path where pywinpty supplies the application name. POSIX
        # exec needs argv[0] spelled out, so it is added here rather than asking
        # every adapter to carry a platform-shaped argv.
        child_argv = [appname, *argv]
        child_env = dict(env) if env is not None else dict(os.environ)
        pid, master = pty.fork()
        if pid == 0:
            # Child. Nothing here may allocate more than it must, and nothing may
            # raise past the exec: a Python traceback on a pseudoterminal would be
            # indistinguishable from program output.
            try:
                if cwd:
                    os.chdir(cwd)
                os.execvpe(appname, child_argv, child_env)
            except BaseException:  # noqa: BLE001 - the child must never unwind
                os._exit(127)
            os._exit(127)  # unreachable; exec does not return
        self._pid = pid
        self._master = master
        os.set_blocking(master, False)
        self._apply_size(self._cols, self._rows)
        _await_own_session(pid)

    def read(self) -> bytes | None:
        if self._master < 0:
            raise PtyError("pseudoterminal is not open")
        try:
            chunk = os.read(self._master, _READ_CHUNK)
        except BlockingIOError:
            return None
        except OSError as exc:
            if exc.errno == errno.EAGAIN:
                return None
            if exc.errno == errno.EIO:
                # The slave side closed: every process holding it has exited. This
                # is the POSIX end-of-output signal, not a fault, and the shared
                # reader learns the child is gone from isalive() on the next pass.
                return None
            if exc.errno == errno.EBADF:
                raise PtyError("pseudoterminal was closed") from exc
            raise PtyError(f"pseudoterminal read failed: {exc}") from exc
        return chunk or None

    def write(self, data: str) -> None:
        if self._master < 0:
            raise PtyError("pseudoterminal is not open")
        payload = data.encode("utf-8", "replace")
        while payload:
            try:
                written = os.write(self._master, payload)
            except BlockingIOError:
                continue
            except OSError as exc:
                raise PtyError(f"pseudoterminal write failed: {exc}") from exc
            payload = payload[written:]

    def set_size(self, cols: int, rows: int) -> None:
        if self._master < 0:
            return
        self._apply_size(cols, rows)
        self._cols, self._rows = cols, rows

    def _apply_size(self, cols: int, rows: int) -> None:
        # TIOCSWINSZ takes rows before columns; reversing them is the classic bug
        # and presents as a terminal that wraps at the wrong width rather than as
        # an error.
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._master, termios.TIOCSWINSZ, packed)
        except OSError as exc:
            log.debug("could not resize pseudoterminal: %s", exc)

    def isalive(self) -> bool:
        if self._pid <= 0 or self._reaped:
            return False
        try:
            waited, status = os.waitpid(self._pid, os.WNOHANG)
        except ChildProcessError:
            # Somebody else reaped it (an asyncio child watcher, or a double
            # check). It is gone; the exit code is simply not ours to report.
            self._reaped = True
            return False
        except OSError as exc:
            log.debug("waitpid on %s failed: %s", self._pid, exc)
            return True
        if waited == 0:
            return True
        self._reaped = True
        self._exit_status = _exit_code(status)
        return False

    def exit_status(self) -> int | None:
        if self.isalive():
            return None
        return self._exit_status

    def interrupt_read(self) -> None:
        # No-op by construction: POSIX reads here are nonblocking, so no reader is
        # ever parked in a platform call that needs waking. The shared reader's own
        # interruptible sleep covers the latency case.
        return None

    def force_kill(self) -> None:
        if self._pid <= 0 or self._reaped:
            return
        try:
            pgid = os.getpgid(self._pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None
        try:
            if pgid is not None and pgid != os.getpgid(0):
                # The whole session, not just the root: a shell's children are
                # what actually hold the pseudoterminal open.
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(self._pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            log.debug("could not kill pty child %s: %s", self._pid, exc)
        self.isalive()

    def close(self) -> None:
        master, self._master = self._master, -1
        if master >= 0:
            try:
                os.close(master)
            except OSError as exc:
                log.debug("could not close pseudoterminal master: %s", exc)
        # Reap if it has already exited, so a released session leaves no zombie.
        self.isalive()


def _await_own_session(pid: int) -> None:
    """Block until ``pid`` is out of this process's group, or give up loudly.

    The one synchronisation point between the fork half and the ownership half of
    POSIX lifetime management; see this module's docstring for why it cannot be
    skipped. Never raises: every outcome other than success is something the
    caller's own ownership step reports better than an exception thrown from
    inside `spawn` would.

    * Child gone already (a failed exec, an instant exit): nothing to wait for.
      Its group is not this daemon's problem, and the pty's exit path reports it.
    * Group unreadable: this host will not answer, so waiting cannot help.
    * Deadline reached: the child is starved. Returning lets
      ``ProcessGroupReaper.assign`` refuse ownership with its own explicit
      message, which is a session running unowned rather than a session that
      never starts.
    """
    own_pgid = os.getpgid(0)
    deadline = time.monotonic() + _SESSION_WAIT_SECONDS
    # unsupervised-loop-ok: not a daemon loop. This is a bounded synchronous wait
    # inside one `spawn` call, capped by `_SESSION_WAIT_SECONDS` on the thread
    # `asyncio.to_thread` already gave that spawn, and every path out of it is an
    # explicit return in the body.
    while True:
        try:
            if os.getpgid(pid) != own_pgid:
                return
        except ProcessLookupError:
            return
        except (PermissionError, OSError) as exc:
            log.debug("could not read the process group of pty child %s: %s", pid, exc)
            return
        if time.monotonic() >= deadline:
            log.warning(
                "pty child %s was still in this process's group (%s) after %.1fs; "
                "it cannot be owned as a session of its own",
                pid,
                own_pgid,
                _SESSION_WAIT_SECONDS,
            )
            return
        time.sleep(_SESSION_POLL_SECONDS)


def _exit_code(status: int) -> int:
    """Normalize a waitpid status into an exit code.

    A signalled death is reported the way a POSIX shell reports it, 128+signal, so
    a caller comparing exit codes across hosts sees one number space instead of a
    raw wait status that means nothing to the rest of the system.
    """
    if os.WIFEXITED(status):
        return int(os.WEXITSTATUS(status))
    if os.WIFSIGNALED(status):
        return 128 + int(os.WTERMSIG(status))
    return int(status)


__all__ = ["PosixPtyProcess"]
