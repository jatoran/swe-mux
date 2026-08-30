"""The pinned desktop closure: that it describes `uv.lock`, and its one sdist.

ROADMAP Phase 24. `swe_mux.desktop_runtime` acquires the tray/native-window
closure from pins generated out of `uv.lock` by
`packaging/generate_desktop_pins.py`; a stale table is a first-use download of a
closure this repository never audited, so parity is a gate exactly as it is for
the voice table.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from swe_mux import desktop_wheels

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The desktop generator imports its sibling by module name, so load that first.
_load("generate_voice_pins")
generate_desktop_pins = _load("generate_desktop_pins")


def test_the_committed_pin_table_matches_a_fresh_generation() -> None:
    lock = generate_desktop_pins.load_lock()
    names = generate_desktop_pins.acquired_packages(lock)
    rows = generate_desktop_pins.wheel_rows(lock, names)
    sdists = generate_desktop_pins.sdist_rows(lock, names)
    rendered = generate_desktop_pins.render(rows, sdists)
    committed = (REPO_ROOT / "src" / "swe_mux" / "desktop_wheels.py").read_text(encoding="utf-8")
    assert committed == rendered, (
        "desktop_wheels.py is stale; run `uv run python "
        "packaging/generate_desktop_pins.py --write`"
    )


def test_the_closure_is_the_desktop_extra_and_nothing_base_reachable() -> None:
    """The set difference is what keeps compiled base packages out.

    `pillow` and `cffi` are compiled and version-specific, and both are
    reachable from the base application - so the acquired closure must not
    carry them, which is also what keeps it pure Python and small (~2.4 MB on
    Windows) rather than the phase's original hand-waved 'no compiled
    extensions' premise.
    """
    lock = generate_desktop_pins.load_lock()
    names = set(generate_desktop_pins.acquired_packages(lock))
    assert "pystray" in names and "pywebview" in names
    assert "pillow" not in names and "cffi" not in names and "typing-extensions" not in names


def test_the_sdist_pin_exists_because_pypi_has_no_wheel_for_it() -> None:
    """`proxy-tools` is the reason sdist pins exist at all; if it ever grows a
    wheel, the pin should move to WHEELS and this table should shrink."""
    assert [entry.distribution for entry in desktop_wheels.SDISTS] == ["proxy-tools"]
    sdist = desktop_wheels.SDISTS[0]
    assert sdist.filename.endswith(".tar.gz")
    assert len(sdist.sha256) == 64 and sdist.size > 0
    assert "proxy-tools" not in desktop_wheels.DISTRIBUTIONS


def test_the_digest_moves_when_an_sdist_moves() -> None:
    """The closure digest is the identity a state file trusts, so it must cover
    the sdist rows exactly as it covers wheels."""
    lock = generate_desktop_pins.load_lock()
    names = generate_desktop_pins.acquired_packages(lock)
    rows = generate_desktop_pins.wheel_rows(lock, names)
    sdists = generate_desktop_pins.sdist_rows(lock, names)
    tampered = [dict(row) for row in sdists]
    tampered[0]["sha256"] = "0" * 64
    original = generate_desktop_pins.lock_digest(rows + sdists)
    assert generate_desktop_pins.lock_digest(rows + tampered) != original


def test_this_interpreter_selects_one_wheel_per_distribution() -> None:
    selected = desktop_wheels.wheels_for_this_interpreter()
    assert sorted({wheel.distribution for wheel in selected}) == sorted(
        desktop_wheels.DISTRIBUTIONS
    )
    assert len(selected) == len(desktop_wheels.DISTRIBUTIONS)
