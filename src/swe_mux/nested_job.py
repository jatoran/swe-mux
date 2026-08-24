"""The nested per-session process owner, created identically wherever it is created.

A supervised session's process tree is owned twice: by the daemon-wide reaper
that outlives any one session, and by a *nested* owner beneath it so that
removing one session ends exactly that session's tree and nothing else. Two
processes create that nested owner - the supervisor for supervisor-backed
sessions (a daemon-held handle would kill the tree on daemon exit and defeat the
whole session-survival property) and the daemon for in-process ones - and the
two had drifted into near-verbatim copies of the same six lines, *including* the
assignment strings that process forensics read back
(`development/CODE_QUALITY_AUDIT_2026-08-23.md`, finding 22).

Those strings are the contract, so they live here as constants rather than being
spelled out at each call site.

This module is inside the hash-gated supervisor source closure
(`packaging/build_desktop.py`). It deliberately imports nothing but
`process_reaper`, which is already in that closure, so the daemon can adopt it
without widening what a supervisor rebuild covers - and widening that closure is
expensive, because a supervisor rebuild reaps every live session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .process_reaper import ProcessReaper

log = logging.getLogger(__name__)

# The forensic contract. `process_job_assignment` / `reaper_assignment` are read
# by operational telemetry and by incident triage, so these suffixes are as much
# an interface as the wire protocol is.
ASSIGNED_SUFFIX = ";nested_session_job_assigned"
FAILED_SUFFIX_PREFIX = ";nested_session_job_failed:"
# No parent owner to nest under: the caller already recorded why in the base
# assignment string, so there is nothing to append.
UNAVAILABLE_SUFFIX = ""


@dataclass(frozen=True)
class NestedJob:
    """The outcome of nesting one session's owner under the daemon-wide one."""

    job: ProcessReaper | None
    #: Appended verbatim to the caller's assignment string.
    suffix: str
    error: str | None = None

    @property
    def owned(self) -> bool:
        return self.job is not None


def create_nested_session_job(
    parent: ProcessReaper | None, pid: int, *, sid: str = ""
) -> NestedJob:
    """Nest a per-session owner under ``parent`` and take ownership of ``pid``.

    Never raises. A session whose cleanup is weaker than intended is still
    better than no session, so a failure is *recorded* - in ``suffix``, which the
    caller appends to the assignment string, and in the log - rather than failing
    the spawn. What it must never do is report ownership it does not have, so a
    job that was created but could not take the pid is closed rather than kept or
    leaked.
    """
    create_child = getattr(parent, "create_child", None) if parent is not None else None
    if create_child is None:
        log.info("nested session job unavailable sid=%s pid=%s", sid or "-", pid)
        return NestedJob(None, UNAVAILABLE_SUFFIX, None)
    job: ProcessReaper | None = None
    try:
        job = create_child()
        job.assign(pid)
    except OSError as exc:
        error = str(exc)
        log.warning(
            "nested session job failed sid=%s pid=%s error=%s", sid or "-", pid, error
        )
        if job is not None:
            job.close()
        return NestedJob(None, f"{FAILED_SUFFIX_PREFIX}{error}", error)
    log.info("nested session job assigned sid=%s pid=%s", sid or "-", pid)
    return NestedJob(job, ASSIGNED_SUFFIX, None)


__all__ = [
    "ASSIGNED_SUFFIX",
    "FAILED_SUFFIX_PREFIX",
    "UNAVAILABLE_SUFFIX",
    "NestedJob",
    "create_nested_session_job",
]
