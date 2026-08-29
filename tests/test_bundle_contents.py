"""Phase 21 A4: the bundle's package membership is asserted, not assumed.

A PyInstaller bundle contains whatever the *build venv* made importable, which is
not the same thing as what this repository declares it ships. `dist/swe-mux` as
built 2026-08-27 carried 101 MB of `playwright/driver` behind the lazy `import
playwright` in `preview_capture.py`, while `license_audit.py` says plainly that
`preview-capture` does not ship. Nothing failed: `verify_bundle_licenses` reads
the built tree for copyleft payloads and Playwright is Apache-2.0.

`build_desktop.verify_bundle_contents` closes that hole from both sides - a stray
package and a package that quietly stopped being collected - and these tests pin
its behaviour plus the invariants tying its manifest to the spec.
"""

from __future__ import annotations

import importlib.util
import re
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


build_desktop = _load("build_desktop")


def _bundle(root: Path, packages: dict[str, int]) -> Path:
    """Build a stand-in bundle whose `_internal/` holds `packages` at given sizes."""
    internal = root / "swe-mux" / "_internal"
    internal.mkdir(parents=True)
    for name, size in packages.items():
        package = internal / name
        package.mkdir()
        (package / "payload.bin").write_bytes(b"x" * size)
    return root / "swe-mux"


def _expected(*extra: str) -> set[str]:
    return set(build_desktop.EXPECTED_BUNDLE_PACKAGES) | set(extra)


# --------------------------------------------------------------------------- listing


def test_the_listing_reports_directories_with_their_sizes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, {"spacy": 40, "swe_mux": 10})
    assert build_desktop.bundle_top_level_packages(bundle) == {"spacy": 40, "swe_mux": 10}


def test_the_listing_ignores_dist_info_and_loose_files(tmp_path: Path) -> None:
    """Version-carrying metadata directories would churn the manifest for nothing.

    A `numpy-2.5.1.dist-info` entry renames itself on every dependency bump, so a
    manifest listing them would be edited without being read. The package it
    describes is checked in its own right.
    """
    bundle = _bundle(tmp_path, {"numpy": 30, "numpy-2.5.1.dist-info": 1})
    (bundle / "_internal" / "python312.dll").write_bytes(b"loose")
    assert build_desktop.bundle_top_level_packages(bundle) == {"numpy": 30}


# --------------------------------------------------------------------------- the gate


def test_a_bundle_matching_the_manifest_passes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, {name: 1 for name in build_desktop.EXPECTED_BUNDLE_PACKAGES})
    build_desktop.verify_bundle_contents(bundle)  # does not raise


def test_a_stray_build_venv_dependency_fails_the_build_by_name_and_size(
    tmp_path: Path,
) -> None:
    """The Playwright case, reproduced: an Apache-2.0 package the closure never declared."""
    packages = {name: 1 for name in build_desktop.EXPECTED_BUNDLE_PACKAGES}
    packages["playwright"] = 101_000_000
    bundle = _bundle(tmp_path, packages)

    with pytest.raises(SystemExit) as failure:
        build_desktop.verify_bundle_contents(bundle)

    message = str(failure.value)
    assert "playwright" in message, "the failure must name the offending package"
    assert "101.0 MB" in message, "the failure must say what it costs"
    assert "swe_mux.spec" in message, "the failure must say what to do about it"


def test_the_largest_stray_is_reported_first(tmp_path: Path) -> None:
    packages = {name: 1 for name in build_desktop.EXPECTED_BUNDLE_PACKAGES}
    packages["small_passenger"] = 1_000
    packages["large_passenger"] = 90_000_000
    bundle = _bundle(tmp_path, packages)

    with pytest.raises(SystemExit) as failure:
        build_desktop.verify_bundle_contents(bundle)

    message = str(failure.value)
    assert message.index("large_passenger") < message.index("small_passenger")


def test_a_package_that_stopped_being_collected_fails_the_build(tmp_path: Path) -> None:
    """The other direction, and the one that is silent in every other check.

    `tzdata` is pure data with no importable code path of its own; a bundle
    without it starts healthy and fails every timezone-naming schedule, in the
    frozen app only.
    """
    packages = {
        name: 1 for name in build_desktop.EXPECTED_BUNDLE_PACKAGES if name != "tzdata"
    }
    bundle = _bundle(tmp_path, packages)

    with pytest.raises(SystemExit) as failure:
        build_desktop.verify_bundle_contents(bundle)

    assert "tzdata" in str(failure.value)
    assert "collect_all" in str(failure.value)


def test_the_manifest_is_a_parameter_so_the_gate_is_not_only_testable_here(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, {"only_this": 5})
    build_desktop.verify_bundle_contents(bundle, expected={"only_this"})  # does not raise


# --------------------------------------------------------------------------- invariants


