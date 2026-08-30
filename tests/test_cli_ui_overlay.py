"""`swemux ui-overlay`, which is the overlay's only control that does not need the UI.

That is the whole reason it exists as a command rather than only an endpoint: an
overlay's own failure mode is a frontend that will not load, so a revert reachable
only through that frontend would be no revert at all. These tests hold the parts a
person in that situation depends on - that the right endpoint is called, that the
gesture header is sent, and that the source they typed is classified without a
daemon having to guess at a path it cannot see the way their shell can.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swe_mux import cli


def _capture(monkeypatch: pytest.MonkeyPatch, answer: Any = None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _request(
        method: str,
        path: str,
        body: Any = None,
        *,
        base: str,
        headers: dict[str, str] | None = None,
        timeout: float = 10,
    ) -> Any:
        calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        return answer if answer is not None else {"supported": True, "installed": False}

    monkeypatch.setattr(cli, "request", _request)
    return calls


def test_status_is_the_default_action(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch)
    assert cli.main(["ui-overlay"]) == cli.EXIT_OK
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "/api/frontend/overlay"
    # A read must not carry a gesture header: nothing acquires one by accident.
    assert calls[0]["headers"] == {}


def test_revert_sends_its_own_gesture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch, {"reverted": True, "changed": True})
    assert cli.main(["ui-overlay", "revert"]) == cli.EXIT_OK
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/frontend/overlay/revert"
    assert calls[0]["headers"]["X-Mux-User-Gesture"] == "frontend-overlay-revert"


def test_restore_sends_a_different_gesture_from_revert(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch, {"restored": True, "changed": True})
    assert cli.main(["ui-overlay", "restore"]) == cli.EXIT_OK
    assert calls[0]["headers"]["X-Mux-User-Gesture"] == "frontend-overlay-restore"


def test_installing_an_archive_sends_an_absolute_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The daemon resolves the path in its own process; a relative one would mean
    # something different there, and silently so.
    archive = tmp_path / "ui.zip"
    archive.write_bytes(b"PK\x03\x04")
    calls = _capture(monkeypatch, {"installed": True, "digest": "a" * 64})
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ui-overlay", "install", "ui.zip"]) == cli.EXIT_OK
    assert calls[0]["path"] == "/api/frontend/overlay/install"
    assert Path(calls[0]["body"]["archive"]).is_absolute()
    assert Path(calls[0]["body"]["archive"]) == archive.resolve()


def test_installing_a_directory_is_classified_as_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    built = tmp_path / "static"
    built.mkdir()
    calls = _capture(monkeypatch, {"installed": True, "digest": "a" * 64})
    assert cli.main(["ui-overlay", "install", str(built)]) == cli.EXIT_OK
    assert "directory" in calls[0]["body"]
    assert "archive" not in calls[0]["body"]


def test_installing_a_url_requires_a_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch)
    assert cli.main(["ui-overlay", "install", "https://example.invalid/ui.zip"]) != cli.EXIT_OK
    assert calls == []


def test_installing_a_url_with_a_digest_sends_both(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch, {"installed": True, "digest": "a" * 64})
    assert (
        cli.main(
            [
                "ui-overlay",
                "install",
                "https://example.invalid/ui.zip",
                "--sha256",
                "b" * 64,
            ]
        )
        == cli.EXIT_OK
    )
    assert calls[0]["body"] == {"url": "https://example.invalid/ui.zip", "sha256": "b" * 64}


def test_installing_something_that_is_not_a_path_or_a_url_fails_locally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _capture(monkeypatch)
    assert cli.main(["ui-overlay", "install", str(tmp_path / "nope.zip")]) != cli.EXIT_OK
    assert calls == []


def test_installing_with_no_source_fails_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture(monkeypatch)
    assert cli.main(["ui-overlay", "install"]) != cli.EXIT_OK
    assert calls == []


def test_installing_raises_the_timeout_above_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The handler hashes and extracts the whole tree; a client that gave up at the
    # ten seconds every other command uses would report a failure that did not
    # happen while the daemon went on to succeed.
    built = tmp_path / "static"
    built.mkdir()
    calls = _capture(monkeypatch, {"installed": True, "digest": "a" * 64})
    cli.main(["ui-overlay", "install", str(built)])
    assert calls[0]["timeout"] > 10


def test_the_status_renderer_names_the_refusal_rather_than_hiding_it() -> None:
    # An overlay that is installed and refused looks, from a browser, exactly like
    # one that was never installed. Printing the reason is the difference between
    # this feature and the trap it replaces.
    rendered = cli._render_ui_overlay(
        {
            "supported": True,
            "installed": True,
            "active": True,
            "tree_exists": True,
            "backend_version": "0.1.3",
            "state": {"digest": "d" * 64, "requires_backend": "0.1.2", "active": True},
            "serving": {
                "serving": "bundled",
                "directory": "/app/static",
                "reason": "version_mismatch",
                "message": "built for swe-mux 0.1.2",
                "faulted": True,
            },
        }
    )
    assert "version_mismatch" in rendered
    assert "0.1.2" in rendered
    assert "bundled" in rendered


def test_the_status_renderer_says_when_an_overlay_is_reverted() -> None:
    rendered = cli._render_ui_overlay(
        {
            "supported": True,
            "installed": True,
            "active": False,
            "tree_exists": True,
            "backend_version": "0.1.3",
            "state": {"digest": "d" * 64, "requires_backend": "0.1.3", "active": False},
            "serving": {"serving": "bundled", "directory": "/app/static", "reason": "reverted"},
        }
    )
    assert "reverted" in rendered


def test_the_status_renderer_survives_a_daemon_with_no_overlay_support() -> None:
    assert "no frontend overlay support" in cli._render_ui_overlay({"supported": False})
