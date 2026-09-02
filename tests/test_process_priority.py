"""Session trees are only ever lowered; the daemon is only ever raised.

Pinned against a fake process so the ordering rule is tested on every host, plus
one real-process check where the platform can express a class at all.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import psutil
import pytest

from swe_mux import process_priority
from swe_mux.config import Config


class _FakeProcess:
    def __init__(self, value: int, *, pid: int = 4242) -> None:
        self.value = value
        self.pid = pid
        self.set_calls: list[int] = []

    def nice(self, value: int | None = None) -> int:
        if value is None:
            return self.value
        self.set_calls.append(value)
        self.value = value
        return value


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows scheduling classes")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX nice values")


@windows_only
def test_lower_process_moves_a_normal_process_below_normal_and_leaves_lower_ones() -> None:
    normal = _FakeProcess(psutil.NORMAL_PRIORITY_CLASS)
    assert process_priority.lower_process(normal, "below_normal") == process_priority.LOWERED
    assert normal.set_calls == [psutil.BELOW_NORMAL_PRIORITY_CLASS]

    idle = _FakeProcess(psutil.IDLE_PRIORITY_CLASS)
    assert process_priority.lower_process(idle, "below_normal") == process_priority.UNCHANGED
    assert idle.set_calls == [], "an agent that chose idle for its own gate is not raised"

    already = _FakeProcess(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    assert process_priority.lower_process(already, "below_normal") == process_priority.UNCHANGED

    high = _FakeProcess(psutil.HIGH_PRIORITY_CLASS)
    assert process_priority.lower_process(high, "below_normal") == process_priority.LOWERED


@posix_only
def test_lower_process_raises_nice_only_when_below_target() -> None:
    zero = _FakeProcess(0)
    assert process_priority.lower_process(zero, "below_normal") == process_priority.LOWERED
    assert zero.set_calls == [5]
    ten = _FakeProcess(10)
    assert process_priority.lower_process(ten, "below_normal") == process_priority.UNCHANGED


def test_normal_target_is_a_no_op_that_touches_nothing() -> None:
    process = _FakeProcess(0)
    assert process_priority.lower_process(process, "normal") == process_priority.UNCHANGED
    assert process.set_calls == []
    assert process_priority.apply_session_root(4242, "normal") == process_priority.UNCHANGED
    assert process_priority.raise_self("normal") == process_priority.UNCHANGED


def test_refusals_and_exits_are_outcomes_rather_than_exceptions() -> None:
    class Denied(_FakeProcess):
        def nice(self, value: int | None = None) -> int:
            raise psutil.AccessDenied(self.pid)

    class Gone(_FakeProcess):
        def nice(self, value: int | None = None) -> int:
            raise psutil.NoSuchProcess(self.pid)

    assert process_priority.lower_process(Denied(0), "below_normal") == process_priority.DENIED
    assert process_priority.lower_process(Gone(0), "below_normal") == process_priority.GONE
    assert process_priority.apply_session_root(-1, "below_normal") == process_priority.UNSUPPORTED


def test_priority_name_reads_the_class_back_by_policy_name() -> None:
    if sys.platform == "win32":
        normal = _FakeProcess(psutil.NORMAL_PRIORITY_CLASS)
        assert process_priority.priority_name(normal) == "normal"
        assert (
            process_priority.priority_name(_FakeProcess(psutil.BELOW_NORMAL_PRIORITY_CLASS))
            == "below_normal"
        )
        assert (
            process_priority.priority_name(_FakeProcess(psutil.ABOVE_NORMAL_PRIORITY_CLASS))
            == "above_normal"
        )
    else:
        assert process_priority.priority_name(_FakeProcess(0)) == "normal"
        assert process_priority.priority_name(_FakeProcess(7)) == "below_normal"
        assert process_priority.priority_name(_FakeProcess(-5)) == "above_normal"


@windows_only
def test_raise_self_moves_this_process_above_normal_and_is_idempotent() -> None:
    me = psutil.Process(os.getpid())
    original = me.nice()
    try:
        outcome = process_priority.raise_self("above_normal")
        assert outcome in {process_priority.RAISED, process_priority.UNCHANGED}
        assert process_priority.priority_name(me) in {"above_normal", "high", "realtime"}
        assert process_priority.raise_self("above_normal") == process_priority.UNCHANGED
    finally:
        me.nice(original)


def test_config_accepts_only_the_two_spellings_per_knob(tmp_path: Any) -> None:
    config = Config(data_dir=tmp_path)
    assert config.session_process_priority == "below_normal"
    assert config.daemon_process_priority == "above_normal"
    from swe_mux.config import _validate

    config.session_process_priority = "idle"
    with pytest.raises(ValueError, match="session_process_priority"):
        _validate(config)
    config.session_process_priority = "normal"
    config.daemon_process_priority = "high"
    with pytest.raises(ValueError, match="daemon_process_priority"):
        _validate(config)
    config.daemon_process_priority = "normal"
    _validate(config)
