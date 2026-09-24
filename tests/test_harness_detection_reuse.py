"""The polled harness registry reuses a recent detection instead of re-walking PATH.

Measured 2026-09-24: every fleet refresh re-read `/api/harnesses`, detection resolved
every registered harness against PATH (twice for an absent one), and that was ~9% of
the daemon's GIL time at 2.5 refreshes a second.
"""

from __future__ import annotations

from typing import Any

import pytest

from swe_mux import harness
from swe_mux.harness import HarnessInstallation, reused_installations_with_versions


@pytest.fixture
def detections(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    def detect(overrides: dict[str, str] | None = None) -> dict[str, HarnessInstallation]:
        calls.append(dict(overrides or {}))
        return {"claude": HarnessInstallation(installed=True, resolved_path=f"claude-{len(calls)}")}

    monkeypatch.setattr(harness, "detect_installations_with_versions", detect)
    monkeypatch.setattr(harness, "_detection_reuse", {})
    monkeypatch.setenv("PATH", "C:/bin")
    return calls


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_a_recent_detection_is_reused_and_an_old_one_is_not(detections: list[Any]) -> None:
    clock = Clock()
    first = reused_installations_with_versions({}, clock=clock)
    clock.now += harness.DETECTION_REUSE_SECONDS - 1
    again = reused_installations_with_versions({}, clock=clock)
    clock.now += 2
    later = reused_installations_with_versions({}, clock=clock)

    assert len(detections) == 2
    assert first == again
    assert later["claude"].resolved_path == "claude-2"


def test_a_changed_override_or_path_is_never_answered_from_the_old_detection(
    detections: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    reused_installations_with_versions({}, clock=clock)
    reused_installations_with_versions({"claude": "C:/tools/claude.exe"}, clock=clock)
    monkeypatch.setenv("PATH", "C:/other")
    reused_installations_with_versions({"claude": "C:/tools/claude.exe"}, clock=clock)

    assert detections == [{}, {"claude": "C:/tools/claude.exe"}, {"claude": "C:/tools/claude.exe"}]


def test_a_fresh_read_detects_and_refreshes_the_reuse(detections: list[Any]) -> None:
    clock = Clock()
    reused_installations_with_versions({}, clock=clock)
    fresh = reused_installations_with_versions({}, max_age=0.0, clock=clock)
    reused = reused_installations_with_versions({}, clock=clock)

    assert len(detections) == 2
    assert fresh == reused
    assert reused["claude"].resolved_path == "claude-2"


def test_a_caller_cannot_mutate_the_reused_answer(detections: list[Any]) -> None:
    clock = Clock()
    first = reused_installations_with_versions({}, clock=clock)
    first.clear()

    assert "claude" in reused_installations_with_versions({}, clock=clock)