def test_every_collect_all_package_is_in_the_manifest() -> None:
    """The spec and the manifest have to agree about what is deliberately shipped.

    A name in the spec's `collect_all` loop is there because its absence is
    invisible until the frozen app runs. If the manifest does not also expect it,
    the two halves stop describing the same bundle and one of them is decoration.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    loop = spec.split("for package in (")[1].split("):")[0]
    collected = {line.strip().strip('",') for line in loop.splitlines() if '"' in line}
    assert collected, "the collect_all loop parsed as empty; this guard would assert nothing"
    assert collected <= set(build_desktop.EXPECTED_BUNDLE_PACKAGES)


def test_every_relinkable_lgpl_package_is_in_the_manifest() -> None:
    """`verify_bundle_licenses` promises these ship as source; this says they ship."""
    assert set(build_desktop.RELINKABLE_LGPL) <= set(build_desktop.EXPECTED_BUNDLE_PACKAGES)


def test_nothing_the_spec_excludes_is_expected_in_the_bundle() -> None:
    """`av` and `edge_tts` are excluded for licensing and distribution-boundary
    reasons; expecting either here would make this gate argue with that one."""
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    clause = re.search(r"excludes=\[([^\]]*)\]", spec)
    assert clause, "the excludes clause did not parse; this guard would assert nothing"
    excluded = {token.strip().strip('"') for token in clause.group(1).split(",")}
    # `*EXCLUDED_VOICE_CLOSURE` is a splat rather than a literal; the names it
    # contributes are checked by the test below, which resolves it for real.
    excluded = {name for name in excluded if name and not name.startswith("*")}
    assert excluded, "the excludes clause parsed as empty; this guard would assert nothing"
    assert not (excluded & set(build_desktop.EXPECTED_BUNDLE_PACKAGES))


def test_the_acquired_voice_closure_is_excluded_and_not_expected() -> None:
    """The two gates that keep 277 MB out of the bundle must not contradict.

    `EXCLUDED_VOICE_CLOSURE` in the spec and `EXPECTED_BUNDLE_PACKAGES` here are
    derived from different sources - installed distribution metadata and a
    measured build - so nothing structural stops them disagreeing. A name in both
    would mean the spec excludes a package the manifest requires, and every build
    would fail on the missing-package half with a message about `collect_all`.
    """
    closure = set(build_desktop.voice_closure_top_levels())
    assert "spacy" in closure and "num2words" in closure and "onnxruntime" in closure
    assert not (closure & set(build_desktop.EXPECTED_BUNDLE_PACKAGES))


def test_the_voice_closure_gate_names_the_packages_that_returned(tmp_path: Path) -> None:
    """A second, more specific failure than "unexpected package", on purpose.

    `verify_bundle_contents` would already reject `spacy` as undeclared and point
    at the build venv. This one names the mechanism that was supposed to keep it
    out and the size that is at stake, because "an undeclared package appeared" and
    "the thing you deliberately stopped shipping is back" lead to different fixes.
    """
    bundle = _bundle(tmp_path, {"swe_mux": 10, "spacy": 40})
    with pytest.raises(SystemExit, match="Voice closure regression"):
        build_desktop.verify_voice_closure_absent(bundle)
    clean = _bundle(tmp_path / "clean", {"swe_mux": 10})
    build_desktop.verify_voice_closure_absent(clean)


def test_the_stable_abi_forwarder_is_required_in_a_windows_bundle(tmp_path: Path) -> None:
    """`python3.dll` is present for code the bundle does not contain.

    Every abi3 wheel in the acquired closure links against it by name, and nothing
    in the bundle's own analysis pulls it in - so it is exactly the kind of file
    that disappears silently. Measured on a frozen probe: without it, `tokenizers`
    fails with "DLL load failed", which names neither the file nor the reason.
    """
    internal = tmp_path / "swe-mux" / "_internal"
    internal.mkdir(parents=True)
    (internal / "python312.dll").write_bytes(b"")
    with pytest.raises(SystemExit, match="python3.dll"):
        build_desktop.verify_stable_abi_forwarder(tmp_path / "swe-mux")
    (internal / "python3.dll").write_bytes(b"")
    build_desktop.verify_stable_abi_forwarder(tmp_path / "swe-mux")


def test_a_non_windows_bundle_is_not_asked_for_a_windows_forwarder(tmp_path: Path) -> None:
    """The forwarder is a Windows concept; a POSIX bundle must not fail on it."""
    internal = tmp_path / "swe-mux" / "_internal"
    internal.mkdir(parents=True)
    build_desktop.verify_stable_abi_forwarder(tmp_path / "swe-mux")


def test_the_spec_collects_the_forwarder_explicitly() -> None:
    """Asserting the mechanism as well as the result.

    `cryptography` ships an abi3 `.pyd` and would pull `python3.dll` in today, so
    a bundle check alone would keep passing if the explicit collection were
    deleted - right up until the day the base closure changed.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    assert "PYTHON3_DLL" in spec
    assert 'binaries += [(str(PYTHON3_DLL), ".")]' in spec


def test_the_spec_ships_the_whole_standard_library() -> None:
    """The sidecar's import graph is invisible to PyInstaller; the stdlib is not ours to guess.

    Measured while proving the sidecar loads at all: a frozen probe carrying only
    its own stdlib closure failed on `platform`, then `ctypes`, then `json`, then
    `http.cookies` - one at a time, each revealed only by fixing the one before.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    assert "sys.stdlib_module_names" in spec
    assert "hiddenimports=hiddenimports + STDLIB_HIDDENIMPORTS" in spec
