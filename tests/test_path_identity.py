"""Path comparison must be exact, and must not block on somebody else's server.

The reason this file exists is a measurement rather than a theory. A single
``os.path.exists`` on ``//wsl.localhost/<distro>`` with the distro stopped took
**80.1 seconds** on the development host, and `agent_environment` was making that
call once per key of Claude's project map inside a request. The tests below hold
the two halves of the fix in place: a comparison answers without touching the
filesystem whenever the strings already settle it, and a filesystem call that is
made anyway is bounded and its failure remembered per provider.

Nothing here probes a real unreachable location. Doing so would make the suite's
runtime a property of whichever machine ran it - fast where the provider fails
quickly, minutes where it does not - and it cannot be arranged on a runner at
all. The prober is injected instead, and blocks on an `Event` the test releases,
so no thread outlives the test that started it.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from swe_mux import path_identity
from swe_mux.host_platform import IS_WINDOWS
from swe_mux.path_identity import (
    UnreachableLocation,
    is_within,
    lexical_key,
    reset_probe_cache,
    same_path,
    same_path_lexically,
)


@pytest.fixture(autouse=True)
def _clean_probe_state() -> None:
    """Provider reachability is process-wide state, so no test may inherit it."""
    reset_probe_cache()


def _unreachable_location() -> str:
    """A path on a provider that does not exist, spelled the way the host would.

    Never touched by any test - only handed to an injected prober - so the string
    only has to be syntactically a foreign provider, not a real one.
    """
    return r"\\no-such-host\no-such-share\project" if IS_WINDOWS else "/mnt/no-such-mount/project"


class _BlockingProber:
    """Stands in for a filesystem whose provider never answers.

    Calls against ``blocked`` wait until the test releases them, which is what an
    unreachable UNC path does for tens of seconds. Everything else is answered
    immediately by the real implementation, so a test can watch one dead provider
    without pretending the whole filesystem is dead.
    """

    def __init__(self, blocked: str) -> None:
        self.blocked = os.path.normcase(os.path.normpath(blocked))
        self.released = threading.Event()
        self.calls: list[str] = []

    def _is_blocked(self, path: str | os.PathLike[str]) -> bool:
        text = os.path.normcase(os.path.normpath(os.fspath(path)))
        return text == self.blocked or text.startswith(self.blocked + os.sep)

    def stat(self, path: str | os.PathLike[str]) -> os.stat_result:
        self.calls.append(os.fspath(path))
        if self._is_blocked(path):
            self.released.wait(30)
            raise OSError("the provider finally gave up")
        return os.stat(path)

    def blocked_calls(self) -> list[str]:
        return [call for call in self.calls if self._is_blocked(call)]


@pytest.fixture
def blocking_prober(monkeypatch: pytest.MonkeyPatch) -> Iterator[_BlockingProber]:
    prober = _BlockingProber(_unreachable_location())
    monkeypatch.setattr(path_identity, "PROBE_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(path_identity, "PROBE_BLOCK_SECONDS", 30.0)
    monkeypatch.setattr(path_identity, "_stat", prober.stat)
    try:
        yield prober
    finally:
        # Release the abandoned watchdog thread rather than leaving it parked:
        # a thread that outlives its test reports its failure against whichever
        # test the collector happens to interrupt.
        prober.released.set()


def test_identical_spellings_are_settled_without_asking_the_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common comparison is a directory against its own recorded spelling.

    That pair is the same path by construction, so reaching the filesystem for it
    is pure cost - and the cost is unbounded when the recorded spelling names a
    provider that is not there.
    """
    target = tmp_path / "repo"
    target.mkdir()

    def refuse(path: str | os.PathLike[str]) -> os.stat_result:
        raise AssertionError("same_path reached the filesystem for two equal spellings")

    monkeypatch.setattr(path_identity, "_stat", refuse)

    assert same_path(target, str(target))
    # normpath, not a string compare: a redundant component and the other
    # separator are the same spelling.
    assert same_path(target, os.path.join(str(tmp_path), ".", "repo"))
    assert same_path(target, str(target).replace(os.sep, "/"))


def test_lexical_comparison_never_folds_case(tmp_path: Path) -> None:
    """Equal keys must be *sufficient* for sameness, never merely likely.

    Folding case here would need `paths_are_case_insensitive`, whose honest
    answer comes from probing the directory. Guessing it from the platform is how
    two genuinely different directories on a case-sensitive volume come to
    compare equal, which is the failure this module exists to prevent.
    """
    assert not same_path_lexically(tmp_path / "Repo", tmp_path / "repo")
    assert lexical_key(tmp_path / "Repo") != lexical_key(tmp_path / "repo")


