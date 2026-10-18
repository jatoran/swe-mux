"""Filesystem change detection for the Git monitor.

A checkout's Git state can only move when a file in its working tree or in its Git
directory moves, so the monitor asks the operating system which checkouts changed
instead of re-reading every attached one on a timer.
Measured 2026-10-12 on the operator's fleet, the timer cost ~7 `git` subprocesses
per attached checkout every five seconds (`git status` alone 1.3s in the largest),
the monitor loop was busy a third of its life, and its median iteration took 9.9s -
for checkouts that had not changed at all.

This module owns only the *detection*: which directories are watched, which change
events matter to which checkout, and which are noise. The monitor owns what a change
causes (`git_monitor.GitMonitor`) and keeps a slow safety sweep, so a watch that
misses an event costs staleness bounded by that sweep rather than correctness.

Four rules shape it:

- **A change is classified without touching the filesystem.** Every event is a path
  string; matching it against a checkout is string work. A `resolve` per event would
  be the per-event filesystem call that stalled the event loop in the first place,
  and a build storm delivers tens of thousands of them. The string work is per
  *folder*, not per file (`classify_batch`): it holds the GIL against the event loop,
  and a worktree purge inside `.git` delivers about 26,000 deletions per checkout.
- **No per-path work runs on the event loop.** Each OS watch lives on its own thread
  (watchfiles' blocking `watch`), classifies its own batches there, and hands the
  loop only the checkouts a batch concerned. `awatch` would build every batch's
  change set on the loop and park its waits on anyio's shared 40-thread limiter.
- **Noise is decided by Git, not guessed.** Each watched checkout's ignored
  directories are read from `git ls-files --others --ignored --directory` and changes
  under them are dropped, so a running build, an `npm ci`, or a nested worktree's
  test run cannot turn into a Git read storm. A short fixed list of directories that
  are never tracked covers the window before that answer arrives.
- **Inside a Git directory only the files that carry state count**: `HEAD`, the
  index, refs, and the in-progress operation markers. Object writes, reflogs, lock
  files and the worktree graveyard are what a commit or a purge *does*, and they are
  followed by a ref or index change that is the signal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from watchfiles import watch as watch_changes

from .parked_threads import parked

log = logging.getLogger(__name__)

#: Directories that are never tracked content in practice. Applied before Git's own
#: ignore answer is known, and always, because a nested build in one of these is the
#: storm the whole filter exists for. Deliberately short: anything a repository
#: might genuinely track (`dist`, `build`, `target`) is left to Git's answer.
ALWAYS_NOISE = frozenset(
    {
        "node_modules",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".next",
        ".turbo",
        ".parcel-cache",
    }
)

#: Top-level entries of a Git directory whose change is a change of state.
GIT_STATE_ENTRIES = frozenset(
    {
        "HEAD",
        "index",
        "packed-refs",
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "ORIG_HEAD",
        "BISECT_LOG",
        "rebase-merge",
        "rebase-apply",
    }
)

#: How long Git's ignore answer is trusted before it is asked again. A `.gitignore`
#: edit re-asks immediately; this bounds what a rule added some other way costs.
IGNORE_RULES_TTL_SECONDS = 15 * 60.0
#: Entries kept from Git's ignore answer. A repository with more distinct ignored
#: paths than this outside ignored directories keeps the rest as plain changes,
#: which the monitor's per-checkout rate limit already bounds.
MAX_IGNORED_ENTRIES = 5000
#: A watch that ended on its own (directory gone, watch limit hit) is not restarted
#: before this, so a vanished directory cannot become a tight respawn loop.
WATCH_FAILURE_COOLDOWN_SECONDS = 60.0
#: Bound on how long shutdown waits for a watch thread to notice its stop event.
#: The Rust side checks it every `step`, so this is generous rather than tight.
WATCH_JOIN_SECONDS = 5.0

_CASE_INSENSITIVE = os.name == "nt"
_SEPARATORS = re.compile(r"[\\/]+")

WatchKind = Literal["tree", "gitdir"]
#: (root key, interest key, kind, ignore rules) - what one watch classifies against.
Candidate = tuple[str, str, WatchKind, "IgnoreRules"]


def normalize_key(path: str) -> str:
    """The comparison form of an absolute path. String-only: never resolves."""
    return os.path.normcase(os.path.normpath(path))


def relative_parts(changed: str, base_key: str) -> list[str] | None:
    """`changed` split into components below `base_key`, or None when outside it.

    `base_key` is `normalize_key` of the base. The components keep their original
    case so Git-directory entry names compare exactly; only the containment test is
    case-folded where the filesystem is.
    """
    normalized = os.path.normpath(changed)
    folded = os.path.normcase(normalized)
    if folded == base_key:
        return []
    prefix = base_key.rstrip("\\/") + os.sep
    if not folded.startswith(prefix):
        return None
    return [part for part in _SEPARATORS.split(normalized[len(prefix) :]) if part]


def git_dir_change_matters(parts: list[str]) -> bool:
    """Whether a change at `parts` inside a Git directory moves repository state.

    A linked worktree's private state lives under the shared directory's
    `worktrees/<name>/`, and is read with the same rules as a primary's.
    """
    if len(parts) >= 3 and parts[0] == "worktrees":
        parts = parts[2:]
    if not parts or parts[-1].endswith(".lock"):
        return False
    if parts[0] == "refs":
        return True
    return parts[0] in GIT_STATE_ENTRIES


def _fold(value: str) -> str:
    return value.lower() if _CASE_INSENSITIVE else value


@dataclass(slots=True, frozen=True)
class IgnoreRules:
    """What Git says is ignored in one checkout, as relative POSIX paths."""

    directories: frozenset[str] = frozenset()
    files: frozenset[str] = frozenset()

    def covers(self, parts: list[str]) -> bool:
        """Whether the path, or any directory containing it, is ignored."""
        if not self.directories and not self.files:
            return False
        folded = [_fold(part) for part in parts]
        prefix = ""
        for part in folded:
            prefix = f"{prefix}{part}/"
            if prefix in self.directories:
                return True
        return "/".join(folded) in self.files


def parse_ignored_listing(output: str, *, limit: int = MAX_IGNORED_ENTRIES) -> IgnoreRules:
    """`git ls-files --others --ignored --exclude-standard --directory` as rules.

    Directory entries end with `/`; everything else is a single ignored file. Git
    quotes unusual paths, and a quoted entry is dropped rather than half-matched:
    missing an ignore costs one rate-limited read, matching a wrong path would hide
    a real change.

    Over `limit`, directories are kept ahead of files, because a directory entry
    silences a whole subtree and a file entry one path. Git lists in path order, so
    cutting in order keeps whatever sorts first: measured 2026-10-13 on a checkout
    listing 142,589 entries, an in-order cut kept 1,373 of its 2,803 ignored
    directories and filled the rest with single files.
    """
    directories: set[str] = set()
    files: list[str] = []
    for line in output.splitlines():
        entry = line.strip()
        if not entry or entry.startswith('"'):
            continue
        if entry.endswith("/"):
            if len(directories) < limit:
                directories.add(_fold(entry))
        elif len(files) < limit:
            files.append(_fold(entry))
    room = max(limit - len(directories), 0)
    return IgnoreRules(frozenset(directories), frozenset(files[:room]))


def tree_change_matters(parts: list[str], rules: IgnoreRules) -> tuple[bool, bool]:
    """(matters, ignore rules changed) for a change inside a working tree."""
    if not parts:
        return False, False
    if parts[0] == ".git":
        return git_dir_change_matters(parts[1:]), False
    if any(part in ALWAYS_NOISE for part in parts):
        return False, False
    rules_changed = parts[-1] == ".gitignore"
    if not rules_changed and rules.covers(parts):
        return False, False
    return True, rules_changed


#: What can change below a directory, for every checkout watching it.
_LIVE = 0
_QUIET_UNLESS_GITIGNORE = 1
_QUIET = 2

if _CASE_INSENSITIVE:

    def _last_separator(path: str) -> int:
        return max(path.rfind("\\"), path.rfind("/"))

    _STRIP = "\\/"
else:

    def _last_separator(path: str) -> int:
        return path.rfind("/")

    _STRIP = "/"


def _strip_key(key: str) -> str:
    return key.rstrip(_STRIP)


def _ancestors(key: str) -> Iterable[str]:
    """`key` and every directory above it, as stripped keys. String-only."""
    probe = key
    cut = 0
    while cut >= 0:
        yield probe
        cut = _last_separator(probe)
        probe = probe[:cut]


def _gitdir_subtree_is_quiet(parts: list[str]) -> bool:
    """Whether no change anywhere below `parts` (inside a Git directory) can matter.

    The subtree form of `git_dir_change_matters`: below a linked worktree's
    `worktrees/<name>/` the first component is what decides, everywhere else the
    first component of the path itself, and only `refs` and the state entries carry
    state at any depth.
    """
    if not parts:
        return False
    if parts[0] == "worktrees":
        if len(parts) < 3:
            return False
        parts = parts[2:]
    return parts[0] != "refs" and parts[0] not in GIT_STATE_ENTRIES


def _covers_directory(rules: IgnoreRules, parts: list[str]) -> bool:
    """Whether Git's ignore answer covers every path below `parts` by a directory."""
    if not rules.directories or not parts:
        return False
    prefix = ""
    for part in parts:
        prefix = f"{prefix}{_fold(part)}/"
        if prefix in rules.directories:
            return True
    return False


