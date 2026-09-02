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
