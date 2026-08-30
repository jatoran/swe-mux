"""One interpreter version, asserted everywhere a copy of it lives.

The project's interpreter is CPython 3.12, but until 2026-08-30 that fact
lived only inside the CI workflows (`uv python install 3.12`), where local
tooling cannot see it. The day another session installed CPython 3.14.0 into
uv's managed store, every *fresh* venv on the machine silently switched to it
- `requires-python = ">=3.12"` has no ceiling, uv picks the newest satisfying
interpreter - and `.worktree-setup` started dying at resolve, because the
locked `spacy==3.8.15` ships no cp314 wheel. Existing venvs kept working,
which is exactly what made it a trap: the primary checkout proved nothing
about a fresh worktree.

`.python-version` is now the committed pin uv reads for every new venv. A
ceiling on `requires-python` would have been the wrong fix: that is published
wheel metadata, and spacy never ships to end users (it is acquired at first
use behind the voice stack), so it would refuse `pip install swe-mux` to
every 3.14 user over a dev-only constraint.

That leaves the same drift problem the `-m` marker expression has
(`test_live_daemon_guards.py`): the version is copied into the workflows and
both mypy configs, and a copy that drifts reintroduces the trap on whichever
host or runner reads it. The workflow copies are discovered by scanning every
workflow file rather than enumerated, so a new job cannot add an unlisted
copy.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pinned_version() -> str:
    return (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()


def test_the_pin_exists_and_is_a_bare_major_minor() -> None:
    """A patch-level pin (`3.12.11`) would refuse a host that has only a newer
    3.12 patch, and an exotic form (`cpython-3.12`, a range) would mean the
    other copies can no longer be compared to it by string equality."""
    pin = _pinned_version()
    assert re.fullmatch(r"3\.\d+", pin), (
        f".python-version must be a bare major.minor, got {pin!r}"
    )


def test_every_workflow_interpreter_install_matches_the_pin() -> None:
    """Discovered, not enumerated: any `uv python install <v>` in any workflow
    is a copy of the pin, including ones added after this test was written."""
    pin = _pinned_version()
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflows found - the glob is looking in the wrong place"
    installs: list[tuple[str, str]] = []
    for path in workflows:
        for version in re.findall(
            r"uv python install\s+(\S+)", path.read_text(encoding="utf-8")
        ):
            installs.append((path.name, version))
    assert installs, "no `uv python install` lines found - CI stopped pinning?"
    mismatched = [(name, v) for name, v in installs if v != pin]
    assert not mismatched, (
        f"workflow interpreter installs disagree with .python-version={pin}: "
        f"{mismatched}"
    )


def test_both_mypy_configs_typecheck_the_pinned_version() -> None:
    """mypy answering questions about a different interpreter than the one the
    code runs on is the quiet half of the same drift."""
    pin = _pinned_version()
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["tool"]["mypy"]["python_version"] == pin
    platform_config = tomllib.loads(
        (REPO_ROOT / "mypy-platform.toml").read_text(encoding="utf-8")
    )
    assert platform_config["tool"]["mypy"]["python_version"] == pin


def test_the_pin_is_the_floor_the_package_publishes() -> None:
    """`requires-python` is the published claim and the pin is the tested
    reality; a floor raised without moving the pin means shipping a claim no
    gate ever ran, and a pin raised without the floor means testing an
    interpreter users are told they do not need."""
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    specifier = pyproject["project"]["requires-python"]
    match = re.search(r">=\s*(3\.\d+)", specifier)
    assert match, f"requires-python has no >= floor: {specifier!r}"
    assert match.group(1) == _pinned_version()
