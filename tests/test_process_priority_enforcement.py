"""The inspector lowers what a session owns and never the daemon's own successor.

A redeploy launched from a session's shell puts the new desktop shell and daemon
under that session's process tree. Attribution already rejects them by image;
enforcement has to be downstream of that rejection, because on 2026-09-01 it was
not and the freshly raised daemon was lowered below the fleet it was meant to beat.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import psutil
import pytest

from swe_mux import processes
from swe_mux.event_bus import EventBus
from swe_mux.models import SessionRecord
from swe_mux.processes import ProcessInspector

OWN_IMAGE = str(Path(sys.executable).resolve())


def _normal_class() -> int:
    return int(psutil.NORMAL_PRIORITY_CLASS) if sys.platform == "win32" else 0


def _record(pid: int) -> SessionRecord:
    record = SessionRecord(
        "session-a", "session-a", "project-a", "claude", "native-a", ".", "claude", []
    )
    record.pid = pid
    record.state = "running"
    return record


def test_session_descendants_are_lowered_and_the_daemon_successor_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 500 = session root, 501 = its shell, 900 = a swe-mux app the shell launched
    # (a redeploy from inside the session), 901 = that app's daemon child.
    created = {500: 1_000.0, 501: 1_001.0, 900: 1_002.0, 901: 1_003.0}
    names = {500: "claude.exe", 501: "bash.exe", 900: "swe-mux.exe", 901: "swe-mux.exe"}
    images = {900: OWN_IMAGE, 901: OWN_IMAGE}
    set_calls: dict[int, list[int]] = {}

    class Fake:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return created[self.pid]

        def oneshot(self) -> Fake:
            return self

        def __enter__(self) -> Fake:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1

        def name(self) -> str:
            return names[self.pid]

        def cmdline(self) -> list[str]:
            return [names[self.pid]] + (["--daemon-child"] if self.pid == 901 else [])

        def exe(self) -> str:
            return images.get(self.pid, f"C:/tools/{names[self.pid]}")

        def cpu_times(self) -> Any:
            return SimpleNamespace(user=0.0, system=0.0)

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=1024)

        def nice(self, value: int | None = None) -> int:
            if value is None:
                history = set_calls.get(self.pid)
                return history[-1] if history else _normal_class()
            set_calls.setdefault(self.pid, []).append(value)
            return value

    # This test process *is* the daemon at pid 901, so the walk's infrastructure
    # scan starts from a pid the fakes know rather than from the real interpreter.
    monkeypatch.setattr(processes.os, "getpid", lambda: 901)
    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=Fake,
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
            _ppid_map=lambda: {500: 1, 501: 500, 900: 501, 901: 900},
            net_connections=lambda **_: [],
        ),
    )
    sessions = SimpleNamespace(
        sessions={"session-a": SimpleNamespace(record=_record(500))},
        resolve=lambda _identity: None,
    )
    inspector = ProcessInspector(
        cast(Any, sessions), EventBus(), session_priority="below_normal"
    )
    inspector._own_executable = OWN_IMAGE

    inspector._collect_all()

    assert set(set_calls) == {500, 501}, (
        "the session's own tree is lowered; the daemon and its shell are infrastructure"
    )
    assert 900 not in set_calls and 901 not in set_calls
    stats = inspector.priority_stats()
    assert stats["target"] == "below_normal"
    assert stats["lowered_total"] == 2 and stats["lowered_last_pass"] == 2

    # A second pass finds nothing above target and lowers nothing again.
    inspector._collect_all()
    assert inspector.priority_stats()["lowered_last_pass"] == 0


def test_enforcement_is_off_at_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class Fake:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def nice(self, value: int | None = None) -> int:
            if value is None:
                return _normal_class()
            calls.append(value)
            return value

    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    inspector._enforce_priority(Fake(1), 1, "x.exe")
    assert calls == []
    assert inspector.priority_stats()["lowered_total"] == 0
