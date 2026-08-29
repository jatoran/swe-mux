"""Staging a release archive: what gets written, and what gets linked instead.

The property under test is not "does the tree end up correct" - that would be
true of the full extraction this replaces. It is **which files were touched to
get there**, because an update's minutes go into image-scanning files the machine
has never seen, and a hard-linked file is the same filesystem object the scanner
already has a verdict for. So nearly every assertion here counts files rather
than checking contents, and the contents are checked once, hard, to prove that
counting fewer writes did not cost correctness.

Every archive is a real one, produced by the real release writer
(`packaging/package_desktop_release.py`) over a directory of small files, so the
manifest under test is the manifest a release would actually carry rather than
one this file invented.

The second theme is the fallback. Every refusal in `bundle_stage` is "extract the
whole archive instead", never "do not install" - the full path is what shipped
before, so falling back to it cannot be wrong. Each way the delta can go wrong
therefore has a test asserting that a *complete, correct* tree still appears, and
that the reason was recorded rather than swallowed.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

from swe_mux.bundle_archive import ARCHIVE_ROOT, TAR_GZ_SUFFIX
from swe_mux.bundle_manifest import (
    BUNDLE_FILES_NAME,
    DELTA_MANIFEST_DISAGREES,
    DELTA_NO_MANIFEST,
    DELTA_OK,
    DELTA_TOO_LITTLE_REUSE,
    build_file_manifest,
    manifest_bytes,
)
from swe_mux.bundle_metadata import bundle_metadata, write_bundle_metadata
from swe_mux.bundle_stage import MODE_DELTA, MODE_FULL, PLACED_LINK, stage_bundle
from swe_mux.update_install import release_platform_tag

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

import package_desktop_release  # noqa: E402 - packaging module, added to path above

PROTOCOL = 1

#: Enough shape to be a bundle, and enough bulk that a byte share means
#: something: `heavy.bin` is the stand-in for the ~370 MB of machine-learning
#: dependencies that dominate a real bundle and change on nobody's release
#: schedule.
BUNDLE = {
    "swe-mux.exe": b"MZ" + b"\x00" * 2048,
    "_internal/python312.dll": b"interpreter" * 256,
    "_internal/heavy/heavy.bin": b"H" * 200_000,
    "_internal/swe_mux/static/index.html": b"<html></html>",
}


def make_bundle(root: Path, files: dict[str, bytes], *, version: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    write_bundle_metadata(
        root,
        bundle_metadata(
            version=version, supervisor_protocol=PROTOCOL, platform=release_platform_tag()
        ),
    )
    return root


def make_archive(tmp_path: Path, files: dict[str, bytes], *, version: str) -> Path:
    bundle = make_bundle(tmp_path / f"build-{version}" / "swe-mux", files, version=version)
    archive, _ = package_desktop_release.build_archive(bundle, tmp_path / f"out-{version}")
    return archive


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def same_file(left: Path, right: Path) -> bool:
    """Whether two paths are one filesystem object - the scan verdict's identity."""
    return os.path.samefile(left, right)


# --- the win ------------------------------------------------------------------


def test_only_the_changed_files_are_written_and_the_rest_are_linked(
    tmp_path: Path,
) -> None:
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    archive = make_archive(
        tmp_path, {**BUNDLE, "swe-mux.exe": b"MZ" + b"\x01" * 2048}, version="2.0.0"
    )
    result = stage_bundle(archive, tmp_path / "staging", current_root=installed)
    assert result.mode == MODE_DELTA
    assert result.reason == DELTA_OK
    # `swe-mux.exe` changed and `bundle.json` carries the version, so exactly two
    # files are new bytes; `heavy.bin` - the whole point - is not one of them.
    assert result.written_files == 2
    assert result.reused_files == len(BUNDLE) - 1
    heavy = "_internal/heavy/heavy.bin"
    assert same_file(result.root / heavy, installed / heavy)
    assert not same_file(result.root / "swe-mux.exe", installed / "swe-mux.exe")


def test_the_staged_tree_is_byte_for_byte_the_release(tmp_path: Path) -> None:
    # The one test that checks contents rather than counts. Everything else here
    # is about writing less; this is the proof that writing less produced the
    # same application.
    incoming = {**BUNDLE, "swe-mux.exe": b"MZ" + b"\x01" * 2048, "_internal/new.pyd": b"new"}
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    archive = make_archive(tmp_path, incoming, version="2.0.0")
    delta = stage_bundle(archive, tmp_path / "delta", current_root=installed)
    full = stage_bundle(archive, tmp_path / "full", current_root=None)
    assert delta.mode == MODE_DELTA
    assert full.mode == MODE_FULL
    assert tree(delta.root) == tree(full.root)
    # ...including the manifest itself, which is in neither plan because it
    # cannot carry its own digest.
    assert (delta.root / BUNDLE_FILES_NAME).read_bytes() == (
        full.root / BUNDLE_FILES_NAME
    ).read_bytes()


