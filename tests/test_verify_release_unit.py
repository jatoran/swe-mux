"""Phase 11: the gate that proves a tag, its source, and its wheel are one release.

`verify_release_artifact.py` answers "is this wheel well-formed" without ever
looking at the tag or the tree, so a `v0.2.0` tag over a `pyproject.toml` still
saying `0.1.0` passes it and publishes a valid artifact of the wrong version.
`packaging/verify_release_unit.py` is what notices.

Every input here is built in `tmp_path`: a small source tree in the shape the
real one has, and a wheel-shaped zip. Nothing runs `uv build` and nothing reads
or writes a real git tag - the checks are pure functions over a directory and a
zip, so a real toolchain would add a minute of work and prove nothing the
construction does not. Each failure mode is exercised on an otherwise-healthy
tree, because a version-mismatch case that also has a stale changelog proves
nothing about which check fired.

The one test that does read the repository is at the foot of the file, and it is
the point of the exercise: today's tree, simulated as its own release, has to
pass.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import a `packaging/` script by path; it is not an installed package."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify_release_unit = _load("verify_release_unit")


# --------------------------------------------------------------------------- fixtures

VERSION = "0.1.0"
TAG = f"v{VERSION}"
DIST_INFO = f"swe_mux-{VERSION}.dist-info"

URLS = {
    "Homepage": "https://swemux.dev",
    "Repository": "https://github.com/jatoran/swe-mux",
    "Issues": "https://github.com/jatoran/swe-mux/issues",
}
SCRIPTS = {
    "mux": "swe_mux.cli:main",
    "muxd": "swe_mux.__main__:main",
    "swe-mux": "swe_mux.desktop:main",
}

# One reporting location per shape the real tree has: a route module with two
# literals, and three with one each. The paths must be the real ones, because
# `VERSION_REPORTING_SOURCES` is what the checker looks at.
REPORTING_BODIES = {
    "src/swe_mux/routes/system.py": (
        '"""Health. The version below is a literal; RELEASING.md records why."""\n'
        "\n"
        "def health() -> dict[str, str]:\n"
        '    return {"version": "%(v)s"}\n'
        "\n"
        "def ready() -> dict[str, str]:\n"
        '    return {"version": "%(v)s"}\n'
    ),
    "src/swe_mux/routes/diagnostics.py": (
        "def bundle() -> dict[str, str]:\n"
        '    return {"swe_mux_version": "%(v)s", "version": "%(v)s"}\n'
    ),
    "src/swe_mux/mcp.py": (
        "def server_info() -> dict[str, str]:\n"
        '    return {"name": "mux", "version": "%(v)s"}\n'
    ),
    "src/swe_mux/provider_accounts.py": (
        "def client_info() -> dict[str, str]:\n"
        '    return {"name": "swe-mux", "version": "%(v)s"}\n'
    ),
}

CHANGELOG_BODY = """# Changelog

## [Unreleased]

## [{version}] - 2026-08-28

First public release.

### Added

- Everything.

[Unreleased]: https://github.com/jatoran/swe-mux/compare/v{version}...HEAD
[{version}]: https://github.com/jatoran/swe-mux/releases/tag/v{version}
"""

README_BODY = """# swe-mux

Install it, then run it.

```
git clone https://github.com/jatoran/swe-mux
cd swe-mux
uv sync --extra desktop
uv run --extra desktop swe-mux
```

For a headless daemon run `uv run muxd`, and `uv run mux doctor` is the health
report. `muxd --local-only` keeps it local. The `verify` job is what CI calls it.

<!-- TODO(release): pypi - once published, `uv tool install swe-mux`. -->
"""

RELEASING_BODY = """# Releasing swe-mux

Bump `pyproject.toml` and `src/swe_mux/__init__.py` together.

```bash
uv build
git tag -a v0.1.0 -m "v0.1.0"
```