@dataclass(slots=True, frozen=True)
class CandidateIndex:
    """One watch's candidates, arranged so a path finds its own without a scan.

    `by_interest` maps an interest (without trailing separators) to the candidates
    watching it, so the candidates containing a path are found by looking up the
    path's own ancestors - a dozen dict reads - instead of testing every candidate.
    `above` holds every directory with an interest strictly below it, which is what
    decides that a directory's subtree can be judged as a whole.
    """

    candidates: tuple[Candidate, ...] = ()
    by_interest: Mapping[str, tuple[Candidate, ...]] = field(default_factory=dict)
    above: frozenset[str] = frozenset()
    #: Per interest, the distinct (kind, rules) a subtree is judged by. Every linked
    #: worktree lists the shared Git directory as an interest, so forty nested
    #: worktrees are forty candidates there and one judgement.
    judges: Mapping[str, tuple[tuple[WatchKind, IgnoreRules | None], ...]] = field(
        default_factory=dict
    )

    @classmethod
    def build(cls, candidates: Iterable[Candidate]) -> CandidateIndex:
        listed = tuple(candidates)
        grouped: dict[str, list[Candidate]] = {}
        judges: dict[str, dict[tuple[WatchKind, IgnoreRules | None], None]] = {}
        above: set[str] = set()
        for candidate in listed:
            _root, interest, kind, rules = candidate
            key = _strip_key(interest)
            grouped.setdefault(key, []).append(candidate)
            # A Git directory's verdict never reads ignore rules.
            judges.setdefault(key, {})[(kind, rules if kind == "tree" else None)] = None
            above.update(list(_ancestors(key))[1:])
        return cls(
            listed,
            {key: tuple(items) for key, items in grouped.items()},
            frozenset(above),
            {key: tuple(found) for key, found in judges.items()},
        )

    def containing(self, folded: str) -> list[Candidate]:
        """The candidates whose interest is `folded` or a directory above it."""
        found: list[Candidate] = []
        for probe in _ancestors(_strip_key(folded)):
            hits = self.by_interest.get(probe)
            if hits:
                found.extend(hits)
        return found


