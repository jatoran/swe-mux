"""The per-file hash manifest, and the plan a delta update is decided from.

Two halves, and the second is where the value is. The manifest itself is a
schema-gated document like `bundle.json` and the update manifest, and it is
tested the same way: an unrecognized schema, a malformed entry, and a path that
would write outside the bundle all have to be refused *before* anything reads a
field, because everything downstream of this file turns a path into a write.

The plan is the half that decides how much of an update gets rewritten, and its
one non-obvious property is what it does **not** decide on. Phase 21 asked for a
full replacement when "the Python version or the dependency set moves"; the
measurement that motivated this work refutes the reasoning - two real consecutive
bundles of this project shared 92.3% of their bytes across a release that removed
an entire 101 MB top-level package. So the trigger here is the measured share of
bytes already present, and the structural facts are recorded as observations
rather than acted on. The tests below pin both halves of that: a package that
comes or goes does not by itself force a full replacement, and a bundle that
genuinely shares almost nothing does.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from swe_mux.bundle_manifest import (
    BUNDLE_FILES_NAME,
    BUNDLE_FILES_SCHEMA,
    DELTA_NO_CURRENT_BUNDLE,
    DELTA_NO_MANIFEST,
    DELTA_OK,
    DELTA_TOO_LITTLE_REUSE,
    FILES_MALFORMED,
    FILES_OK,
    FILES_UNSUPPORTED_SCHEMA,
    KIND_LINK,
    build_file_manifest,
    bundle_packages,
    bundle_python_tag,
    manifest_bytes,
    parse_file_manifest,
    plan_delta,
)


def make_bundle(root: Path, files: dict[str, bytes]) -> Path:
    """A directory shaped like a bundle, with exactly the files given."""
    for name, payload in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


ORDINARY = {
    "swe-mux.exe": b"MZ" + b"\x00" * 4096,
    "bundle.json": b'{"schema": 1}',
    "_internal/python312.dll": b"interpreter" * 512,
    "_internal/numpy/core.pyd": b"numpy" * 2048,
    "_internal/swe_mux/static/index.html": b"<html></html>",
}


# --- the document -------------------------------------------------------------


def test_the_manifest_describes_every_file_but_itself(tmp_path: Path) -> None:
    # It cannot carry its own digest, so it must not claim to: everything that
    # consumes a manifest treats `files.json` as supplied by the archive.
    bundle = make_bundle(tmp_path / "swe-mux", ORDINARY)
    (bundle / BUNDLE_FILES_NAME).write_bytes(b'{"stale": true}')
    manifest = build_file_manifest(bundle, version="1.0.0", platform="windows-x64")
    paths = {entry.path for entry in manifest.entries}
    assert paths == set(ORDINARY)
    assert BUNDLE_FILES_NAME not in paths


def test_the_manifest_round_trips_through_its_own_bytes(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "swe-mux", ORDINARY)
    manifest = build_file_manifest(bundle, version="1.0.0", platform="windows-x64")
    reread, reason = parse_file_manifest(json.loads(manifest_bytes(manifest)))
    assert reason == FILES_OK
    assert reread is not None
    assert reread.entries == manifest.entries
    assert reread.version == "1.0.0"
    assert reread.platform == "windows-x64"
    assert reread.python == manifest.python
    assert reread.packages == manifest.packages


def test_an_unrecognized_schema_is_refused_before_any_field_is_read() -> None:
    manifest, reason = parse_file_manifest({"schema": BUNDLE_FILES_SCHEMA + 1, "files": []})
    assert manifest is None
    assert reason == FILES_UNSUPPORTED_SCHEMA


@pytest.mark.parametrize(
    "entry",
    [
        {"path": "../escape", "size": 1, "sha256": "a" * 64},
        {"path": "/absolute", "size": 1, "sha256": "a" * 64},
        {"path": "C:/drive", "size": 1, "sha256": "a" * 64},
        {"path": "ok", "size": 1, "sha256": "not-a-digest"},
        {"path": "ok", "size": -1, "sha256": "a" * 64},
        {"path": "ok", "size": 1},
        {"path": "ok", "kind": KIND_LINK, "target": "../out"},
    ],
    ids=["parent", "absolute", "drive", "digest", "size", "missing", "link-escape"],
)
def test_one_bad_entry_fails_the_whole_manifest(entry: dict[str, object]) -> None:
    # Dropping it - which is right for an *artifact list*, where the consequence
    # is "no artifact for you" - would here mean staging a tree that is silently
    # not the release. A manifest missing one file is not a smaller manifest.
    manifest, reason = parse_file_manifest({"schema": BUNDLE_FILES_SCHEMA, "files": [entry]})
    assert manifest is None
    assert reason == FILES_MALFORMED


def test_a_repeated_path_fails_the_manifest() -> None:
    entry = {"path": "same", "size": 1, "sha256": "a" * 64}
    manifest, reason = parse_file_manifest(
        {"schema": BUNDLE_FILES_SCHEMA, "files": [entry, dict(entry)]}
    )
    assert manifest is None
    assert reason == FILES_MALFORMED


def test_the_python_tag_and_packages_are_read_off_the_tree(tmp_path: Path) -> None:
    # Read off the bundle rather than from `sys.version_info`, because the
    # process asking is never the bundle: the redeploy script runs under the
    # source checkout's interpreter and the daemon runs the *old* build.
    bundle = make_bundle(tmp_path / "swe-mux", ORDINARY)
    (bundle / "_internal" / "numpy-2.0.dist-info").mkdir(parents=True, exist_ok=True)
    assert bundle_python_tag(bundle) == "python312"
    assert bundle_packages(bundle) == ("numpy", "python312.dll", "swe_mux")


def test_the_stable_abi_forwarder_does_not_win_the_python_tag(tmp_path: Path) -> None:
    # Found by running this code over a real bundle rather than by reading it.
    # PyInstaller collects Windows' `python3.dll` beside `python312.dll`, and
    # `python3.dll` sorts first - so a looser match reported every bundle ever
    # built as `python3`, a value that cannot tell 3.12 from 3.13. A useless
    # observation is worse than an absent one, because it looks like an answer.
    bundle = make_bundle(
        tmp_path / "swe-mux",
        {"_internal/python3.dll": b"forwarder", "_internal/python313.dll": b"real"},
    )
    assert bundle_python_tag(bundle) == "python313"


# --- the plan -----------------------------------------------------------------


def test_an_unchanged_bundle_is_entirely_reusable(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "swe-mux", ORDINARY)
    manifest = build_file_manifest(bundle, version="1.0.0", platform="windows-x64")
    plan = plan_delta(manifest, bundle)
    assert plan.eligible
    assert plan.reason == DELTA_OK
    assert not plan.fetch
    assert len(plan.reuse) == len(ORDINARY)
    assert plan.reuse_share == 1.0


def test_only_the_files_whose_bytes_differ_are_fetched(tmp_path: Path) -> None:
    installed = make_bundle(tmp_path / "installed", ORDINARY)
    incoming = make_bundle(
        tmp_path / "incoming",
        {**ORDINARY, "swe-mux.exe": b"MZ" + b"\xff" * 4096, "new.txt": b"added"},
    )
    manifest = build_file_manifest(incoming, version="2.0.0", platform="windows-x64")
    plan = plan_delta(manifest, installed)
    assert plan.eligible
    assert sorted(entry.path for entry in plan.fetch) == ["new.txt", "swe-mux.exe"]
    assert len(plan.reuse) == len(ORDINARY) - 1
    # Nothing was written, moved, or linked by planning it.
    assert not (installed / "new.txt").exists()


def test_a_same_size_different_content_file_is_not_reused(tmp_path: Path) -> None:
    # The size check is an early-out and never a substitute for the hash: "same
    # path, same size" is exactly the shape a stale build has.
    installed = make_bundle(tmp_path / "installed", {"a.bin": b"x" * 64})
    incoming = make_bundle(tmp_path / "incoming", {"a.bin": b"y" * 64})
    manifest = build_file_manifest(incoming, version="2.0.0", platform="windows-x64")
    plan = plan_delta(manifest, installed)
    assert [entry.path for entry in plan.fetch] == ["a.bin"]


def test_a_package_appearing_or_vanishing_does_not_force_a_full_replacement(
    tmp_path: Path,
) -> None:
    # The refutation, pinned. Phase 21 asked for a full replacement when the
    # dependency set moves; the 2026-08-27 -> 2026-08-29 pair removed 101 MB of
    # `playwright/driver` and still shared 92.3% of its bytes. A package that
    # comes or goes invalidates that package and nothing else.
    installed = make_bundle(
        tmp_path / "installed", {**ORDINARY, "_internal/playwright/node.exe": b"P" * 4096}
    )
    incoming = make_bundle(tmp_path / "incoming", ORDINARY)
    manifest = build_file_manifest(incoming, version="2.0.0", platform="windows-x64")
    plan = plan_delta(manifest, installed)
    assert plan.eligible
    assert not plan.fetch
    assert any("packages moved" in note for note in plan.observations)


def test_a_bundle_that_shares_almost_nothing_falls_back_to_a_full_replacement(
    tmp_path: Path,
) -> None:
    installed = make_bundle(tmp_path / "installed", {"a.bin": b"x" * 4096})
    incoming = make_bundle(
        tmp_path / "incoming", {"a.bin": b"x" * 4096, "b.bin": b"y" * 65536}
    )
    manifest = build_file_manifest(incoming, version="2.0.0", platform="windows-x64")
    plan = plan_delta(manifest, installed)
    assert not plan.eligible
    assert plan.reason == DELTA_TOO_LITTLE_REUSE


def test_a_python_move_is_reported_and_lands_under_the_floor_by_itself(
    tmp_path: Path,
) -> None:
    # The named trigger's job is done by the measurement: replacing the
    # interpreter replaces the bytes, and the bytes are what is compared.
    installed = make_bundle(
        tmp_path / "installed", {"_internal/python312.dll": b"old" * 4096}
    )
    incoming = make_bundle(
        tmp_path / "incoming", {"_internal/python313.dll": b"new" * 4096}
    )
    manifest = build_file_manifest(incoming, version="2.0.0", platform="windows-x64")
    plan = plan_delta(manifest, installed)
    assert not plan.eligible
    assert plan.reason == DELTA_TOO_LITTLE_REUSE
    assert any("python moved" in note for note in plan.observations)


def test_no_manifest_and_no_installed_bundle_are_different_words() -> None:
    assert plan_delta(None, None).reason == DELTA_NO_MANIFEST
    manifest, _ = parse_file_manifest({"schema": BUNDLE_FILES_SCHEMA, "files": []})
    assert plan_delta(manifest, Path("nowhere-at-all")).reason == DELTA_NO_CURRENT_BUNDLE


def test_an_unreadable_installed_file_is_simply_not_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The running app's own image can be locked, and a permission failure must
    # degrade to "write it fresh" rather than to an exception on a UI path.
    installed = make_bundle(tmp_path / "installed", ORDINARY)
    incoming = make_bundle(tmp_path / "incoming", ORDINARY)
    manifest = build_file_manifest(incoming, version="2.0.0", platform="windows-x64")

    def refuse(path: Path) -> str:
        if path.name == "swe-mux.exe":
            raise OSError("locked by a running process")
        return "0" * 64

    monkeypatch.setattr("swe_mux.bundle_manifest.file_sha256", refuse)
    plan = plan_delta(manifest, installed, floor=0.0)
    assert [entry.path for entry in plan.fetch] == sorted(ORDINARY)


@pytest.mark.skipif(os.name == "nt", reason="Windows bundles carry no symlinks")
def test_a_symlink_is_recorded_as_a_link_and_never_hashed(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "swe-mux", {"libpython3.12.so.1.0": b"payload"})
    os.symlink("libpython3.12.so.1.0", bundle / "libpython3.12.so")
    manifest = build_file_manifest(bundle, version="1.0.0", platform="linux-x64")
    link = next(entry for entry in manifest.entries if entry.path == "libpython3.12.so")
    assert link.kind == KIND_LINK
    assert link.target == "libpython3.12.so.1.0"
    assert link.sha256 == ""
    plan = plan_delta(manifest, bundle)
    assert [entry.path for entry in plan.links] == ["libpython3.12.so"]
