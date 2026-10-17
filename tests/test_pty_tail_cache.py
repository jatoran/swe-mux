"""The screen explanation is computed once per screen, not once per ask.

It is regex work over up to 32 KiB, it runs on the event loop, and several
readers ask for it per status pass per session. A quiet session's screen does not
change between those asks, so the answer must not be recomputed for them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from swe_mux import session as session_module
from swe_mux.scrollback import ScrollbackBuffer
from swe_mux.session import SessionManager


def _session(backend: str = "shell") -> SimpleNamespace:
    scrollback = ScrollbackBuffer(64 * 1024)
    scrollback.append(b"$ cargo test\nrunning 12 tests\n")
    return SimpleNamespace(
        record=SimpleNamespace(backend=backend, id="s1"),
        scrollback=scrollback,
        osc_signals=SimpleNamespace(title=None, progress=None),
        cli_state=None,
    )


def test_an_unchanged_screen_is_explained_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = session_module.pty_tail_explain

    def counting(tail: str, **kwargs: object) -> dict[str, object]:
        calls.append(tail)
        return real(tail, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session_module, "pty_tail_explain", counting)
    session = _session()
    first = SessionManager._pty_tail_explanation(session)
    second = SessionManager._pty_tail_explanation(session)
    assert first == second
    assert len(calls) == 1, "the second ask must be served from the cache"

    # A caller mutating its copy must not poison the next reader.
    first["outcome"] = "poisoned"
    assert SessionManager._pty_tail_explanation(session)["outcome"] != "poisoned"
    assert len(calls) == 1


def test_new_output_invalidates_the_cached_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = session_module.pty_tail_explain

    def counting(tail: str, **kwargs: object) -> dict[str, object]:
        calls.append(tail)
        return real(tail, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session_module, "pty_tail_explain", counting)
    session = _session()
    SessionManager._pty_tail_explanation(session)
    session.scrollback.append(b"test result: ok. 12 passed\n$ ")
    SessionManager._pty_tail_explanation(session)
    assert len(calls) == 2, "a byte of new output is a new screen"

    # So is a change in any other input the explanation is derived from.
    session.osc_signals.title = "claude - working"
    SessionManager._pty_tail_explanation(session)
    assert len(calls) == 3


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = session_module.pty_tail_explain

    def counting(tail: str, **kwargs: object) -> dict[str, object]:
        calls.append(tail)
        return real(tail, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session_module, "pty_tail_explain", counting)
    return calls


def _agent_session() -> SimpleNamespace:
    session = _session(backend="claude")
    session.record.agent_run_id = "run-1"
    return session


def test_every_caller_shares_one_classification_of_the_same_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The readiness watcher, the fleet rows, authorization and the observation
    helpers each classified the same tail on their own: 44% of the daemon's GIL at
    36 sessions (`DAEMON_LOAD_ISOLATION.md` item 4)."""
    from swe_mux.delivery_readiness import DeliveryReadinessTracker
    from swe_mux.observation import session_pty_state

    calls = _counting(monkeypatch)
    session = _agent_session()
    tracker = DeliveryReadinessTracker()
    verdict = tracker.screen_state(session)  # authorization: fresh, max age zero
    assert session_pty_state(session) == verdict
    assert SessionManager._pty_tail_explanation(session)["outcome"] == verdict
    assert len(calls) == 1, "one screen, one classification, whoever asks"


def test_authorization_never_takes_a_verdict_measured_on_other_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux.delivery_readiness import DISPLAY_SCREEN_MAX_AGE_SECONDS, DeliveryReadinessTracker

    calls = _counting(monkeypatch)
    session = _agent_session()
    now = [1000.0]
    tracker = DeliveryReadinessTracker(clock=lambda: now[0])
    tracker._pty_state(session, snapshot_max_age=DISPLAY_SCREEN_MAX_AGE_SECONDS)
    session.scrollback.append(b"\n\xe2\x9d\xaf ")  # the screen moves on
    now[0] += 0.5
    assert len(calls) == 1
    tracker._pty_state(session, snapshot_max_age=DISPLAY_SCREEN_MAX_AGE_SECONDS)
    assert len(calls) == 1, "a display may show a verdict under its bound"
    tracker.screen_state(session)
    assert len(calls) == 2, "authorization re-measures the bytes that are there now"
    assert calls[-1].endswith("❯ "), "and measures exactly those bytes"


def test_the_shared_cache_is_bounded_and_reports_its_rate() -> None:
    cache = session_module.ScreenVerdictCache(maxsize=2)
    for index in range(3):
        cache.explain(f"$ step {index}\n".encode(), backend="shell")
    cache.explain(b"$ step 2\n", backend="shell")
    snapshot = cache.snapshot()
    assert snapshot["entries"] == 2
    assert (snapshot["hits"], snapshot["classifications"]) == (1, 3)
    assert snapshot["hit_rate"] == 0.25
    cache.explain(b"$ step 0\n", backend="shell")
    assert cache.snapshot()["classifications"] == 4, "the oldest screen was evicted"