def _parts_below(normalized: str, folded: str, interest: str) -> list[str]:
    """`relative_parts` for a path already known to be inside `interest`."""
    key = _strip_key(interest)
    if _strip_key(folded) == key:
        return []
    return [part for part in _SEPARATORS.split(normalized[len(key) :]) if part]


def _subtree_state(directory: str, index: CandidateIndex) -> int:
    """What can change below `directory`, for every checkout watching it.

    Exact rather than heuristic: a directory is quiet only when every candidate
    containing it would reject every path below it, judged with the same rules
    `classify_batch` applies one path at a time. A deletion storm empties one folder
    at a time, so one judgement here answers every file in that folder.
    """
    normalized = os.path.normpath(directory)
    folded = os.path.normcase(normalized)
    if _strip_key(folded) in index.above:
        return _LIVE
    state = _QUIET
    for interest in _ancestors(_strip_key(folded)):
        judges = index.judges.get(interest)
        if not judges:
            continue
        parts = _parts_below(normalized, folded, interest)
        for kind, rules in judges:
            if kind == "gitdir":
                if not _gitdir_subtree_is_quiet(parts):
                    return _LIVE
            elif parts and parts[0] == ".git":
                if not _gitdir_subtree_is_quiet(parts[1:]):
                    return _LIVE
            elif any(part in ALWAYS_NOISE for part in parts):
                continue
            elif rules is not None and _covers_directory(rules, parts):
                # Ignored, except that an edited `.gitignore` is always a change.
                state = min(state, _QUIET_UNLESS_GITIGNORE)
            else:
                return _LIVE
    return state


