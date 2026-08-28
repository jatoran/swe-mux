"""A clean machine must be *told* what it is missing, and must fetch nothing on its own.

Phase 11 W9. Two optional capabilities are absent on a fresh install and both used
to present as silence or as an odd failure: preview capture (an optional Python
extra *and* a separately downloaded browser binary) and the local speech models.

Everything here simulates absence rather than requiring it, because the machine
running the gate may well have any of these installed. That is the point: the
report has to be right on a machine that has nothing, and no test can prove that
by being run on a machine that has everything.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import app_keys as keys
from swe_mux import preview_capture
from swe_mux.doctor import build_doctor_report, optional_asset_rows
from swe_mux.preview_capture import (
    BROWSER_INSTALL_COMMAND,
    EXTRA_INSTALL_COMMAND,
    CaptureCapability,
    capture_capability,
)


def _fake_playwright(monkeypatch: pytest.MonkeyPatch, package_root: Path) -> None:
    """Make `import playwright.async_api` succeed without installing the extra.

    The extra is deliberately outside `.worktree-setup`, so the "installed but no
    browser" state is unreachable on this machine without a stand-in. Only the
    import and `__file__` matter to the capability probe.
    """
    package = types.ModuleType("playwright")
    package.__file__ = str(package_root / "playwright" / "__init__.py")
    package.__path__ = [str(package_root / "playwright")]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", types.ModuleType("async_api"))


def test_capture_reports_a_missing_extra_separately_from_a_missing_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole defect: both halves rendered as one "unavailable", so an operator
    # who had already run `uv sync` was told to run it again and got nowhere.
    browsers = tmp_path / "ms-playwright"
    browsers.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))

    # `None` in sys.modules is how CPython records a blocked import, so this is
    # the same ImportError a machine without the extra raises.
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    monkeypatch.setitem(sys.modules, "playwright", None)
    extra_missing = capture_capability()
    assert extra_missing.state == "extra_missing"
    assert not extra_missing.ready
    assert EXTRA_INSTALL_COMMAND in (extra_missing.remedy or "")

    # Extra present, browsers root empty: a different state and a different command.
    _fake_playwright(monkeypatch, tmp_path)
    browser_missing = capture_capability()
    assert browser_missing.state == "browser_missing"
    assert browser_missing.remedy == BROWSER_INSTALL_COMMAND
    # It names where it looked, so a wrong-cache diagnosis is checkable.
    assert str(browsers) in browser_missing.detail

    # A Chromium laid down by `playwright install` flips it, with the binary it
    # found as the evidence for the claim.
    executable = browsers / "chromium-1148" / "chrome-win" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    ready = capture_capability()
    assert ready.state == "ready" and ready.ready
    assert ready.remedy is None
    assert ready.browser_path == str(executable)


def test_capture_finds_the_headless_shell_build_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `playwright install chromium` lays down a separate headless-shell build from
    # 1.49 on, and newer Playwright launches headless through it. Missing it would
    # report "no browser" on a correctly installed machine.
    browsers = tmp_path / "ms-playwright"
    shell = browsers / "chromium_headless_shell-1148" / "chrome-win" / "headless_shell.exe"
    shell.parent.mkdir(parents=True)
    shell.write_bytes(b"")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    _fake_playwright(monkeypatch, tmp_path)

    assert capture_capability().state == "ready"


def test_a_frozen_build_says_no_command_helps_rather_than_a_useless_one(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    # `preview-capture` is outside DISTRIBUTED_EXTRAS, so the packaged app has no
    # Playwright and `uv sync` against the source tree cannot reach its
    # interpreter. Printing that command anyway sends the operator somewhere that
    # provably will not work.
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    capability = capture_capability()

    assert capability.state == "extra_missing"
    assert "packaged desktop app" in capability.detail
    assert "source checkout" in (capability.remedy or "")


def test_a_launch_that_finds_no_browser_is_reclassified_not_reported_as_a_failure() -> None:
    # The filesystem probe can be wrong in one direction - a browsers root this
    # host uses that the scan does not know about - and Playwright's own error is
    # the authority. Without this the operator gets a raw launch traceback with
    # no command in it.
    assert preview_capture._is_missing_browser_error(
        RuntimeError(
            "Executable doesn't exist at C:\\ms-playwright\\chromium-1148\\chrome.exe"
        )
    )
    assert preview_capture._is_missing_browser_error(
        RuntimeError("Please run the following command to download new browsers:\n"
                     "playwright install")
    )
    assert not preview_capture._is_missing_browser_error(
        RuntimeError("net::ERR_CONNECTION_REFUSED")
    )


@pytest.mark.asyncio
async def test_capture_route_hands_back_the_state_and_its_own_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swe_mux.config import Config
    from swe_mux.routes import processes as processes_routes
    from swe_mux.routes.processes import capture_preview

    item = SimpleNamespace(id="pv1", host="127.0.0.1", port=5173, session_id="", project_id="")
    app = {
        keys.PREVIEWS: SimpleNamespace(items={"pv1": item}),
        keys.CONFIG: Config(data_dir=tmp_path / "data"),
        keys.SESSIONS: SimpleNamespace(sessions={}),
        keys.PROJECTS: SimpleNamespace(projects={}),
    }

    async def body() -> dict[str, object]:
        return {}

    request = SimpleNamespace(
        match_info={"preview_id": "pv1"}, app=app, can_read_body=True, json=body
    )

    monkeypatch.setattr(
        processes_routes,
        "capture_capability",
        lambda: CaptureCapability(
            state="browser_missing", detail="no chromium under D:/cache", remedy="x install"
        ),
    )
    payload = json.loads((await capture_preview(cast(Any, request))).body)  # type: ignore[arg-type]

    # A state, not prose to parse, and a 200 because an uninstalled optional
    # integration is a state rather than a fault.
    assert payload == {
        "available": False,
        "state": "browser_missing",
        "reason": "no chromium under D:/cache",
        "remedy": "x install",
    }


@pytest.mark.asyncio
async def test_a_launch_time_discovery_reports_the_same_shape_as_the_pre_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swe_mux.config import Config
    from swe_mux.preview_capture import PreviewCaptureUnavailable
    from swe_mux.routes import processes as processes_routes
    from swe_mux.routes.processes import capture_preview

    item = SimpleNamespace(id="pv1", host="127.0.0.1", port=5173, session_id="", project_id="")
    app = {
        keys.PREVIEWS: SimpleNamespace(items={"pv1": item}),
        keys.CONFIG: Config(data_dir=tmp_path / "data"),
        keys.SESSIONS: SimpleNamespace(sessions={}),
        keys.PROJECTS: SimpleNamespace(projects={}),
    }

    async def body() -> dict[str, object]:
        return {}

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise PreviewCaptureUnavailable(
            CaptureCapability(state="browser_missing", detail="none found", remedy="install it")
        )

    request = SimpleNamespace(
        match_info={"preview_id": "pv1"}, app=app, can_read_body=True, json=body
    )
    monkeypatch.setattr(
        processes_routes,
        "capture_capability",
        lambda: CaptureCapability(state="ready", detail="ready"),
    )
    monkeypatch.setattr(processes_routes, "capture_loopback", refuse)

    payload = json.loads((await capture_preview(cast(Any, request))).body)  # type: ignore[arg-type]

    assert payload["available"] is False and payload["state"] == "browser_missing"
    assert payload["remedy"] == "install it"


def test_nothing_first_use_downloads_is_switched_on_by_default() -> None:
    """The cheapest guarantee that a fresh install fetches nothing: it is all off.

    A default flipped to true would make the first launch download a speech model
    with no one asking, which is the failure the explicit download states exist to
    replace rather than to paper over. Asserted here so the flip cannot happen
    quietly.
    """
    from swe_mux.config import Config

    defaults = Config()
    assert defaults.tts_enabled is False
    assert defaults.stt_enabled is False
    # Locale neutrality of the shipped voice choices: the read-aloud default is a
    # generic en-US voice rather than one operator's regional pick.
    assert defaults.tts_edge_voice.startswith("en-US-")
    assert defaults.stt_language == "en-US"


def test_optional_asset_rows_keep_each_kind_of_absence_distinct() -> None:
    rows = optional_asset_rows(
        capture={
            "state": "browser_missing",
            "detail": "Playwright is installed but no Chromium",
            "remedy": BROWSER_INSTALL_COMMAND,
        },
        voice={
            "tts_enabled": False,
            "tts_engine": "kokoro",
            "stt_enabled": False,
            "stt_engine": "whisper",
            "kokoro": {"status": "not_downloaded", "error": None},
            "whisper": [
                {
                    "model": "turbo",
                    "status": "not_downloaded",
                    "backend_installed": True,
                    "size_hint": "about 1.6 GB",
                },
                {"model": "small.en", "status": "not_downloaded", "backend_installed": False},
            ],
        },
    )
    by_id = {row["id"]: row for row in rows}

    assert by_id["preview_capture"]["state"] == "browser_missing"
    assert by_id["preview_capture"]["remedy"] == BROWSER_INSTALL_COMMAND
    # Off by default means "nothing fetched it", which is a different fact from a
    # capability that is wanted and missing. Reporting the first as the second
    # would invent a problem on every clean install.
    assert "nothing has fetched it" in by_id["voice_kokoro"]["detail"]
    assert "about 1.6 GB" in by_id["voice_whisper:turbo"]["detail"]
    # A missing extra is not a missing download, and no Download button fixes it.
    absent_backend = by_id["voice_whisper:small.en"]
    assert absent_backend["state"] == "extra_missing"
    assert absent_backend["remedy"] == "uv sync --extra voice-local"


def test_doctor_reports_optional_assets_without_calling_any_of_them_broken() -> None:
    report = build_doctor_report(
        health={},
        remote={},
        firewall={"supported": False},
        prerequisites=[],
        status_health={},
        background={},
        harnesses={"harnesses": []},
        freshness=[],
        platform={},
        daemon={},
        now=0.0,
        optional_assets=[
            {
                "id": "preview_capture",
                "label": "Preview capture",
                "state": "extra_missing",
                "detail": "not installed",
                "remedy": EXTRA_INSTALL_COMMAND,
            },
            {
                "id": "voice_whisper:turbo",
                "label": "Whisper 'turbo'",
                "state": "ready",
                "detail": "downloaded",
                "remedy": None,
            },
        ],
    )
    checks = {check["id"]: check for check in report["checks"]}

    absent = checks["optional_asset:preview_capture"]
    assert absent["status"] == "unavailable" and absent["severity"] == "optional"
    assert absent["remedy"] == EXTRA_INSTALL_COMMAND
    assert checks["optional_asset:voice_whisper:turbo"]["status"] == "ok"
    # An optional asset that is simply absent must never fail the report: `mux
    # doctor` exit-codes on failures, and a clean install has all of these.
    assert report["ok"] is True
    assert [entry["state"] for entry in report["capabilities"]["optional_assets"]] == [
        "extra_missing",
        "ready",
    ]
