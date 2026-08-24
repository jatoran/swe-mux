"""Phase 10.5: the distribution license gate and the generated notices.

The gate has two halves that must both stay alive, because the audit's central
finding is that a wheel's declared license does not describe its shipped
binaries. `packaging/license_audit.py` reads the resolved closure's metadata;
`build_desktop.verify_bundle_licenses` reads the built tree for payloads by
artifact name. These tests pin both, plus the invariants that connect them: the
allowlist, the notices file being generated rather than hand-edited, and the
`collect_all` entries that make the LGPL relink promise true.
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


license_audit = _load("license_audit")
build_desktop = _load("build_desktop")


# --------------------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MIT", "permissive"),
        ("Apache-2.0", "permissive"),
        ("BSD-3-Clause AND MIT", "permissive"),
        ("MPL-2.0", "file-level-copyleft"),
        ("MPL-2.0 AND MIT", "file-level-copyleft"),
        ("LGPL", "weak-copyleft"),
        ("LGPLv3", "weak-copyleft"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "weak-copyleft"),
        ("GPL-3.0", "strong-copyleft"),
        ("GNU General Public License v2 (GPLv2)", "strong-copyleft"),
        ("AGPL-3.0", "strong-copyleft"),
        ("", "unknown"),
    ],
)
def test_classify_buckets_licenses(text: str, expected: str) -> None:
    assert license_audit.classify(text) == expected


def test_lesser_is_checked_before_plain_gpl() -> None:
    """Ordering is load-bearing: every LGPL string also contains "GPL".

    If the strong-copyleft test ran first, both allowlisted packages would read
    as GPL and the gate would demand their removal instead of their notices.
    """
    assert license_audit.classify("GNU Lesser General Public License") == "weak-copyleft"
    assert license_audit.classify("GNU Library General Public License") == "weak-copyleft"


# --------------------------------------------------------------------------- the closure


def test_python_closure_excludes_dev_only_packages() -> None:
    """`pyinstaller` is GPL-2.0-with-exception and is never distributed.

    Including dev groups would make the gate fire on the one copyleft package
    that provably cannot reach a user, which is how a gate gets disabled.
    """
    closure = license_audit.python_closure()
    assert "pyinstaller" not in closure
    assert "pytest" not in closure
    assert "mypy" not in closure


def test_python_closure_includes_the_desktop_extra_and_its_transitives() -> None:
    closure = license_audit.python_closure()
    assert "pystray" in closure  # the desktop extra ships in the bundle
    assert "num2words" in closure  # a direct runtime dependency via misaki.en
    assert "misaki" in closure
    assert "onnxruntime" in closure


def test_python_closure_drops_unreachable_platform_markers() -> None:
    """`httpx2` carries a Pyodide-only dependency no swe-mux artifact can hold.

    Markers are evaluated against every supported platform rather than the
    running one, so a Linux-only package still counts on Windows - but
    `sys_platform == 'emscripten'` matches nothing swe-mux distributes for.
    """
    assert "httpx2-jsfetch" not in license_audit.python_closure()


def test_markers_are_evaluated_across_platforms_not_just_this_one() -> None:
    assert license_audit._reachable("sys_platform == 'win32'") is True
    assert license_audit._reachable("sys_platform == 'linux'") is True
    assert license_audit._reachable("sys_platform == 'darwin'") is True
    assert license_audit._reachable("sys_platform == 'emscripten'") is False
    assert license_audit._reachable(None) is True


def test_npm_closure_excludes_dev_dependencies() -> None:
    closure = license_audit.npm_closure()
    assert "@xterm/xterm" in closure
    assert "vite" not in closure
    assert "typescript" not in closure


# --------------------------------------------------------------------------- the gate


def test_recorded_closure_has_no_unallowlisted_copyleft() -> None:
    """The gate itself, over the checked-in sidecar. No environment needed."""
    assert license_audit.violations(license_audit.read_sidecar()) == []


def test_recorded_closure_matches_the_lockfiles() -> None:
    """A dependency that entered, left, or moved without regenerating fails here.

    This is the drift half of the gate and the reason the sidecar exists: it
    runs with nothing installed, so CI and every worktree catch a forgotten
    `--write` before a diligence review does.
    """
    assert license_audit.membership_drift(license_audit.read_sidecar()) == []


def test_notices_file_is_generated_not_edited() -> None:
    notices = (REPO_ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    assert notices == license_audit.render_notices(license_audit.read_sidecar())


def test_the_only_copyleft_that_ships_is_the_two_allowlisted_lgpl_packages() -> None:
    """Pins the actual posture, so a third entry is a deliberate decision.

    Strong copyleft must be absent outright; weak copyleft must be exactly the
    two recorded packages. MPL is file-level and needs license text only, which
    is why it is not in the allowlist.
    """
    recorded = license_audit.read_sidecar()
    weak = {item.name for item in recorded if item.category == "weak-copyleft"}
    strong = {item.name for item in recorded if item.category == "strong-copyleft"}
    unknown = {item.name for item in recorded if item.category == "unknown"}
    assert unknown == set()
    assert weak == {"pystray", "num2words"}
    assert weak == set(license_audit.ALLOWLIST)
    # The one GPL package in the closure is excluded from the bundle, not shipped.
    assert strong == set(license_audit.BUNDLE_EXCLUDED)


def test_pyav_is_classified_by_what_it_ships_not_what_it_declares() -> None:
    """The audit's central finding, encoded so the gate cannot repeat it.

    PyAV declares BSD-3-Clause and links GPL x264/x265. Without the override the
    gate reads it as permissive and reports a clean closure with 63 MB of GPL
    FFmpeg inside it - which is exactly the state this phase started from.
    """
    recorded = {item.name: item for item in license_audit.read_sidecar()}
    assert recorded["av"].license == "GPL-2.0-or-later"
    assert recorded["av"].category == "strong-copyleft"
    assert "av" in license_audit.BUNDLE_EXCLUDED


def test_bundle_excluded_packages_record_what_keeps_them_out() -> None:
    for name, reason in license_audit.BUNDLE_EXCLUDED.items():
        assert "bundle" in reason.lower(), f"{name} must say what keeps it out"
        assert len(reason) > 200, f"{name} must record the consequence, not just the fact"


def test_faster_whisper_still_requires_av_so_the_wheel_gap_is_real() -> None:
    """Guards the BUNDLE_EXCLUDED note against becoming stale good news.

    If faster-whisper ever drops its hard `av` dependency, `av` leaves the
    closure, this fails, and the honest thing is to delete the entry rather than
    keep warning about a gap that closed itself.
    """
    assert "av" in license_audit.python_closure()


def test_allowlist_entries_explain_themselves() -> None:
    """An allowlist entry is a decision record, not a suppression."""
    for name, reason in license_audit.ALLOWLIST.items():
        assert "LGPL" in reason, f"{name} must name its license"
        assert len(reason) > 120, f"{name} must record why it may ship"


def test_violations_reject_gpl_and_unknown_licenses() -> None:
    """Negative coverage: the gate has to fail, not just pass on today's data."""
    gpl = license_audit.Package("readline", "8.0", "GPL-3.0", "python")
    silent = license_audit.Package("mystery", "1.0", "", "python")
    allowed = license_audit.Package("pystray", "0.19.5", "LGPLv3", "python")
    problems = license_audit.violations([gpl, silent, allowed])
    assert len(problems) == 2
    assert any("readline" in item for item in problems)
    assert any("mystery" in item and "no license" in item for item in problems)
    assert not any("pystray" in item for item in problems)


