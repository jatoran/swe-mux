"""Change detection for the Git monitor (`git_watch.py`).

The classification half is pure string work and is tested exhaustively with
host-shaped paths. The watch half is tested against a real repository on the real
OS notification backend, because the two properties that matter most - a change
is noticed, and the monitor's own reads are not mistaken for one - are properties
of that backend and nothing a fake can establish.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from swe_mux import git_watch
from swe_mux.git_watch import (
    Candidate,
    CheckoutLayout,
    CheckoutWatches,
    IgnoreRules,
    classify_batch,
    git_dir_change_matters,
    normalize_key,
    parse_ignored_listing,
    relative_parts,
    tree_change_matters,
)

from .support.settle import until

ROOT = os.path.abspath(os.sep.join(["", "work", "repo"]))


def _p(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


# --- classification -----------------------------------------------------------


def test_relative_parts_splits_below_the_base_and_refuses_outside_it() -> None:
    base = normalize_key(ROOT)
    assert relative_parts(_p("src", "app.py"), base) == ["src", "app.py"]
    assert relative_parts(ROOT, base) == []
    # A sibling that merely shares a prefix is outside, not inside.
    assert relative_parts(ROOT + "-other" + os.sep + "x", base) is None


@pytest.mark.skipif(os.name != "nt", reason="case folding is a Windows path rule")
def test_relative_parts_folds_case_for_containment_and_keeps_it_for_names() -> None:
    base = normalize_key(ROOT)
    assert relative_parts(ROOT.upper() + "\\.git\\HEAD", base) == [".git", "HEAD"]


@pytest.mark.parametrize(
    ("parts", "matters"),
    [
        (["HEAD"], True),
        (["index"], True),
        (["packed-refs"], True),
        (["refs", "heads", "feature"], True),
        (["refs", "remotes", "origin", "main"], True),
        (["MERGE_HEAD"], True),
        (["rebase-merge", "done"], True),
        # What a commit or a purge *does*; the ref or index change that follows is
        # the signal, so none of these may trigger a read on its own.
        (["objects", "ab", "cdef"], False),
        (["logs", "HEAD"], False),
        (["index.lock"], False),
        (["refs", "heads", "feature.lock"], False),
        (["swe-mux-graveyard", "wt", "file.txt"], False),
        (["COMMIT_EDITMSG"], False),
        (["FETCH_HEAD"], False),
        # A linked worktree's private state, read with the primary's rules.
        (["worktrees", "wt-a", "HEAD"], True),
        (["worktrees", "wt-a", "index"], True),
        (["worktrees", "wt-a", "logs", "HEAD"], False),
        ([], False),
    ],
)
def test_git_directory_changes_count_only_when_they_carry_state(
    parts: list[str], matters: bool
) -> None:
    assert git_dir_change_matters(parts) is matters


def test_tree_changes_skip_noise_and_flag_ignore_rule_edits() -> None:
    rules = IgnoreRules(directories=frozenset({"dist/", "frontend/build/"}))
    assert tree_change_matters(["src", "app.py"], rules) == (True, False)
    assert tree_change_matters(["frontend", "node_modules", "x", "index.js"], rules) == (
        False,
        False,
    )
    assert tree_change_matters(["dist", "bundle.js"], rules) == (False, False)
    assert tree_change_matters(["frontend", "build", "a.css"], rules) == (False, False)
    # An ignored directory's own node moving is still ignored.
    assert tree_change_matters(["dist"], rules) == (False, False)
    # Editing the rules is a change *and* a reason to re-read them.
    assert tree_change_matters(["sub", ".gitignore"], rules) == (True, True)
    assert tree_change_matters([".git", "index"], rules) == (True, False)
    assert tree_change_matters([".git", "objects", "aa"], rules) == (False, False)
    assert tree_change_matters([], rules) == (False, False)


def test_ignored_listing_parses_directories_and_files_and_drops_quoted_paths() -> None:
    rules = parse_ignored_listing('.venv/\nfrontend/node_modules/\nnotes.log\n"odd\\tname"\n')
    assert rules.covers(["frontend", "node_modules", "a.js"])
    assert rules.covers(["notes.log"])
    assert not rules.covers(["frontend", "src", "a.ts"])
    assert not rules.covers(["odd\tname"])


def test_ignored_listing_is_bounded() -> None:
    listing = "\n".join(f"build{index}/" for index in range(50))
    rules = parse_ignored_listing(listing, limit=10)
    assert len(rules.directories) == 10


def test_a_nested_worktree_is_attributed_to_itself_not_to_the_primary() -> None:
    """A linked worktree inside the primary's tree shares the primary's OS watch.

    The primary ignores the directory the worktree lives in, so the worktree's own
    edits must reach the worktree and only the worktree - otherwise every agent
    working in a nested checkout would re-read the primary too.
    """
    primary = normalize_key(ROOT)
    nested = normalize_key(_p(".claude", "worktrees", "wt-a"))
    primary_rules = IgnoreRules(directories=frozenset({".claude/"}))
    candidates: list[Candidate] = [
        (primary, primary, "tree", primary_rules),
        (nested, nested, "tree", IgnoreRules()),
        (nested, normalize_key(_p(".git")), "gitdir", IgnoreRules()),
    ]
    verdicts = classify_batch([_p(".claude", "worktrees", "wt-a", "src", "a.py")], candidates)
    assert verdicts == {nested: False}
    # The worktree's own HEAD lives under the primary's `.git`, and a commit there
    # concerns the worktree as well as (by the tree rule) the primary.
    verdicts = classify_batch([_p(".git", "worktrees", "wt-a", "HEAD")], candidates)
    assert verdicts == {primary: False, nested: False}


def test_a_storm_inside_ignored_directories_concerns_no_checkout() -> None:
    key = normalize_key(ROOT)
    paths = [_p("node_modules", f"pkg{index}", "index.js") for index in range(5000)]
    candidate: Candidate = (key, key, "tree", IgnoreRules())
    assert classify_batch(paths, [candidate]) == {}


def test_layout_interests_are_the_tree_plus_git_directories_outside_it() -> None:
    primary = CheckoutLayout(ROOT, _p(".git"), _p(".git"))
    assert primary.interests() == ((normalize_key(ROOT), "tree"),)
    outside = os.path.abspath(os.sep.join(["", "work", "repo-wt"]))
    linked = CheckoutLayout(outside, _p(".git", "worktrees", "repo-wt"), _p(".git"))
    # The worktree's private Git directory sits under the shared one, which covers it.
    assert linked.interests() == (
        (normalize_key(outside), "tree"),
        (normalize_key(_p(".git")), "gitdir"),
    )


# --- real watches ----------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, CheckoutLayout]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    toplevel = _git(repo, "rev-parse", "--show-toplevel")
    git_dir = _git(repo, "rev-parse", "--absolute-git-dir")
    layout = CheckoutLayout(str(Path(toplevel)), str(Path(git_dir)), str(Path(git_dir)))
    return repo, layout


class _Recorder:
    def __init__(self) -> None:
        self.changes: list[tuple[str, bool]] = []

    def __call__(self, root_key: str, rules_changed: bool) -> None:
        self.changes.append((root_key, rules_changed))


async def _no_ignore_listing(_toplevel: str) -> str | None:
    return ""


async def _settle(recorder: _Recorder, *, quiet: float = 1.5, limit: float = 20.0) -> None:
    """Wait until no change has arrived for `quiet` seconds (startup echoes drain)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    seen = -1
    quiet_since = loop.time()
    while loop.time() < deadline:
        if len(recorder.changes) != seen:
            seen = len(recorder.changes)
            quiet_since = loop.time()
        elif loop.time() - quiet_since >= quiet:
            return
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_a_real_watch_reports_edits_commits_and_rule_changes(tmp_path: Path) -> None:
    repo, layout = _repository(tmp_path)
    recorder = _Recorder()
    watches = CheckoutWatches(recorder, _no_ignore_listing, debounce_ms=300, step_ms=50)
    try:
        watches.sync({layout.key: layout})
        await until(lambda: watches.watched(layout.key), seconds=10, what="watch live")
        await _settle(recorder)

        recorder.changes.clear()
        (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
        await until(lambda: bool(recorder.changes), seconds=20, what="edit noticed")
        assert {key for key, _ in recorder.changes} == {layout.key}

        await _settle(recorder)
        recorder.changes.clear()
        _git(repo, "commit", "-q", "-am", "second")
        await until(lambda: bool(recorder.changes), seconds=20, what="commit noticed")

        await _settle(recorder)
        recorder.changes.clear()
        (repo / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")
        await until(
            lambda: any(rules_changed for _, rules_changed in recorder.changes),
            seconds=20,
            what="rule change noticed",
        )
    finally:
        await watches.stop()


@pytest.mark.asyncio
async def test_noise_and_the_monitors_own_reads_are_not_changes(tmp_path: Path) -> None:
    """The two ways an event-driven monitor can turn back into a polling storm.

    A dependency install or a build writes thousands of files nobody tracks, and a
    read that wrote anything (the index refresh `--no-optional-locks` exists to
    suppress) would trigger the next read forever.
    """
    repo, layout = _repository(tmp_path)
    recorder = _Recorder()
    watches = CheckoutWatches(recorder, _no_ignore_listing, debounce_ms=300, step_ms=50)
    try:
        watches.sync({layout.key: layout})
        await until(lambda: watches.watched(layout.key), seconds=10, what="watch live")
        await _settle(recorder)
        recorder.changes.clear()

        noise = repo / "node_modules" / "pkg"
        noise.mkdir(parents=True)
        for index in range(50):
            (noise / f"f{index}.js").write_text("x", encoding="utf-8")
        _git(repo, "--no-optional-locks", "status", "--porcelain")
        _git(repo, "--no-optional-locks", "diff", "--numstat", "HEAD")
        # A real quiet window guarding a negative assertion (see tests/support/settle.py).
        await asyncio.sleep(2.5)
        assert recorder.changes == []
    finally:
        await watches.stop()


@pytest.mark.asyncio
async def test_ignore_rules_come_from_git_and_silence_ignored_directories(
    tmp_path: Path,
) -> None:
    repo, layout = _repository(tmp_path)
    (repo / "build").mkdir()
    recorder = _Recorder()
    asked: list[str] = []

    async def listing(toplevel: str) -> str | None:
        asked.append(toplevel)
        return "build/\n"

    watches = CheckoutWatches(recorder, listing, debounce_ms=300, step_ms=50)
    try:
        watches.sync({layout.key: layout})
        await until(lambda: bool(asked), seconds=10, what="rules requested")
        await until(lambda: watches.watched(layout.key), seconds=10, what="watch live")
        await until(
            lambda: watches.status()["ignore_refreshes"] == 1, seconds=10, what="rules applied"
        )
        await _settle(recorder)
        recorder.changes.clear()

        for index in range(20):
            (repo / "build" / f"out{index}.o").write_text("x", encoding="utf-8")
        await asyncio.sleep(2.5)
        assert recorder.changes == []
    finally:
        await watches.stop()


@pytest.mark.asyncio
async def test_a_directory_that_cannot_be_watched_cools_down_instead_of_respawning(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "gone"
    layout = CheckoutLayout(str(missing), str(missing / ".git"), str(missing / ".git"))
    recorder = _Recorder()
    watches = CheckoutWatches(recorder, _no_ignore_listing)
    try:
        watches.sync({layout.key: layout})
        await until(lambda: watches.status()["failures"] == 1, seconds=10, what="failure seen")
        assert not watches.watched(layout.key)
        starts = watches.status()["starts"]
        watches.sync({layout.key: layout})
        assert watches.status()["starts"] == starts, "a failed watch is held off, not retried"
        assert watches.status()["cooling_down"] == 1
    finally:
        await watches.stop()


@pytest.mark.asyncio
async def test_a_primary_and_its_nested_worktree_share_one_os_watch(tmp_path: Path) -> None:
    repo, primary = _repository(tmp_path)
    nested_dir = repo / ".claude" / "worktrees" / "wt-a"
    _git(repo, "worktree", "add", "-q", str(nested_dir), "-b", "wt-a")
    git_dir = _git(nested_dir, "rev-parse", "--absolute-git-dir")
    nested = CheckoutLayout(str(nested_dir), str(Path(git_dir)), primary.common_dir)
    watches = CheckoutWatches(_Recorder(), _no_ignore_listing)
    try:
        watches.sync({primary.key: primary, nested.key: nested})
        assert watches.status()["watches"] == 1
        assert watches.watched(primary.key) and watches.watched(nested.key)
        # Dropping the primary leaves the worktree its own tree and the shared
        # Git directory it reads its HEAD from.
        watches.sync({nested.key: nested})
        await until(lambda: watches.status()["watches"] == 2, seconds=10, what="re-covered")
        assert watches.watched(nested.key)
    finally:
        await watches.stop()


def test_the_watch_module_spends_nothing_per_path_on_the_loop() -> None:
    """Pinned at the source because it is a cost property no assertion can time.

    Classification belongs to the watch thread; the loop receives verdicts.
    `awatch` would build every batch's change set on the loop instead.
    """
    source = Path(git_watch.__file__).read_text(encoding="utf-8")
    assert "awatch" not in source.replace("`awatch`", "")
    assert "call_soon_threadsafe" in source
