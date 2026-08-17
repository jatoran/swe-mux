"""Who blocks the frozen-app bundle swap: classification and boundaries.

The redeploy's one non-retryable step is renaming dist/swe-mux; a foreign
process anchoring it (dev server behind a Preview tab, terminal cd'd into the
bundle) survives everything the redeploy may stop, so the gate must name it
before anything is built or stopped — and must never false-block on the app's
own processes or the sibling supervisor bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from swe_mux.bundle_locks import (
    bundle_lock_holders,
    classify_bundle_holder,
    describe_holders,
)

BUNDLE = Path(r"D:\PROJECTS\swe-mux\dist\swe-mux")


def test_cwd_exe_and_open_file_anchors_are_classified() -> None:
    assert classify_bundle_holder(
        BUNDLE, name="node.exe", exe=r"C:\nodejs\node.exe",
        cwd=str(BUNDLE / "_internal"),
    ) == ("cwd", str(BUNDLE / "_internal"))
    assert classify_bundle_holder(
        BUNDLE, name="OpenConsole.exe",
        exe=str(BUNDLE / "_internal" / "winpty" / "OpenConsole.exe"), cwd=r"C:\x",
    ) == ("exe", str(BUNDLE / "_internal" / "winpty" / "OpenConsole.exe"))
    assert classify_bundle_holder(
        BUNDLE, name="python.exe", exe=r"C:\py\python.exe", cwd=r"C:\x",
        open_paths=[r"C:\other\log.txt", str(BUNDLE / "swe-mux.exe")],
    ) == ("open_file", str(BUNDLE / "swe-mux.exe"))
    # The bundle root itself (a shell sitting exactly at dist/swe-mux) counts.
    assert classify_bundle_holder(
        BUNDLE, name="pwsh.exe", exe=r"C:\pwsh\pwsh.exe", cwd=str(BUNDLE)
    ) == ("cwd", str(BUNDLE))


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS case-insensitive path matching")
def test_matching_is_case_insensitive_like_the_filesystem() -> None:
    assert classify_bundle_holder(
        BUNDLE, name="node.exe", exe=None, cwd=str(BUNDLE).upper()
    ) is not None


def test_the_sibling_supervisor_bundle_never_matches() -> None:
    # dist/swe-mux-supervisor shares the dist/swe-mux prefix as a *string*; the
    # separator-suffixed comparison is what keeps the supervisor (which
    # deliberately survives every redeploy) from reading as a blocker.
    supervisor = BUNDLE.parent / "swe-mux-supervisor"
    assert classify_bundle_holder(
        BUNDLE,
        name="swe-mux-supervisor.exe",
        exe=str(supervisor / "swe-mux-supervisor.exe"),
        cwd=str(supervisor),
        open_paths=[str(supervisor / "_internal" / "base_library.zip")],
    ) is None


def test_the_apps_own_image_is_never_a_blocker() -> None:
    # The redeploy stops swe-mux.exe itself (escalating image-wide when a lock
    # demands it), so the shell/daemon/hook helpers must not trip the gate.
    assert classify_bundle_holder(
        BUNDLE, name="swe-mux.exe", exe=str(BUNDLE / "swe-mux.exe"), cwd=str(BUNDLE)
    ) is None


def test_unrelated_processes_hold_nothing() -> None:
    assert classify_bundle_holder(
        BUNDLE, name="chrome.exe", exe=r"C:\chrome\chrome.exe", cwd=r"C:\Users\x",
        open_paths=[r"C:\Users\x\file.txt"],
    ) is None


def test_a_missing_bundle_scans_to_nothing(tmp_path: Path) -> None:
    # Source-only checkouts have no dist/swe-mux; the gate must be a no-op.
    assert bundle_lock_holders(tmp_path / "dist" / "swe-mux") == []


def test_describe_holders_is_bounded_and_names_the_process() -> None:
    holders = [
        {"pid": 100 + index, "name": "node.exe", "via": "cwd", "path": str(BUNDLE)}
        for index in range(7)
    ]
    text = describe_holders(holders)
    assert "pid 100 node.exe" in text
    assert "and 2 more" in text
    assert "pid 106" not in text
