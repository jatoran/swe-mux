"""Where `swe_mux.hook_client` gets the session it speaks for.

The environment is not a trustworthy channel for that. Claude Code 2.1.227 runs a
parked conversation inside a shared `claude daemon run` process, and every
background agent that daemon starts inherits *its* environment — measured
2026-08-10, one such daemon held `MUX_HOOK_URL` for a pane that had exited an
hour earlier and posted 744 hook events to it, all HTTP 404, while the pane the
work belonged to received one hook in its whole lifetime. `--settings` is passed
per request and always names the requesting pane, so the identity file it points
at is the channel that survives the hand-off.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux import hook_client


@pytest.fixture(autouse=True)
def _no_ambient_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MUX_HOOK_URL", "MUX_HOOK_SECRET", "MUX_HOOK_SPOOL"):
        monkeypatch.delenv(name, raising=False)


def write_identity(tmp_path: Path, **payload: str) -> Path:
    path = tmp_path / "hook-identity.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def capture_post(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict]]:
    posted: list[tuple[str, str, dict]] = []

    def _post(url: str, secret: str, body: bytes) -> bool:
        posted.append((url, secret, json.loads(body)))
        return True

    monkeypatch.setattr(hook_client, "_post", _post)
    monkeypatch.setattr(hook_client, "_read_payload", lambda: '{"session_id": "conv"}')
    return posted


def test_the_identity_file_outranks_a_stale_inherited_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUX_HOOK_URL", "http://127.0.0.1:8765/api/hooks/retired-pane")
    monkeypatch.setenv("MUX_HOOK_SECRET", "retired-secret")
    identity = write_identity(
        tmp_path, url="http://127.0.0.1:8765/api/hooks/live-pane", secret="live-secret"
    )
    posted = capture_post(monkeypatch)
    monkeypatch.setattr(
        hook_client.sys, "argv", ["hook_client", "Stop", "--identity", str(identity)]
    )

    hook_client.main()

    assert posted == [
        (
            "http://127.0.0.1:8765/api/hooks/live-pane",
            "live-secret",
            {"event": "Stop", "payload": {"session_id": "conv"}},
        )
    ]


def test_the_environment_remains_the_fallback_without_an_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Harnesses with no per-session settings file of their own still work."""
    del tmp_path
    monkeypatch.setenv("MUX_HOOK_URL", "http://127.0.0.1:8765/api/hooks/pane")
    monkeypatch.setenv("MUX_HOOK_SECRET", "pane-secret")
    posted = capture_post(monkeypatch)
    monkeypatch.setattr(hook_client.sys, "argv", ["hook_client", "Stop"])

    hook_client.main()

    assert posted[0][:2] == ("http://127.0.0.1:8765/api/hooks/pane", "pane-secret")


def test_an_unreadable_identity_falls_back_rather_than_going_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "hook-identity.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("MUX_HOOK_URL", "http://127.0.0.1:8765/api/hooks/pane")
    monkeypatch.setenv("MUX_HOOK_SECRET", "pane-secret")
    posted = capture_post(monkeypatch)
    monkeypatch.setattr(
        hook_client.sys, "argv", ["hook_client", "Stop", "--identity", str(broken)]
    )

    hook_client.main()

    assert posted[0][:2] == ("http://127.0.0.1:8765/api/hooks/pane", "pane-secret")


def test_no_identity_and_no_environment_posts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = capture_post(monkeypatch)
    monkeypatch.setattr(hook_client.sys, "argv", ["hook_client", "Stop"])

    hook_client.main()

    assert posted == []


def test_the_identity_flag_is_consumed_before_the_inline_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A harness may pass the hook body as an argument instead of on stdin."""
    identity = write_identity(tmp_path, url="http://127.0.0.1:8765/api/hooks/p", secret="s")
    posted = capture_post(monkeypatch)
    monkeypatch.setattr(hook_client, "_read_payload", lambda: "")
    monkeypatch.setattr(
        hook_client.sys,
        "argv",
        ["hook_client", "codex_notify", "--identity", str(identity), '{"type": "turn_ended"}'],
    )

    hook_client.main()

    (_, _, body) = posted[0]
    assert body == {"event": "turn_ended", "payload": {"type": "turn_ended"}}


def test_a_failed_post_spools_against_the_identitys_own_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spool must follow the identity: a retired pane's spool is never drained."""
    spool = tmp_path / "live-pane.jsonl"
    identity = write_identity(
        tmp_path,
        url="http://127.0.0.1:8765/api/hooks/live-pane",
        secret="live-secret",
        spool=str(spool),
    )
    monkeypatch.setenv("MUX_HOOK_SPOOL", str(tmp_path / "retired-pane.jsonl"))
    monkeypatch.setattr(hook_client, "_post", lambda *_: False)
    monkeypatch.setattr(hook_client, "_read_payload", lambda: "{}")
    monkeypatch.setattr(
        hook_client.sys, "argv", ["hook_client", "Stop", "--identity", str(identity)]
    )

    hook_client.main()

    assert json.loads(spool.read_text(encoding="utf-8"))["event"] == "Stop"
    assert not (tmp_path / "retired-pane.jsonl").exists()
