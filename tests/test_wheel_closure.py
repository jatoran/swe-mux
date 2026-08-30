"""The shared closure store's sdist half: extract, never build.

The condition that makes a pinned sdist as auditable as a pinned wheel is that
nothing from the archive is ever executed - `_extract_sdist` copies the
already-importable package source and refuses everything that would need a
build step. These tests pin the refusals as hard as the happy path, because a
future pin that quietly grows a build requirement must fail loudly rather than
be handled cleverly.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from swe_mux.wheel_closure import ClosureAcquisitionError, _extract_sdist


def _sdist(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def test_a_pure_python_package_is_extracted_and_nothing_else(tmp_path: Path) -> None:
    archive = _sdist(
        tmp_path / "proxy_tools-0.1.0.tar.gz",
        {
            "proxy_tools-0.1.0/proxy_tools/__init__.py": b"answer = 42\n",
            "proxy_tools-0.1.0/setup.py": b"raise SystemExit('must never run')\n",
            "proxy_tools-0.1.0/PKG-INFO": b"Metadata-Version: 2.1\n",
        },
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    _extract_sdist(archive, staging, "proxy-tools")
    assert (staging / "proxy_tools" / "__init__.py").read_bytes() == b"answer = 42\n"
    # The build machinery is not extracted, let alone executed.
    assert not (staging / "setup.py").exists()
    assert not (staging / "PKG-INFO").exists()


def test_a_single_module_sdist_is_extracted(tmp_path: Path) -> None:
    archive = _sdist(
        tmp_path / "thing-1.0.tar.gz",
        {"thing-1.0/thing.py": b"x = 1\n", "thing-1.0/setup.py": b""},
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    _extract_sdist(archive, staging, "thing")
    assert (staging / "thing.py").is_file()


def test_an_sdist_that_needs_building_is_refused(tmp_path: Path) -> None:
    """Compiled sources inside the package are the build-step signature."""
    archive = _sdist(
        tmp_path / "native-1.0.tar.gz",
        {
            "native-1.0/native/__init__.py": b"",
            "native-1.0/native/_speed.c": b"int main(){}",
        },
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ClosureAcquisitionError, match="extract-never-build"):
        _extract_sdist(archive, staging, "native")
    assert not list(staging.iterdir()), "a refusal must leave nothing behind"


def test_an_sdist_with_no_plain_package_is_refused(tmp_path: Path) -> None:
    """A src-layout or otherwise unextractable sdist is a refusal, not a guess."""
    archive = _sdist(
        tmp_path / "odd-1.0.tar.gz",
        {"odd-1.0/src/odd/__init__.py": b"", "odd-1.0/setup.py": b""},
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ClosureAcquisitionError, match="needs building is refused"):
        _extract_sdist(archive, staging, "odd")


def test_a_multi_root_archive_is_refused(tmp_path: Path) -> None:
    archive = _sdist(
        tmp_path / "weird-1.0.tar.gz",
        {"weird-1.0/weird/__init__.py": b"", "other-root/x.py": b""},
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ClosureAcquisitionError, match="single-root"):
        _extract_sdist(archive, staging, "weird")


def test_an_out_of_tree_member_is_refused(tmp_path: Path) -> None:
    archive = _sdist(
        tmp_path / "evil-1.0.tar.gz",
        {
            "evil-1.0/evil/__init__.py": b"",
            "evil-1.0/evil/../../escape.py": b"",
        },
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ClosureAcquisitionError, match="out-of-tree"):
        _extract_sdist(archive, staging, "evil")
    assert not (tmp_path / "escape.py").exists()


# The two desktop-store tests that used to close this file went with the store
# itself on 2026-08-30, when `pystray`/`pywebview` became base dependencies and
# there was no longer a closure to acquire. What they covered - platform
# gating and the "estimate equals the pinned selection" property - is still
# covered for the one remaining store in `tests/test_voice_runtime.py`
# (`supported` gating, and `total_bytes` asserted equal to the pinned selection).
