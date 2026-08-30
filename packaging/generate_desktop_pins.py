"""Generate `swe_mux/desktop_wheels.py` - the pinned desktop closure - from `uv.lock`.

ROADMAP Phase 24. A PyPI install without the `desktop` extra has no tray and no
native window, and `uv tool upgrade` does not preserve an extra - so the only
recovery used to be a reinstall the user has to know to spell. The closure is
acquired at first use instead, exactly like the voice closure (Workstream D):
pinned URLs with pinned SHA-256s, fetched on an explicit press, unpacked into a
data-dir site directory.

Everything structural is imported from `generate_voice_pins` - the lock loader,
the marker-aware closure walk, the wheel filters, the row builder and the digest
- because a second copy of the closure arithmetic is a second thing that can
drift from `uv.lock`. Only the seeds and the rendered module differ:

    acquire = closure(root + desktop) - closure(root)

A package is acquired only if the `desktop` extra is the only way to reach it.
That is what keeps `cffi`, `pillow` and `typing-extensions` out wherever the
base application already carries them: the set difference is a graph fact, not a
judgement, and `tests/test_desktop_wheels.py` regenerates the table so a
dependency bump that forgets the pins is a red gate rather than an unaudited
first-use download.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from generate_voice_pins import (
    UV_LOCK,
    closure,
    load_lock,
    lock_digest,
    wheel_rows,
)

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "swe_mux" / "desktop_wheels.py"

ACQUIRE_EXTRAS = ("desktop",)


def acquired_packages(lock: dict[str, dict[str, Any]]) -> list[str]:
    base = closure(lock, extras=(), groups=())
    whole = closure(lock, extras=ACQUIRE_EXTRAS, groups=())
    return sorted(whole - base)


def sdist_rows(lock: dict[str, dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    """Pinned sdists for the distributions that publish no wheel at all.

    The desktop closure is the reason this exists: `proxy-tools` is sdist-only
    on PyPI and pywebview imports it unconditionally, so unlike the voice
    closure's `docopt` (sdist-only and never imported) it cannot be omitted.
    The consumer extracts these under the extract-never-build rule
    (`wheel_closure._extract_sdist`); a distribution that is sdist-only AND
    needs a build step is refused there, loudly, at unpack time.
    """
    rows: list[dict[str, Any]] = []
    for name in names:
        entry = lock[name]
        if entry.get("wheels"):
            continue
        sdist = entry.get("sdist")
        if not isinstance(sdist, dict):
            raise SystemExit(
                f"{name}: uv.lock records neither a wheel nor an sdist; the "
                "desktop closure cannot be pinned"
            )
        digest = sdist["hash"]
        if not digest.startswith("sha256:"):
            raise SystemExit(f"{name}: uv.lock records a non-SHA-256 sdist hash")
        url = sdist["url"]
        rows.append(
            {
                "distribution": entry["name"],
                "version": entry["version"],
                "filename": url.rsplit("/", 1)[-1],
                "url": url,
                "sha256": digest.split(":", 1)[1],
                "size": int(sdist["size"]),
            }
        )
    rows.sort(key=lambda row: (row["distribution"].lower(), row["filename"]))
    return rows


HEADER = '''"""The pinned desktop closure, generated from `uv.lock`. Do not edit by hand.

Regenerate with `uv run python packaging/generate_desktop_pins.py --write` after
any change to the `desktop` extra or its transitive resolution.
`tests/test_desktop_wheels.py` fails when this file and `uv.lock` disagree,
because a stale table means a first-use download of a closure this repository
never audited.

The table is every wheel `uv.lock` records for every distribution reachable only
through the `desktop` extra. Which of them this machine wants is a runtime
question (`wheels_for_this_interpreter`), answered against the running
interpreter's own `packaging.tags`. The consumer is
`swe_mux.desktop_runtime`; `packaging/generate_desktop_pins.py` documents the
derivation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesktopWheel:
    """One pinned wheel: what it is, where it is, and what it must hash to."""

    distribution: str
    version: str
    filename: str
    url: str
    sha256: str
    size: int


