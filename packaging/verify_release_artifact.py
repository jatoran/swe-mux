"""Prove a built wheel actually carries the frontend it claims to.

Phase 11 ("guarantee every wheel contains a frontend bundle from the same
revision; fail release validation on stale or missing assets"). Structural twin
of `license_audit.py`: a pure-data reader over an artifact, a fixed list of
checks, and a diagnostic that says what to do rather than only what is wrong.

The failure this exists to catch
-------------------------------
`src/swe_mux/static/assets/` and `src/swe_mux/static/index.html` are **build
output and are gitignored**. Hatchling's

    artifacts = ["src/swe_mux/static/**", "src/swe_mux/assets/**"]

includes them only if they happen to be on disk when the wheel is built. So:

- A wheel built from a clean clone (CI, a fresh worktree, a release runner that
  forgot the `npm run build` step) contains **no frontend at all**, builds
  cleanly, uploads cleanly, and serves a blank page to every user.
- A wheel built on a machine that has ever run `npm run build` looks fine.

Nothing in the build distinguishes those two outcomes, which is why this is a
separate gate rather than a build-time assertion: the build has no opinion about
a missing optional artifact, and by the time a human notices, the artifact is
published.

Why the staleness check is the one that matters
-----------------------------------------------
Presence is the easy half and the weaker one. `emptyOutDir` notwithstanding, the
static tree accumulates assets across builds in practice (the desktop redeploy
builds into a staging directory, the compression postbuild writes `.gz`
sidecars, and a merge can restore a directory that was never cleaned), so "there
are some .js files under assets/" is satisfied by a bundle from *any* revision.
The load-bearing check is therefore the join: every `assets/...` filename that
the wheel's own `index.html` references must be a file the wheel contains.
Vite's content-hashed names make that check exact - an entry chunk from a
different build has a different hash, so a stale `index.html` beside fresh
assets (or the reverse) cannot satisfy it.

The join runs in one direction only. Extra assets that nothing references are
normal - every `import()`ed route is its own chunk, reached from the entry
rather than from the HTML - so an unreferenced file is not evidence of anything
and is reported as a count, never as a failure.

Usage
-----
    uv run python packaging/verify_release_artifact.py dist/swe_mux-*.whl
    uv run python packaging/verify_release_artifact.py --json <wheel>

Exit 0 when every check passes, 1 when any fails (including a wheel that cannot
be opened at all). `--json` writes the whole report - every check's verdict, its
observed detail, and the evidence behind it - to stdout, so a CI step can
publish the reading rather than only the exit code.
"""

from __future__ import annotations

import argparse
import email
import json
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# The shipped configurator guides and hook scripts, in the source tree. Used as
# the *expected set* for the wheel rather than a hard-coded count: these files
# are tracked, so they exist in any checkout, and deriving the expectation means
# a guide added under `src/swe_mux/assets/` is covered the day it lands instead
# of the day someone remembers to bump a number here.
SOURCE_ASSETS = ROOT / "src" / "swe_mux" / "assets"

# Paths inside the wheel. Zip entries always use forward slashes, whatever host
# built them, so these are compared literally and never through `os.path`.
STATIC_ROOT = "swe_mux/static"
INDEX_HTML = f"{STATIC_ROOT}/index.html"
STATIC_ASSETS = f"{STATIC_ROOT}/assets"
SHIPPED_ASSETS = "swe_mux/assets"

# Carried into the wheel by `license-files` in pyproject.toml, which hatchling
# writes under `<name>-<version>.dist-info/licenses/`. Verified here because a
# wheel is the artifact that answers "what am I allowed to do with this" without
# the repository; Phase 10.5 established the content, this proves it shipped.
REQUIRED_LICENSE_FILES = ("LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md")
EXPECTED_LICENSE_EXPRESSION = "Apache-2.0"

# Injected into `index.html` by the `swe-mux-ui-build-identity` vite plugin
# (`frontend/vite.config.ts`), and only on a production build - the dev server
# deliberately emits none. Its value is a digest over that build's own emitted
# filenames and cannot be recomputed from the wheel, because the static tree may
# hold assets from more than one build. So it is not compared; its *absence* is
# the signal, and it means the `index.html` in the wheel did not come out of
# `vite build` at all.
UI_BUILD_META = "ui-build"