def test_two_names_for_one_file_still_get_the_exact_answer(tmp_path: Path) -> None:
    """The lexical shortcut may only add answers, never remove one.

    Two spellings that share no components can still be one filesystem object,
    and only the filesystem knows. The fast path returning False must therefore
    fall through to the stat rather than settle it.

    A hard link rather than a symlink because it needs no privilege anywhere: a
    directory symlink on Windows is elevation-gated, so a symlink here would skip
    on the host this defect was found on and prove nothing there.
    """
    real = tmp_path / "real.txt"
    real.write_text("x", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    try:
        os.link(real, alias)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - filesystem dependent
        pytest.skip(f"this filesystem cannot create a hard link: {exc}")

    assert not same_path_lexically(alias, real)
    assert same_path(alias, real)


def test_a_symlinked_directory_still_gets_the_exact_answer(tmp_path: Path) -> None:
    """The same guarantee for containment, which resolves rather than stats."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "inner").mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - privilege dependent
        pytest.skip(f"this host cannot create a directory symlink: {exc}")

    assert not same_path_lexically(link, real)
    assert same_path(link, real)
    assert is_within(link / "inner", real)


def test_an_unreachable_provider_costs_the_deadline_once_and_nothing_after(
    tmp_path: Path, blocking_prober: _BlockingProber
) -> None:
    """One dead provider must cost a bounded amount once, not per comparison.

    This is the shape of the defect: the caller that found it made this
    comparison 183 times in one request, and the real measured cost of the single
    blocking call was 80.1 seconds.
    """
    deadline = path_identity.PROBE_DEADLINE_SECONDS
    unreachable = _unreachable_location()
    live = tmp_path / "repo"
    live.mkdir()

    started = time.monotonic()
    assert not same_path(unreachable, live)
    first = time.monotonic() - started
    # Generous against a loaded worker, and still two orders below the
    # unbounded call it replaces.
    assert first < deadline * 20

    started = time.monotonic()
    for _ in range(200):
        assert not same_path(unreachable, live)
    repeated = time.monotonic() - started
    # The negative result is cached per provider, so 200 further comparisons
    # cost less than one deadline between them.
    assert repeated < deadline
    # Exactly one call was ever handed to the filesystem for that provider.
    assert len(blocking_prober.blocked_calls()) == 1


def test_a_blocked_provider_degrades_to_a_lexical_answer_not_a_wrong_one(
    tmp_path: Path, blocking_prober: _BlockingProber
) -> None:
    """Refusing to probe may only lose precision, never invent a match."""
    unreachable = _unreachable_location()
    live = tmp_path / "repo"
    live.mkdir()
    assert not same_path(unreachable, live)  # blocks the provider

    # Still exact where the strings settle it.
    assert same_path(unreachable, unreachable)
    assert same_path(unreachable, unreachable.replace(os.sep, "/") if IS_WINDOWS else unreachable)
    # And still False for a genuinely different path on the same dead provider.
    assert not same_path(unreachable, os.path.join(unreachable, "child"))
    assert is_within(os.path.join(unreachable, "child"), unreachable)


def test_a_provider_that_answers_quickly_is_not_run_through_a_watchdog_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thread per stat would be the more expensive mistake on a live disk.

    The watchdog exists for a provider that has not proved itself. Once one has
    returned inside the deadline it is called inline, so the ordinary local
    comparison pays a set lookup rather than a thread.
    """
    left = tmp_path / "one"
    right = tmp_path / "two"
    left.mkdir()
    right.mkdir()
    watched: list[int] = []
    real = path_identity._call_with_deadline

    def counting(call: object) -> object:
        watched.append(1)
        return real(call)  # type: ignore[arg-type]

    monkeypatch.setattr(path_identity, "_call_with_deadline", counting)

    assert not same_path(left, right)
    assert sum(watched) == 1
    for _ in range(50):
        assert not same_path(left, right)
    assert sum(watched) == 1
    # "No such file" is an answer too, and comparing against a recorded directory
    # that has since been deleted is routine. A provider that returns it quickly
    # has proved itself just as well as one that returns a stat.
    for _ in range(50):
        assert not same_path(tmp_path / "deleted", right)
    assert sum(watched) == 1


def test_a_dead_share_does_not_silence_its_live_siblings() -> None:
    """The negative result is remembered per provider, and a share is a provider.

    One unreachable export under ``/mnt`` - or one dead share on a file server
    that is otherwise up - must not stop the others being compared exactly.
    """
    if IS_WINDOWS:
        dead = r"\\fileserver\dead\project"
        live = r"\\fileserver\live\project"
        other_host = r"\\otherserver\dead\project"
    else:
        dead = "/mnt/dead/project"
        live = "/mnt/live/project"
        other_host = "/net/dead/project"
    assert path_identity._provider_of(dead) != path_identity._provider_of(live)
    assert path_identity._provider_of(dead) != path_identity._provider_of(other_host)
    assert path_identity._provider_of(dead) == path_identity._provider_of(
        os.path.join(dead, "deeper", "still")
    )


def test_an_abandoned_probe_raises_an_oserror_callers_already_handle() -> None:
    """`UnreachableLocation` is an OSError on purpose.

    Every caller here already had a branch for a path it could not stat, so an
    unreachable provider takes the one written for a deleted directory instead of
    raising into code that never expected a comparison to fail.
    """
    assert issubclass(UnreachableLocation, OSError)