Then confirm `mux --help` and `muxd --local-only` both work.
"""

STORE_BODY = '''"""A store. It never uses PRAGMA user_version, for the usual reason."""

from .sqlite_store import write_schema_version

{constant} = {declared}


class Store:
    def _connect(self) -> None:
        # Per-store row, not PRAGMA user_version: that pragma is per file.
        write_schema_version(self._db, {store!r}, {stamped})
'''


LAND_MODULE = "src/swe_mux/land_store.py"
VOICE_MODULE = "src/swe_mux/voice.py"
LAND_CONSTANT = "LAND_SCHEMA_VERSION"
VOICE_CONSTANT = "VOICE_SCHEMA_VERSION"

StoreRow = tuple[str, str, str, str, str]


def store_row(
    module: str, constant: str, declared: str, key: str, stamped: str | None = None
) -> StoreRow:
    """One synthetic store: where it lives, what it declares, and what it stamps.

    `stamped` defaults to the module's own constant, which is the healthy shape.
    Passing anything else is the defect a test is about.
    """
    return (module, constant, declared, key, stamped if stamped is not None else constant)


HEALTHY_STORES: tuple[StoreRow, ...] = (
    store_row(LAND_MODULE, LAND_CONSTANT, "5", "land_queue"),
    store_row(VOICE_MODULE, VOICE_CONSTANT, "3", "voice"),
)


def _pyproject(
    *,
    version: str = VERSION,
    urls: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
    gui_scripts: dict[str, str] | None = None,
) -> str:
    lines = [
        "[project]",
        'name = "swe-mux"',
        f'version = "{version}"',
        'description = "A browser-based terminal multiplexer"',
        "",
        "[project.urls]",
    ]
    for label, url in (URLS if urls is None else urls).items():
        lines.append(f'{label} = "{url}"')
    lines.extend(["", "[project.scripts]"])
    for command, target in (SCRIPTS if scripts is None else scripts).items():
        lines.append(f'{command} = "{target}"')
    if gui_scripts:
        lines.extend(["", "[project.gui-scripts]"])
        for command, target in gui_scripts.items():
            lines.append(f'{command} = "{target}"')
    return "\n".join(lines) + "\n"


def _write(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def build_tree(
    root: Path,
    *,
    pyproject: str | None = None,
    init_version: str = VERSION,
    literal_version: str = VERSION,
    frontend_version: str | None = VERSION,
    reporting: dict[str, str] | None = None,
    changelog: str | None = None,
    readme: str | None = None,
    releasing: str | None = None,
    stores: tuple[StoreRow, ...] | None = None,
) -> Path:
    """Write a source tree in the shape the checker reads. Every knob is a defect."""
    root.mkdir(parents=True, exist_ok=True)
    _write(root, "pyproject.toml", pyproject if pyproject is not None else _pyproject())
    _write(root, "src/swe_mux/__init__.py", f'__version__ = "{init_version}"\n')
    for relative, template in (reporting or REPORTING_BODIES).items():
        _write(root, relative, template % {"v": literal_version})
    if frontend_version is not None:
        _write(
            root,
            "frontend/package.json",
            json.dumps({"name": "swe-mux-frontend", "version": frontend_version}) + "\n",
        )
    _write(
        root,
        "CHANGELOG.md",
        changelog if changelog is not None else CHANGELOG_BODY.format(version=VERSION),
    )
    _write(root, "README.md", README_BODY if readme is None else readme)
    _write(root, "RELEASING.md", RELEASING_BODY if releasing is None else releasing)
    for module, constant, declared, store, stamped in stores or HEALTHY_STORES:
        _write(
            root,
            module,
            STORE_BODY.format(
                constant=constant, declared=declared, store=store, stamped=stamped
            ),
        )
    return root


def metadata_text(
    *,
    version: str = VERSION,
    name: str = "swe-mux",
    urls: dict[str, str] | None = None,
) -> str:
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {name}",
        f"Version: {version}",
        "License-Expression: Apache-2.0",
    ]
    for label, url in (URLS if urls is None else urls).items():
        lines.append(f"Project-URL: {label}, {url}")
    return "\n".join(lines) + "\n\n"


def entry_points_text(
    scripts: dict[str, str] | None = None, gui_scripts: dict[str, str] | None = None
) -> str:
    lines = ["[console_scripts]"]
    for command, target in (SCRIPTS if scripts is None else scripts).items():
        lines.append(f"{command} = {target}")
    if gui_scripts:
        lines.extend(["", "[gui_scripts]"])
        for command, target in gui_scripts.items():
            lines.append(f"{command} = {target}")
    return "\n".join(lines) + "\n"


def build_wheel(
    path: Path,
    *,
    version: str = VERSION,
    name: str = "swe-mux",
    urls: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
    gui_scripts: dict[str, str] | None = None,
    with_metadata: bool = True,
    with_entry_points: bool = True,
) -> Path:
    members = {
        "swe_mux/__init__.py": f'__version__ = "{version}"\n',
        f"{DIST_INFO}/WHEEL": "Wheel-Version: 1.0\n",
        f"{DIST_INFO}/RECORD": "",
    }
    if with_metadata:
        members[f"{DIST_INFO}/METADATA"] = metadata_text(version=version, name=name, urls=urls)
    if with_entry_points:
        members[f"{DIST_INFO}/entry_points.txt"] = entry_points_text(scripts, gui_scripts)
    with zipfile.ZipFile(path, "w") as archive:
        for member, body in members.items():
            archive.writestr(member, body)
    return path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path / "tree")


@pytest.fixture
def wheel(tmp_path: Path) -> Path:
    return build_wheel(tmp_path / "swe_mux-0.1.0-py3-none-any.whl")


def run(wheel: Path, tree: Path, tag: str = TAG, stage: Any = verify_release_unit.RELEASE) -> Any:
    """The default stage is the strict one, matching `verify`'s own default.

    Every test that does not name a stage is therefore asking the release-time
    question, which is the one that must not weaken.
    """
    return verify_release_unit.verify(wheel, tag, tree, stage=stage)


def verdict(report: Any, name: str) -> bool:
    return next(check.ok for check in report.checks if check.name == name)


def message(report: Any, name: str) -> str:
    check = next(check for check in report.checks if check.name == name)
    return f"{check.detail} {check.remedy}"


def only_failure(report: Any) -> str:
    """The name of the single failing check, so a test proves which one fired."""
    failures = [check.name for check in report.checks if not check.ok]
    assert len(failures) == 1, f"expected exactly one failure, got {failures}"
    return failures[0]


# ------------------------------------------------------------------------ the healthy case


def test_a_coherent_release_passes_every_check(wheel: Path, tree: Path) -> None:
    report = run(wheel, tree)
    assert report.ok, verify_release_unit.render(report, subject="Release unit")
    assert [check.name for check in report.checks] == [
        "artifact-readable",
        "tag-format",
        "version-sources",
        "version-literals",
        "changelog-entry",
        "changelog-links",
        "wheel-version",
        "project-urls",
        "documented-commands",
        "console-scripts",
        "migration-coherence",
    ]


def test_every_passing_check_still_says_what_it_observed(wheel: Path, tree: Path) -> None:
    """A validator that speaks only when unhappy cannot be told from one that skipped."""
    report = run(wheel, tree)
    assert all(check.detail for check in report.checks)


# ---------------------------------------------------------------------------- tag format


@pytest.mark.parametrize("tag", ["0.1.0", "release-0.1.0", "v0.1", "v0.1.0-final", "V0.1.0"])
def test_a_tag_that_is_not_vxyz_fails_the_format_check(
    wheel: Path, tree: Path, tag: str
) -> None:
    report = run(wheel, tree, tag)
    assert verdict(report, "tag-format") is False
    assert "vX.Y.Z" in message(report, "tag-format")


@pytest.mark.parametrize("tag", ["v0.1.0", "v10.20.30", "v0.1.0rc1", "v0.1.0a1", "v0.1.0b2"])
def test_a_release_or_prerelease_tag_is_accepted(tmp_path: Path, tag: str) -> None:
    version = tag[1:]
    tree = build_tree(
        tmp_path / "tree",
        pyproject=_pyproject(version=version),
        init_version=version,
        literal_version=version,
        frontend_version=version,
        changelog=CHANGELOG_BODY.format(version=version),
    )
    wheel = build_wheel(tmp_path / "w.whl", version=version)
    report = run(wheel, tree, tag)
    assert verdict(report, "tag-format") is True
    assert report.ok, verify_release_unit.render(report, subject="Release unit")


def test_a_malformed_tag_still_lets_the_other_checks_answer(
    wheel: Path, tree: Path
) -> None:
    """Falling back to the declared version keeps eight verdicts instead of one."""
    report = run(wheel, tree, "0.1.0")
    assert only_failure(report) == "tag-format"


# -------------------------------------------------------------------------- version sources


def test_a_tag_ahead_of_pyproject_fails_version_sources(tmp_path: Path, tree: Path) -> None:
    wheel = build_wheel(tmp_path / "w.whl", version="0.2.0")
    report = run(wheel, tree, "v0.2.0")
    assert verdict(report, "version-sources") is False
    text = message(report, "version-sources")
    assert "pyproject.toml says 0.1.0" in text
    assert "src/swe_mux/__init__.py says 0.1.0" in text


def test_pyproject_alone_disagreeing_names_only_pyproject(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(tmp_path / "tree", pyproject=_pyproject(version="0.0.9"))
    report = run(wheel, tree)
    assert verdict(report, "version-sources") is False
    text = message(report, "version-sources")
    assert "pyproject.toml says 0.0.9" in text
    assert "__init__.py says" not in text


def test_dunder_version_alone_disagreeing_names_only_the_module(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(tmp_path / "tree", init_version="0.0.9")
    report = run(wheel, tree)
    assert only_failure(report) == "version-sources"
    text = message(report, "version-sources")
    assert "src/swe_mux/__init__.py says 0.0.9" in text
    assert "pyproject.toml says" not in text


def test_a_missing_dunder_version_is_reported_as_missing(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(tmp_path / "tree")
    (tree / "src/swe_mux/__init__.py").write_text("# nothing here\n", encoding="utf-8")
    report = run(wheel, tree)
    assert only_failure(report) == "version-sources"
    assert "declares no `__version__`" in message(report, "version-sources")


def test_a_dunder_version_mentioned_only_in_a_docstring_is_not_the_declaration(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(tmp_path / "tree")
    (tree / "src/swe_mux/__init__.py").write_text(
        '"""Sets __version__ = \\"9.9.9\\" for the package."""\n\n__version__ = "0.1.0"\n',
        encoding="utf-8",
    )
    report = run(wheel, tree)
    assert verdict(report, "version-sources") is True


# ------------------------------------------------------------------------- version literals


def test_a_stale_literal_in_a_reporting_route_fails(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(tmp_path / "tree", literal_version="0.0.9")
    report = run(wheel, tree)
    assert only_failure(report) == "version-literals"
    text = message(report, "version-literals")
    assert "src/swe_mux/routes/system.py reports 0.0.9" in text
    assert "mux doctor" in text


def test_a_stale_frontend_package_version_fails(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(tmp_path / "tree", frontend_version="0.0.9")
    report = run(wheel, tree)
    assert only_failure(report) == "version-literals"
    assert "frontend/package.json says 0.0.9" in message(report, "version-literals")


def test_a_file_that_reads_dunder_version_instead_of_a_literal_passes(
    tmp_path: Path, wheel: Path
) -> None:
    """The check exists to catch a *stale* literal, not to require that one exists.

    A reporting module with no version literal left in it is the fix
    `RELEASING.md` calls owed, so it must not read as a failure.
    """
    reporting = dict(REPORTING_BODIES)
    reporting["src/swe_mux/mcp.py"] = (
        "from swe_mux import __version__\n"
        "\n"
        "def server_info() -> dict[str, str]:\n"
        '    return {"name": "mux", "version": __version__}\n'
    )
    tree = build_tree(tmp_path / "tree", reporting=reporting)
    report = run(wheel, tree)
    assert verdict(report, "version-literals") is True


def test_a_version_shaped_string_in_a_docstring_is_not_a_literal(
    tmp_path: Path, wheel: Path
) -> None:
    reporting = dict(REPORTING_BODIES)
    reporting["src/swe_mux/mcp.py"] = (
        '"""Superseded 0.0.1 with the current protocol."""\n'
        "\n"
        "def server_info() -> dict[str, str]:\n"
        '    return {"name": "mux", "version": "%(v)s"}\n'
    )
    tree = build_tree(tmp_path / "tree", reporting=reporting)
    report = run(wheel, tree)
    assert verdict(report, "version-literals") is True


def test_a_missing_reporting_file_is_reported_rather_than_passed(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(tmp_path / "tree")
    (tree / "src/swe_mux/mcp.py").unlink()
    report = run(wheel, tree)
    assert only_failure(report) == "version-literals"
    assert "Could not read src/swe_mux/mcp.py" in message(report, "version-literals")


# ----------------------------------------------------------------------------- changelog


def test_a_version_with_no_changelog_section_fails(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(
        tmp_path / "tree",
        changelog="# Changelog\n\n## [Unreleased]\n\n## [0.0.9] - 2026-01-01\n\nOld.\n",
    )
    report = run(wheel, tree)
    assert verdict(report, "changelog-entry") is False
    assert "no `## [0.1.0]` section" in message(report, "changelog-entry")


def test_entries_still_under_unreleased_are_named_as_the_likely_cause(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(
        tmp_path / "tree",
        changelog="# Changelog\n\n## [Unreleased]\n\n- Everything, still here.\n",
    )
    report = run(wheel, tree)
    assert verdict(report, "changelog-entry") is False
    assert "still sitting in it" in message(report, "changelog-entry")


def test_a_heading_with_nothing_under_it_fails(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(
        tmp_path / "tree",
        changelog=(
            "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-08-28\n\n"
            "[Unreleased]: https://github.com/jatoran/swe-mux/compare/v0.1.0...HEAD\n"
            "[0.1.0]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.0\n"
        ),
    )
    report = run(wheel, tree)
    assert only_failure(report) == "changelog-entry"
    assert "nothing under it" in message(report, "changelog-entry")


def test_a_written_entry_with_leftover_unreleased_content_fails(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(
        tmp_path / "tree",
        changelog=CHANGELOG_BODY.format(version=VERSION).replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- One more thing.\n", 1
        ),
    )
    report = run(wheel, tree)
    assert only_failure(report) == "changelog-entry"
    assert "recorded as unreleased" in message(report, "changelog-entry")


# ------------------------------------------------------------------- the two changelog stages
#
# The rule above ("nothing may be left under `## [Unreleased]`") is a release-time
# rule. Between releases the same tree is correct: `pyproject.toml` still declares
# the version that was published, and `## [Unreleased]` is where the next
# version's entries belong. These tests pin both directions, because a mode that
# is only ever exercised one way is a mode that will be wrong the first time
# somebody relies on the other.


def _tree_with_leftover_unreleased(root: Path) -> Path:
    """A written entry for the declared version, plus content under `Unreleased`.

    The exact state this repository is in after a release, and the state that
    made three branches revert their changelog entries to keep the gate green.
    """
    return build_tree(
        root,
        changelog=CHANGELOG_BODY.format(version=VERSION).replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- One more thing.\n", 1
        ),
    )


def test_leftover_unreleased_content_passes_at_the_development_stage(
    tmp_path: Path, wheel: Path
) -> None:
    tree = _tree_with_leftover_unreleased(tmp_path / "tree")
    report = run(wheel, tree, stage=verify_release_unit.DEVELOPMENT)
    assert report.ok, verify_release_unit.render(report, subject="Release unit")
    assert verdict(report, "changelog-entry") is True
    assert "next version" in message(report, "changelog-entry")


def test_the_same_tree_still_fails_at_the_release_stage(tmp_path: Path, wheel: Path) -> None:
    """The other direction of the test above, over the identical bytes.

    Written as a pair on purpose: the two differ only in the stage, so a change
    that made the stage inert would fail here rather than passing both.
    """
    tree = _tree_with_leftover_unreleased(tmp_path / "tree")
    report = run(wheel, tree, stage=verify_release_unit.RELEASE)
    assert only_failure(report) == "changelog-entry"
    assert "recorded as unreleased" in message(report, "changelog-entry")


def test_the_stage_defaults_to_the_strict_one(tmp_path: Path, wheel: Path) -> None:
    """A caller that says nothing gets the release-time reading.

    This is the property that keeps the release path safe by construction rather
    than by every caller remembering: relaxing it takes an argument.
    """
    tree = _tree_with_leftover_unreleased(tmp_path / "tree")
    report = verify_release_unit.verify(wheel, TAG, tree)
    assert only_failure(report) == "changelog-entry"


def test_the_development_stage_still_requires_the_version_to_have_an_entry(
    tmp_path: Path, wheel: Path
) -> None:
    """Only the third question is relaxed; the first two are asked at both stages."""
    tree = build_tree(
        tmp_path / "tree",
        changelog="# Changelog\n\n## [Unreleased]\n\n- Everything, still here.\n",
    )
    report = run(wheel, tree, stage=verify_release_unit.DEVELOPMENT)
    assert verdict(report, "changelog-entry") is False
    assert "no `## [0.1.0]` section" in message(report, "changelog-entry")


def test_the_development_stage_still_requires_the_entry_to_say_something(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(
        tmp_path / "tree",
        changelog=(
            "# Changelog\n\n## [Unreleased]\n\n- Next version's work.\n\n"
            "## [0.1.0] - 2026-08-28\n\n"
            "[Unreleased]: https://github.com/jatoran/swe-mux/compare/v0.1.0...HEAD\n"
            "[0.1.0]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.0\n"
        ),
    )
    report = run(wheel, tree, stage=verify_release_unit.DEVELOPMENT)
    assert only_failure(report) == "changelog-entry"
    assert "nothing under it" in message(report, "changelog-entry")


def test_the_stage_flag_selects_the_reading_and_the_evidence_records_it(
    tmp_path: Path, wheel: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both stages over one tree, through `main`, with the report saying which ran.

    A `--json` consumer reading `changelog-entry: ok` has to be able to tell
    which of the two questions earned that verdict, or the report is ambiguous
    about the only check with two correct answers.
    """
    tree = _tree_with_leftover_unreleased(tmp_path / "tree")
    monkeypatch.setattr(verify_release_unit, "ROOT", tree)

    assert verify_release_unit.main(["--json", "--tag", TAG, str(wheel)]) == 1
    strict = json.loads(capsys.readouterr().out)
    assert strict["evidence"]["stage"] == verify_release_unit.RELEASE
    assert {c["name"]: c["ok"] for c in strict["checks"]}["changelog-entry"] is False

    assert (
        verify_release_unit.main(
            ["--json", "--tag", TAG, "--stage", verify_release_unit.DEVELOPMENT, str(wheel)]
        )
        == 0
    )
    relaxed = json.loads(capsys.readouterr().out)
    assert relaxed["evidence"]["stage"] == verify_release_unit.DEVELOPMENT
    assert relaxed["ok"] is True


