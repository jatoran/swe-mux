"""The daemon's GIL switch interval: set where it says, bounded, and live on change.

`DAEMON_LOAD_ISOLATION.md` item 5. The interval is process-wide, so every test that
moves it puts it back.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from aiohttp import web

from swe_mux import app_keys as keys
from swe_mux.config import RESTART_FIELDS, Config, _validate, update_config
from swe_mux.gil_switch import apply_gil_switch_interval, gil_switch_interval_ms
from swe_mux.runtime_config import apply_runtime_config


@pytest.fixture
def restore_interval() -> Iterator[None]:
    previous = sys.getswitchinterval()
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def test_applying_sets_the_interval_and_reports_what_is_in_force(restore_interval: None) -> None:
    assert apply_gil_switch_interval(2.0) == 2.0
    assert sys.getswitchinterval() == pytest.approx(0.002)
    assert gil_switch_interval_ms() == 2.0


@pytest.mark.parametrize("value", [0.5, 0.1, 0.0, -1.0, 25.0])
def test_a_value_outside_the_measured_range_is_refused(value: float) -> None:
    with pytest.raises(ValueError) as caught:
        _validate(Config(gil_switch_interval_ms=value))
    assert "gil_switch_interval_ms" in caught.value.args[0]


def test_the_default_validates() -> None:
    _validate(Config())


def test_a_change_applies_to_the_running_daemon_without_a_restart(
    tmp_path: Path, restore_interval: None
) -> None:
    config = Config(data_dir=tmp_path)
    app = web.Application()
    app[keys.CONFIG] = config
    hot, restart = update_config(config, {"gil_switch_interval_ms": 1.0})
    assert "gil_switch_interval_ms" in hot and not restart
    apply_runtime_config(app, hot)
    assert gil_switch_interval_ms() == 1.0
    assert "gil_switch_interval_ms" not in RESTART_FIELDS


def test_the_module_stays_out_of_the_supervisor_closure() -> None:
    """The supervisor's bundle is rebuilt only by reaping every session, and only when a
    file in its source closure changes; the interval lives in its own module so that
    tuning the daemon can never be that change."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
    try:
        import build_desktop
    finally:
        sys.path.pop(0)
    closure = {path.name for path in build_desktop.SUPERVISOR_SOURCES}
    assert "gil_switch.py" not in closure
