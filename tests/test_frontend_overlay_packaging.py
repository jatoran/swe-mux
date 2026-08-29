"""The producer script, and the property that makes it a contract rather than a helper.

`packaging/build_frontend_overlay.py` is the only thing that mints a compatibility
pin. If it stopped reading the checkout's own `__version__` - or started writing
its manifest into `src/swe_mux/static` instead of into the archive - the failure
would not show up as an error anywhere. It would show up as an overlay that never
serves, or as a checkout with an untracked file that changes every build.

Imported by path rather than as a package, because `packaging/` deliberately is
not one: the scripts there are run with `uv run python packaging/<name>.py` and
adding an `__init__.py` to make them importable would make them look like library
code that ships.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from swe_mux import __version__
from swe_mux.frontend_overlay import (
    ARCHIVE_ROOT,
    MANIFEST_NAME,
    OverlayStore,
    extract_overlay,
    read_manifest,
)

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_frontend_overlay", PACKAGING / "build_frontend_overlay.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _built_tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_bytes(
        b'<!doctype html><meta name="ui-build" content="' + b"ef" * 32 + b'">'
    )
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "app.js").write_bytes(b"console.log(1)\n")
    return root


def test_the_archive_is_named_for_the_version_and_carries_no_platform() -> None:
    # A static tree has no platform, and naming it as though it did would invite a
    # per-platform artifact that does not need to exist.
    module = _load()
    assert module.overlay_archive_name("1.2.3") == "swe-mux-1.2.3-ui.zip"


def test_packaging_pins_the_checkouts_own_version(tmp_path: Path) -> None:
    module = _load()
    static = _built_tree(tmp_path / "static")
    assert module.main(["--static", str(static), "--out", str(tmp_path / "out")]) == 0
    archive = tmp_path / "out" / module.overlay_archive_name(__version__)
    root = extract_overlay(archive, tmp_path / "staging")
    assert read_manifest(root).requires_backend == __version__


def test_packaging_leaves_the_static_tree_exactly_as_the_build_left_it(
    tmp_path: Path,
) -> None:
    module = _load()
    static = _built_tree(tmp_path / "static")
    before = {path.name for path in static.rglob("*") if path.is_file()}
    module.main(["--static", str(static), "--out", str(tmp_path / "out")])
    after = {path.name for path in static.rglob("*") if path.is_file()}
    assert before == after
    assert not (static / MANIFEST_NAME).exists()


def test_a_packaged_archive_installs_and_verifies(tmp_path: Path) -> None:
    """The round trip, which is the only thing that proves the two halves agree."""
    module = _load()
    static = _built_tree(tmp_path / "static")
    module.main(["--static", str(static), "--out", str(tmp_path / "out")])
    archive = tmp_path / "out" / module.overlay_archive_name(__version__)
    store = OverlayStore(tmp_path / "data", backend_version=__version__)
    result = store.install_from_archive(archive)
    assert result.manifest.requires_backend == __version__
    assert store.state.active


def test_packaging_refuses_a_directory_that_is_not_a_frontend(tmp_path: Path) -> None:
    module = _load()
    empty = tmp_path / "static"
    (empty / "assets").mkdir(parents=True)
    (empty / "assets" / "orphan.js").write_bytes(b"x")
    with pytest.raises(SystemExit) as exit_info:
        module.main(["--static", str(empty), "--out", str(tmp_path / "out")])
    assert "no_index" in str(exit_info.value)


def test_packaging_a_missing_tree_says_how_to_build_one(tmp_path: Path) -> None:
    module = _load()
    with pytest.raises(SystemExit) as exit_info:
        module.main(["--static", str(tmp_path / "absent"), "--out", str(tmp_path / "out")])
    assert "npm run build" in str(exit_info.value)


def test_the_archive_is_rooted_where_the_reader_expects(tmp_path: Path) -> None:
    # The archive root is a contract between this script and `extract_overlay`,
    # exactly like the release archive's `swe-mux/` root is between
    # `package_desktop_release.py` and `bundle_archive`.
    import zipfile

    module = _load()
    static = _built_tree(tmp_path / "static")
    module.main(["--static", str(static), "--out", str(tmp_path / "out")])
    archive = tmp_path / "out" / module.overlay_archive_name(__version__)
    with zipfile.ZipFile(archive) as payload:
        names = payload.namelist()
    assert all(name.startswith(f"{ARCHIVE_ROOT}/") for name in names)
    assert f"{ARCHIVE_ROOT}/{MANIFEST_NAME}" in names
