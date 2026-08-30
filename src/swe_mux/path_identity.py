"""Is this the same file, and is this inside that - answered per filesystem.

The shipped answer everywhere was ``str(path.resolve()).casefold()``. On Windows
that is right, because NTFS is case-insensitive and two spellings of one directory
really are one directory. On Linux it is *wrong in the dangerous direction*:
``~/Repo`` and ``~/repo`` are two different directories, and folding them makes a
session, a Project, a transcript, or a worktree containment check silently accept
one for the other. A comparison that over-matches cannot be detected downstream -
everything simply behaves as though the wrong directory were the right one.

Three rules, in order of strength:

1. **Ask the filesystem.** When both paths exist, ``os.path.samefile`` compares
   device and inode (st_dev/st_ino, and the file-id equivalent on Windows). That
   answer is exact and free of every guess below it: symlinks, junctions,
   hardlinks, a UNC path against a mapped drive, a bind mount, and a
   case-sensitive directory on an otherwise case-insensitive volume all resolve
   correctly because none of them are string questions.

2. **Normalize when a path does not exist yet.** Plenty of callers compare a
   recorded path against a live one, and the recorded one may be gone. Then the
   only available answer is textual, and it must at least apply the right case
   rule and the right Unicode form for this host.

3. **Never fold case on a case-sensitive filesystem.** The static per-platform
   default is refined by an actual probe of the directory in question where one
   is available, because both of the interesting hosts can be configured against
   type - a per-directory case-sensitive flag on NTFS, a case-sensitive APFS
   volume on macOS.

Unicode matters on macOS specifically: HFS+ and APFS decompose filenames, so a
path read back from the filesystem can be NFD while the same name typed by a user
or stored in a config file is NFC. They are the same file and compare unequal as
strings, so both sides are normalized to NFC before any textual comparison.

Asking the filesystem has a cost the three rules above do not mention, and it is
not the cost anyone expects from a *comparison*. A path is a string that names a
provider, and a provider can be unreachable: a mapped drive whose server is off,
an SMB share on a laptop that left the building, an automounted NFS export, a
`\\\\wsl.localhost\\<distro>` path whose distro is stopped. Windows does not fail
those quickly - it retries. A single ``os.path.exists`` on a stopped distro's UNC
path was measured on the development host at **80.1 seconds**, and the caller
that found it (`agent_environment`, comparing a live directory against every key
of Claude's project map) paid it inside a request. Nothing in the caller's code
suggests that a comparison can block for over a minute, which is exactly why it
did.

So two more rules sit under the three above:

4. **Answer without a syscall when the answer is already certain.** Two paths
   whose lexical normalizations are identical are the same path by construction,
   and that covers nearly every real comparison - a recorded directory against
   the directory it was recorded from. `same_path_lexically` is that answer on
   its own, for callers that must not touch the filesystem at all.

5. **Bound every syscall, and remember an unreachable provider.** Filesystem
   access here runs through `_probe`, which times each call, gives a provider it
   has not seen return quickly a hard deadline, and caches a negative result per
   provider so the next caller pays nothing instead of the deadline. An
   abandoned probe raises `UnreachableLocation`, which is an ``OSError`` - so the
   fallbacks that were already written for a path that cannot be stat'ed handle
   it with no new branches, and the answer degrades from exact to lexical rather
   than becoming wrong.
"""

from __future__ import annotations

import functools
import os
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path, PurePath

from .host_platform import IS_LINUX, IS_MACOS, IS_WINDOWS

__all__ = [
    "UnreachableLocation",
    "canonical_path",
    "identity_key",
    "is_within",
    "lexical_key",
    "paths_are_case_insensitive",
    "reset_probe_cache",
    "same_path",
    "same_path_lexically",
]


#: How long one filesystem call may take before the location it points at is
#: treated as unreachable rather than merely slow. A local stat is microseconds
#: and a warm network stat is single-digit milliseconds, so this is three orders
#: of magnitude of headroom - and two orders below the tens of seconds an
#: unreachable provider actually costs.
PROBE_DEADLINE_SECONDS = 1.0

#: How long an unreachable location is answered from the negative cache before it
#: is probed again. A mount that comes back is picked up within this window; until
#: it does, every caller pays nothing rather than the deadline.
PROBE_BLOCK_SECONDS = 60.0