def classify_batch(
    paths: Iterable[str], candidates: CandidateIndex | Iterable[Candidate]
) -> dict[str, bool]:
    """Which checkouts a batch of changed paths concerns, and whether its rules moved.

    Pure string work, run on the watch's own thread. A checkout stops being examined
    once it is known to be concerned *and* its rules are known to have moved, so a
    storm inside an ignored directory costs one pass and nothing more.

    Each path's folder is judged once per batch (`_subtree_state`): a folder whose
    subtree no checkout can care about answers every file in it with one dict read.
    That is what a purge or an `npm ci` needs. Measured 2026-10-18 on a watch of the
    primary checkout and forty nested worktrees (81 candidates), a graveyard purge's
    deletions cost 147-184 us of GIL-holding Python per file before and 1.2 us after
    (3.3 us when every path is in a different folder). While a batch is classified the
    event loop's requests convoy behind it (0.5 ms became ~300 ms), so total time is
    what matters: about 150 s of it for a 32-checkout purge before, about 1 s after.
    """
    index = (
        candidates if isinstance(candidates, CandidateIndex) else CandidateIndex.build(candidates)
    )
    verdicts: dict[str, bool] = {}
    #: Per folder in this batch: its subtree state, and (only once a path in it needs
    #: judging one at a time) the candidates containing it.
    folders: dict[str, int] = {}
    containing_by_folder: dict[str, list[Candidate]] = {}
    for path in paths:
        if path[-1:] in _STRIP and len(path) > 1:
            # A directory spelled with its trailing separator is the directory, not
            # something inside it, so it must not be judged by its own subtree.
            path = path.rstrip(_STRIP) or path
        cut = _last_separator(path)
        if cut > 0:
            parent = path[:cut]
            state = folders.get(parent)
            if state is None:
                state = folders[parent] = _subtree_state(parent, index)
            if state == _QUIET:
                continue
            if state == _QUIET_UNLESS_GITIGNORE and path[cut + 1 :] != ".gitignore":
                continue
            normalized = os.path.normpath(path)
            folded = os.path.normcase(normalized)
            containing = containing_by_folder.get(parent)
            if containing is None:
                # A candidate whose interest is the path itself is left out on purpose:
                # a change *at* an interest root concerns nobody
                # (`tree_change_matters([])` and `git_dir_change_matters([])` are false).
                containing = containing_by_folder[parent] = index.containing(
                    os.path.normcase(os.path.normpath(parent))
                )
        else:
            normalized = os.path.normpath(path)
            folded = os.path.normcase(normalized)
            containing = index.containing(folded)
        for root_key, interest, kind, rules in containing:
            if verdicts.get(root_key):
                continue
            parts = _parts_below(normalized, folded, interest)
            if kind == "gitdir":
                matters, rules_changed = git_dir_change_matters(parts), False
            else:
                matters, rules_changed = tree_change_matters(parts, rules)
            if matters:
                verdicts[root_key] = verdicts.get(root_key, False) or rules_changed
    return verdicts


