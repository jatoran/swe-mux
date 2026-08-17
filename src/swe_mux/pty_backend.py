"""The pseudoterminal contract `PtyHost` drives, and the factory that picks one.

`PtyHost` owns everything that is genuinely the same on every host: the reader
thread and its poll ladder, coalescing, the backpressure handoff onto the event
loop, scrollback accounting, the resize and exit-status contracts. What differs
is narrow and is confined to this contract: how a pseudoterminal is allocated,
how a child is started on it, how bytes are read without parking a thread
somewhere no one can reach, and how the whole tree is ended.

Keeping the split at this line is deliberate. The cross-platform findings warn
that a port which reimplements the buffering per platform works the week it is
written and then diverges - one host gets a coalescing fix, the other keeps the
bug. There is one reader loop, and it is tested once.

``PtyError`` is the unified failure type. Windows raises ``winpty.WinptyError``
and POSIX raises ``OSError`` for the same conditions, and the shared reader must
not name either.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .host_platform import IS_WINDOWS


class PtyError(Exception):
    """A pseudoterminal operation failed. Platform errors are mapped onto this."""


@runtime_checkable
class PtyProcess(Protocol):
    """One pseudoterminal with one root child on it."""

    @property
    def pid(self) -> int:
        """The root child's pid, or -1 before spawn."""
        ...

    def spawn(
        self,
        appname: str,
        argv: tuple[str, ...],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> None:
        """Start ``appname`` on this pseudoterminal.

        ``argv`` is structured and platform-neutral: quoting is this boundary's
        job, because the quoting rules belong to the process boundary rather than
        to the adapter that built the argument list. ``env`` of None inherits the
        current process environment.
        """
        ...

    def read(self) -> bytes | None:
        """Read whatever is available without blocking.

        Returns None when nothing is available right now, and b"" is never
        returned for that case - the shared reader treats b"" as end of output.
        Raises ``PtyError`` when the pseudoterminal can no longer be read.
        """
        ...

    def write(self, data: str) -> None:
        """Write to the child's input."""
        ...

    def set_size(self, cols: int, rows: int) -> None:
        """Resize the pseudoterminal."""
        ...

    def isalive(self) -> bool:
        """Whether the root child is still running."""
        ...

    def exit_status(self) -> int | None:
        """The root child's exit code once it has stopped, when available."""
        ...

    def interrupt_read(self) -> None:
        """Wake a reader parked in a platform read call. Best effort, never raises."""
        ...

    def force_kill(self) -> None:
        """Kill the root child and its descendants. Best effort, never raises."""
        ...

    def close(self) -> None:
        """Release the pseudoterminal and any platform helper processes it owns."""
        ...


def open_pty(cols: int, rows: int) -> PtyProcess:
    """Allocate a pseudoterminal for this host."""
    if IS_WINDOWS:
        from .pty_backend_windows import WindowsPtyProcess

        return WindowsPtyProcess(cols, rows)
    from .pty_backend_posix import PosixPtyProcess

    return PosixPtyProcess(cols, rows)


def pty_backend_name() -> str:
    """Which pseudoterminal implementation this host uses, for diagnostics."""
    return "conpty" if IS_WINDOWS else "posix"


__all__ = ["PtyError", "PtyProcess", "open_pty", "pty_backend_name"]