# Files that exist to make a directory importable or are a build cache; never
# part of the shipped guide set even when they are sitting in the source tree.
_IGNORED_SOURCE_PARTS = frozenset({"__pycache__"})
_IGNORED_SOURCE_SUFFIXES = (".pyc", ".pyo")

_BUILD_FRONTEND = (
    "run `npm --prefix frontend run build` (which writes into `src/swe_mux/static`), "
    "then rebuild the wheel with `uv build --wheel`"
)


@dataclass(frozen=True)
class Check:
    """One verdict, with what was observed and - when it failed - what to do.

    `detail` is filled on success too. A validator that speaks only when it is
    unhappy leaves a reader unable to tell "checked and fine" from "never ran",
    which is the same distinction the diagnostic bundles elsewhere in this
    repository are careful about.
    """

    name: str
    ok: bool
    detail: str
    remedy: str = ""


@dataclass(frozen=True)
class Report:
    wheel: str
    ok: bool
    checks: list[Check]
    evidence: dict[str, Any]

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]


# --------------------------------------------------------------------------- wheel reading


@dataclass(frozen=True)
class WheelContents:
    """Everything the checks need, read in one pass so the zip closes early."""

    names: list[str]
    index_html: str | None
    metadata: str | None
    dist_info: str | None


def _dist_info_dir(names: Sequence[str]) -> str | None:
    for name in names:
        head = name.split("/", 1)[0]
        if head.endswith(".dist-info"):
            return head
    return None


def _decode(archive: zipfile.ZipFile, name: str) -> str:
    # `errors="replace"` deliberately: a mojibake byte in an asset filename must
    # surface as a mismatched reference, not as a UnicodeDecodeError traceback
    # that says nothing about the artifact.
    return archive.read(name).decode("utf-8", errors="replace")


def read_wheel(wheel: Path) -> WheelContents:
    """Open the wheel and pull out the entry list plus the two files parsed below."""
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(info.filename for info in archive.infolist() if not info.is_dir())
        dist_info = _dist_info_dir(names)
        metadata_name = f"{dist_info}/METADATA" if dist_info else None
        return WheelContents(
            names=names,
            index_html=_decode(archive, INDEX_HTML) if INDEX_HTML in names else None,
            metadata=(
                _decode(archive, metadata_name)
                if metadata_name and metadata_name in names
                else None
            ),
            dist_info=dist_info,
        )


# --------------------------------------------------------------------------- html parsing


def _attributes(tag: str) -> dict[str, str]:
    """Attribute map of a single tag. Tolerant on purpose - this is a scanner.

    Quoted values only. An unquoted or valueless attribute (`crossorigin`) is
    skipped rather than guessed at: nothing here needs one, and a scanner that
    guesses is how a check starts reporting on markup it did not understand.
    """
    result: dict[str, str] = {}
    rest = tag
    while True:
        equals = rest.find("=")
        if equals == -1:
            break
        words = rest[:equals].split()
        name = words[-1].lower() if words else ""
        remainder = rest[equals + 1 :].lstrip()
        if not remainder or remainder[0] not in "\"'":
            rest = remainder
            continue
        quote = remainder[0]
        close = remainder.find(quote, 1)
        if close == -1:
            break
        if name:
            result[name] = remainder[1:close]
        rest = remainder[close + 1 :]
    return result


