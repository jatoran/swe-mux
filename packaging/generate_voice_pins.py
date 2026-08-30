"""Generate `swe_mux/voice_wheels.py` - the pinned voice closure - from `uv.lock`.

Why this is generated rather than hand-written
----------------------------------------------
The desktop bundle no longer carries the on-device speech closure (ROADMAP Phase
21, Workstream D). It is acquired at first use instead, from the same index the
project resolved it from, verified against a pinned SHA-256. That makes the pin
table a *description of this repository's resolution*, and a description that is
maintained by hand drifts from the thing it describes the first time anybody runs
`uv lock --upgrade`.

So the table is derived from `uv.lock`, which is the only artifact in this
repository that already knows the answer: every wheel's URL, size and SHA-256 are
in it, because uv put them there in order to install them. `tests/
test_voice_wheels.py` regenerates the table and fails when the committed copy
differs, which turns "somebody bumped a dependency and forgot the pins" from a
runtime download of an unaudited closure into a red gate.

What "the voice closure" means here, exactly
--------------------------------------------
The set difference between two closures over `uv.lock`'s own dependency graph:

    acquire = closure(root + desktop + voice-local + g2p-model) - closure(root + desktop)

A package is *kept* if the base application can reach it on any supported
environment, and *acquired* only if the voice extras are the only way to reach it.
That is a graph question rather than a judgement, which is the point: `numpy`,
`jinja2`, `wrapt` and `pyyaml` all look like base infrastructure and all of them
are, in this project, reachable only through spaCy and faster-whisper.

`pyinstaller` is deliberately not a seed. It is in the `package` group, it is a
build tool, and it reaches no user - the same reasoning `license_audit.
python_closure` applies when it excludes groups by default.

Markers are evaluated across every supported environment rather than this one, so
a Linux-only or macOS-only edge is still in the table when the generator runs on
Windows. Wheel *selection* is then a runtime question, answered by
`voice_wheels.wheels_for_this_interpreter` against `packaging.tags`.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
UV_LOCK = ROOT / "uv.lock"
TARGET = ROOT / "src" / "swe_mux" / "voice_wheels.py"

#: The project's own distribution name in `uv.lock`.
ROOT_PACKAGE = "swe-mux"

#: Seeds that stay in the shipped bundle, beyond the base requirements the walk
#: always takes. Empty since 2026-08-30: `desktop` was the only entry, and
#: `pystray`/`pywebview` moved into base `dependencies` (`pyproject.toml`), which
#: is where the walk already finds them. Naming an empty extra here would read as
#: a seed while contributing nothing - the failure mode this file's own docstring
#: warns about for the acquire side.
KEEP_EXTRAS: tuple[str, ...] = ()
KEEP_GROUPS: tuple[str, ...] = ()

#: Seeds that are acquired at first use instead of shipped.
ACQUIRE_EXTRAS = ("voice-local",)
ACQUIRE_GROUPS = ("g2p-model",)

# Acquired at first use, but by a store that already exists and already owns it.
# `en-core-web-sm` is the spaCy model `voice_models.SpacyModelStore` has fetched,
# pinned, unpacked onto `sys.path` and reported since 2026-08-28, with its own
# settings panel and its own `mux doctor` check. Two stores fetching one wheel
# into two directories is two answers to "is the G2P model ready", and the one
# that is wrong is whichever the reader did not look at.
#
# It is also the only entry in `uv.lock` with no `size`, because it resolves from
# a GitHub release asset rather than from an index - which is exactly why it
# needed a store with a measured constant in the first place.
OWNED_BY_ANOTHER_STORE = ("en-core-web-sm",)

# The environments swe-mux is distributed for, mirroring
# `license_audit.SUPPORTED_ENVIRONMENTS`. A package counts as reachable if any of
# them reaches it, because the table is consumed on all of them.
SUPPORTED_ENVIRONMENTS: tuple[dict[str, str], ...] = tuple(
    {
        "sys_platform": platform,
        "os_name": os_name,
        "platform_system": system,
        "platform_machine": machine,
        "python_full_version": version,
        "python_version": version.rsplit(".", 1)[0],
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
    }
    for platform, os_name, system, machine in (
        ("win32", "nt", "Windows", "AMD64"),
        ("linux", "posix", "Linux", "x86_64"),
        ("linux", "posix", "Linux", "aarch64"),
        ("darwin", "posix", "Darwin", "arm64"),
        ("darwin", "posix", "Darwin", "x86_64"),
    )
    for version in ("3.12.0", "3.13.0", "3.14.0")
)


# Wheel platform tags the table keeps. `uv.lock` records every wheel a
# distribution publishes - 583 of them for this closure, most for interpreters and
# architectures swe-mux does not support - and a table four times larger than it
# needs to be is one nobody reads the diff of. The filter is a property of what
# this project ships for, so it lives in the generator and is asserted by
# `tests/test_voice_wheels.py` rather than trimmed by hand.
KEPT_PLATFORM_PREFIXES = (
    "any",
    "win_amd64",
    "win_arm64",
    "manylinux",  # narrowed to x86_64/aarch64 by the suffix check
    "macosx",  # narrowed to arm64/x86_64/universal2 by the suffix check
)
KEPT_PLATFORM_SUFFIXES = ("x86_64", "aarch64", "arm64", "universal2", "amd64", "any")

#: `requires-python = ">=3.12"`, plus room for the interpreters CI runs.
MIN_MINOR = 12
MAX_MINOR = 14


def _keeps_platform(platform: str) -> bool:
    if not platform.startswith(KEPT_PLATFORM_PREFIXES):
        return False
    # musllinux and the 32-bit legs share the manylinux prefix space in name only;
    # the architecture suffix is what actually decides.
    return platform.endswith(KEPT_PLATFORM_SUFFIXES)


def _keeps_interpreter(interpreter: str, abi: str) -> bool:
    if interpreter.startswith("py"):  # py3, py2.py3 - version-agnostic pure Python
        return True
    if not interpreter.startswith("cp"):  # pypy, graalpy and friends
        return False
    digits = interpreter[2:]
    if not digits.isdigit():
        return False
    minor = int(digits[1:]) if len(digits) > 1 else 0
    if abi == "abi3":
        # A stable-ABI wheel built for an older interpreter loads on every later
        # one, so the floor is what matters and the ceiling does not apply.
        return minor <= MAX_MINOR
    if digits.endswith("t"):  # free-threaded builds are not a supported target
        return False
    return MIN_MINOR <= minor <= MAX_MINOR


def _keeps(filename: str) -> bool:
    from packaging.utils import InvalidWheelFilename, parse_wheel_filename

    try:
        _, _, _, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return False
    return any(
        _keeps_platform(tag.platform)
        and _keeps_interpreter(tag.interpreter, tag.abi)
        for tag in tags
    )


def _canon(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def load_lock(lock_path: Path = UV_LOCK) -> dict[str, dict[str, Any]]:
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {_canon(entry["name"]): entry for entry in data.get("package", [])}


def _reachable(marker: str | None) -> bool:
    """Whether an edge exists on any supported environment.

    A marker that this `packaging` cannot parse is treated as reachable. The
    alternative - dropping the edge - would silently shrink the closure, and a
    closure that is too small is a download that fails at first use on somebody
    else's machine.
    """
    if not marker:
        return True
    from packaging.markers import InvalidMarker, Marker

    try:
        parsed = Marker(marker)
    except InvalidMarker:
        return True
    for environment in SUPPORTED_ENVIRONMENTS:
        try:
            if parsed.evaluate(environment):
                return True
        except Exception:  # noqa: BLE001 - an unevaluable marker is not a reason to drop an edge
            return True
    return False


def _edges(entries: list[dict[str, Any]] | None) -> list[str]:
    return [
        _canon(item["name"])
        for item in (entries or [])
        if _reachable(item.get("marker"))
    ]


def closure(
    lock: dict[str, dict[str, Any]],
    *,
    extras: tuple[str, ...],
    groups: tuple[str, ...],
) -> set[str]:
    """Every distribution reachable from the root plus the named extras/groups."""
    root = lock[_canon(ROOT_PACKAGE)]
    stack = list(_edges(root.get("dependencies")))
    optional = root.get("optional-dependencies", {})
    for extra in extras:
        stack += _edges(optional.get(extra))
    dev = root.get("dev-dependencies", {})
    for group in groups:
        stack += _edges(dev.get(group))

    seen: set[str] = set()
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        entry = lock.get(name)
        if entry is None:
            continue
        stack += _edges(entry.get("dependencies"))
        # An optional edge of a *transitive* package is only taken when something
        # asked for that extra, and nothing in this project's graph does. Ignoring
        # them here matches what uv resolved rather than widening past it.
    return seen


def acquired_packages(lock: dict[str, dict[str, Any]]) -> list[str]:
    keep = closure(lock, extras=KEEP_EXTRAS, groups=KEEP_GROUPS)
    whole = closure(
        lock,
        extras=KEEP_EXTRAS + ACQUIRE_EXTRAS,
        groups=KEEP_GROUPS + ACQUIRE_GROUPS,
    )
    return sorted(whole - keep - {_canon(name) for name in OWNED_BY_ANOTHER_STORE})


def wheel_rows(lock: dict[str, dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    """Every wheel `uv.lock` records for the acquired distributions.

    Every wheel rather than this platform's, because the generator runs on one
    machine and the table is consumed on three. Selection is a runtime decision
    made against the running interpreter's own tags, which is the only place the
    answer is knowable.
    """
    rows: list[dict[str, Any]] = []
    sdist_only: list[str] = []
    for name in names:
        entry = lock[name]
        wheels = entry.get("wheels") or []
        if not wheels:
            sdist_only.append(name)
            continue
        kept = [w for w in wheels if _keeps(w["url"].rsplit("/", 1)[-1])]
        if not kept:
            raise SystemExit(
                f"{name}: uv.lock publishes {len(wheels)} wheels and the supported-platform "
                "filter kept none of them. Either the distribution stopped publishing for a "
                "target swe-mux ships for, or KEPT_PLATFORM_PREFIXES/MIN_MINOR/MAX_MINOR "
                "need widening. Do not narrow the closure to make this pass."
            )
        for wheel in kept:
            url = wheel["url"]
            digest = wheel["hash"]
            if not digest.startswith("sha256:"):
                raise SystemExit(
                    f"{name}: uv.lock records a non-SHA-256 hash ({digest.split(':')[0]}). "
                    "The store verifies SHA-256 and nothing else; a new hash algorithm "
                    "here is a deliberate change to the verification, not a pin update."
                )
            rows.append(
                {
                    "distribution": entry["name"],
                    "version": entry["version"],
                    "filename": url.rsplit("/", 1)[-1],
                    "url": url,
                    "sha256": digest.split(":", 1)[1],
                    "size": int(wheel["size"]),
                }
            )
    if sdist_only:
        # Not fatal, and the reason matters: `docopt` is a `num2words` dependency
        # that has never published a wheel, and `num2words`'s importable package
        # does not import it (only its console script does). A package that is
        # both sdist-only and actually imported would be a real problem, and this
        # message is what would surface it.
        print(
            "note: no wheel in uv.lock for "
            + ", ".join(sdist_only)
            + " - these are omitted from the pin table and must not be imported "
            "by any voice code path.",
            file=sys.stderr,
        )
    rows.sort(key=lambda row: (row["distribution"].lower(), row["filename"]))
    return rows


def lock_digest(rows: list[dict[str, Any]]) -> str:
    """A digest over exactly what the table pins.

    Not over `uv.lock` as a whole: that changes whenever any dependency moves,
    and a digest that churns on unrelated bumps is one somebody re-blesses without
    reading. This one moves only when the acquired closure moves.
    """
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row['filename']}\n{row['sha256']}\n{row['size']}\n".encode())
    return digest.hexdigest()


HEADER = '''"""The pinned voice closure, generated from `uv.lock`. Do not edit by hand.

