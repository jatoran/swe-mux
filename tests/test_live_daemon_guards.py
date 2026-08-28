"""Default-tier guards for the `live_daemon` tier, which the default tier deselects.

Some things about a gated tier can only be checked from *outside* it.

Whether the gate that runs everywhere actually deselects it - a mark one runner
names and another does not turns a gated tier into a red gate against a third
party, which is exactly what `live_edge_tts` did the first time CI ran in public.
Whether the list of files carrying that expression is itself complete, since a
copy nobody knows about is the next drift. And whether the tier's own derivations
find anything at all: `assert_startup_is_complete` compares what the daemon
reported against what `server.py` declares, and it passes trivially if the AST
walk that reads those declarations returns an empty list.

None of these starts a daemon, binds a port, or spawns anything, so they all
belong in the suite everyone runs.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from tests.support.live_daemon import declared_startup_phases

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every live mark the default gate must deselect. `conpty` is deliberately not
#: here: it runs in the gate on Windows and is meant to.
LIVE_MARKS = (
    "live_agent",
    "live_subagent",
    "live_telemetry",
    "live_quota",
    "live_automations",
    "live_mcp",
    "live_edge_tts",
    "live_daemon",
)

#: Every file that carries a copy of the deselect expression, found by searching
#: for the expression rather than by memory. `.worktree-verify` is the local gate,
#: `ci.yml` runs it twice (the Windows `verify` job and the POSIX `platform`
#: matrix), `CLAUDE.md` and `AGENTS.md` are what an agent reads and re-runs by
#: hand, `CONTRIBUTING.md` is what a human contributor is told to run, and
#: `tools/linux_container_verify.sh` runs the suite on Linux from a Windows host.
#:
#: Three of those six had already drifted when this guard was written: `ci.yml`
#: was missing `live_edge_tts` (it would have gone red against Microsoft's hosted
#: endpoint on all three runners), and `CONTRIBUTING.md` and the container script
#: were each missing several marks. That is why this is a test and not a note.
MARKER_EXPRESSION_SOURCES = (
    Path(".worktree-verify"),
    Path(".github/workflows/ci.yml"),
    Path("CLAUDE.md"),
    Path("AGENTS.md"),
    Path("CONTRIBUTING.md"),
    Path("tools/linux_container_verify.sh"),
)


def test_every_live_mark_is_registered_in_pyproject() -> None:
    """An unregistered mark is a warning, and `filterwarnings = ["error"]` is on.

    So a mark added to a test file and not to `[tool.pytest.ini_options] markers`
    fails the run rather than silently selecting nothing - but only once someone
    runs that file. This states the registry membership directly.
    """
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        entry.split(":", 1)[0].strip()
        for entry in config["tool"]["pytest"]["ini_options"]["markers"]
    }
    missing = [mark for mark in LIVE_MARKS if mark not in declared]
    assert not missing, f"markers used by the suite but not registered: {missing}"


def test_the_gate_deselects_every_live_mark_wherever_the_expression_is_copied() -> None:
    """Drift between the copies is the failure mode: a step that never runs is not
    a step that passes, and a tier a runner forgot to deselect goes red against a
    third-party service rather than against the code."""
    for relative in MARKER_EXPRESSION_SOURCES:
        path = REPO_ROOT / relative
        assert path.is_file(), f"{relative} is missing"
        # `CLAUDE.md` wraps the command over several lines, so the expression is
        # matched by its terms against collapsed whitespace rather than as one
        # literal string.
        collapsed = " ".join(path.read_text(encoding="utf-8").split())
        missing = [mark for mark in LIVE_MARKS if f"not {mark}" not in collapsed]
        assert not missing, f"{relative} does not deselect {missing}"


def test_no_runnable_copy_of_the_expression_escapes_the_list() -> None:
    """A seventh copy nobody added to `MARKER_EXPRESSION_SOURCES` is the next drift.

    The list is only as good as its completeness, so it is discovered as well as
    asserted. The scan covers the places a *runnable* copy can live - repository
    root, the workflows, and `tools/` - and deliberately not `.docs/`, where two
    archived implementation records quote the expression as it stood when they
    were written and are history rather than instructions.
    """
    searched: list[Path] = [
        *(path for path in REPO_ROOT.iterdir() if path.is_file()),
        *sorted((REPO_ROOT / ".github" / "workflows").glob("*")),
        *sorted(path for path in (REPO_ROOT / "tools").iterdir() if path.is_file()),
    ]
    found: set[Path] = set()
    for path in searched:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "not live_agent" in " ".join(text.split()):
            found.add(path.relative_to(REPO_ROOT))
    listed = {Path(relative) for relative in MARKER_EXPRESSION_SOURCES}
    assert found == listed, (
        f"unlisted copies: {sorted(map(str, found - listed))}; "
        f"listed but no longer carrying it: {sorted(map(str, listed - found))}"
    )


def test_the_startup_phase_derivation_actually_finds_phases() -> None:
    """The self-check the whole startup assertion rests on.

    `assert_startup_is_complete` requires every declared phase to have completed;
    with an empty declaration list that requirement is vacuous, and an AST walk
    against a file that moved or a call that was renamed produces exactly that.
    Two named phases are pinned so the derivation has to keep resolving, and the
    count floor is what catches a walk that finds only the first one.
    """
    declared = declared_startup_phases()
    assert "database-integrity" in declared, declared
    assert "adapters-and-shims" in declared, declared
    assert "restore-attention" in declared, declared
    assert len(declared) >= 10, declared
