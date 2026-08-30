"""The GitHub Release body, extracted from `CHANGELOG.md` rather than generated.

`release.yml` used to create the Release with `gh release create --generate-notes`,
which publishes GitHub's own commit list and compare link. v0.1.3 went out with a
body that was one line, while `RELEASING.md` had been describing the contract as
"a Release whose body is the new `CHANGELOG.md` section" since before the first
release. Neither half was wrong on its own; they had simply never been asked to
agree, and no artifact anybody looks at after a release records which one it was.

Two things are covered here, and the second is the one that keeps the first true:

1. `packaging/release_notes.py` takes exactly one version's section, stops at the
   next `## [`, leaves the markdown alone, and **fails** rather than falling back
   when there is nothing to take.
2. `release.yml` actually invokes it, and does not invoke `--generate-notes`. A
   parser that is correct and unwired is the state this whole file exists to
   leave behind.

The parse is pure, so every fixture is a string. The one test that reads the
repository's own `CHANGELOG.md` is at the foot: today's newest release has to
produce notes, because a changelog shape nobody can extract from is a release
that stops on the day it is most expensive to stop.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


release_notes = _load("release_notes")
verify_release_unit = _load("verify_release_unit")


# --------------------------------------------------------------------------- fixtures

REPO = "https://github.com/jatoran/swe-mux"

# Two releases and an empty `Unreleased`, which is the shape the file has the
# moment a release commit is made. The older release is deliberately *below* the
# newer one and the link references are deliberately at the foot, because both
# are boundaries the extractor has to respect and neither is visible in a
# single-section fixture.
CHANGELOG = f"""# Changelog

All notable changes to swe-mux are recorded here.

## [Unreleased]

## [0.2.0] - 2026-09-01

Prose the release wrote about itself.

### Added

- **A thing.**
  With a continuation line that is part of the same bullet.
- Another thing, with `code` and a [link]({REPO}).

### Fixed

- A defect.

## [0.1.0] - 2026-08-28

First public release.

