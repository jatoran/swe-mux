"""POSIX process-group ownership: the analogue of the Windows kill-on-close Job.

Windows gives ownership away for free. A Job object holds its members, the kernel
closes the handle when the owning process dies for any reason, and
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` turns that into "children cannot outlive
the daemon" without the daemon being alive to enforce it.

POSIX has no equivalent primitive, and the cross-platform findings are explicit
that ``setsid`` plus ``killpg`` is not a substitute. A process group gives
*addressing* - one signal reaches a whole tree, including descendants whose
intermediate parent already exited - but nothing kills the group when the daemon
dies. This class supplies the addressing half and the orderly-shutdown half. The
daemon-death half needs a separate live process holding a pipe, which is
``posix_guardian.py``.

Two properties are load-bearing and both are asserted in tests:

* A group is only ever tracked when the child is its own group leader. If the PTY
  layer failed to call ``setsid``, ``os.getpgid(pid)`` returns *the daemon's own
  group*, and closing that reaper would signal the daemon, the supervisor, and
  every sibling session. That is refused loudly rather than accepted quietly,
  because it converts a session-cleanup bug into a whole-app kill.
* Membership is re-read live rather than cached. Like Windows job membership, a
  pid's group is a kernel fact that disappears the moment it exits, so a recycled
  pid cannot inherit a dead session's ownership by coincidence.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import time

import psutil

from .posix_guardian import PosixGuardian, start_guardian

log = logging.getLogger(__name__)

# How long a group gets to honour SIGTERM before the group SIGKILL. Matches the
# graceful window the Windows path allows a session between `exit\r` and taskkill.
_GRACEFUL_SECONDS = 2.0
_POLL_SECONDS = 0.05


class ProcessGroupError(OSError):
    """A group could not be taken under ownership."""


class ProcessGroupReaper:
    """Owns zero or more POSIX process groups and kills them on close.

    Presents the ``ProcessReaper`` surface (`assign` / `process_ids` /
    `create_child` / `close`) so `Session`, `PtyHost`, and the supervisor call one
    contract on every host.
    """

    def __init__(self, *, guard_against_daemon_death: bool = True) -> None:
        self._pgids: set[int] = set()
        self._closed = False
        # Whether this reaper starts a guardian process for the groups it owns.
        # A nested per-session reaper does, because a session is the unit that
        # must not survive the daemon. Turned off in tests that only exercise the
        # addressing half, so they do not leave processes behind.
        self._guarding = guard_against_daemon_death
        self._guardian: PosixGuardian | None = None

    def assign(self, pid: int) -> None:
        """Take ownership of ``pid``'s process group.

        Raises ``ProcessGroupError`` (an ``OSError``, which is what the Windows
        path already raises and every caller already handles) when the pid is not
        in a group of its own.
        """
        if self._closed:
            raise ProcessGroupError("cannot assign to a closed reaper")
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            raise ProcessGroupError(
                f"could not read the process group of pid {pid}: {exc}"
            ) from exc
        own_pgid = os.getpgid(0)
        if pgid == own_pgid:
            # The child shares this daemon's group: it was spawned without setsid.
            # Owning it would make close() signal the daemon itself.
            raise ProcessGroupError(
                f"pid {pid} is in this process's own group ({pgid}); it was not "
                "started in a session of its own, so it cannot be owned separately"
            )
        if pgid <= 0:
            raise ProcessGroupError(f"pid {pid} reported a nonsensical process group ({pgid})")
        if pgid in self._pgids:
            return
        self._pgids.add(pgid)
        self._guard(pgid)

    def _guard(self, pgid: int) -> None:
        """Extend daemon-death protection to a newly owned group.

        One guardian process per reaper, not per group: the guardian accepts more
        groups over its pipe, so a reaper owning several sessions still costs one
        process. A guardian that could not be started leaves the group addressable
        but unprotected against an unclean daemon death, which is logged by
        `start_guardian` and is strictly better than refusing the session.
        """
        if not self._guarding:
            return
        if self._guardian is None:
            self._guardian = start_guardian(pgid)
            return
        self._guardian.watch(pgid)

    def release(self) -> None:
        """Stop guarding without killing anything.

        This is what a deliberate daemon restart calls. The groups stay alive and
        the guardian exits, so the successor daemon adopts running sessions rather
        than finding them reaped - the POSIX half of the session-preserving reload
        contract.
        """
        self._closed = True
        self._pgids.clear()
        guardian, self._guardian = self._guardian, None
        if guardian is not None:
            guardian.release()

    def process_ids(self) -> list[int]:
        """Every live pid currently in one of the owned groups.

        The POSIX counterpart to ``ReaperJob.process_ids``, and it answers the same
        question that a parent/child walk cannot: a descendant whose intermediate
        parent already exited was reparented to init, so the walk loses it while its
        process group still names it exactly.

        Returns an empty list rather than raising, so callers treat an unreadable
        group as "no extra evidence" and fall back to the walk.
        """
        if not self._pgids:
            return []
        found: list[int] = []
        for process in psutil.process_iter(["pid"]):
            pid = int(process.info["pid"])
            try:
                if os.getpgid(pid) in self._pgids:
                    found.append(pid)
            except (ProcessLookupError, PermissionError, OSError):
                continue
        return found

    def create_child(self) -> ProcessGroupReaper:
        """A nested per-session reaper beneath the daemon-wide one.

        Windows nests real Job objects; here the nesting is bookkeeping only. Both
        reapers name the same group, so closing either ends the session and closing
        the daemon-wide one ends every session, which is the contract callers rely
        on.
        """
        return ProcessGroupReaper(guard_against_daemon_death=self._guarding)

    def close(self) -> None:
        """Signal every owned group: SIGTERM, bounded wait, then SIGKILL."""
        if self._closed:
            return
        self._closed = True
        pgids, self._pgids = self._pgids, set()
        guardian, self._guardian = self._guardian, None
        if guardian is not None:
            # The daemon is doing the killing itself, so the guardian must not also
            # try: release it first, then signal. Dropping its pipe instead would
            # race two killers onto one group for no benefit.
            guardian.release()
        alive = [pgid for pgid in pgids if _signal_group(pgid, signal.SIGTERM)]
        if not alive:
            return
        deadline = time.monotonic() + _GRACEFUL_SECONDS
        while time.monotonic() < deadline:
            alive = [pgid for pgid in alive if _group_is_alive(pgid)]
            if not alive:
                return
            time.sleep(_POLL_SECONDS)
        for pgid in alive:
            _signal_group(pgid, signal.SIGKILL)


def _signal_group(pgid: int, sig: int) -> bool:
    """Send ``sig`` to a whole group. True when the group existed to receive it."""
    try:
        os.killpg(pgid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        log.warning("not permitted to signal process group %s: %s", pgid, exc)
        return False
    except OSError as exc:
        log.warning("could not signal process group %s: %s", pgid, exc)
        return False


def _group_is_alive(pgid: int) -> bool:
    """Whether any process remains in the group (signal 0 probes without sending)."""
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def terminate_group(pgid: int, *, graceful_seconds: float = _GRACEFUL_SECONDS) -> None:
    """Free-function group teardown, for the guardian and forced-stop paths."""
    if not _signal_group(pgid, signal.SIGTERM):
        return
    deadline = time.monotonic() + graceful_seconds
    while time.monotonic() < deadline:
        if not _group_is_alive(pgid):
            return
        time.sleep(_POLL_SECONDS)
    _signal_group(pgid, signal.SIGKILL)


def process_group_of(pid: int) -> int | None:
    """The process group of ``pid``, or None when it cannot be read."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        return os.getpgid(pid)
    return None


__all__ = [
    "ProcessGroupError",
    "ProcessGroupReaper",
    "process_group_of",
    "terminate_group",
]