@dataclass(slots=True, frozen=True)
class CheckoutLayout:
    """Where one checkout keeps its tree and its Git state, as absolute paths."""

    toplevel: str
    git_dir: str
    common_dir: str

    @property
    def key(self) -> str:
        return normalize_key(self.toplevel)

    def interests(self) -> tuple[tuple[str, WatchKind], ...]:
        """The directories whose changes concern this checkout, keyed.

        The tree covers a primary checkout's `.git`; a linked worktree's Git
        directory lives under the shared one, which is listed once for both.
        """
        top = self.key
        found: list[tuple[str, WatchKind]] = [(top, "tree")]
        for directory in (self.common_dir, self.git_dir):
            key = normalize_key(directory)
            if within(key, top) or any(within(key, other) for other, _ in found[1:]):
                continue
            found.append((key, "gitdir"))
        return tuple(found)


def within(child: str, parent: str) -> bool:
    """Whether normalized `child` is `parent` or below it. String-only."""
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


@dataclass(slots=True)
class WatchStats:
    batches: int = 0
    paths: int = 0
    relevant_batches: int = 0
    starts: int = 0
    failures: int = 0
    last_failure: str | None = None
    last_failure_at: float | None = None
    ignore_refreshes: int = 0
    ignore_failures: int = 0


@dataclass(slots=True)
class _Watch:
    key: str
    path: str
    #: Replaced wholesale (never mutated) whenever roots or their rules change, so
    #: the watch thread can read it without a lock.
    candidates: CandidateIndex = field(default_factory=CandidateIndex)
    thread: threading.Thread | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    ended: bool = False
    failed_until: float = 0.0

    @property
    def live(self) -> bool:
        return self.thread is not None and not self.ended


@dataclass(slots=True)
class _Root:
    layout: CheckoutLayout
    rules: IgnoreRules = field(default_factory=IgnoreRules)
    rules_read_at: float | None = None
    rules_wanted: bool = True
    rules_task: asyncio.Task[None] | None = None


#: (root key, ignore rules changed) for every checkout a batch concerned.
ChangeCallback = Callable[[str, bool], None]
#: Reads one checkout's ignored-path listing; returns the raw output, or None.
IgnoreReader = Callable[[str], Awaitable[str | None]]