def test_the_release_workflow_asks_the_strict_question() -> None:
    """`release.yml` must never pass `--stage`, so it gets the default.

    The whole point of adding a stage was to stop the landing gate demanding a
    release-time property between releases. It would be self-defeating if the
    flag leaked into the one job where that property is the thing being enforced,
    and a leak is invisible: the workflow would go on exiting 0 while asking a
    weaker question.
    """
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    steps = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    invocations = [line for line in steps if "verify_release_unit.py" in line]
    assert invocations, "release.yml no longer runs the release-unit validator"
    for line in invocations:
        assert "--stage" not in line, line


def test_an_undated_heading_still_matches_its_version(tmp_path: Path, wheel: Path) -> None:
    """`## [0.1.0] - unreleased` is what the tree carries until the tag is cut."""
    tree = build_tree(
        tmp_path / "tree",
        changelog=CHANGELOG_BODY.format(version=VERSION).replace("- 2026-08-28", "- unreleased"),
    )
    report = run(wheel, tree)
    assert verdict(report, "changelog-entry") is True


def test_a_missing_release_link_reference_fails(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(
        tmp_path / "tree",
        changelog=CHANGELOG_BODY.format(version=VERSION).replace(
            "[0.1.0]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.0\n", ""
        ),
    )
    report = run(wheel, tree)
    assert only_failure(report) == "changelog-links"
    assert "no `[0.1.0]:` link reference" in message(report, "changelog-links")


def test_an_unreleased_link_comparing_from_the_wrong_tag_fails(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(
        tmp_path / "tree",
        changelog=CHANGELOG_BODY.format(version=VERSION).replace(
            "compare/v0.1.0...HEAD", "compare/v0.0.9...HEAD"
        ),
    )
    report = run(wheel, tree)
    assert only_failure(report) == "changelog-links"
    assert "does not compare from v0.1.0" in message(report, "changelog-links")


# -------------------------------------------------------------------------- wheel metadata


def test_a_wheel_built_from_another_revision_fails_wheel_version(
    tmp_path: Path, tree: Path
) -> None:
    wheel = build_wheel(tmp_path / "w.whl", version="0.0.9")
    report = run(wheel, tree)
    assert only_failure(report) == "wheel-version"
    assert "declares Version: 0.0.9" in message(report, "wheel-version")


def test_a_wheel_for_another_distribution_is_named_as_such(
    tmp_path: Path, tree: Path
) -> None:
    wheel = build_wheel(tmp_path / "w.whl", name="some-other-package")
    report = run(wheel, tree)
    assert verdict(report, "wheel-version") is False
    assert "not this project's wheel" in message(report, "wheel-version")


def test_the_normalized_underscore_spelling_of_the_name_is_accepted(
    tmp_path: Path, tree: Path
) -> None:
    wheel = build_wheel(tmp_path / "w.whl", name="swe_mux")
    report = run(wheel, tree)
    assert verdict(report, "wheel-version") is True


def test_a_wheel_with_no_metadata_fails_rather_than_raising(
    tmp_path: Path, tree: Path
) -> None:
    wheel = build_wheel(tmp_path / "w.whl", with_metadata=False)
    report = run(wheel, tree)
    assert verdict(report, "wheel-version") is False
    assert "no dist-info METADATA" in message(report, "wheel-version")


# --------------------------------------------------------------------------- project urls


def test_an_unresolved_owner_placeholder_fails(tmp_path: Path, wheel: Path) -> None:
    urls = dict(URLS, Repository="https://github.com/OWNER/swe-mux")
    tree = build_tree(tmp_path / "tree", pyproject=_pyproject(urls=urls))
    report = run(wheel, tree)
    assert verdict(report, "project-urls") is False
    assert "placeholder 'OWNER'" in message(report, "project-urls")


def test_a_plain_http_url_fails(tmp_path: Path, wheel: Path) -> None:
    urls = dict(URLS, Homepage="http://swemux.dev")
    tree = build_tree(tmp_path / "tree", pyproject=_pyproject(urls=urls))
    report = run(wheel, tree)
    assert verdict(report, "project-urls") is False
    assert "rather than https" in message(report, "project-urls")


def test_a_url_that_never_reached_the_wheel_metadata_fails(
    tmp_path: Path, tree: Path
) -> None:
    wheel = build_wheel(tmp_path / "w.whl", urls={"Homepage": URLS["Homepage"]})
    report = run(wheel, tree)
    assert only_failure(report) == "project-urls"
    text = message(report, "project-urls")
    assert "did not reach the wheel's METADATA" in text
    assert "Repository -> https://github.com/jatoran/swe-mux" in text


def test_declaring_no_urls_at_all_fails(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(tmp_path / "tree", pyproject=_pyproject(urls={}))
    report = run(wheel, tree)
    assert verdict(report, "project-urls") is False
    assert "declares no `[project.urls]`" in message(report, "project-urls")


# ---------------------------------------------------------------------- documented commands


def test_a_document_naming_a_command_that_does_not_exist_fails(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(
        tmp_path / "tree",
        readme=README_BODY + "\nRun `muxctl --status` to see the fleet.\n",
    )
    report = run(wheel, tree)
    assert only_failure(report) == "documented-commands"
    text = message(report, "documented-commands")
    assert "muxctl --status" in text
    assert "README.md" in text


def test_a_command_inside_an_html_comment_is_not_an_instruction(
    tmp_path: Path, wheel: Path
) -> None:
    """README's `TODO(release)` block holds a command the project cannot honour yet."""
    tree = build_tree(
        tmp_path / "tree",
        readme=README_BODY + "\n<!-- TODO(release): then `muxctl --status`. -->\n",
    )
    report = run(wheel, tree)
    assert verdict(report, "documented-commands") is True


def test_a_bare_inline_noun_is_not_read_as_a_command(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(
        tmp_path / "tree",
        readme=README_BODY + "\nThe `verify` job, the `desktop` extra, and `master`.\n",
    )
    report = run(wheel, tree)
    assert verdict(report, "documented-commands") is True


def test_a_bare_command_inside_a_fenced_block_is_read_as_one(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(tmp_path / "tree", readme=README_BODY + "\n```\nmuxctl\n```\n")
    report = run(wheel, tree)
    assert only_failure(report) == "documented-commands"
    assert "muxctl" in message(report, "documented-commands")


def test_the_runner_prefix_is_stripped_so_uv_run_names_the_real_command(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(
        tmp_path / "tree",
        readme=README_BODY + "\nRun `uv run --extra desktop --group package muxctl now`.\n",
    )
    report = run(wheel, tree)
    assert only_failure(report) == "documented-commands"
    assert "muxctl" in message(report, "documented-commands")


def test_a_known_third_party_tool_resolves(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(
        tmp_path / "tree",
        readme=README_BODY + "\nThen `docker compose up` and `gh release view v0.1.0`.\n",
    )
    report = run(wheel, tree)
    assert verdict(report, "documented-commands") is True


def test_renaming_an_entry_point_breaks_the_documents_that_name_it(
    tmp_path: Path, wheel: Path
) -> None:
    """The whole point: the docs and `[project.scripts]` are one fact, checked once."""
    scripts = {"mux2": "swe_mux.cli:main", "muxd": "swe_mux.__main__:main"}
    tree = build_tree(tmp_path / "tree", pyproject=_pyproject(scripts=scripts))
    report = run(wheel, tree)
    assert verdict(report, "documented-commands") is False
    assert "mux doctor" in message(report, "documented-commands")


# ------------------------------------------------------------------------- console scripts


def test_a_declared_script_missing_from_the_wheel_fails(
    tmp_path: Path, tree: Path
) -> None:
    scripts = {"mux": SCRIPTS["mux"], "muxd": SCRIPTS["muxd"]}
    wheel = build_wheel(tmp_path / "w.whl", scripts=scripts)
    report = run(wheel, tree)
    assert only_failure(report) == "console-scripts"
    assert "swe-mux (declared swe_mux.desktop:main, wheel has (absent))" in message(
        report, "console-scripts"
    )


def test_a_script_pointing_at_a_different_target_fails(
    tmp_path: Path, tree: Path
) -> None:
    wheel = build_wheel(tmp_path / "w.whl", scripts=dict(SCRIPTS, mux="swe_mux.old_cli:main"))
    report = run(wheel, tree)
    assert only_failure(report) == "console-scripts"
    assert "wheel has swe_mux.old_cli:main" in message(report, "console-scripts")


def test_a_wheel_with_no_entry_points_at_all_fails(tmp_path: Path, tree: Path) -> None:
    wheel = build_wheel(tmp_path / "w.whl", with_entry_points=False)
    report = run(wheel, tree)
    assert only_failure(report) == "console-scripts"
    assert "puts nothing on PATH" in message(report, "console-scripts")


# --------------------------------------------------------------------------- gui scripts


def _split_launchers() -> tuple[dict[str, str], dict[str, str]]:
    """The real repository's split: two console launchers and one GUI launcher."""
    console = {"mux": SCRIPTS["mux"], "muxd": SCRIPTS["muxd"]}
    return console, {"swe-mux": SCRIPTS["swe-mux"]}


def test_a_gui_script_is_a_command_this_project_ships(tmp_path: Path) -> None:
    """`[project.gui-scripts]` builds a launcher in the same scripts directory.

    Which table a command is declared in decides only whether its launcher opens
    a console, which is invisible to "does this command exist". Reading only
    `[project.scripts]` reported `swe-mux` as a command the README documents and
    the project does not ship, the moment it moved to stop the tray popping a
    console window.
    """
    console, gui = _split_launchers()
    tree = build_tree(
        tmp_path / "tree", pyproject=_pyproject(scripts=console, gui_scripts=gui)
    )
    wheel = build_wheel(tmp_path / "w.whl", scripts=console, gui_scripts=gui)
    report = run(wheel, tree)
    assert verdict(report, "documented-commands") is True
    assert verdict(report, "console-scripts") is True
    assert report.evidence["project_scripts"] == SCRIPTS


def test_a_gui_script_missing_from_the_wheel_still_fails(tmp_path: Path) -> None:
    """Widening the tables must not widen them into not checking anything."""
    console, gui = _split_launchers()
    tree = build_tree(
        tmp_path / "tree", pyproject=_pyproject(scripts=console, gui_scripts=gui)
    )
    wheel = build_wheel(tmp_path / "w.whl", scripts=console)
    report = run(wheel, tree)
    assert only_failure(report) == "console-scripts"
    assert "swe-mux (declared swe_mux.desktop:main, wheel has (absent))" in message(
        report, "console-scripts"
    )


# ---------------------------------------------------------------------- migration coherence


def test_two_stores_sharing_one_key_fails(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(
        tmp_path / "tree",
        stores=(
            store_row(LAND_MODULE, LAND_CONSTANT, "5", "shared"),
            store_row(VOICE_MODULE, VOICE_CONSTANT, "3", "shared"),
        ),
    )
    report = run(wheel, tree)
    assert only_failure(report) == "migration-coherence"
    assert "both stamp the store key 'shared'" in message(report, "migration-coherence")


def test_executing_the_forbidden_pragma_fails(tmp_path: Path, wheel: Path) -> None:
    tree = build_tree(tmp_path / "tree")
    (tree / "src/swe_mux/voice.py").write_text(
        "VOICE_SCHEMA_VERSION = 3\n"
        "\n"
        "class Store:\n"
        "    def _connect(self) -> None:\n"
        '        self._db.execute("PRAGMA user_version=3")\n',
        encoding="utf-8",
    )
    report = run(wheel, tree)
    assert only_failure(report) == "migration-coherence"
    text = message(report, "migration-coherence")
    assert "src/swe_mux/voice.py" in text
    assert "neighbour's version" in text


def test_a_comment_or_docstring_naming_the_pragma_is_not_a_violation(
    tmp_path: Path, wheel: Path
) -> None:
    """Three real modules explain in a comment why they do not use it."""
    tree = build_tree(tmp_path / "tree")
    (tree / "src/swe_mux/voice.py").write_text(
        '"""Never PRAGMA user_version: it is a property of the file."""\n'
        "\n"
        "from .sqlite_store import write_schema_version\n"
        "\n"
        "VOICE_SCHEMA_VERSION = 3\n"
        "\n"
        "class Store:\n"
        "    def _connect(self) -> None:\n"
        "        # Per-store row, not PRAGMA user_version.\n"
        '        write_schema_version(self._db, "voice", VOICE_SCHEMA_VERSION)\n',
        encoding="utf-8",
    )
    report = run(wheel, tree)
    assert verdict(report, "migration-coherence") is True


def test_stamping_a_literal_rather_than_the_module_constant_fails(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(
        tmp_path / "tree",
        stores=(
            store_row(LAND_MODULE, LAND_CONSTANT, "5", "land_queue", stamped="5"),
            store_row(VOICE_MODULE, VOICE_CONSTANT, "3", "voice"),
        ),
    )
    report = run(wheel, tree)
    assert only_failure(report) == "migration-coherence"
    assert "stamps a literal version" in message(report, "migration-coherence")


def test_a_schema_version_of_zero_fails(tmp_path: Path, wheel: Path) -> None:
    """`read_schema_version` returns 0 for a database that was never stamped."""
    tree = build_tree(
        tmp_path / "tree",
        stores=(
            store_row(LAND_MODULE, LAND_CONSTANT, "0", "land_queue"),
            store_row(VOICE_MODULE, VOICE_CONSTANT, "3", "voice"),
        ),
    )
    report = run(wheel, tree)
    assert only_failure(report) == "migration-coherence"
    assert "0 is what an unstamped database already reads as" in message(
        report, "migration-coherence"
    )


def test_a_constant_that_is_not_a_module_level_int_fails(
    tmp_path: Path, wheel: Path
) -> None:
    tree = build_tree(tmp_path / "tree")
    (tree / "src/swe_mux/voice.py").write_text(
        "from .sqlite_store import write_schema_version\n"
        "\n"
        "class Store:\n"
        "    def _connect(self) -> None:\n"
        '        write_schema_version(self._db, "voice", VOICE_SCHEMA_VERSION)\n',
        encoding="utf-8",
    )
    report = run(wheel, tree)
    assert only_failure(report) == "migration-coherence"
    assert "not a module-level int" in message(report, "migration-coherence")


# ------------------------------------------------------------------------- unreadable input


def test_a_missing_wheel_reports_the_path_rather_than_raising(
    tmp_path: Path, tree: Path
) -> None:
    report = run(tmp_path / "never-built.whl", tree)
    assert not report.ok
    assert "does not exist" in message(report, "artifact-readable")
    assert "uv build --wheel" in message(report, "artifact-readable")


def test_a_file_that_is_not_a_zip_reports_that(tmp_path: Path, tree: Path) -> None:
    wheel = tmp_path / "truncated.whl"
    wheel.write_bytes(b"not a zip archive")
    report = run(wheel, tree)
    assert not report.ok
    assert "could not be read as a wheel" in message(report, "artifact-readable")


def test_an_unreadable_artifact_still_reports_every_evidence_key(
    tmp_path: Path, tree: Path
) -> None:
    report = run(tmp_path / "never-built.whl", tree)
    assert set(report.evidence) == set(verify_release_unit._empty_evidence())
    assert report.evidence["tag"] == TAG


# ----------------------------------------------------------------------------- the CLI


def test_the_cli_exits_zero_on_a_coherent_release(
    tmp_path: Path, tree: Path, wheel: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(verify_release_unit, "ROOT", tree)
    assert verify_release_unit.main(["--tag", TAG, str(wheel)]) == 0
    out = capsys.readouterr().out
    assert "Release unit check" in out
    assert "11 checks passed" in out


def test_the_cli_exits_nonzero_with_a_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    tree = build_tree(tmp_path / "tree", init_version="0.0.9")
    wheel = build_wheel(tmp_path / "w.whl")
    monkeypatch.setattr(verify_release_unit, "ROOT", tree)
    assert verify_release_unit.main(["--tag", TAG, str(wheel)]) == 1
    out = capsys.readouterr().out
    assert "Release unit validation FAILED" in out
    assert "in one commit" in out


def test_the_tag_falls_back_to_the_workflow_environment(
    tmp_path: Path, tree: Path, wheel: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(verify_release_unit, "ROOT", tree)
    monkeypatch.setenv("GITHUB_REF_NAME", TAG)
    assert verify_release_unit.main([str(wheel)]) == 0
    capsys.readouterr()


def test_a_branch_name_in_the_environment_is_not_taken_for_a_tag(
    tmp_path: Path, tree: Path, wheel: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_release_unit, "ROOT", tree)
    monkeypatch.setenv("GITHUB_REF_NAME", "master")
    with pytest.raises(SystemExit) as raised:
        verify_release_unit.main([str(wheel)])
    assert raised.value.code == 2


def test_no_tag_anywhere_refuses_rather_than_passing(
    tmp_path: Path, tree: Path, wheel: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run with nothing to compare against must not report a pass it did not earn."""
    monkeypatch.setattr(verify_release_unit, "ROOT", tree)
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    with pytest.raises(SystemExit) as raised:
        verify_release_unit.main([str(wheel)])
    assert raised.value.code == 2
    assert "--tag vX.Y.Z" in capsys.readouterr().err


def test_json_output_carries_every_verdict_and_its_evidence(
    tmp_path: Path, wheel: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tree = build_tree(tmp_path / "tree", literal_version="0.0.9")
    monkeypatch.setattr(verify_release_unit, "ROOT", tree)
    assert verify_release_unit.main(["--json", "--tag", TAG, str(wheel)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["evidence"]["tag"] == TAG
    assert payload["evidence"]["project_scripts"] == SCRIPTS
    assert payload["evidence"]["documented_commands"]
    names = {check["name"]: check["ok"] for check in payload["checks"]}
    assert names["version-literals"] is False
    assert names["version-sources"] is True


# ------------------------------------------------------------------- the real repository


def test_this_repository_simulated_as_its_own_release_is_coherent() -> None:
    """The acceptance case, run against the tree rather than a fixture.

    The wheel is synthesized from what `pyproject.toml` declares right now, so
    this asserts the *source-side* half of the release unit - the version
    locations, the changelog, the URLs, the documented commands, and the schema
    stamps - without a `uv build`. The artifact-side half needs a real wheel and
    is `release.yml`'s job.

    When this fails after a version bump, it is not the test that is wrong:
    `RELEASING.md` § 1 lists what a bump has to move in the same commit.

    Asked at the *development* stage, and that is the whole of the difference
    between this test and `release.yml`. This one runs in the landing gate, on
    every branch, at every point in the release cycle - so it can never know that
    a release is happening, and simulating one unconditionally made it demand a
    property that is only true at the tag. It asked for an empty
    `## [Unreleased]` on a tree whose entire purpose between releases is to fill
    that section, and the branches that hit it resolved it the only way a red
    gate allows: by deleting their changelog entries. A check meant to stop a
    release shipping unrecorded changes was causing changes to ship unrecorded.
    `release.yml` runs the same validator at the tag with the default stage,
    where the property is real and still enforced.
    """
    module = verify_release_unit
    tree = module.SourceTree(REPO_ROOT)
    version = module.declared_version(tree)
    assert version, "pyproject.toml declares no version"
    project = tree.project_table()

    with tempfile.TemporaryDirectory() as temporary:
        wheel = build_wheel(
            Path(temporary) / f"swe_mux-{version}-py3-none-any.whl",
            version=version,
            urls=dict(project["urls"]),
            scripts=dict(project["scripts"]),
            # Both launcher tables, because hatchling builds a wheel from both:
            # a synthesized wheel that carried only `[console_scripts]` would
            # report `swe-mux` as absent from an artifact that ships it.
            gui_scripts=dict(project.get("gui-scripts") or {}),
        )
        report = module.verify(wheel, f"v{version}", REPO_ROOT, stage=module.DEVELOPMENT)
    assert report.ok, module.render(report, subject="Release unit")