def test_a_reused_file_is_proven_rather_than_assumed(tmp_path: Path) -> None:
    # An installed file that merely has the right *name* is not reused. This is
    # the difference between a delta and a guess: nothing keys off a version, a
    # timestamp, or a path.
    installed = make_bundle(
        tmp_path / "installed", {**BUNDLE, "_internal/heavy/heavy.bin": b"T" * 200_000},
        version="1.0.0",
    )
    archive = make_archive(tmp_path, BUNDLE, version="2.0.0")
    result = stage_bundle(archive, tmp_path / "staging", current_root=installed)
    assert (result.root / "_internal/heavy/heavy.bin").read_bytes() == b"H" * 200_000
    assert not same_file(
        result.root / "_internal/heavy/heavy.bin", installed / "_internal/heavy/heavy.bin"
    )


def test_the_installed_bundle_is_never_modified(tmp_path: Path) -> None:
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    before = tree(installed)
    archive = make_archive(
        tmp_path, {**BUNDLE, "swe-mux.exe": b"MZ" + b"\x01" * 2048}, version="2.0.0"
    )
    stage_bundle(archive, tmp_path / "staging", current_root=installed)
    assert tree(installed) == before


def test_a_first_install_extracts_everything(tmp_path: Path) -> None:
    archive = make_archive(tmp_path, BUNDLE, version="1.0.0")
    result = stage_bundle(archive, tmp_path / "staging", current_root=None)
    assert result.mode == MODE_FULL
    assert result.root == tmp_path / "staging" / ARCHIVE_ROOT
    assert set(tree(result.root)) == set(BUNDLE) | {"bundle.json", BUNDLE_FILES_NAME}


def test_a_wholly_different_bundle_falls_back_to_a_full_extraction(
    tmp_path: Path,
) -> None:
    installed = make_bundle(tmp_path / "installed", {"only.bin": b"z" * 16}, version="1.0.0")
    archive = make_archive(tmp_path, BUNDLE, version="2.0.0")
    result = stage_bundle(archive, tmp_path / "staging", current_root=installed)
    assert result.mode == MODE_FULL
    assert result.reason == DELTA_TOO_LITTLE_REUSE
    assert set(tree(result.root)) == set(BUNDLE) | {"bundle.json", BUNDLE_FILES_NAME}


# --- the fallbacks ------------------------------------------------------------


def test_an_archive_from_before_this_feature_installs_the_way_it_always_did(
    tmp_path: Path,
) -> None:
    # No version negotiation anywhere: an archive with no `files.json` is simply
    # the `missing` case, and the full extraction that shipped before is what a
    # release published last month gets.
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    archive = make_archive(tmp_path, BUNDLE, version="2.0.0")
    stripped = _rewrite_without(archive, f"{ARCHIVE_ROOT}/{BUNDLE_FILES_NAME}")
    result = stage_bundle(stripped, tmp_path / "staging", current_root=installed)
    assert result.mode == MODE_FULL
    assert result.reason == DELTA_NO_MANIFEST
    assert any("no usable file manifest" in note for note in result.observations)
    assert set(tree(result.root)) == set(BUNDLE) | {"bundle.json"}


def test_a_manifest_that_disagrees_with_its_archive_falls_back(tmp_path: Path) -> None:
    # Both documents are covered by the same whole-archive SHA-256, so this is a
    # packaging bug rather than tampering - which is exactly the case where
    # guessing would produce a tree that is neither bundle.
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    archive = make_archive(tmp_path, BUNDLE, version="2.0.0")
    lying = _rewrite_with_extra_manifest_entry(archive, tmp_path)
    result = stage_bundle(lying, tmp_path / "staging", current_root=installed)
    assert result.mode == MODE_FULL
    assert result.reason == DELTA_MANIFEST_DISAGREES
    assert set(tree(result.root)) == set(BUNDLE) | {"bundle.json", BUNDLE_FILES_NAME}


def test_a_filesystem_that_cannot_link_still_stages_a_correct_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A copy is correct and costs a write and a scan, which is the thing being
    # avoided - so it is counted separately rather than silently substituted, and
    # the first failure describes the whole pair of directories.
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    archive = make_archive(tmp_path, BUNDLE, version="2.0.0")
    attempts = 0

    def refuse(source: str, destination: str) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("hard links are not supported here")

    monkeypatch.setattr(os, "link", refuse)
    result = stage_bundle(archive, tmp_path / "staging", current_root=installed)
    assert result.mode == MODE_DELTA
    assert result.linked == 0
    assert result.copied == len(BUNDLE)
    assert attempts == 1
    assert tree(result.root)["_internal/heavy/heavy.bin"] == b"H" * 200_000


def test_a_delta_that_fails_part_way_leaves_a_complete_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    archive = make_archive(tmp_path, BUNDLE, version="2.0.0")
    monkeypatch.setattr(
        "swe_mux.bundle_stage._place",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk went away")),
    )
    result = stage_bundle(archive, tmp_path / "staging", current_root=installed)
    assert result.mode == MODE_FULL
    assert set(tree(result.root)) == set(BUNDLE) | {"bundle.json", BUNDLE_FILES_NAME}


