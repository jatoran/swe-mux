"""Phase 21 A1-A3: `--clean` stays, UPX is pinned off, and both were measured.

ROADMAP Phase 21 named PyInstaller's `--clean` the prime suspect for local
rebuild time. It is not, and the shape of the refutation is worth keeping because
the obvious next move is also wrong:

- `--clean` discards a bincache that only ever holds UPX-compressed and stripped
  binaries, and this spec sets neither, so that half is a pass-through.
- It also discards the workpath's analysis cache, which never validated in the
  first place: `Analysis(excludes=[...])` is a list and
  `PyInstaller.depend.analysis.initialize_modgraph` does
  `excludes += ("__main__",)`, extending it in place, so the saved guts can never
  match the next run's input. Passing a tuple fixes that and was measured fixing
  it - a no-op rebuild fell from 64s to 12s.
- And that fix still does not pay, because the analysed `pure` and `datas` TOCs
  are mtime-checked: any changed source file or rebuilt frontend asset forces a
  full re-analysis, and every real redeploy has one. Measured with an edit before
  each build, the arms are indistinguishable.

So the gate is that nothing here quietly changes back without a new measurement.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "packaging" / "swe_mux.spec"


def _load(name: str):
    """Import a `packaging/` script by path; it is not an installed package."""
    sys.path.insert(0, str(REPO_ROOT / "packaging"))
    try:
        spec = importlib.util.spec_from_file_location(
            f"{name}_build_cache_under_test", REPO_ROOT / "packaging" / f"{name}.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


build_desktop = _load("build_desktop")


class _Image:
    def save(self, *args: Any, **kwargs: Any) -> None:
        return None


def _record_pyinstaller(monkeypatch: Any) -> list[list[str]]:
    """Stub everything `build_app_bundle` does except assembling the command."""
    commands: list[list[str]] = []

    def _run(command: list[str], **kwargs: Any) -> Any:
        commands.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_desktop, "verify_build_extras_installed", lambda: None)
    monkeypatch.setattr(build_desktop, "create_tray_image", lambda _size: _Image())
    monkeypatch.setattr(build_desktop, "verify_bundle_contents", lambda _root: None)
    monkeypatch.setattr(build_desktop, "verify_bundle_licenses", lambda _root: None)
    monkeypatch.setattr(build_desktop, "describe_bundle", lambda _root: None)
    monkeypatch.setattr(build_desktop.subprocess, "run", _run)
    return commands


# --------------------------------------------------------------------------- the build command


def test_every_app_build_still_discards_the_cache(monkeypatch: Any, tmp_path: Path) -> None:
    """Measured, not assumed: reusing it saved nothing and risked a stale bundle."""
    commands = _record_pyinstaller(monkeypatch)
    build_desktop.build_app_bundle(distpath=tmp_path)
    assert "--clean" in commands[0]
    assert "--noconfirm" in commands[0]


def test_the_clean_flag_carries_its_measurement(monkeypatch: Any) -> None:
    """An unexplained default is what made this an open audit item in the first place.

    The roadmap's complaint was not that `--clean` was wrong, it was that nothing
    said why it was there. A future reader must find the numbers next to the flag
    rather than re-run seventeen builds to rediscover them.
    """
    source = (REPO_ROOT / "packaging" / "build_desktop.py").read_text(encoding="utf-8")
    body = source.split("def build_app_bundle")[1].split("\ndef ")[0]
    assert "--clean" in body
    assert "excludes" in body, "the analysis-cache finding belongs beside the flag"
    assert "bincache" in body, "the bincache half of the finding belongs here too"


def test_the_membership_check_runs_on_the_built_tree(monkeypatch: Any, tmp_path: Path) -> None:
    """A4's gate has to be wired in, not merely defined."""
    _record_pyinstaller(monkeypatch)
    checked: list[Path] = []
    monkeypatch.setattr(build_desktop, "verify_bundle_contents", checked.append)

    build_desktop.build_app_bundle(distpath=tmp_path)

    assert checked == [tmp_path / "swe-mux"]


# --------------------------------------------------------------------------- the UPX pin


def test_upx_is_pinned_off_in_the_app_spec() -> None:
    """UPX is a no-op today and a liability the day it is installed.

    It would add a compression pass over a ~400 MB closure to every build and give
    every shipped binary a packer signature, which is one of the best-known
    antivirus heuristics - and antivirus scanning is already the dominant cost of
    a swe-mux update, so the upside is a smaller download and the downside is more
    of the exact thing that makes updates slow.
    """
    spec = SPEC.read_text(encoding="utf-8")
    code = [line for line in spec.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "upx=True" in line]
    assert "UPX = False" in spec
    assert spec.count("upx=UPX") == 2, "both EXE and COLLECT must read the pin"


def test_the_supervisor_spec_is_left_alone_on_purpose() -> None:
    """Its `upx=True` is equally inert, and fixing it costs every live session.

    `packaging/swe_mux_supervisor.spec` is a member of `SUPERVISOR_SOURCES`, whose
    SHA-256 gates the supervisor bundle and is taken over file *bytes* - so even a
    comment there would make `supervisor_bundle_current()` report the running
    bundle stale forever, and clearing that report means a rebuild that reaps
    every live session. This test does not pin `upx=True` in that file, because
    the next deliberate supervisor rebuild should fix it; it pins that the app
    spec explains the asymmetry, so the omission reads as a decision rather than
    an oversight.
    """
    assert (
        REPO_ROOT / "packaging" / "swe_mux_supervisor.spec"
    ) in build_desktop.SUPERVISOR_SOURCES
    assert "swe_mux_supervisor.spec" in SPEC.read_text(encoding="utf-8")
