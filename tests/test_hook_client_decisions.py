"""The hook shim's decision path, which runs inside the agent's blocked turn.

Every assertion here is about failing open. The CLI reads an empty stdout as
"no opinion" and falls through to its normal permission prompt, so the shim must
print nothing whenever it is not certain of a decision — a daemon that is down,
slow, or answering something it does not understand must all look identical to
having no hook installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from swe_mux import hook_client


@pytest.fixture(autouse=True)
def _no_ambient_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MUX_HOOK_URL", "MUX_HOOK_SECRET", "MUX_HOOK_SPOOL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MUX_HOOK_URL", "http://127.0.0.1:8765/api/hooks/s1")
    monkeypatch.setenv("MUX_HOOK_SECRET", "secret")


def _run(
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    *,
    decision: tuple[bool, Any],
    payload: str = '{"tool_name": "Read"}',
) -> None:
    monkeypatch.setattr(hook_client, "_read_payload", lambda: payload)
    monkeypatch.setattr(hook_client, "sys", hook_client.sys)
    monkeypatch.setattr(
        hook_client, "_post_for_decision", lambda url, secret, body: decision
    )
    monkeypatch.setattr(hook_client.sys, "argv", ["hook_client", event])
    hook_client.main()


def test_a_decision_is_relayed_verbatim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The daemon composes the harness-specific shape; the shim only prints it.

    Keeping the schema on the daemon side is what lets a second decision-capable
    harness be added in the registry rather than in the command every session
    already runs.
    """
    body = {"hookEventName": "PermissionRequest", "decision": "allow", "reason": "matched Read"}
    _run(monkeypatch, "PermissionRequest", decision=(True, body))
    assert json.loads(capsys.readouterr().out) == {"hookSpecificOutput": body}


def test_no_decision_prints_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(monkeypatch, "PermissionRequest", decision=(True, None))
    assert capsys.readouterr().out == ""


def test_a_delivered_event_with_no_decision_is_not_retried_or_spooled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The report landed; only the answer was "ask". Re-posting would duplicate it."""
    spool = tmp_path / "s1.jsonl"
    monkeypatch.setenv("MUX_HOOK_SPOOL", str(spool))
    posted: list[bytes] = []
    monkeypatch.setattr(hook_client, "_post", lambda url, secret, body: posted.append(body) or True)
    _run(monkeypatch, "PermissionRequest", decision=(True, None))
    assert posted == []
    assert not spool.exists()


def test_an_undelivered_decision_falls_back_to_the_durable_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A permission raised during a daemon restart has no second source.

    The fast attempt is deliberately single-shot, so when it misses the ordinary
    retry-and-spool path still has to record that the approval happened —
    otherwise the transcript reads "open" and the PTY reads "approval", neither
    watchdog can fire, and the session sits displayed as working.
    """
    spool = tmp_path / "s1.jsonl"
    monkeypatch.setenv("MUX_HOOK_SPOOL", str(spool))
    monkeypatch.setattr(hook_client, "_post", lambda url, secret, body: False)
    _run(monkeypatch, "PermissionRequest", decision=(False, None))
    entries = [json.loads(line) for line in spool.read_text(encoding="utf-8").splitlines()]
    assert [entry["event"] for entry in entries] == ["PermissionRequest"]


def test_a_non_decision_event_never_reaches_the_fast_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    posted: list[bytes] = []
    monkeypatch.setattr(hook_client, "_post", lambda url, secret, body: posted.append(body) or True)

    def _explode(*args: object) -> tuple[bool, Any]:
        raise AssertionError("Stop must not use the decision path")

    monkeypatch.setattr(hook_client, "_post_for_decision", _explode)
    monkeypatch.setattr(hook_client, "_read_payload", lambda: "{}")
    monkeypatch.setattr(hook_client.sys, "argv", ["hook_client", "Stop"])
    hook_client.main()
    assert len(posted) == 1
    assert capsys.readouterr().out == ""


def test_a_malformed_daemon_response_is_read_as_no_opinion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Parsing happens in the user's turn, so it must never raise."""

    class _Response:
        def read(self, _size: int) -> bytes:
            return b"<html>not json</html>"

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(hook_client.urllib.request, "urlopen", lambda *a, **k: _Response())
    delivered, decision = hook_client._post_for_decision("http://x", "s", b"{}")
    assert delivered is True
    assert decision is None


def test_a_daemon_that_is_not_running_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*args: object, **kwargs: object) -> object:
        raise OSError("connection refused")

    monkeypatch.setattr(hook_client.urllib.request, "urlopen", _refuse)
    assert hook_client._post_for_decision("http://x", "s", b"{}") == (False, None)


def test_the_decision_attempt_is_bounded_and_single_shot() -> None:
    """A retry loop here is time the agent spends parked on a prompt."""
    assert hook_client._DECISION_TIMEOUT <= 5.0
    assert hook_client._DECISION_TIMEOUT < sum(hook_client._TIMEOUTS)
