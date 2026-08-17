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

The child is deliberately doing almost nothing between fork and exec. This
process is multi-threaded, so any allocation in the child can deadlock against a
lock held by a thread that does not exist after the fork. `chdir` and `execvpe`
are all that runs.
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

from .pty_backend import PtyError

log = logging.getLogger(__name__)

# One read syscall's ceiling. The shared reader coalesces across calls, so this
# only bounds a single buffer rather than a burst.
_READ_CHUNK = 65536


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
