"""Fails when a release shipped without a changelog entry. Run from anywhere:

    python site/tools/check_changelog.py               # every released version
    python site/tools/check_changelog.py --tag v0.1.2  # one, before it is published

The site publishes `/changelog/` straight out of `CHANGELOG.md`, so a release with
no entry is not merely an internal lapse: the public page silently omits a version
that exists on PyPI and in GitHub Releases, and the omission looks identical to a
release that changed nothing. `tools/build.py` cannot catch it, because a
changelog with a missing section renders perfectly.

**What counts as a release** is a `v*` tag, which is what `release.yml` publishes
from and what `[X.Y.Z]: .../releases/tag/vX.Y.Z` in `CHANGELOG.md` points at. The
package version in `pyproject.toml` is checked too, because a version bumped and
not yet tagged is the state a release is cut from and the cheapest moment to
notice the entry is missing.

Five things are checked per version, and each of them has shipped wrong somewhere:

1. **The section exists.** `## [X.Y.Z] - <date>`.
2. **It carries a real date.** The RELEASING.md procedure says to date the section
   with the day the tag is cut, and the placeholder it replaces is the literal
   word `unreleased` - which renders on the public page as a released version
   claiming not to be one.
3. **It is not empty.** A heading with nothing under it reads as a rendering bug.
4. **Its link reference resolves to that tag's release.** The site turns the
   heading into a link from exactly this reference, so a missing one publishes a
   version nobody can click through to.
5. **No `TODO(release)` marker survives.** RELEASING.md's own checklist says to
   confirm this, and the marker is how step 2 gets forgotten in the first place.

It also checks the two structural invariants a Keep a Changelog file has: an
`## [Unreleased]` heading with a reference, and no version appearing twice.

No arguments, no configuration, and a non-zero exit on failure, like
`tools/check.mjs` and `tools/contrast.py` beside it. `--tag` exists for the one
caller that needs it: a release workflow, which knows the tag it is about to
publish and wants the check to fail before the artifacts go out rather than after.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"

#: The heading a release takes, and the two halves of it that are checked
#: separately: a section with no date is a different failure from no section.
RELEASE_HEADING = re.compile(r"^## \[([^\]]+)\](?:\s*-\s*(.*?))?\s*$", re.M)
LINK_REF = re.compile(r"^\[([^\]]+)\]:\s*(\S+)\s*$", re.M)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL {msg}")


def released_versions(tag: str | None) -> list[str]:
    """The versions that must have an entry: `--tag`'s, or every `v*` tag there is.

    A repository with no tags is not an error. That is a fresh clone with no
    fetched tags, or a project before its first release, and failing there would
    train whoever runs this to ignore it.
    """
    if tag:
        return [tag.removeprefix("v")]
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "tag", "--list", "v*"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"  note: could not list git tags ({exc}); checking the package version only")
        return []
    return [line.strip().removeprefix("v") for line in out.splitlines() if line.strip()]


def package_version() -> str | None:
    m = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tag",
        help="check this tag alone (for example v0.1.2), rather than every tag in the "
        "repository. Use it from a release workflow, where the tag exists but the "
        "release does not yet.",
    )
    args = ap.parse_args()

    text = CHANGELOG.read_text(encoding="utf-8")
    headings = RELEASE_HEADING.findall(text)
    sections = {name: (date or "").strip() for name, date in headings}
    names = [name for name, _ in headings]
    refs = dict(LINK_REF.findall(text))

    print(f"changelog  {CHANGELOG.relative_to(ROOT).as_posix()}")

    duplicates = sorted({n for n in names if names.count(n) > 1})
    for name in duplicates:
        fail(f"[{name}] appears {names.count(name)} times; a version has one section")

    if "Unreleased" not in sections:
        fail("there is no `## [Unreleased]` heading; Keep a Changelog keeps one standing")
    elif "Unreleased" not in refs:
        fail("`[Unreleased]` has no link reference at the foot of the file")

    for marker in re.findall(r"TODO\(release\)[^\n]*", text):
        fail(f"a release placeholder survives: {marker.strip()}")

    wanted = released_versions(args.tag)
    version = package_version()
    if version and version not in wanted:
        # Not a released version yet, so the entry is allowed to be undated: this
        # is the bump that a release is about to be cut from, and the useful thing
        # to say now is that the section is missing entirely.
        if version not in sections:
            fail(
                f"pyproject.toml declares version {version}, which has no "
                f"`## [{version}]` section. Write the entry before cutting the tag."
            )
        else:
            print(f"  {version}  declared in pyproject.toml, section present")

    if not wanted:
        print("  no released versions to check")

    for name in wanted:
        if name not in sections:
            fail(
                f"v{name} is tagged but `CHANGELOG.md` has no `## [{name}]` section. The "
                "published changelog would omit a release that exists."
            )
            continue
        date = sections[name]
        if not ISO_DATE.match(date):
            fail(
                f"[{name}] is dated {date!r}, which is not a YYYY-MM-DD date. RELEASING.md "
                "dates the section with the day the tag is cut."
            )
        body = _section_body(text, name)
        if not body.strip():
            fail(f"[{name}] has a heading and no content under it")
        href = refs.get(name)
        if not href:
            fail(
                f"[{name}] has no link reference, so the published page cannot link the "
                "release it names"
            )
        elif f"/releases/tag/v{name}" not in href:
            fail(f"[{name}] links to {href}, which is not that version's release tag")
        print(f"  {name}  {date or '(undated)'}  entry present")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S)")
        return 1
    print("every released version has a dated changelog entry")
    return 0


def _section_body(text: str, name: str) -> str:
    m = re.search(
        rf"^## \[{re.escape(name)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        text,
        re.M | re.S,
    )
    if not m:
        return ""
    # Link references live at the foot of the file and belong to no section; a
    # comment is not content either, and the placeholder comment is exactly what
    # an empty first release contains.
    body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    return re.sub(r"^\[[^\]]+\]:\s*\S+\s*$", "", body, flags=re.M)


if __name__ == "__main__":
    sys.exit(main())