Regenerate with `uv run python packaging/generate_voice_pins.py --write` after any
change to the `voice-local` extra, the `g2p-model` group, or their transitive
resolution. `tests/test_voice_wheels.py` fails when this file and `uv.lock`
disagree, because a stale table means a first-use download of a closure this
repository never audited.

The table is every wheel `uv.lock` records for every distribution reachable only
through the voice extras. Which of them this machine wants is a runtime question
(`wheels_for_this_interpreter`), answered against the running interpreter's own
`packaging.tags`, because the generator runs on one platform and the table is
consumed on three.

`swe_mux.voice_runtime` is the consumer; `packaging/generate_voice_pins.py`
documents how the closure is derived and why it is a set difference rather than a
list somebody maintains.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceWheel:
    """One pinned wheel: what it is, where it is, and what it must hash to."""

    distribution: str
    version: str
    filename: str
    url: str
    sha256: str
    size: int


#: SHA-256 over (filename, sha256, size) of every pin, in table order. Moves only
#: when the acquired closure moves, so it is a stable identity for "this exact
#: voice closure" that a state file and a diagnostic can both carry.
CLOSURE_DIGEST = "{digest}"

#: The distributions acquired at first use, in dependency-name order.
DISTRIBUTIONS: tuple[str, ...] = (
{distributions}
)