class UnreachableLocation(OSError):
    """A filesystem probe was abandoned, or its provider is known to be blocked.

    Deliberately an ``OSError``. Every caller in this module already had to
    handle a path it could not stat, so an unreachable provider takes the branch
    that was written for a deleted directory: the comparison falls back to its
    lexical answer instead of raising into code that never expected it.
    """


_PROBE_LOCK = threading.Lock()
#: Providers that returned inside the deadline and may be called inline.
_PROBE_TRUSTED: set[str] = set()
#: Providers that have ever been slow. Never trusted again for the life of the
#: process, so a mount that dies cannot cost the full block a second time.
_PROBE_SUSPECT: set[str] = set()
#: Providers whose probes are refused outright, until this monotonic deadline.
_PROBE_BLOCKED_UNTIL: dict[str, float] = {}


def reset_probe_cache() -> None:
    """Forget what is known about provider reachability and case sensitivity.

    For tests, and for a caller that knows a mount came back and does not want to
    wait out `PROBE_BLOCK_SECONDS`. Everything it clears is an optimization, so
    clearing it can only cost time.
    """
    with _PROBE_LOCK:
        _PROBE_TRUSTED.clear()
        _PROBE_SUSPECT.clear()
        _PROBE_BLOCKED_UNTIL.clear()
    _probe_case_sensitivity.cache_clear()


def _provider_of(path: str | os.PathLike[str]) -> str:
    """The provider that answers this path's filesystem calls.

    Blocking is a property of the provider, not of the file: one stopped distro,
    one dead share, one disconnected mapped drive. On Windows ``splitdrive``
    gives exactly the right granularity - ``\\\\server\\share`` for a UNC path and
    ``D:`` for a local one - so a dead share does not silence its live siblings
    on the same server.

    POSIX has no drive, so the first two components stand in for the mount point,
    which puts ``/mnt/nfs`` and ``/mnt/other`` in different buckets and is where
    an automounted export normally sits. That is a heuristic and it is allowed to
    be: getting the grouping wrong only changes *which* neighbours share a
    negative result, and a negative result only ever degrades an exact answer to
    a lexical one. It can never make a comparison wrong.

    Purely lexical, which is the one property it may not lose: deciding whether a
    location is reachable must not itself need the location.
    """
    try:
        text = os.path.normpath(os.path.expanduser(os.fspath(path)))
    except (OSError, ValueError, TypeError):
        return ""
    drive, _ = os.path.splitdrive(text)
    if drive:
        return os.path.normcase(drive)
    if not os.path.isabs(text):
        # Whatever the process is currently sitting on, and one bucket rather
        # than one per spelling - otherwise every distinct relative path would be
        # a provider nobody has heard of and would earn its own watchdog.
        return ""
    parts = PurePath(text).parts
    return str(PurePath(*parts[:3])) if len(parts) > 3 else text


def _probe[T](paths: tuple[str | os.PathLike[str], ...], call: Callable[[], T]) -> T:
    """Run one filesystem call, bounded by a deadline and remembered per provider.

    A provider that has already returned quickly is called inline - the ordinary
    case, where adding a thread per stat would be the more expensive mistake. Its
    time is still measured, so a mount that dies is demoted after one slow call
    and never gets an inline call again.

    A provider that has not proved itself runs under a watchdog thread and is
    abandoned at the deadline. The thread stays stuck in the syscall until the OS
    lets go, which is unavoidable - the call cannot be cancelled - but it is
    bounded: the provider is blocked immediately afterwards, so a second thread
    is not started for it until the block expires.
    """
    providers = tuple(dict.fromkeys(_provider_of(path) for path in paths))
    now = time.monotonic()
    with _PROBE_LOCK:
        for provider in providers:
            until = _PROBE_BLOCKED_UNTIL.get(provider)
            if until is None:
                continue
            if until > now:
                raise UnreachableLocation(f"{provider} did not answer within the probe deadline")
            del _PROBE_BLOCKED_UNTIL[provider]
        watched = [item for item in providers if item not in _PROBE_TRUSTED]

    if not watched:
        started = time.monotonic()
        try:
            return call()
        finally:
            if time.monotonic() - started >= PROBE_DEADLINE_SECONDS:
                _demote(providers)

    try:
        value = _call_with_deadline(call)
    except UnreachableLocation:
        _demote(watched, block=True)
        raise
    except Exception:
        # "No such file" is an answer, and a fast one. Trusting the provider here
        # matters more than it looks: comparing against a recorded directory that
        # has been deleted is routine, and without this every one of those would
        # start a watchdog thread forever.
        _trust(watched)
        raise
    _trust(watched)
    return value