#: SHA-256 over (filename, sha256, size) of every pin, in table order; the stable
#: identity for "this exact desktop closure".
CLOSURE_DIGEST = "{digest}"

#: The distributions acquired at first use, in dependency-name order.
DISTRIBUTIONS: tuple[str, ...] = (
{distributions}
)

WHEELS: tuple[DesktopWheel, ...] = (
{wheels}
)

#: Distributions that publish no wheel at all, pinned as sdists and extracted -
#: never built - by `wheel_closure._extract_sdist`. `proxy-tools` is the reason
#: this exists: pywebview imports it unconditionally and PyPI has only its
#: sdist (2,978 bytes of pure Python).
SDISTS: tuple[DesktopWheel, ...] = (
{sdists}
)
'''

FOOTER = '''

def wheels_for_this_interpreter(
    wheels: tuple[DesktopWheel, ...] = WHEELS,
) -> tuple[DesktopWheel, ...]:
    """The one wheel per distribution this interpreter can load, best tag first.

    Same selection as `voice_wheels.wheels_for_this_interpreter`, for the same
    reasons; raises `LookupError` naming the distributions with no loadable
    wheel rather than performing a partial install.
    """
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename

    order = {tag: index for index, tag in enumerate(sys_tags())}
    best: dict[str, tuple[int, DesktopWheel]] = {}
    for wheel in wheels:
        try:
            _, _, _, tags = parse_wheel_filename(wheel.filename)
        except Exception:  # noqa: BLE001 - an unparseable filename is simply not a candidate
            continue
        ranks = [order[tag] for tag in tags if tag in order]
        if not ranks:
            continue
        rank = min(ranks)
        current = best.get(wheel.distribution)
        if current is None or rank < current[0]:
            best[wheel.distribution] = (rank, wheel)

    missing = sorted(set(DISTRIBUTIONS) - set(best))
    if missing:
        raise LookupError(
            "the pinned desktop closure has no wheel this interpreter can load for: "
            + ", ".join(missing)
        )
    return tuple(wheel for _, wheel in sorted(best.values(), key=lambda item: item[1].filename))
'''


def _row_lines(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f'    DesktopWheel("{row["distribution"]}", "{row["version"]}", '
        f'"{row["filename"]}", "{row["url"]}", "{row["sha256"]}", {row["size"]}),'
        for row in rows
    )


def render(rows: list[dict[str, Any]], sdists: list[dict[str, Any]]) -> str:
    distributions = "\n".join(
        f'    "{name}",' for name in sorted({row["distribution"] for row in rows})
    )
    return (
        HEADER.format(
            # The digest covers everything the store fetches, sdists included:
            # it is the identity of "this exact closure", and an sdist swap must
            # move it exactly as a wheel swap does.
            digest=lock_digest(rows + sdists),
            distributions=distributions,
            wheels=_row_lines(rows),
            sdists=_row_lines(sdists),
        )
        + FOOTER
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--lock", type=Path, default=UV_LOCK)
    parser.add_argument("--target", type=Path, default=TARGET)
    args = parser.parse_args(argv)

    lock = load_lock(args.lock)
    names = acquired_packages(lock)
    rows = wheel_rows(lock, names)
    sdists = sdist_rows(lock, names)
    rendered = render(rows, sdists)

    if args.write:
        args.target.write_text(rendered, encoding="utf-8", newline="\n")
        print(
            f"wrote {args.target} ({len(rows)} wheels, {len(sdists)} sdists, "
            f"{len(names)} distributions)"
        )
        return 0

    current = args.target.read_text(encoding="utf-8") if args.target.is_file() else ""
    status = "current" if current == rendered else "STALE - run with --write"
    print(f"{len(names)} acquired distributions, {len(rows)} wheels: {status}")
    print("  " + ", ".join(names))
    return 0 if current == rendered else 1


if __name__ == "__main__":
    raise SystemExit(main())