[Unreleased]: {REPO}/compare/v0.2.0...HEAD
[0.2.0]: {REPO}/releases/tag/v0.2.0
[0.1.0]: {REPO}/releases/tag/v0.1.0
"""

FOOTER_020 = f"**Full changelog**: {REPO}/blob/v0.2.0/CHANGELOG.md"


def build(text: str = CHANGELOG, tag: str = "v0.2.0") -> str:
    return release_notes.build(text, tag)


# --------------------------------------------------------------------------- extraction


def test_the_body_is_that_version_s_section_and_stops_at_the_next_one() -> None:
    notes = build()
    assert notes.startswith("Prose the release wrote about itself.")
    # The heading of the section itself is not repeated: the Release is titled
    # with the tag and dated by GitHub, so a `## [0.2.0] - 2026-09-01` line at the
    # top of the body would say both things a second time.
    assert "## [0.2.0]" not in notes
    # And the release below it is a different release.
    assert "First public release." not in notes
    assert "## [0.1.0]" not in notes


def test_an_older_version_is_reachable_by_its_own_tag() -> None:
    notes = build(tag="v0.1.0")
    assert "First public release." in notes
    assert "Prose the release wrote about itself." not in notes


def test_the_markdown_is_kept_intact() -> None:
    notes = build()
    assert "### Added" in notes
    assert "### Fixed" in notes
    assert "- **A thing.**\n  With a continuation line that is part of the same bullet." in notes
    assert f"- Another thing, with `code` and a [link]({REPO})." in notes
    # A blank line between a heading and its list is what makes the list a list.
    assert "### Added\n\n- **A thing.**" in notes


def test_the_link_reference_footer_never_reaches_the_body() -> None:
    """The last section contains the file's references by any structural reading.

    `changelog_sections` splits on headings, so `[0.1.0]: ...` and its neighbours
    land inside whichever release is last. Publishing them would put three bare
    reference lines at the end of that release's notes, and they render as
    nothing at all - a body that ends in silence rather than in the entry.
    """
    notes = build(tag="v0.1.0")
    assert "[Unreleased]: " not in notes
    assert "[0.1.0]: " not in notes
    assert notes.splitlines()[0] == "First public release."


def test_a_release_below_the_footer_boundary_still_ends_at_its_entry() -> None:
    notes = build(tag="v0.1.0")
    body = notes.replace(f"\n\n**Full changelog**: {REPO}/blob/v0.1.0/CHANGELOG.md\n", "")
    assert body == "First public release."


# --------------------------------------------------------------------------- the footer


def test_the_footer_links_the_changelog_at_the_tag() -> None:
    """Pinned to the tag, not to master.

    A reader arrives at a release page long after it was cut, and `blob/master`
    would show them a file that has moved on - including, eventually, entries for
    versions that did not exist when this one shipped.
    """
    assert build().endswith(f"{FOOTER_020}\n")
    assert build(tag="v0.1.0").endswith(f"{REPO}/blob/v0.1.0/CHANGELOG.md\n")


def test_the_repository_url_comes_from_the_changelog_s_own_reference() -> None:
    """So the footer cannot point somewhere the changelog does not.

    `verify_release_unit`'s `changelog-links` check already proves that reference
    names the tag, one job earlier in the same workflow. Deriving the footer from
    it means the two cannot disagree; deriving it from `$GITHUB_REPOSITORY` or a
    constant here would be a second, unchecked opinion about where this project
    lives.
    """
    moved = CHANGELOG.replace(f"{REPO}/releases/tag/v0.2.0", "https://example.invalid/x/releases/tag/v0.2.0")
    assert build(moved).endswith("https://example.invalid/x/blob/v0.2.0/CHANGELOG.md\n")


def test_a_missing_release_reference_is_a_failure_rather_than_a_guess() -> None:
    without = CHANGELOG.replace(f"[0.2.0]: {REPO}/releases/tag/v0.2.0\n", "")
    with pytest.raises(release_notes.NotesError) as excinfo:
        build(without)
    assert "`[0.2.0]:` link reference" in str(excinfo.value)


def test_a_reference_that_does_not_name_the_tag_is_refused() -> None:
    wrong = CHANGELOG.replace(f"[0.2.0]: {REPO}/releases/tag/v0.2.0", f"[0.2.0]: {REPO}/releases")
    with pytest.raises(release_notes.NotesError) as excinfo:
        build(wrong)
    assert "/releases/tag/v0.2.0" in str(excinfo.value)


# --------------------------------------------------------------------------- refusals


def test_a_version_with_no_section_stops_the_release() -> None:
    with pytest.raises(release_notes.NotesError) as excinfo:
        build(tag="v0.3.0")
    message = str(excinfo.value)
    assert "no `## [0.3.0]` section" in message
    # The reader is told what the file does have, which is how a typo in the tag
    # and a changelog nobody updated tell themselves apart.
    assert "Unreleased" in message and "0.2.0" in message


def test_an_entry_still_sitting_under_unreleased_stops_the_release() -> None:
    """The exact state `RELEASING.md` step 1 exists to prevent.

    The version was bumped and its heading written, but the entries were never
    moved out of `## [Unreleased]` above it. `verify_release_unit` fails the
    `build` job for this too, so in the real pipeline it never reaches here.
    Covered anyway because the two are separate scripts and the second one must
    not have a softer answer than the first.
    """
    stalled = f"""# Changelog

## [Unreleased]

### Added

- The entries for 0.2.0, never moved down.

## [0.2.0] - 2026-09-01