def _trust(providers: Sequence[str]) -> None:
    with _PROBE_LOCK:
        _PROBE_TRUSTED.update(item for item in providers if item not in _PROBE_SUSPECT)


def _demote(providers: Sequence[str], *, block: bool = False) -> None:
    deadline = time.monotonic() + PROBE_BLOCK_SECONDS
    with _PROBE_LOCK:
        for provider in providers:
            _PROBE_SUSPECT.add(provider)
            _PROBE_TRUSTED.discard(provider)
            if block:
                _PROBE_BLOCKED_UNTIL[provider] = deadline


def _stat(path: str | os.PathLike[str]) -> os.stat_result:
    """The filesystem call `same_path` makes against a path it was handed.

    A named indirection rather than ``os.stat`` inline, so a test can make one
    location unreachable without replacing ``os.stat`` for the whole process -
    which would also intercept pytest's own bookkeeping and the temporary
    directory it is tearing down.
    """
    return os.stat(path)


def _call_with_deadline[T](call: Callable[[], T]) -> T:
    value: list[T] = []
    failure: list[BaseException] = []
    finished = threading.Event()

    def run() -> None:
        try:
            value.append(call())
        except BaseException as exc:  # noqa: BLE001 - relayed to the calling thread
            failure.append(exc)
        finally:
            finished.set()

    threading.Thread(target=run, name="path-identity-probe", daemon=True).start()
    if not finished.wait(PROBE_DEADLINE_SECONDS):
        raise UnreachableLocation("filesystem probe abandoned at the deadline")
    if failure:
        raise failure[0]
    return value[0]


def lexical_key(path: str | os.PathLike[str]) -> str:
    """A normalized spelling derived with no filesystem access whatsoever.

    Unlike `identity_key` this never folds case and never resolves. Folding would
    need `paths_are_case_insensitive`, whose honest answer comes from probing the
    directory - and guessing from the platform instead would report a
    case-sensitive directory on NTFS, or a case-sensitive APFS volume, as a
    match. Equality here is therefore *sufficient* for `same_path` and never
    necessary: two paths with the same lexical key are the same path, and two
    with different keys still might be.

    NFC is applied only on macOS, where the filesystem itself normalizes, so two
    spellings really are one file. On Linux and Windows they are two files and
    normalizing would over-match in the direction this module exists to avoid.
    """
    text = os.path.normpath(os.path.expanduser(os.fspath(path)))
    return unicodedata.normalize("NFC", text) if IS_MACOS else text


def same_path_lexically(
    left: str | os.PathLike[str] | None, right: str | os.PathLike[str] | None
) -> bool:
    """Whether two paths are the same one, judged on the strings alone.

    True is exact. False means "not provably the same", which is the answer a
    caller wants when the alternative is stat'ing a path it did not choose - a
    recorded directory on a drive that may not be attached, a share on a machine
    that may be off. `same_path` starts here and escalates; a caller that must
    not touch the filesystem at all stops here.
    """
    if not left or not right:
        return False
    try:
        return lexical_key(left) == lexical_key(right)
    except (OSError, ValueError, TypeError):
        return False


def paths_are_case_insensitive(near: str | os.PathLike[str] | None = None) -> bool:
    """Whether path comparison on this host (or this directory) should fold case.

    ``near`` lets a caller ask about a specific location rather than the platform
    in general, which is what makes a case-sensitive directory on NTFS or a
    case-sensitive APFS volume answerable at all. Without it the platform default
    is used: insensitive on Windows and macOS, sensitive on Linux.
    """
    if near is not None:
        probed = _probe_case_sensitivity(str(near))
        if probed is not None:
            return probed
    if IS_LINUX:
        return False
    return IS_WINDOWS or IS_MACOS


@functools.lru_cache(maxsize=512)
def _probe_case_sensitivity(directory: str) -> bool | None:
    """True when this directory is case-*insensitive*, None when it cannot be told.

    Probing is read-only: it asks whether the same directory, spelled in a
    different case, resolves to the same filesystem object. Creating a temporary
    file to test would be more definitive and is deliberately not done - this runs
    on paths mux does not own, including read-only ones.

    Up to four filesystem calls, so the whole body runs under one `_probe`: an
    unreachable provider raises `UnreachableLocation` out of the first of them and
    the answer becomes "cannot be told", which is what the platform default is
    for.
    """
    try:
        return _probe((directory,), lambda: _case_sensitivity_syscalls(directory))
    except (OSError, ValueError):
        return None


