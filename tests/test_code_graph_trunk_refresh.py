"""W4.5.1 - the code graph after a branch lands on the trunk.

The seed runs once per Project per process and incremental maintenance only ever
sees files *this daemon's own sessions* wrote, so everything arriving by
`git merge` - which is every landing - used to be invisible to the graph until
some session happened to edit it (D3 soak, 2026-08-24: three modules absent from
`code_context` outright, `logsetup.py` answering with its pre-S7 symbols).

These build **real** repositories and run **real** merges. A stubbed `rev-parse`
would prove the plumbing calls itself and nothing about a merge being seen.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swe_mux.code_graph import (
    CodeGraphStore,
    changed_between,
    index_project,
    parsing_available,
    refresh_indexed_project,
    repo_head,
)

pytestmark = pytest.mark.skipif(
    not parsing_available(), reason="tree-sitter grammars not available on this host"
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path) -> Path:
    """A real repository holding a two-module project, committed on `main`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 1\n")
    (root / "app.py").write_text(
        "from pkg.helper import make_thing\ndef run(n):\n    return make_thing(n)\n"
    )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    return root


def _land_a_branch(root: Path) -> None:
    """Add a module on a branch and merge it, the way a landing arrives."""
    _git(root, "checkout", "-b", "feature")
    (root / "pkg" / "extra.py").write_text("def brand_new(value):\n    return value * 2\n")
    (root / "app.py").write_text(
        "from pkg.helper import make_thing\n"
        "from pkg.extra import brand_new\n"
        "def run(n):\n"
        "    return brand_new(make_thing(n))\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "add brand_new")
    _git(root, "checkout", "main")
    _git(root, "merge", "--no-ff", "-m", "land feature", "feature")


async def test_merge_arrived_file_becomes_visible(tmp_path: Path) -> None:
    """The mandatory one: a file that arrived by merge is in the graph."""
    root = _repository(tmp_path / "repo")
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))

    _land_a_branch(root)
    # Precondition, so this test measures the fix rather than the fixture: nothing
    # about the merge has reached the graph yet.
    assert await store.definitions("p1", "brand_new") == []

    outcome, updated = await refresh_indexed_project(store, "p1", str(root))
    assert outcome == "delta"
    assert updated == 2  # the new module, and the caller the merge rewrote

    definitions = await store.definitions("p1", "brand_new")
    assert [row["path"] for row in definitions] == ["pkg/extra.py"]
    # And the edge, not merely the node: the reverse-dependency query is what the
    # blast radius and `code_context` answer from.
    assert "pkg/extra.py" in await store.imports_of("p1", "app.py")
    blast = await store.reverse_dependents("p1", "pkg/extra.py", hops=2)
    assert [node.path for node in blast] == ["app.py"]
    store.close()


async def test_second_pass_over_an_unmoved_head_does_no_work(tmp_path: Path) -> None:
    """The cost guard: a quiet trunk must not re-parse anything, ever."""
    root = _repository(tmp_path / "repo")
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))
    _land_a_branch(root)
    assert (await refresh_indexed_project(store, "p1", str(root)))[0] == "delta"

    for _ in range(3):
        assert await refresh_indexed_project(store, "p1", str(root)) == ("current", 0)
    store.close()


async def test_a_documentation_only_merge_is_recorded_rather_than_repeated(
    tmp_path: Path,
) -> None:
    """A merge that changes no node still moves the recorded head.

    Otherwise the same diff is recomputed on every turn boundary from then on -
    the cheap case turning into a permanent per-turn cost is exactly the failure
    this task was told not to introduce.
    """
    root = _repository(tmp_path / "repo")
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))

    (root / "README.md").write_text("# notes\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "docs")

    assert await refresh_indexed_project(store, "p1", str(root)) == ("delta", 0)
    assert await refresh_indexed_project(store, "p1", str(root)) == ("current", 0)
    state = await store.index_state("p1")
    assert state is not None and state[0] == await repo_head(str(root))
    store.close()


async def test_a_delta_larger_than_the_bound_reseeds_instead(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))
    _land_a_branch(root)

    outcome, count = await refresh_indexed_project(store, "p1", str(root), delta_limit=1)
    assert outcome == "reindex"
    assert count >= 3  # the whole tree, not the two changed files
    assert [row["path"] for row in await store.definitions("p1", "brand_new")] == [
        "pkg/extra.py"
    ]
    store.close()


async def test_an_unanswerable_delta_reseeds_rather_than_trusting_the_graph(
    tmp_path: Path,
) -> None:
    """A commit git can no longer resolve must not read as "nothing changed"."""
    root = _repository(tmp_path / "repo")
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))
    # A head from another repository entirely: `git diff` cannot answer it.
    await store.record_index_state("p1", head="0" * 40, file_count=3)
    _land_a_branch(root)

    outcome, count = await refresh_indexed_project(store, "p1", str(root))
    assert outcome == "reindex"
    assert count >= 3
    assert await store.definitions("p1", "brand_new") != []
    store.close()


async def test_a_project_that_is_not_a_checkout_is_left_alone(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return 1\n")
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))

    assert await repo_head(str(root)) is None
    assert await refresh_indexed_project(store, "p1", str(root)) == ("unversioned", 0)
    # The seed recorded that it reflects no commit, which is a different fact from
    # never having been seeded.
    state = await store.index_state("p1")
    assert state is not None and state[0] is None
    store.close()


async def test_an_unseeded_project_is_not_refreshed_behind_the_seed(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    store = CodeGraphStore(tmp_path / "graph.db")
    assert await refresh_indexed_project(store, "p1", str(root)) == ("unseeded", 0)
    store.close()


async def test_clearing_a_project_drops_the_commit_it_claimed(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))
    assert await store.index_state("p1") is not None

    await store.clear_project("p1")
    assert await store.index_state("p1") is None
    store.close()


async def test_changed_between_reports_both_halves_of_a_rename(tmp_path: Path) -> None:
    """The old path has to leave the graph and the new one has to enter it."""
    root = _repository(tmp_path / "repo")
    before = await repo_head(str(root))
    assert before is not None
    _git(root, "mv", "pkg/helper.py", "pkg/renamed.py")
    _git(root, "commit", "-m", "rename")
    after = await repo_head(str(root))
    assert after is not None

    paths = await changed_between(str(root), before, after)
    assert paths is not None
    assert sorted(paths) == ["pkg/helper.py", "pkg/renamed.py"]


async def test_a_rename_that_lands_leaves_no_phantom_node(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))

    _git(root, "mv", "pkg/helper.py", "pkg/renamed.py")
    _git(root, "commit", "-m", "rename")
    assert (await refresh_indexed_project(store, "p1", str(root)))[0] == "delta"

    assert await store.known_files("p1", ["pkg/helper.py"]) == set()
    assert await store.known_files("p1", ["pkg/renamed.py"]) == {"pkg/renamed.py"}
    store.close()


async def test_changed_between_answers_none_for_an_unknown_commit(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    head = await repo_head(str(root))
    assert head is not None
    # None, not [] - "git cannot answer" and "the merge changed nothing" are the
    # two readings that must never collapse into one.
    assert await changed_between(str(root), "0" * 40, head) is None