class CheckoutWatches:
    """Recursive OS watches over the checkouts the monitor is following.

    One watch per directory in the minimal cover of every followed checkout's
    interests, so a primary checkout and the linked worktrees nested inside it share
    one OS watch, and a shared Git directory is watched once for all its worktrees.
    Owned by the event loop: every method here runs on it, and the watch threads
    reach back only through `call_soon_threadsafe`.
    """

    def __init__(
        self,
        on_change: ChangeCallback,
        read_ignored: IgnoreReader,
        *,
        max_watches: int = 32,
        debounce_ms: int = 1600,
        step_ms: int = 300,
        rust_timeout_ms: int = 5000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_change = on_change
        self._read_ignored = read_ignored
        self._max_watches = max_watches
        self._debounce_ms = debounce_ms
        self._step_ms = step_ms
        self._rust_timeout_ms = rust_timeout_ms
        self._clock = clock
        self._roots: dict[str, _Root] = {}
        self._watches: dict[str, _Watch] = {}
        self.stats = WatchStats()

    # -- membership ------------------------------------------------------------

    def sync(self, layouts: Mapping[str, CheckoutLayout]) -> set[str]:
        """Follow exactly `layouts` (keyed by root). Returns roots a new watch covers.

        A root that gained a watch may have changed in the gap before it started, so
        the caller decides whether that gap needs a read.
        """
        loop = asyncio.get_running_loop()
        for key in tuple(self._roots):
            if key not in layouts:
                root = self._roots.pop(key)
                if root.rules_task is not None and not root.rules_task.done():
                    root.rules_task.cancel()
        for key, layout in layouts.items():
            current = self._roots.get(key)
            if current is None or current.layout != layout:
                self._roots[key] = _Root(layout)
        desired = self._desired_watch_dirs()
        now = self._clock()
        for key in tuple(self._watches):
            if key not in desired:
                self._stop_watch(self._watches.pop(key))
        fresh: set[str] = set()
        for key in sorted(desired):
            watch = self._watches.get(key)
            if watch is not None and watch.live:
                continue
            if watch is not None and watch.failed_until > now:
                continue
            if sum(1 for item in self._watches.values() if item.live) >= self._max_watches:
                break
            if watch is not None:
                self._stop_watch(watch)
            watch = _Watch(key, desired[key])
            self._watches[key] = watch
            self._start_watch(watch, loop)
            fresh.update(self._roots_interested_in(key))
        self._publish_candidates()
        self._refresh_ignore_rules(now)
        return fresh

    def watched(self, root_key: str) -> bool:
        """Whether every directory this checkout depends on has a live watch."""
        root = self._roots.get(root_key)
        if root is None:
            return False
        return all(
            any(watch.live and within(interest, key) for key, watch in self._watches.items())
            for interest, _kind in root.layout.interests()
        )

    async def stop(self) -> None:
        threads: list[threading.Thread] = []
        for watch in self._watches.values():
            watch.stop.set()
            if watch.thread is not None:
                threads.append(watch.thread)
        tasks = [
            root.rules_task
            for root in self._roots.values()
            if root.rules_task is not None and not root.rules_task.done()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if threads:
            await asyncio.to_thread(_join_all, threads, WATCH_JOIN_SECONDS)
        self._watches.clear()
        self._roots.clear()

    def status(self) -> dict[str, object]:
        now = self._clock()
        return {
            "roots": len(self._roots),
            "watches": sum(1 for watch in self._watches.values() if watch.live),
            "cooling_down": sum(1 for watch in self._watches.values() if watch.failed_until > now),
            "unwatched_roots": sum(1 for key in self._roots if not self.watched(key)),
            "batches": self.stats.batches,
            "paths": self.stats.paths,
            "relevant_batches": self.stats.relevant_batches,
            "starts": self.stats.starts,
            "failures": self.stats.failures,
            "last_failure": self.stats.last_failure,
            "last_failure_at": self.stats.last_failure_at,
            "ignore_refreshes": self.stats.ignore_refreshes,
            "ignore_failures": self.stats.ignore_failures,
        }

    # -- watch bookkeeping -------------------------------------------------------

    def _desired_watch_dirs(self) -> dict[str, str]:
        """Minimal cover of every interest: drop any directory inside another."""
        wanted: dict[str, str] = {}
        for root in self._roots.values():
            for key, _kind in root.layout.interests():
                wanted.setdefault(key, _display_path(root.layout, key))
        cover: dict[str, str] = {}
        for key in sorted(wanted, key=len):
            if any(within(key, kept) for kept in cover):
                continue
            cover[key] = wanted[key]
        return cover

    def _roots_interested_in(self, watch_key: str) -> set[str]:
        return {
            key
            for key, root in self._roots.items()
            if any(within(interest, watch_key) for interest, _ in root.layout.interests())
        }

    def _publish_candidates(self) -> None:
        for watch_key, watch in self._watches.items():
            watch.candidates = CandidateIndex.build(
                (root_key, interest, kind, root.rules)
                for root_key, root in self._roots.items()
                for interest, kind in root.layout.interests()
                if within(interest, watch_key)
            )

    def _start_watch(self, watch: _Watch, loop: asyncio.AbstractEventLoop) -> None:
        watch.thread = threading.Thread(
            target=self._run_watch,
            args=(watch, loop),
            name=f"git-watch:{os.path.basename(watch.key) or watch.key}",
            daemon=True,
        )
        self.stats.starts += 1
        watch.thread.start()

    @staticmethod
    def _stop_watch(watch: _Watch) -> None:
        watch.stop.set()

    def _run_watch(self, watch: _Watch, loop: asyncio.AbstractEventLoop) -> None:
        """Body of one watch thread: wait on the OS, classify here, hand over verdicts."""
        error: BaseException | None = None
        try:
            # Checked here because `ignore_permission_denied` - which a tree holding
            # one unreadable directory needs - also makes a *missing* root watch
            # nothing in silence (measured, watchfiles 1.2), and a watch that looks
            # live while reporting nothing would take its checkout off polling.
            if not os.path.isdir(watch.path):
                raise FileNotFoundError(f"watch root is not a directory: {watch.path}")
            batches = iter(
                watch_changes(
                    watch.path,
                    watch_filter=None,
                    debounce=self._debounce_ms,
                    step=self._step_ms,
                    stop_event=watch.stop,
                    rust_timeout=self._rust_timeout_ms,
                    raise_interrupt=False,
                    recursive=True,
                    ignore_permission_denied=True,
                )
            )
            # unsupervised-loop-ok: the watch thread's own body, an OS thread rather
            # than asyncio; the enclosing `except` ends the watch and `_record_end`
            # degrades its checkout to polling.
            while True:
                # The wait inside watchfiles is Rust with a Python generator frame on
                # top, which a stall dump cannot tell from work; the thread says so.
                with parked("git_watch"):
                    changes = next(batches, None)
                if changes is None:
                    break
                paths = [path for _change, path in changes]
                verdicts = classify_batch(paths, watch.candidates)
                if not _post(loop, self._record_batch, len(paths), verdicts):
                    return
        except Exception as exc:  # noqa: BLE001 - a lost watch degrades to polling
            error = exc
        _post(loop, self._record_end, watch, error)

    def _record_batch(self, path_count: int, verdicts: dict[str, bool]) -> None:
        self.stats.batches += 1
        self.stats.paths += path_count
        if not verdicts:
            return
        self.stats.relevant_batches += 1
        rules_moved = False
        for root_key, rules_changed in verdicts.items():
            root = self._roots.get(root_key)
            if root is None:
                continue
            if rules_changed:
                root.rules_wanted = True
                rules_moved = True
            self._on_change(root_key, rules_changed)
        if rules_moved:
            self._refresh_ignore_rules(self._clock())

    def _record_end(self, watch: _Watch, error: BaseException | None) -> None:
        watch.ended = True
        if watch.stop.is_set():
            return
        watch.failed_until = self._clock() + WATCH_FAILURE_COOLDOWN_SECONDS
        if error is None:
            log.info("git_watch_ended path=%s", watch.path)
            return
        self.stats.failures += 1
        self.stats.last_failure = f"{type(error).__name__}: {error}"[:300]
        self.stats.last_failure_at = time.time()
        log.warning(
            "git_watch_failed path=%s error=%s cooldown_s=%.0f",
            watch.path,
            self.stats.last_failure,
            WATCH_FAILURE_COOLDOWN_SECONDS,
        )

    # -- ignore rules -------------------------------------------------------------

    def _refresh_ignore_rules(self, now: float) -> None:
        for key, root in self._roots.items():
            if root.rules_task is not None and not root.rules_task.done():
                continue
            stale = (
                root.rules_read_at is None or now - root.rules_read_at >= IGNORE_RULES_TTL_SECONDS
            )
            if not (root.rules_wanted or stale):
                continue
            root.rules_wanted = False
            root.rules_task = asyncio.create_task(
                self._read_rules(key, root), name=f"git-ignore-rules:{key}"
            )

    async def _read_rules(self, key: str, root: _Root) -> None:
        output = await self._read_ignored(root.layout.toplevel)
        root.rules_read_at = self._clock()
        if output is None:
            self.stats.ignore_failures += 1
            return
        # Off the loop: a listing can run to six figures of lines (see
        # `parse_ignored_listing`), and every terminal shares the loop.
        root.rules = await asyncio.to_thread(parse_ignored_listing, output)
        self.stats.ignore_refreshes += 1
        if self._roots.get(key) is root:
            self._publish_candidates()
        log.debug(
            "git_ignore_rules_read root=%s directories=%d files=%d",
            key,
            len(root.rules.directories),
            len(root.rules.files),
        )


def _post(loop: asyncio.AbstractEventLoop, callback: Callable[..., None], *args: object) -> bool:
    """Run `callback(*args)` on the loop; False once the loop is gone (shutdown)."""
    try:
        loop.call_soon_threadsafe(callback, *args)
    except RuntimeError:
        return False
    return True


def _join_all(threads: list[threading.Thread], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))


def _display_path(layout: CheckoutLayout, key: str) -> str:
    for candidate in (layout.toplevel, layout.common_dir, layout.git_dir):
        if normalize_key(candidate) == key:
            return candidate
    return key