def _tags(html: str, tag_name: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    lowered = html.lower()
    needle = f"<{tag_name}"
    start = lowered.find(needle)
    while start != -1:
        end = html.find(">", start)
        if end == -1:
            break
        # `<meta` must not also match `<metadata`; the name ends at a boundary.
        boundary = html[start + len(needle) : start + len(needle) + 1]
        if boundary in ("", " ", "\t", "\n", "\r", ">", "/"):
            found.append(_attributes(html[start + len(needle) : end]))
        start = lowered.find(needle, end)
    return found


def referenced_assets(html: str) -> list[str]:
    """Asset filenames the document points at, as they appear under `assets/`.

    Only `src`/`href` are read. Vite emits the entry chunk as a `<script
    type="module" src>` and the entry stylesheet as a `<link rel="stylesheet"
    href>`, and any `modulepreload` it adds is a `<link href>` as well, so those
    two attributes cover every reference a built `index.html` can carry. The
    base is not assumed: `/assets/x.js`, `./assets/x.js`, and `assets/x.js` all
    reduce to `x.js`, because which one vite writes is a `base` setting and not
    a fact about whether the bundle is complete.
    """
    found: list[str] = []
    for tag_name in ("script", "link"):
        for attributes in _tags(html, tag_name):
            for key in ("src", "href"):
                value = attributes.get(key, "")
                marker = value.rfind("assets/")
                if marker == -1:
                    continue
                # Guard against a path merely *containing* "assets/" mid-segment
                # (`/my-assets/x.js`): the segment has to start at a boundary.
                if marker and value[marker - 1] not in "/":
                    continue
                name = value[marker + len("assets/") :].split("?", 1)[0].split("#", 1)[0]
                if name and name not in found:
                    found.append(name)
    return sorted(found)


def ui_build_id(html: str) -> str | None:
    for attributes in _tags(html, "meta"):
        if attributes.get("name") == UI_BUILD_META:
            return attributes.get("content") or None
    return None


# --------------------------------------------------------------------------- the checks


def _check_frontend_entry(contents: WheelContents) -> Check:
    if contents.index_html is None:
        return Check(
            "frontend-entry",
            False,
            f"{INDEX_HTML} is not in the wheel, so an install serves no UI at all.",
            f"The frontend was never built before the wheel was: {_BUILD_FRONTEND}.",
        )
    return Check(
        "frontend-entry",
        True,
        f"{INDEX_HTML} present ({len(contents.index_html)} bytes).",
    )


def _static_asset_names(names: Sequence[str]) -> list[str]:
    prefix = f"{STATIC_ASSETS}/"
    return [name[len(prefix) :] for name in names if name.startswith(prefix)]


def _check_frontend_assets(contents: WheelContents) -> Check:
    assets = _static_asset_names(contents.names)
    scripts = [name for name in assets if name.endswith(".js")]
    if not scripts:
        return Check(
            "frontend-assets",
            False,
            f"{STATIC_ASSETS}/ contains no .js file "
            f"({len(assets)} entries under it in total).",
            f"The frontend bundle is missing from the wheel: {_BUILD_FRONTEND}.",
        )
    return Check(
        "frontend-assets",
        True,
        f"{len(scripts)} .js asset(s) under {STATIC_ASSETS}/ "
        f"({len(assets)} entries including stylesheets and .gz sidecars).",
    )


def _check_frontend_consistency(contents: WheelContents) -> Check:
    """The staleness check: the wheel's index.html must name files the wheel has."""
    name = "frontend-consistency"
    if contents.index_html is None:
        return Check(
            name,
            False,
            f"Not evaluated: {INDEX_HTML} is absent, so there is nothing to reconcile "
            "the assets against.",
            f"Fix `frontend-entry` first: {_BUILD_FRONTEND}.",
        )

    referenced = referenced_assets(contents.index_html)
    present = set(_static_asset_names(contents.names))
    if not referenced:
        return Check(
            name,
            False,
            f"{INDEX_HTML} references no file under assets/, so it is not a built "
            "bundle - most likely the un-built `frontend/index.html` template.",
            f"To produce one, {_BUILD_FRONTEND}.",
        )

    missing = [item for item in referenced if item not in present]
    if missing:
        return Check(
            name,
            False,
            f"{INDEX_HTML} references {len(missing)} asset(s) the wheel does not "
            f"contain: {', '.join(missing)}. The wheel carries {len(present)} asset "
            "file(s), so this index.html and these assets came from different builds.",
            "The static tree was stale or partial when the wheel was built; "
            f"{_BUILD_FRONTEND}, and never copy `src/swe_mux/static` between "
            "revisions - vite's content hashes are what make this detectable.",
        )
    identity = ui_build_id(contents.index_html)
    if identity is None:
        return Check(
            name,
            False,
            f"All {len(referenced)} referenced asset(s) are present, but {INDEX_HTML} "
            f"carries no `{UI_BUILD_META}` meta tag, which only a production build "
            "emits. This index.html did not come out of `vite build`.",
            f"{_BUILD_FRONTEND}. A hand-assembled or dev-server index.html is not a "
            "release artifact even when the filenames happen to line up.",
        )
    return Check(
        name,
        True,
        f"All {len(referenced)} asset(s) referenced by index.html are in the wheel "
        f"({', '.join(referenced)}); {len(present) - len(referenced)} further asset "
        f"file(s) are lazily-loaded chunks or .gz sidecars. Build identity {identity}.",
    )


def expected_shipped_assets(source_assets: Path) -> list[str]:
    """Wheel paths for every tracked file under `src/swe_mux/assets/`."""
    if not source_assets.is_dir():
        return []
    return sorted(
        f"{SHIPPED_ASSETS}/{path.relative_to(source_assets).as_posix()}"
        for path in source_assets.rglob("*")
        if path.is_file()
        and not _IGNORED_SOURCE_PARTS.intersection(path.parts)
        and path.suffix not in _IGNORED_SOURCE_SUFFIXES
    )


def _check_shipped_assets(contents: WheelContents, source_assets: Path) -> Check:
    """The configurator guides and hook scripts, which `.docs/` deliberately is not.

    These live under `src/swe_mux/assets/` precisely because that is what the
    wheel and the PyInstaller bundle both carry (`.docs/CLAUDE.md`, the
    configurator routing entry). A guide that fails to ship reads correctly from
    source and is silently absent for every installed user.
    """
    name = "shipped-assets"
    present = [item for item in contents.names if item.startswith(f"{SHIPPED_ASSETS}/")]
    expected = expected_shipped_assets(source_assets)

    if not expected:
        # The source tree is not beside this script (the wheel is being checked
        # from somewhere else). Degrade to presence and say so, rather than
        # reporting an empty expectation as a pass.
        if not present:
            return Check(
                name,
                False,
                f"The wheel contains nothing under {SHIPPED_ASSETS}/, and "
                f"{source_assets} is not readable so the expected set is unknown.",
                "Rebuild the wheel from a complete checkout with "
                "`uv build --wheel`; the guides under `src/swe_mux/assets/` are "
                "tracked files, so their absence means the source tree was "
                "incomplete.",
            )
        return Check(
            name,
            True,
            f"{len(present)} file(s) under {SHIPPED_ASSETS}/ (presence only: "
            f"{source_assets} is not readable, so the exact set was not compared).",
        )

    missing = [item for item in expected if item not in set(contents.names)]
    if missing:
        return Check(
            name,
            False,
            f"{len(missing)} of {len(expected)} shipped asset(s) are missing from the "
            f"wheel: {', '.join(missing)}.",
            "These are tracked files, so a wheel without them was built over an "
            "incomplete source tree. Rebuild with `uv build --wheel` from a full "
            "checkout, and check that `artifacts` in pyproject.toml still covers "
            "`src/swe_mux/assets/**`.",
        )
    return Check(
        name,
        True,
        f"All {len(expected)} shipped asset(s) from src/swe_mux/assets/ are in the "
        f"wheel ({len(present)} entries under {SHIPPED_ASSETS}/).",
    )


def _check_license_files(contents: WheelContents) -> Check:
    name = "license-files"
    if contents.dist_info is None:
        return Check(
            name,
            False,
            "The wheel has no *.dist-info directory, so it is not a valid wheel.",
            "Rebuild it with `uv build --wheel`.",
        )
    prefix = f"{contents.dist_info}/licenses"
    missing = [item for item in REQUIRED_LICENSE_FILES if f"{prefix}/{item}" not in contents.names]
    if missing:
        return Check(
            name,
            False,
            f"{', '.join(missing)} missing from {prefix}/, so an installed copy cannot "
            "answer what its terms are without the repository.",
            "Check that `license-files` in pyproject.toml still lists "
            f"{', '.join(REQUIRED_LICENSE_FILES)} and that each exists at the repository "
            "root, then rebuild with `uv build --wheel`.",
        )
    return Check(
        name,
        True,
        f"{', '.join(REQUIRED_LICENSE_FILES)} present under {prefix}/.",
    )


def _check_license_metadata(contents: WheelContents) -> Check:
    name = "license-metadata"
    if contents.metadata is None:
        return Check(
            name,
            False,
            "The wheel has no dist-info METADATA to read a license from.",
            "Rebuild it with `uv build --wheel`.",
        )
    message = email.message_from_string(contents.metadata)
    expression = (message.get("License-Expression") or "").strip()
    if expression != EXPECTED_LICENSE_EXPRESSION:
        # The two failures are not the same fact. An absent expression is the
        # one direction that publishes a permissive project as all-rights-
        # reserved by omission (the state pyproject.toml was in before Phase
        # 10.5); a *different* expression is a wheel disagreeing with LICENSE.
        consequence = (
            "Metadata silence reads as all-rights-reserved over an Apache-2.0 "
            "repository."
            if not expression
            else "The wheel's metadata and the repository's LICENSE disagree."
        )
        return Check(
            name,
            False,
            f"METADATA declares License-Expression: {expression or '(absent)'}, not "
            f"{EXPECTED_LICENSE_EXPRESSION}. {consequence}",
            f'Set `license = "{EXPECTED_LICENSE_EXPRESSION}"` in pyproject.toml '
            "(PEP 639) and rebuild with `uv build --wheel`.",
        )
    return Check(
        name,
        True,
        f"METADATA declares License-Expression: {expression}.",
    )


# --------------------------------------------------------------------------- driver


def verify(wheel: Path, source_assets: Path | None = None) -> Report:
    """Run every check over `wheel`. Never raises for a bad artifact."""
    source = SOURCE_ASSETS if source_assets is None else source_assets
    try:
        contents = read_wheel(wheel)
    except FileNotFoundError:
        return _unreadable(
            wheel,
            f"{wheel} does not exist.",
            "Pass the path to a built wheel, e.g. `uv build --wheel` then "
            "`uv run python packaging/verify_release_artifact.py dist/*.whl`.",
        )
    except (zipfile.BadZipFile, OSError) as error:
        return _unreadable(
            wheel,
            f"{wheel} could not be read as a wheel (zip): {error}.",
            "A wheel is a zip archive. Check the path points at the `.whl` and that "
            "the build or the download completed, then rebuild with `uv build --wheel`.",
        )

    checks = [
        Check("artifact-readable", True, f"{len(contents.names)} entries in the wheel."),
        _check_frontend_entry(contents),
        _check_frontend_assets(contents),
        _check_frontend_consistency(contents),
        _check_shipped_assets(contents, source),
        _check_license_files(contents),
        _check_license_metadata(contents),
    ]
    evidence = _empty_evidence()
    evidence.update(
        {
            "entry_count": len(contents.names),
            "dist_info": contents.dist_info,
            "static_assets": sorted(_static_asset_names(contents.names)),
            "referenced_assets": (
                referenced_assets(contents.index_html) if contents.index_html else []
            ),
            "ui_build_id": ui_build_id(contents.index_html) if contents.index_html else None,
            "shipped_assets": [
                item for item in contents.names if item.startswith(f"{SHIPPED_ASSETS}/")
            ],
            "expected_shipped_assets": expected_shipped_assets(source),
        }
    )
    return Report(
        wheel=str(wheel),
        ok=all(check.ok for check in checks),
        checks=checks,
        evidence=evidence,
    )


def _empty_evidence() -> dict[str, Any]:
    """The evidence keys, always all of them.

    A consumer parsing `--json` must not have to branch on whether the wheel
    could be opened: an unreadable artifact reports empty evidence, never
    absent evidence, so `evidence["static_assets"]` is a valid read on every
    report this module produces.
    """
    return {
        "entry_count": 0,
        "dist_info": None,
        "static_assets": [],
        "referenced_assets": [],
        "ui_build_id": None,
        "shipped_assets": [],
        "expected_shipped_assets": [],
    }


def _unreadable(wheel: Path, detail: str, remedy: str) -> Report:
    check = Check("artifact-readable", False, detail, remedy)
    return Report(wheel=str(wheel), ok=False, checks=[check], evidence=_empty_evidence())


def render(report: Report, *, subject: str = "Release artifact") -> str:
    """Human output: every verdict, then the failures again with their remedies.

    The passing lines are not noise. This runs in a release pipeline where the
    interesting question after a green run is *what was actually proven*, and a
    validator that prints nothing on success cannot be distinguished from one
    that silently skipped its checks - which is the exact class of failure it
    exists to catch in the artifact.

    `subject` names what was checked, because `install_smoke.py` renders its own
    report through this function: two readers of one CI log need to be able to
    tell which of the two failed without matching check names against a script.
    """
    lines = [f"{subject} check: {report.wheel}"]
    for check in report.checks:
        lines.append(f"  {'PASS' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    failures = report.failures
    if not failures:
        lines.append(f"Artifact valid ({len(report.checks)} checks passed).")
        return "\n".join(lines)
    lines.append("")
    lines.append(
        f"{subject} validation FAILED ({len(failures)} of {len(report.checks)} checks):"
    )
    for check in failures:
        lines.append(f"  - {check.name}: {check.detail}")
        lines.append(f"    -> {check.remedy}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path, help="Path to the built .whl to validate.")
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Write the full report as JSON to stdout instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    report = verify(args.wheel)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
