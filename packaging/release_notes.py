"""Write one release's GitHub Release body out of `CHANGELOG.md`.

    uv run python packaging/release_notes.py --tag v0.1.3
    uv run python packaging/release_notes.py --tag v0.1.3 --out notes.md

The failure this exists to catch
--------------------------------
`release.yml` used to create the Release with `gh release create --generate-notes`,
which produces GitHub's own commit list and compare link rather than the notes a
human wrote. v0.1.3 published with a body that was one line - a `compare/` URL -
while `RELEASING.md` had been stating the contract as "a GitHub Release whose body
is the new `CHANGELOG.md` section" all along. The document described behaviour the
workflow did not have, and nothing could notice: the generated body is a perfectly
well-formed release body, just not this project's.

`CHANGELOG.md` is the source of that text and is deliberately not generated from
commit subjects (`RELEASING.md`, "What is not automated"), which is exactly why
substituting a generated list for it loses the whole of the value.

Why it lives beside `verify_release_unit.py` rather than parsing for itself
--------------------------------------------------------------------------
That module already reads `CHANGELOG.md` in the same workflow run, one job
earlier, and already refuses to publish when the section for the tag is missing,
empty, or still sitting under `## [Unreleased]`. A second parser here could
disagree with the one that gated the release - it would find a section the
validator did not, or draw a different boundary around it - so the section
splitting, the section lookup, and the decision about which bytes a section
publishes are all imported rather than rewritten. `notes_body` is the single
definition of "what this section says", shared by the check that refuses an empty
one and by this, which writes it out.

What it adds on top of the section
----------------------------------
One line: a `**Full changelog**` link to `CHANGELOG.md` **at the tag**, so a
reader who wants the versions before this one has somewhere to go, and so the
link keeps showing the file as it was at that release rather than as master
happens to look years later. The repository URL is derived from the changelog's
own `[X.Y.Z]:` link reference rather than from `$GITHUB_REPOSITORY` or a constant,
because that reference is already validated against the tag by
`verify_release_unit`'s `changelog-links` check - so the footer cannot point
somewhere the changelog itself does not.

GitHub's generated notes are deliberately not appended. This repository lands in
batches, so the generated half is a wall of merge subjects under a hand-written
entry that was written precisely so nobody has to read them.

Exit codes
----------
0 on success, 1 when the notes cannot be produced, 2 when no usable tag was
supplied. There is no fallback and no empty-body success: a Release whose notes
are wrong is worse than a release that stops and says why, because the wrong
notes are what a user reads and nothing later in the pipeline re-examines them.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_release_unit import (  # noqa: E402 - sibling module, path set above
    CHANGELOG,
    ROOT,
    TAG_PATTERN,
    UNRELEASED,
    changelog_links,
    changelog_sections,
    notes_body,
    section_for,
)


class NotesError(Exception):
    """The notes cannot be produced, with the reason a releaser needs to act on."""


def repository_url(text: str, version: str, tag: str) -> str:
    """The repository, read off the changelog's own release-tag reference.

    `[0.1.3]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.3` yields
    `https://github.com/jatoran/swe-mux`. Anything that does not end in that
    exact shape is refused rather than guessed at: a footer link assembled from
    a half-recognized URL is a dead link on the release page, which is the one
    page a user lands on from the update banner.
    """
    reference = changelog_links(text).get(version)
    if reference is None:
        raise NotesError(
            f"{CHANGELOG} has no `[{version}]:` link reference, so the repository URL "
            f"for the footer cannot be derived. Add it at the foot of the file "
            f"pointing at `releases/tag/{tag}`, per RELEASING.md section 1."
        )
    suffix = f"/releases/tag/{tag}"
    if not reference.endswith(suffix):
        raise NotesError(
            f"{CHANGELOG}'s `[{version}]:` reference is {reference}, which does not end "
            f"in `{suffix}`. RELEASING.md section 1 makes that the one spelling, and the "
            f"footer link is built from it."
        )
    return reference[: -len(suffix)]


def build(text: str, tag: str) -> str:
    """The Release body for `tag`, or a `NotesError` saying why there is none."""
    match = TAG_PATTERN.match(tag)
    if match is None:
        raise NotesError(
            f"{tag!r} is not a release tag. RELEASING.md section 3 makes tags `vX.Y.Z` "
            f"exactly, with an optional PEP 440 pre-release suffix."
        )
    version = match.group("version")

    sections = changelog_sections(text)
    section = section_for(sections, version)
    if section is None:
        raise NotesError(
            f"{CHANGELOG} has no `## [{version}]` section "
            f"(it has: {', '.join(sections) or 'no sections at all'}). Move everything "
            f"under `## [{UNRELEASED}]` into a new `## [{version}] - YYYY-MM-DD` section, "
            f"per RELEASING.md section 1. The GitHub Release body is that section."
        )

    body = notes_body(section)
    if not body:
        raise NotesError(
            f"{CHANGELOG} has a `## [{version}]` heading with nothing publishable under "
            f"it. The entry is written by hand from what changed for a user "
            f"(RELEASING.md, 'What is not automated'), so there is nothing to fall back "
            f"to."
        )

    footer = f"**Full changelog**: {repository_url(text, version, tag)}/blob/{tag}/{CHANGELOG}"
    return f"{body}\n\n{footer}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a GitHub Release body from CHANGELOG.md.")
    parser.add_argument(
        "--tag",
        default=os.environ.get("GITHUB_REF_NAME", ""),
        help="the release tag, for example v0.1.3. Defaults to $GITHUB_REF_NAME, which "
        "is what makes the release.yml step a bare invocation.",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=ROOT / CHANGELOG,
        help=f"the changelog to read (default: the checkout's {CHANGELOG}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="write the notes here instead of to stdout. `gh release create "
        "--notes-file` takes a path, and a file needs no shell capture.",
    )
    args = parser.parse_args(argv)

    if not args.tag:
        print(
            "No tag supplied and $GITHUB_REF_NAME is unset or empty. Pass --tag vX.Y.Z; "
            "the notes are the notes *of a release*, so there is no tagless reading.",
            file=sys.stderr,
        )
        return 2

    try:
        text = args.changelog.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Cannot read {args.changelog}: {exc}", file=sys.stderr)
        return 1

    try:
        notes = build(text, args.tag)
    except NotesError as exc:
        print(f"Cannot write release notes for {args.tag}: {exc}", file=sys.stderr)
        return 1

    if args.out is None:
        sys.stdout.write(notes)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so the LF this builds stays LF whatever host runs it. The body is
    # markdown that GitHub renders; CRLF in it is harmless but makes a diff of two
    # generated files unreadable.
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        handle.write(notes)
    print(f"Wrote {len(notes.splitlines())} line(s) of release notes for {args.tag} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
