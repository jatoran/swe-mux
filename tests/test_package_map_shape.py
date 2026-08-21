"""Shape guard for the two package maps in `.docs/technical/`.

These documents are edited by many parallel branches at once, and they used to
be written as tables whose cells held essay-length prose on a single physical
line. Git merges line by line, so every sentence-level edit to a cell became a
conflict over one enormous line, and two branches appending different sentences
to the same cell conflicted every time. Measured over 17 parallel landings in
one week, these two files conflicted on nearly all of them.

The fix is shape, not content: per-feature sections with one sentence per line,
which is this repository's own long-Markdown rule. Disjoint sentence edits then
merge natively. This test keeps that shape from regressing, and lives in
`tests/` so it runs inside the existing verification gate without changing the
gate script's bytes (the land queue's approval is a digest over those bytes).

Both rules below target the same defect from different sides. The sentence
count catches a packed line at any length; the length ceiling catches a run-on
that has no sentence boundaries to count.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).parents[1] / ".docs" / "technical"

# A line over this is a paragraph, not a sentence. The longest line the maps
# currently carry is well under it; anything above needs breaking, not raising.
MAX_LINE_CHARS = 400

# One line may carry a sentence plus a short trailing clause. Three or more
# sentence boundaries on one physical line is the packed-cell pattern.
MAX_SENTENCES_PER_LINE = 2

# End punctuation, optionally closed by inline markup, followed by whitespace
# and the start of a new sentence. Deliberately conservative: it must not fire
# on `foo.py`, an ellipsis, or a decimal.
_SENTENCE_BOUNDARY = re.compile(r"[.?!](?:\*\*|\*|`|\))?\s+(?=[A-Z`(*\[])")


def package_map_files() -> list[Path]:
    """Every package map: the two indexes plus their per-domain files.

    Globbed rather than listed, so a new domain file is covered the moment it
    is added instead of when someone remembers to extend a list.
    """
    files = sorted(DOCS.glob("*/packages.md"))
    files += sorted(DOCS.glob("*/packages/*.md"))
    return files


def test_the_package_maps_are_present() -> None:
    """Guard against the globs silently matching nothing after a move."""
    found = {path.parent.name for path in package_map_files()}
    assert {"backend", "frontend"} <= found, sorted(found)
    assert len(package_map_files()) >= 10


@pytest.mark.parametrize("path", package_map_files(), ids=lambda p: p.as_posix())
def test_no_line_is_a_packed_paragraph(path: Path) -> None:
    offenders = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if len(line) > MAX_LINE_CHARS:
            offenders.append(f"{path.name}:{number} is {len(line)} chars")
        sentences = len(_SENTENCE_BOUNDARY.findall(line))
        if sentences > MAX_SENTENCES_PER_LINE:
            offenders.append(f"{path.name}:{number} packs {sentences + 1} sentences")
    assert not offenders, (
        "Package maps use one sentence per line so parallel branches merge "
        "without conflicting. Break these lines rather than raising the "
        "ceiling:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", package_map_files(), ids=lambda p: p.as_posix())
def test_no_prose_tables(path: Path) -> None:
    """A table row is one line, so prose in a cell is the defect by construction.

    Short inventory tables stay legal; the ceiling is per cell, not per table.
    """
    offenders = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        for cell in stripped.strip("|").split("|"):
            if len(cell.strip()) > 120:
                offenders.append(f"{path.name}:{number} has a {len(cell.strip())}-char cell")
                break
    assert not offenders, (
        "Prose belongs in per-feature sections, not in table cells, because a "
        "cell is one line and cannot be merged sentence by sentence:\n  "
        + "\n  ".join(offenders)
    )


def test_the_indexes_link_every_domain_file() -> None:
    """An unreferenced domain file is invisible to anyone routed to the index."""
    for side in ("backend", "frontend"):
        index = DOCS / side / "packages.md"
        text = index.read_text(encoding="utf-8")
        for domain in sorted((DOCS / side / "packages").glob("*.md")):
            assert f"packages/{domain.name}" in text, (
                f"{index.as_posix()} does not link {domain.name}"
            )