def _case_sensitivity_syscalls(directory: str) -> bool | None:
    path = Path(directory)
    if not path.exists():
        path = path.parent
    if not path.exists():
        return None
    name = path.name
    if not name or name.casefold() == name.upper().casefold() and not name.isalpha():
        # Nothing to flip the case of, so the probe cannot answer.
        return None
    flipped = path.with_name(name.swapcase())
    if flipped == path:
        return None
    return flipped.exists() and os.path.samefile(path, flipped)


def canonical_path(path: str | os.PathLike[str]) -> Path:
    """The most resolved form available, without failing on a path that is gone.

    ``strict=False`` keeps this usable for recorded paths whose target has been
    deleted, which is the ordinary case when comparing history against the live
    filesystem. ``resolve`` is a filesystem call and not a string operation - it
    asks the provider for the final name - so it is bounded like every other one
    here, and an unreachable provider yields the unresolved path.
    """
    try:
        return _probe((path,), lambda: Path(path).expanduser().resolve(strict=False))
    except (OSError, ValueError, RuntimeError):
        return Path(path)


def identity_key(path: str | os.PathLike[str]) -> str:
    """A string two equal paths share on this host, for dict keys and sets.

    Use `same_path` when comparing exactly two paths - it can ask the filesystem
    and is therefore strictly more accurate. This exists for the cases that need a
    hashable key (grouping, dedupe, an index), where a pairwise syscall is not an
    option.
    """
    resolved = canonical_path(path)
    text = unicodedata.normalize("NFC", str(resolved))
    if paths_are_case_insensitive(resolved):
        # normcase also flips separators on Windows, which is wanted: a recorded
        # forward-slash path and a live backslash one are the same path there.
        return os.path.normcase(text)
    return text


def same_path(left: str | os.PathLike[str] | None, right: str | os.PathLike[str] | None) -> bool:
    """Whether two paths name the same filesystem object.

    Three tiers, cheapest first. Equal lexical keys settle it with no syscall at
    all, which is what nearly every real pair is - a directory compared against
    its own recorded spelling. Otherwise each side is stat'ed, and the two
    results compared exactly as ``os.path.samefile`` would. When the filesystem
    cannot answer - a deleted directory, an unreachable provider - the normalized
    keys are compared, which is the only answer left.

    The two sides are stat'ed *separately* rather than through ``samefile``, and
    that is the point rather than an implementation detail. One call covering
    both paths would block on either of them with no way to tell which, so the
    negative result would have to be recorded against both providers - and a
    comparison against one dead share would stop the live disk on the other side
    from ever being asked again. Two calls cost one extra stat and attribute
    correctly.
    """
    if not left or not right:
        return False
    if same_path_lexically(left, right):
        return True
    try:
        # Exact: device + inode. Sees through symlinks, junctions, mapped
        # drives, bind mounts, and per-directory case sensitivity at once.
        left_stat = _probe((left,), lambda: _stat(left))
        right_stat = _probe((right,), lambda: _stat(right))
    except (OSError, ValueError):
        pass
    else:
        return os.path.samestat(left_stat, right_stat)
    try:
        return identity_key(left) == identity_key(right)
    except (OSError, ValueError):
        return False


def is_within(
    child: str | os.PathLike[str] | None, parent: str | os.PathLike[str] | None
) -> bool:
    """Whether ``child`` is ``parent`` or sits underneath it.

    Compared component-wise rather than by string prefix. A prefix test reports
    ``/home/user/project-old`` as inside ``/home/user/project``, which for a
    worktree containment check or a Project boundary is a containment failure that
    silently admits a sibling directory.
    """
    if not child or not parent:
        return False
    child_path = canonical_path(child)
    parent_path = canonical_path(parent)
    if same_path(child_path, parent_path):
        return True
    fold = paths_are_case_insensitive(parent_path)
    child_parts = _comparable_parts(child_path, fold)
    parent_parts = _comparable_parts(parent_path, fold)
    if len(parent_parts) > len(child_parts):
        return False
    return child_parts[: len(parent_parts)] == parent_parts


def _comparable_parts(path: PurePath, fold: bool) -> tuple[str, ...]:
    parts = tuple(unicodedata.normalize("NFC", part) for part in path.parts)
    return tuple(os.path.normcase(part) for part in parts) if fold else parts
