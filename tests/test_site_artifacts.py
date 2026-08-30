"""The committed site pages still match the sources they were generated from.

`site/changelog/index.html` and its thirty siblings are **build output that is
committed**: `site/tools/build.py` writes them, and `pages.yml` uploads `site/`
verbatim without ever running that script, which is what keeps the deploy a
twenty-second file copy with no toolchain in it.

The cost of that trade is one failure mode, and it happened on 0.1.3. The release
commit updated `CHANGELOG.md`, nobody regenerated the page, and `swemux.dev/changelog/`
went on showing 0.1.2 while 0.1.3 was live on PyPI, on GitHub Releases and in
`version.json`. Every gate that existed passed correctly throughout -
`check_changelog.py` asks whether `CHANGELOG.md` carries an entry per released
version, which was true - because nothing anywhere asked whether the *generated
artifact* still matched the source behind it.

`ci.yml`'s `site` job now asks that on every push. This asks it in the landing
gate too, which is a hundred milliseconds and one release earlier: a branch that
edits `CHANGELOG.md`, `site/tools/docs_content.py` or `THIRD-PARTY-NOTICES.md`
and forgets the regenerate fails `.worktree-verify` rather than reddening master.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD = REPO_ROOT / "site" / "tools" / "build.py"


def _load_site_tool(name: str):
    """Import a `site/tools/` script by path; they are not an installed package."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "site" / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_every_generated_site_page_is_current() -> None:
    """`build.py --check` regenerates each page in memory and compares the bytes.

    Run as a subprocess rather than by importing `build.py`: it is a script with
    module-level state and a `sys.path` insertion for its prose module, and
    importing it into a worker that then runs 5400 other tests is a larger
    promise than reading its exit code.
    """
    result = subprocess.run(
        [sys.executable, str(BUILD), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "The committed site pages no longer match their sources:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def _ci_workflow_steps() -> str:
    """`ci.yml` with its comments removed; see `test_release_notes.py` for why."""
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_ci_runs_the_site_s_own_gates() -> None:
    """The four scripts under `site/tools/` that are gates rather than generators.

    Named individually rather than checked as "a site job exists", because the
    job is only worth as much as the checks in it and dropping one of these would
    be invisible in a green run. `docs_content.py`, `ideas.py`, `logo.py` and
    `placeholders.py` are deliberately absent: they are sources and generators,
    and have no verdict to give.
    """
    steps = _ci_workflow_steps()
    for command in (
        "site/tools/build.py --check",
        "site/tools/check_changelog.py --require-tags",
        "site/tools/contrast.py",
        "site/tools/check.mjs",
    ):
        assert command in steps, command


def test_the_site_job_checks_out_the_tags_its_changelog_gate_needs() -> None:
    """`--require-tags` is only satisfiable from a checkout that fetched them.

    `actions/checkout` fetches none by default, and `check_changelog.py`'s
    tolerant reading of that - a note, then the package version alone, then exit
    0 - is right for a fresh clone and wrong for a gate. The flag turns it into a
    failure; this is the other half, and the two are asserted together because
    either alone is a check that quietly asks less than it says.
    """
    assert "fetch-depth: 0" in _ci_workflow_steps()


def test_require_tags_refuses_a_pass_earned_by_seeing_no_tags() -> None:
    """The flag itself, at the boundary the CI step depends on.

    Asserted rather than assumed because it is unobservable in a green run: a
    tagless checkout and a healthy one both print a short report and exit 0
    without it, and the difference between them is the whole point.
    """
    check_changelog = _load_site_tool("check_changelog")
    check_changelog.failures.clear()
    try:
        check_changelog.require_tags(["0.1.0"])
        assert check_changelog.failures == []
        check_changelog.require_tags([])
        assert len(check_changelog.failures) == 1
        assert "no `v*` tags are visible" in check_changelog.failures[0]
    finally:
        check_changelog.failures.clear()