WHEELS: tuple[VoiceWheel, ...] = (
{wheels}
)
'''

FOOTER = '''

def wheels_for_this_interpreter(
    wheels: tuple[VoiceWheel, ...] = WHEELS,
) -> tuple[VoiceWheel, ...]:
    """The one wheel per distribution this interpreter can load, best tag first.

    Uses `packaging.tags.sys_tags()` - the same ordering pip and uv use - rather
    than a hand-rolled `(sys.platform, machine, version)` key, because the
    interesting cases are exactly the ones a hand-rolled key gets wrong: an abi3
    wheel built for cp39 that loads on 3.12, a `py2.py3-none-any` wheel, a macOS
    wheel whose deployment target is older than the running system's.

    Raises `LookupError` naming the distributions this interpreter has no wheel
    for. That is a refusal rather than a partial install: a closure missing one
    native package fails at import time, much later, with an error that names the
    wrong thing.
    """
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename

    order = {tag: index for index, tag in enumerate(sys_tags())}
    best: dict[str, tuple[int, VoiceWheel]] = {}
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
            "the pinned voice closure has no wheel this interpreter can load for: "
            + ", ".join(missing)
        )
    return tuple(wheel for _, wheel in sorted(best.values(), key=lambda item: item[1].filename))


def total_bytes(wheels: tuple[VoiceWheel, ...]) -> int:
    return sum(wheel.size for wheel in wheels)
