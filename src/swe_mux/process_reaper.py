"""The one lifecycle-ownership contract, and the per-platform implementations of it.

`Session`, `PtyHost`, the supervisor, and the server all need the same four
things: take ownership of a spawned root, enumerate what is still inside, nest a
per-session owner beneath the daemon-wide one, and end everything owned. Windows
answers with a Job object; POSIX answers with a process group plus a guardian.
Neither answer belongs in a caller, so callers import ``ProcessReaper`` from here
and never name a platform.

Importing ``win_jobobj`` on POSIX fails at module scope - its ctypes structures
are built from ``wintypes`` at class-body evaluation, which raises on a non-Windows
host - so the platform import lives inside the factory rather than at the top of
this file. That is also why callers must stop importing ``win_jobobj`` directly:
a single stray top-level import of it is enough to make the whole package
unimportable on Linux, which is exactly the class of blocker Phase 10 starts by
removing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .host_platform import IS_WINDOWS


@runtime_checkable
class ProcessReaper(Protocol):
    """Ownership of a spawned process tree, for the lifetime of its owner."""

    def assign(self, pid: int) -> None:
        """Take ownership of ``pid`` and everything it goes on to spawn.

        Raises ``OSError`` when ownership cannot be established. Callers record
        that as a degraded assignment rather than failing the spawn, because a
        session with weak cleanup is still better than no session - but they must
        never report it as owned.
        """
        ...

    def process_ids(self) -> list[int]:
        """Every live pid currently owned. Empty when it cannot be determined."""
        ...

    def create_child(self) -> ProcessReaper:
        """A nested owner for one session, beneath this daemon-wide owner."""
        ...

    def close(self) -> None:
        """End everything owned. Idempotent."""
        ...


def create_reaper() -> ProcessReaper:
    """The daemon-wide reaper for this host."""
    if IS_WINDOWS:
        from .win_jobobj import ReaperJob

        return ReaperJob()
    from .posix_process_group import ProcessGroupReaper

    return ProcessGroupReaper()


def process_in_job() -> bool | None:
    """Whether this process is inside an inherited kill-on-close container.

    Windows answers from ``IsProcessInJob``: a daemon, tray, or supervisor
    relaunched from a shell *inside* a session inherits that session's
    kill-on-close Job and dies silently when the session is removed, so startup
    paths warn while the process is still healthy.

    POSIX returns None - "unknowable" - and that is the truthful answer rather
    than a convenient False. A POSIX child does inherit its parent's process
    group, but membership alone carries no kill-on-close semantics, so there is
    no equivalent latent-death condition to warn about. Callers already treat
    None as "no warning to give".
    """
    if not IS_WINDOWS:
        return None
    from .win_jobobj import process_in_job as _win_process_in_job

    return _win_process_in_job()


__all__ = ["ProcessReaper", "create_reaper", "process_in_job"]
