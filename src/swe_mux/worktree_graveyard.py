"""Fast worktree removal: one rename now, the deletion later.

Removing an agent worktree is honest filesystem work - a checkout carrying
`node_modules` and `.venv` is tens of thousands of small files, and NTFS unlink
plus per-file antivirus scanning turns that into ten to twenty seconds. None of
that time is spent deciding anything, so none of it needs to be spent in front of
the person who pressed the button: the directory is renamed out of the way with
one `MoveFile` call, Git is told to forget the registration, and the bytes are
deleted by a background task afterwards.

**Where the graveyard lives is a correctness question, not a tidiness one.**
It is the repository's own common Git directory (`<common-dir>/swe-mux-graveyard`),
for three reasons that no other location satisfies at once:

  * it is outside every working tree, so a buried checkout can never appear as
    untracked files in `git status` - which would raise dirty counts, and the land
    queue refuses to land a worktree that is not clean;
  * `.git` is the first entry of the default project-ignore list and is skipped by
    the Project watcher and the file surfaces, so a purge deleting thirty thousand
    files does not turn into thirty thousand change events;
  * it is never in `git worktree list`, so Map cannot draw a row for it.

A sibling directory beside the worktree would be same-volume by construction, which
is tempting because it makes the rename always possible - but `.claude/worktrees/`
is only gitignored in repositories that happen to say so, and this repository's own
`.gitignore` is not a fact about anyone else's. The graveyard being on a different
volume from the worktree is handled instead: the rename raises, and the caller falls
back to the ordinary in-place deletion.

**The rename is atomic and its failure is total.** Windows refuses to move a
directory while any handle inside it is open (`WinError 5`/`WinError 32`, measured),
and the source is left exactly as it was - so a defeated rename is a clean signal to
take the slow path rather than a half-moved tree to reason about.

Purging is idempotent and forgiving, but no longer infinitely patient: whatever it
cannot delete stays and the next purge (the next removal, or the sweep at daemon
start) tries again, up to `PURGE_ATTEMPT_LIMIT` times per path. Nothing here ever
removes the graveyard root itself, so a purge racing a burial cannot delete a
directory another removal is renaming into.

**Exactly one thread deletes, for the whole daemon** (`GraveyardPurger`). On
2026-10-18 thirty-five removals in one minute each started a purge of the *whole*
graveyard on the shared default executor: 32 purges took all 32 of its workers and
deleted the same trees concurrently, the 33rd removal's rename queued behind them
forever, and the daemon's heartbeat and health probe - both submitted to the same
pool - went silent until the tray killed it as hung. So a removal now only *asks*: the
purger coalesces every request for a graveyard into at most one pass running and one
waiting, on its own thread. It runs at normal priority on purpose. A lowered thread
can be descheduled while it holds the GIL, and the event loop would then wait behind
it - measured, one deletion thread costs the loop's requests 0.5 ms -> 0.7 ms at p50,
which is not worth that risk.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parked_threads import parked

log = logging.getLogger(__name__)

#: How many times one buried path may fail to delete before the purge stops trying
#: it and says so once. A purge that retried forever wrote 1,165 warnings over three
#: days for a handful of paths (2026-08-21 to 08-24), which is the shape of a log
#: nobody reads. The state is in memory and dies with the daemon, which is the
#: intended bound: a restart is a cheap, deliberate "try that again", and a lock
#: held by a process that has since exited gets one.
PURGE_ATTEMPT_LIMIT = 5

_attempt_lock = threading.Lock()
#: Consecutive failures per buried path, cleared the moment the path is gone.
_purge_attempts: dict[str, int] = {}
#: Paths that exhausted the limit. Skipped, silently, until the daemon restarts.
_purge_abandoned: set[str] = set()


def reset_purge_attempts() -> None:
    """Forget every attempt count and abandonment. For tests, and a deliberate retry."""
    with _attempt_lock:
        _purge_attempts.clear()
        _purge_abandoned.clear()


def _purge_key(entry: Path) -> str:
    return os.path.normcase(os.path.abspath(entry))


def _forget(key: str) -> None:
    with _attempt_lock:
        _purge_attempts.pop(key, None)
        _purge_abandoned.discard(key)


def _record_failure(key: str) -> int:
    with _attempt_lock:
        attempts = _purge_attempts.get(key, 0) + 1
        _purge_attempts[key] = attempts
        if attempts >= PURGE_ATTEMPT_LIMIT:
            _purge_abandoned.add(key)
    return attempts

#: Inside the repository's common Git directory. Not dot-prefixed: `.git` already
#: hides it from everything that walks a working tree, and a name that reads as a
#: swe-mux artefact is what a human finding it needs.
GRAVEYARD_DIR_NAME = "swe-mux-graveyard"


def graveyard_root(common_dir: str | Path) -> Path:
    """Where buried checkouts wait for deletion, for one repository."""
    return Path(common_dir) / GRAVEYARD_DIR_NAME


def bury(source: str | Path, root: Path, operation_id: str) -> Path:
    """Move ``source`` into the graveyard with one rename.

    Raises ``OSError`` when the rename is defeated - a held handle, a cross-volume
    graveyard, a vanished source. The source is untouched in every one of those
    cases, so the caller's fallback runs against the original tree.
    """
    origin = Path(source)
    root.mkdir(parents=True, exist_ok=True)
    # The operation id, not a counter: it correlates the buried directory with the
    # removal's log line, and it cannot collide with a concurrent burial.
    target = root / f"{origin.name}-{operation_id}"
    os.replace(origin, target)
    return target


def exhume(buried: Path, destination: str | Path) -> bool:
    """Put a buried checkout back where it came from.

    Used when Git refused to drop the registration after the rename: the tree is
    restored exactly, and the ordinary in-place removal answers instead. A failure
    here is reported rather than raised - the caller is already handling one.
    """
    try:
        os.replace(buried, Path(destination))
    except OSError as exc:
        log.warning(
            "worktree_graveyard_restore_failed buried=%s destination=%s error_type=%s",
            buried,
            destination,
            type(exc).__name__,
        )
        return False
    return True


def _clear_readonly(function: Callable[..., Any], path: str, excinfo: BaseException) -> None:
    """Retry one deletion after clearing the read-only bit.

    Git writes its loose objects and packs read-only, and a worktree carrying a
    vendored dependency tree usually carries a few more. On Windows a read-only file
    cannot be unlinked at all, so without this a purge stops on the first one and
    leaves the rest of the tree on disk forever.
    """
    del function, excinfo
    try:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)
    except OSError:
        # Genuinely undeletable (a live handle). Left for the next purge.
        pass


def purge(root: Path, *, stop: threading.Event | None = None) -> tuple[int, int]:
    """Delete every buried checkout under ``root``.

    Returns ``(removed, failed)``. Failures are counted rather than raised: a
    locked file means those bytes wait for the next purge, which is the whole
    reason the graveyard is a directory rather than a temporary name. ``stop`` is
    checked between buried checkouts, so a stopping daemon finishes at most the
    one it is deleting and leaves the rest for the next purge.

    Two things bound that patience, and both come from a purge loop that had
    neither (D2 soak, 2026-08-24). **An absent path is a purged path**: a directory
    Windows has marked delete-pending stays in the listing while every operation on
    it reports it missing, so a `FileNotFoundError` there is the deletion working,
    not failing. And a path that genuinely will not go is retried
    `PURGE_ATTEMPT_LIMIT` times and then abandoned with one terminal line, because
    the twenty-fourth identical warning about one locked checkout is not new
    information.
    """
    try:
        entries = sorted(root.iterdir())
    except FileNotFoundError:
        return (0, 0)
    except OSError as exc:
        log.warning(
            "worktree_graveyard_unreadable root=%s error_type=%s", root, type(exc).__name__
        )
        return (0, 0)
    removed = failed = abandoned = 0
    for entry in entries:
        if stop is not None and stop.is_set():
            break
        key = _purge_key(entry)
        with _attempt_lock:
            already_abandoned = key in _purge_abandoned
        if already_abandoned:
            abandoned += 1
            continue
        if not entry.exists() and not entry.is_symlink():
            # Gone before we touched it - a concurrent purge, or a delete-pending
            # directory Windows still lists. Either way the postcondition holds.
            removed += 1
            _forget(key)
            log.debug("worktree_graveyard_purge_already_absent path=%s", entry)
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, onexc=_clear_readonly)
            else:
                entry.unlink()
        except FileNotFoundError:
            removed += 1
            _forget(key)
            log.debug("worktree_graveyard_purge_already_absent path=%s", entry)
            continue
        except OSError as exc:
            failed += 1
            _report_failure(
                entry, key, "worktree_graveyard_purge_failed", type(exc).__name__, str(exc)
            )
            continue
        # `rmtree` with an error handler that swallows can return having left
        # files behind, so success is what the filesystem says afterwards.
        if entry.exists():
            failed += 1
            _report_failure(
                entry,
                key,
                "worktree_graveyard_purge_incomplete",
                "Incomplete",
                "files remained after rmtree",
            )
        else:
            removed += 1
            _forget(key)
    if removed or failed or abandoned:
        log.info(
            "worktree_graveyard_purged root=%s removed=%d failed=%d abandoned=%d",
            root,
            removed,
            failed,
            abandoned,
        )
    return (removed, failed)


def _report_failure(entry: Path, key: str, event: str, error_type: str, detail: str) -> None:
    """Warn about one undeletable path, and say so once more when we give up on it."""
    attempts = _record_failure(key)
    if attempts >= PURGE_ATTEMPT_LIMIT:
        log.warning(
            "worktree_graveyard_purge_abandoned path=%s attempts=%d error_type=%s error=%s "
            "detail=no further purge will retry this path until the daemon restarts",
            entry,
            attempts,
            error_type,
            detail,
        )
        return
    log.warning(
        event + " path=%s attempt=%d limit=%d error_type=%s error=%s",
        entry,
        attempts,
        PURGE_ATTEMPT_LIMIT,
        error_type,
        detail,
    )


def project_graveyards(project_roots: Sequence[str]) -> list[Path]:
    """The graveyards of every Project root whose `.git` is a directory.

    Filesystem only, no Git: a Project root whose `.git` is a directory owns the
    common directory, and its graveyard is where removals buried checkouts. A root
    that is itself a linked worktree carries a `.git` *file* and is skipped - its
    common directory belongs to a repository that is either registered here in its
    own right or swept by its next removal, and resolving it would mean running Git
    for every Project on the startup path.
    """
    found: list[Path] = []
    for root in project_roots:
        common = Path(root) / ".git"
        try:
            if common.is_dir():
                found.append(graveyard_root(common))
        except OSError:
            continue
    return found


#: The purger's thread, named so a stall dump reads as what it is.
PURGER_THREAD_NAME = "graveyard-purge"
#: Operation ids kept per waiting pass, for the log line that says what it serves.
_OPERATION_IDS_KEPT = 8


@dataclass(slots=True)
class _PurgeRequest:
    """One graveyard waiting for a pass, and every request it absorbed."""

    root: Path
    requested_at: float
    requests: int = 1
    operation_ids: list[str] = field(default_factory=list)


class GraveyardPurger:
    """The daemon's one deletion worker for buried checkouts.

    A removal calls `request`; it never deletes anything itself. Per graveyard there
    is at most one pass running and one waiting: a request for a graveyard already
    waiting is absorbed into that pass, and a request for the one being purged queues
    exactly one more, because the running pass listed the graveyard before the new
    checkout was buried into it. Thirty-five removals therefore cost at most two
    passes, where they used to cost thirty-five concurrent ones.

    The thread is its own, never an executor's: a pass runs for minutes over a large
    graveyard, and a shared pool that lends a worker for that long is a pool that has
    stopped serving everything else. It is started on the first request, so a daemon
    that never removes a worktree never has it.
    """

    def __init__(
        self,
        *,
        purge_fn: Callable[..., tuple[int, int]] = purge,
        thread_name: str = PURGER_THREAD_NAME,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._purge = purge_fn
        self._thread_name = thread_name
        self._clock = clock
        self._condition = threading.Condition()
        self._stop = threading.Event()
        #: Insertion-ordered, so passes run in the order their first request arrived.
        self._waiting: dict[str, _PurgeRequest] = {}
        self._sweeps: list[tuple[str, ...]] = []
        #: A sweep taken off `_sweeps` whose graveyards are still being listed. Without
        #: it the purger reads as idle between taking a sweep and queueing its passes.
        self._sweeping = False
        self._active: Path | None = None
        self._active_since: float | None = None
        self._thread: threading.Thread | None = None
        self.requests = 0
        self.coalesced = 0
        self.passes = 0
        self.removed = 0
        self.failed = 0
        self.last_pass: dict[str, Any] | None = None

    # -- asking --------------------------------------------------------------------

    def request(self, root: Path, *, operation_id: str = "") -> bool:
        """Ask for ``root`` to be purged. True when this queued a new pass.

        Never blocks on the filesystem and never waits for the pass, so it is safe on
        the event loop.
        """
        key = _purge_key(root)
        with self._condition:
            if self._stop.is_set():
                log.info(
                    "worktree_graveyard_purge_request_dropped root=%s operation_id=%s "
                    "reason=stopping",
                    root,
                    operation_id,
                )
                return False
            self.requests += 1
            waiting = self._waiting.get(key)
            if waiting is not None:
                waiting.requests += 1
                if operation_id and len(waiting.operation_ids) < _OPERATION_IDS_KEPT:
                    waiting.operation_ids.append(operation_id)
                self.coalesced += 1
                log.debug(
                    "worktree_graveyard_purge_coalesced root=%s operation_id=%s requests=%d",
                    root,
                    operation_id,
                    waiting.requests,
                )
                return False
            self._waiting[key] = _PurgeRequest(
                root=Path(root),
                requested_at=self._clock(),
                operation_ids=[operation_id] if operation_id else [],
            )
            self._ensure_thread()
            self._condition.notify_all()
        log.info(
            "worktree_graveyard_purge_queued root=%s operation_id=%s", root, operation_id
        )
        return True

    def request_sweep(self, project_roots: Sequence[str]) -> None:
        """Purge every Project graveyard a previous daemon left anything in.

        The `.git` checks are filesystem work, so they run on the purger's thread
        rather than on the caller's.
        """
        with self._condition:
            if self._stop.is_set():
                return
            self._sweeps.append(tuple(str(root) for root in project_roots))
            self._ensure_thread()
            self._condition.notify_all()

    # -- observing -----------------------------------------------------------------

    def idle(self) -> bool:
        with self._condition:
            return self._idle_locked()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until nothing is running or waiting; False on timeout. For tests."""
        with self._condition:
            return self._condition.wait_for(self._idle_locked, timeout)

    def status(self) -> dict[str, Any]:
        """What is being purged, what waits, and what the passes so far did."""
        now = self._clock()
        with self._condition:
            return {
                "active": str(self._active) if self._active is not None else None,
                "active_seconds": (
                    round(now - self._active_since, 1) if self._active_since is not None else None
                ),
                "waiting": [str(item.root) for item in self._waiting.values()],
                "sweeps_waiting": len(self._sweeps),
                "sweeping": self._sweeping,
                "requests": self.requests,
                "coalesced": self.coalesced,
                "passes": self.passes,
                "removed": self.removed,
                "failed": self.failed,
                "last_pass": dict(self.last_pass) if self.last_pass is not None else None,
                "stopping": self._stop.is_set(),
            }

    # -- stopping ------------------------------------------------------------------

    def stop(self, timeout: float = 0.0) -> bool:
        """Stop taking requests; finish at most the checkout being deleted.

        Returns whether the thread ended within ``timeout``. A shutdown does not wait
        for it by default: the graveyard is durable, so whatever is left is the next
        daemon's startup sweep, and a restart that waited on a deletion would be a
        restart held hostage by disk speed.
        """
        with self._condition:
            self._stop.set()
            self._waiting.clear()
            self._sweeps.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # -- the worker ----------------------------------------------------------------

    def _idle_locked(self) -> bool:
        return (
            self._active is None and not self._waiting and not self._sweeps and not self._sweeping
        )

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=self._thread_name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # unsupervised-loop-ok: the purger's own OS thread, not an asyncio loop; each
        # pass is guarded below, and a thread that dies is restarted by the next request.
        while True:
            with self._condition:
                with parked("graveyard_purge"):
                    self._condition.wait_for(
                        lambda: self._stop.is_set() or bool(self._waiting or self._sweeps)
                    )
                if self._stop.is_set():
                    return
                sweep = self._sweeps.pop(0) if self._sweeps else None
                job: _PurgeRequest | None = None
                if sweep is not None:
                    self._sweeping = True
                else:
                    key = next(iter(self._waiting))
                    job = self._waiting.pop(key)
                    self._active = job.root
                    self._active_since = self._clock()
            if sweep is not None:
                self._queue_sweep(sweep)
                continue
            assert job is not None
            try:
                self._pass(job)
            finally:
                with self._condition:
                    self._active = None
                    self._active_since = None
                    self._condition.notify_all()

    def _queue_sweep(self, project_roots: tuple[str, ...]) -> None:
        graveyards: list[Path] = []
        try:
            graveyards = project_graveyards(project_roots)
        except Exception:
            log.exception("worktree_graveyard_sweep_failed roots=%d", len(project_roots))
            return
        finally:
            # One acquisition queues the passes and ends the sweep, so no observer can
            # see the purger idle in between.
            with self._condition:
                for root in graveyards:
                    key = _purge_key(root)
                    if key not in self._waiting and not self._stop.is_set():
                        self._waiting[key] = _PurgeRequest(root=root, requested_at=self._clock())
                self._sweeping = False
                self._condition.notify_all()
        log.info(
            "worktree_graveyard_sweep_queued projects=%d graveyards=%d",
            len(project_roots),
            len(graveyards),
        )

    def _pass(self, job: _PurgeRequest) -> None:
        started = self._clock()
        waited = started - job.requested_at
        log.info(
            "worktree_graveyard_purge_started root=%s requests=%d waited_s=%.1f operation_ids=%s",
            job.root,
            job.requests,
            waited,
            ",".join(job.operation_ids) or "-",
        )
        removed = failed = 0
        outcome = "ok"
        try:
            removed, failed = self._purge(job.root, stop=self._stop)
        except Exception:
            outcome = "error"
            log.exception("worktree_graveyard_purge_crashed root=%s", job.root)
        duration = self._clock() - started
        if outcome == "ok" and self._stop.is_set():
            outcome = "stopped"
        with self._condition:
            self.passes += 1
            self.removed += removed
            self.failed += failed
            self.last_pass = {
                "root": str(job.root),
                "requests": job.requests,
                "removed": removed,
                "failed": failed,
                "outcome": outcome,
                "waited_seconds": round(waited, 1),
                "duration_seconds": round(duration, 1),
            }
        log.info(
            "worktree_graveyard_purge_finished root=%s removed=%d failed=%d duration_s=%.1f "
            "outcome=%s",
            job.root,
            removed,
            failed,
            duration,
            outcome,
        )