'''


def render(rows: list[dict[str, Any]], names: list[str]) -> str:
    distributions = "\n".join(
        f'    "{lock_name}",' for lock_name in sorted({row["distribution"] for row in rows})
    )
    # One line per wheel, positionally. A 394-entry table rendered over eight
    # lines each is 3000 lines of diff nobody reads; one line each is a diff where
    # a changed pin is visibly one changed pin.
    wheels = "\n".join(
        f'    VoiceWheel("{row["distribution"]}", "{row["version"]}", '
        f'"{row["filename"]}", "{row["url"]}", "{row["sha256"]}", {row["size"]}),'
        for row in rows
    )
    body = HEADER.format(
        digest=lock_digest(rows),
        distributions=distributions,
        wheels=wheels,
    )
    return body + FOOTER


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite src/swe_mux/voice_wheels.py instead of printing a summary",
    )
    parser.add_argument("--lock", type=Path, default=UV_LOCK)
    parser.add_argument("--target", type=Path, default=TARGET)
    args = parser.parse_args(argv)

    lock = load_lock(args.lock)
    names = acquired_packages(lock)
    rows = wheel_rows(lock, names)
    rendered = render(rows, names)

    if args.write:
        # Explicit newline so this file does not gain CRLF on Windows, which would
        # make the parity test fail against a freshly generated LF copy.
        args.target.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"wrote {args.target} ({len(rows)} wheels, {len(names)} distributions)")
        return 0

    current = args.target.read_text(encoding="utf-8") if args.target.is_file() else ""
    status = "current" if current == rendered else "STALE - run with --write"
    print(f"{len(names)} acquired distributions, {len(rows)} wheels: {status}")
    print("  " + ", ".join(names))
    return 0 if current == rendered else 1


if __name__ == "__main__":
    raise SystemExit(main())