def test_the_callers_logger_is_given_the_one_line_that_matters(tmp_path: Path) -> None:
    # `redeploy_desktop.log` is where an operator is already looking when an
    # update is slow, so the decision has to appear there and not only in the
    # daemon's log.
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    archive = make_archive(tmp_path, BUNDLE, version="2.0.0")
    said: list[str] = []
    stage_bundle(archive, tmp_path / "staging", current_root=installed, say=said.append)
    assert any("delta: reuse" in line for line in said)


@pytest.mark.skipif(os.name == "nt", reason="Windows bundles carry no symlinks")
def test_a_symlink_is_recreated_rather_than_fetched(tmp_path: Path) -> None:
    files = {**BUNDLE, "libpython3.12.so.1.0": b"payload"}
    incoming = make_bundle(tmp_path / "incoming" / "swe-mux", files, version="2.0.0")
    os.symlink("libpython3.12.so.1.0", incoming / "libpython3.12.so")
    archive, _ = package_desktop_release.build_archive(incoming, tmp_path / "out")
    installed = make_bundle(tmp_path / "installed", files, version="1.0.0")
    result = stage_bundle(archive, tmp_path / "staging", current_root=installed)
    assert result.mode == MODE_DELTA
    assert result.links_created == 1
    assert (result.root / "libpython3.12.so").is_symlink()
    assert os.readlink(result.root / "libpython3.12.so") == "libpython3.12.so.1.0"


@pytest.mark.skipif(os.name == "nt", reason="Windows does not carry the mode bits")
def test_the_executable_bit_survives_a_delta_write(tmp_path: Path) -> None:
    # A `.tar.gz` bundle's `swe-mux` binary is unusable without it, and a delta
    # writes that file itself rather than letting `tarfile` restore it.
    incoming = make_bundle(tmp_path / "incoming" / "swe-mux", BUNDLE, version="2.0.0")
    os.chmod(incoming / "swe-mux.exe", 0o755)
    archive, _ = package_desktop_release.build_archive(incoming, tmp_path / "out")
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    result = stage_bundle(archive, tmp_path / "staging", current_root=installed)
    assert result.mode == MODE_DELTA
    assert (result.root / "swe-mux.exe").stat().st_mode & 0o111


def test_a_link_reuses_the_scan_verdict_by_reusing_the_file(tmp_path: Path) -> None:
    # The mechanism, stated as an assertion: a linked file is the *same*
    # filesystem object, which is what a scanner's cache is keyed on. A copy
    # would satisfy every content check in this file and collect none of the win.
    installed = make_bundle(tmp_path / "installed", BUNDLE, version="1.0.0")
    archive = make_archive(tmp_path, BUNDLE, version="2.0.0")
    result = stage_bundle(archive, tmp_path / "staging", current_root=installed)
    if result.linked == 0:
        pytest.skip("this filesystem does not support hard links")
    heavy = result.root / "_internal/heavy/heavy.bin"
    assert same_file(heavy, installed / "_internal/heavy/heavy.bin")
    assert heavy.stat().st_nlink >= 2
    assert PLACED_LINK == "link"


# --- helpers that make a broken archive -------------------------------------


def _rewrite_without(archive: Path, member: str) -> Path:
    """The same archive minus one member, in the same container format."""
    return _rewrite(archive, drop={member})


def _rewrite_with_extra_manifest_entry(archive: Path, tmp_path: Path) -> Path:
    """The same archive whose manifest names a file the archive does not carry."""
    bundle = make_bundle(tmp_path / "phantom" / "swe-mux", BUNDLE, version="2.0.0")
    (bundle / "phantom.bin").write_bytes(b"never packed")
    manifest = build_file_manifest(bundle, version="2.0.0", platform=release_platform_tag())
    return _rewrite(
        archive, replace={f"{ARCHIVE_ROOT}/{BUNDLE_FILES_NAME}": manifest_bytes(manifest)}
    )


def _rewrite(
    archive: Path, *, drop: set[str] | None = None, replace: dict[str, bytes] | None = None
) -> Path:
    import tarfile

    drop = drop or set()
    replace = replace or {}
    output = archive.with_name(f"rewritten-{archive.name}")
    if archive.name.endswith(TAR_GZ_SUFFIX):
        with tarfile.open(archive, "r:gz") as source, tarfile.open(output, "w:gz") as sink:
            for info in source.getmembers():
                if info.name in drop:
                    continue
                if info.name in replace:
                    payload = replace[info.name]
                    info.size = len(payload)
                    sink.addfile(info, __import__("io").BytesIO(payload))
                    continue
                handle = source.extractfile(info)
                sink.addfile(info, handle)
        return output
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as sink:
        for name in source.namelist():
            if name in drop:
                continue
            sink.writestr(name, replace.get(name, source.read(name)))
    return output