[Unreleased]: {REPO}/compare/v0.2.0...HEAD
[0.2.0]: {REPO}/releases/tag/v0.2.0
"""
    with pytest.raises(release_notes.NotesError) as excinfo:
        build(stalled)
    assert "nothing publishable" in str(excinfo.value)


def test_a_heading_with_only_whitespace_under_it_is_empty() -> None:
    blank = CHANGELOG.replace(
        "## [0.1.0] - 2026-08-28\n\nFirst public release.\n", "## [0.1.0] - 2026-08-28\n\n   \n"
    )
    with pytest.raises(release_notes.NotesError) as excinfo:
        build(blank, tag="v0.1.0")
    assert "nothing publishable" in str(excinfo.value)


@pytest.mark.parametrize("tag", ["0.2.0", "release-0.2.0", "v0.2", "Unreleased", "vlatest"])
def test_only_a_release_tag_names_a_release(tag: str) -> None:
    """`RELEASING.md` section 3 makes `vX.Y.Z` the one spelling.

    `Unreleased` is in this list on purpose. It is a real heading in the file, so
    a looser reading of "find the section named by the argument" would happily
    publish the unreleased entries as a release.
    """
    with pytest.raises(release_notes.NotesError) as excinfo:
        build(tag=tag)
    assert "is not a release tag" in str(excinfo.value)


def test_a_prerelease_tag_is_a_release_tag() -> None:
    """The TestPyPI alpha path in `release.yml` keys its `--prerelease` flag off
    exactly this shape, so refusing it here would make an alpha unpublishable."""
    alpha = CHANGELOG.replace("## [0.2.0] - 2026-09-01", "## [0.2.0rc1] - 2026-09-01").replace(
        f"[0.2.0]: {REPO}/releases/tag/v0.2.0", f"[0.2.0rc1]: {REPO}/releases/tag/v0.2.0rc1"
    )
    assert "Prose the release wrote about itself." in build(alpha, tag="v0.2.0rc1")


# --------------------------------------------------------------------------- one parser


def test_the_extractor_and_the_validator_read_the_same_bytes() -> None:
    """Not a tautology, and worth asserting rather than trusting to the import.

    `verify_release_unit` is what refuses to publish an empty section; this is
    what writes the section out. If they ever draw the boundary differently, the
    gate passes on one reading and the body is built from another - which is the
    class of defect that produced this file. `notes_body` is the single
    definition, and this is the assertion that it stayed single.
    """
    sections = verify_release_unit.changelog_sections(CHANGELOG)
    section = verify_release_unit.section_for(sections, "0.2.0")
    assert section is not None
    body = verify_release_unit.notes_body(section)
    assert build() == f"{body}\n\n{FOOTER_020}\n"


# --------------------------------------------------------------------------- the CLI


def test_out_writes_the_notes_and_exits_zero(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    out = tmp_path / "nested" / "notes.md"
    code = release_notes.main(
        ["--tag", "v0.2.0", "--changelog", str(changelog), "--out", str(out)]
    )
    assert code == 0
    assert out.read_text(encoding="utf-8") == build()
    # LF whatever host ran it, so two generated files diff as their content.
    assert "\r\n" not in out.read_bytes().decode("utf-8")


def test_a_missing_section_exits_one_and_writes_no_file(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    out = tmp_path / "notes.md"
    code = release_notes.main(
        ["--tag", "v9.9.9", "--changelog", str(changelog), "--out", str(out)]
    )
    assert code == 1
    assert not out.exists()


def test_an_unreadable_changelog_exits_one(tmp_path: Path) -> None:
    assert release_notes.main(["--tag", "v0.2.0", "--changelog", str(tmp_path / "nope.md")]) == 1


def test_no_tag_at_all_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """A distinct code from a failed extraction, matching `verify_release_unit`.

    Exit 2 is "you did not ask a question"; exit 1 is "the answer is no". A
    workflow that lost `$GITHUB_REF_NAME` and a changelog missing its section are
    different repairs.
    """
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    assert release_notes.main([]) == 2


# --------------------------------------------------------------------------- the wiring


def _release_workflow_steps() -> list[str]:
    """`release.yml` with its comments removed.

    Comment-stripped rather than read whole, because the header of the very step
    under test explains `--generate-notes` at length - it is the reason the step
    exists. An assertion that cannot tell a command from the prose about it is an
    assertion that forbids writing the prose down.
    """
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_the_release_workflow_builds_the_body_from_the_changelog() -> None:
    steps = "\n".join(_release_workflow_steps())
    assert "packaging/release_notes.py" in steps
    assert "--notes-file" in steps


def test_the_release_workflow_does_not_generate_its_notes() -> None:
    """The regression this whole file is about, asserted rather than remembered.

    `--generate-notes` is not a worse-but-related option; it publishes a
    different document, assembled from commit subjects, over a changelog that
    `RELEASING.md` says is deliberately not generated from them.
    """
    assert "--generate-notes" not in "\n".join(_release_workflow_steps())


def test_the_notes_are_applied_however_the_release_came_to_exist() -> None:
    """Create and edit both carry `--notes-file`.

    The draft-release path is the one where a body written by hand would survive
    into publication, so setting the notes only on create would leave exactly the
    gap this closes.
    """
    steps = _release_workflow_steps()
    invocations = [
        line for line in steps if "gh release create " in line or "gh release edit " in line
    ]
    assert len(invocations) == 2, invocations
    for line in invocations:
        assert "--notes-file" in line, line.strip()


# --------------------------------------------------------------------------- this repository


def test_this_repository_s_newest_release_produces_notes() -> None:
    """Today's tree, extracted as the release it most recently was.

    Everything above is a fixture in the shape the file has; this is the file.
    The newest `## [X.Y.Z]` is found rather than named, so this keeps asking the
    question after the next release instead of pinning to a version that ages.
    """
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = verify_release_unit.changelog_sections(text)
    released = [label for label in sections if label != verify_release_unit.UNRELEASED]
    assert released, "CHANGELOG.md has no released sections at all"
    newest = released[0]

    notes = release_notes.build(text, f"v{newest}")
    assert notes.strip(), "the newest release extracts to an empty body"
    assert notes.endswith(f"/blob/v{newest}/CHANGELOG.md\n")
    # No neighbour bled in, in either direction.
    for other in released[1:]:
        assert f"## [{other}]" not in notes