# --------------------------------------------------------------------------- the bundle half


def test_spec_collects_the_lgpl_packages_as_replaceable_source() -> None:
    """The LGPL relink condition is a packaging fact, and this is where it lives.

    `collect_all` defaults to `include_py_files=True`, so a name in that loop
    ships as readable source under `_internal/<pkg>/` rather than frozen into
    the executable archive. Dropping either name would leave
    THIRD-PARTY-NOTICES.md promising a replaceability that no longer holds.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    for name in build_desktop.RELINKABLE_LGPL:
        assert f'"{name}",' in spec, f"{name} must stay in the collect_all loop"


def test_relinkable_set_matches_the_allowlist() -> None:
    """The two halves of the gate must agree on which packages they cover."""
    assert set(build_desktop.RELINKABLE_LGPL) == set(license_audit.ALLOWLIST)


def test_bundle_verification_rejects_a_forbidden_payload(tmp_path: Path) -> None:
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("", encoding="utf-8")
    (internal / "espeakng_loader").mkdir(parents=True)
    with pytest.raises(SystemExit, match="espeak-ng loader"):
        build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_bundle_verification_rejects_a_gpl_shared_library(tmp_path: Path) -> None:
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("", encoding="utf-8")
    (internal / "libx264-164.dll").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="GPL x264"):
        build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_bundle_verification_rejects_frozen_lgpl(tmp_path: Path) -> None:
    """An LGPL package with no loose source is a broken relink promise."""
    internal = tmp_path / "swe-mux" / "_internal"
    internal.mkdir(parents=True)
    (internal / "pystray").mkdir()
    (internal / "pystray" / "__init__.py").write_text("", encoding="utf-8")
    # num2words absent entirely, as it would be if dropped from collect_all.
    with pytest.raises(SystemExit, match="num2words"):
        build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_bundle_verification_tolerates_misakis_own_espeak_module(tmp_path: Path) -> None:
    """`misaki/espeak.py` is inert without the loader and must not fail a build.

    A bare `*espeak*` glob would reject every bundle over a wrapper that can
    never acquire a backend, which is why the globs match shared libraries only.
    """
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("", encoding="utf-8")
    (internal / "misaki").mkdir(parents=True)
    (internal / "misaki" / "espeak.py").write_text("", encoding="utf-8")
    build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_bundle_verification_passes_a_compliant_tree(tmp_path: Path) -> None:
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


# ----------------------------------------------------------------- the project's own terms


@pytest.mark.parametrize(
    "name", ["LICENSE", "NOTICE", "CONTRIBUTING.md", "TRADEMARK.md", "THIRD-PARTY-NOTICES.md"]
)
def test_licensing_files_exist(name: str) -> None:
    assert (REPO_ROOT / name).is_file(), f"{name} is required before publication"


def test_license_is_the_full_apache_text() -> None:
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text
    assert "Version 2.0, January 2004" in text
    assert "END OF TERMS AND CONDITIONS" in text
    # §6 is the trademark reservation TRADEMARK.md exists to give a policy to.
    assert "6. Trademarks." in text


def test_contributing_requires_a_dco_signoff_and_not_a_cla() -> None:
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Developer Certificate of Origin" in text
    assert "Signed-off-by" in text
    assert "git commit -s" in text


def test_the_vendor_relationship_is_stated_where_the_vendors_are_named() -> None:
    """Nominative use of a vendor's mark needs the disclaimer beside it."""
    for relative in ("README.md", "NOTICE", "TRADEMARK.md", "site/index.html"):
        raw = (REPO_ROOT / relative).read_text(encoding="utf-8")
        # Collapsed, because every one of these files hard-wraps its prose and
        # the disclaimer straddles a line break in three of the four.
        text = re.sub(r"\s+", " ", raw).lower()
        assert "anthropic" in text and "openai" in text, relative
        assert "not affiliated" in text, relative


def test_the_wheel_declares_its_license() -> None:
    """An undeclared license publishes a permissive project as all-rights-reserved.

    `pyproject.toml` carried no license field at all, so the wheel's metadata
    said nothing while the repository said Apache-2.0. Metadata silence is the
    one direction that reads as proprietary, so it is pinned here rather than
    left to a release checklist.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "Apache-2.0"' in pyproject
    for name in ("LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md"):
        assert name in pyproject, f"{name} must be carried into the wheel by license-files"


def test_no_unresolved_placeholder_remains_in_the_public_site() -> None:
    """`github.com/REPLACE/swe-mux` shipped in the landing page for months."""
    assert "REPLACE" not in (REPO_ROOT / "site" / "index.html").read_text(encoding="utf-8")
