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
            "kokoro": {
                "status": "not_downloaded",
                "error": None,
                "g2p": {"status": "not_downloaded", "source": None, "error": None},
            },
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
        voice_local_install='pipx install --force "swe-mux[voice-local]"',
    )
    by_id = {row["id"]: row for row in rows}

    assert by_id["preview_capture"]["state"] == "browser_missing"
    assert by_id["preview_capture"]["remedy"] == BROWSER_INSTALL_COMMAND
    # Off by default means "nothing fetched it", which is a different fact from a
    # capability that is wanted and missing. Reporting the first as the second
    # would invent a problem on every clean install.
    assert "nothing has fetched it" in by_id["voice_kokoro"]["detail"]
    assert "about 1.6 GB" in by_id["voice_whisper:turbo"]["detail"]
    # The G2P model is its own row: it is a separate absence with a separate
    # cause, and a Kokoro row that was ready while pronunciation was missing
    # would report a working engine that cannot say a word.
    assert by_id["voice_g2p"]["state"] == "not_downloaded"
    assert "Download Kokoro voices" in by_id["voice_g2p"]["remedy"]
    # A missing extra is not a missing download, and no Download button fixes it.
    # The command is the caller's, because `uv sync` is a source-checkout command
    # that the reader of an installed copy cannot run.
    absent_backend = by_id["voice_whisper:small.en"]
    assert absent_backend["state"] == "extra_missing"
    assert absent_backend["remedy"] == 'pipx install --force "swe-mux[voice-local]"'


def test_an_installed_g2p_model_reports_which_kind_of_present_it_is() -> None:
    """`installed` and `downloaded` are one working state reached two ways.

    A source checkout and the desktop bundle resolve the distribution outright;
    a wheel install fetches it into the data directory. Only the second is
    anything an operator can undo or re-do, so the row says which.
    """
    rows = optional_asset_rows(
        capture={"state": "ready", "detail": "", "remedy": None},
        voice={
            "tts_enabled": True,
            "tts_engine": "kokoro",
            "stt_enabled": False,
            "stt_engine": "sapi",
            "kokoro": {
                "status": "ready",
                "error": None,
                "g2p": {"status": "ready", "source": "installed", "error": None},
            },
            "whisper": [],
        },
        voice_local_install="uv sync --extra voice-local",
    )
    row = {item["id"]: item for item in rows}["voice_g2p"]
    assert row["state"] == "ready"
    assert row["remedy"] is None
    assert "installed" in row["detail"]


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


# --------------------------------------------------------------- the spaCy G2P model


def _wheel(
    path: Path, *, distribution: str, version: str, extra: dict[str, str] | None = None
) -> bytes:
    """A minimal wheel-shaped zip: the package plus its `.dist-info`.

    Both halves matter and only one is obvious. spaCy resolves a bare model name
    through `spacy.util.is_package`, which is `importlib.metadata.distribution` -
    so the `.dist-info` is what makes the unpacked copy *findable*, and a zip
    carrying only the package directory would import and still not load.
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{distribution}/__init__.py", "VALUE = 42\n")
        archive.writestr(
            f"{distribution}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n",
        )
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    payload = buffer.getvalue()
    path.write_bytes(payload)
    return payload


def test_an_unpacked_distribution_on_sys_path_is_what_spacy_would_find(
    tmp_path: Path,
) -> None:
    """The mechanism `SpacyModelStore.activate` rests on, proven directly.

    misaki calls `spacy.load("en_core_web_sm")` by bare name, and spaCy resolves
    a bare name through `importlib.metadata.distribution` rather than through
    importability. The store therefore unpacks the whole wheel - `.dist-info`
    included - into one directory and puts that directory on `sys.path`, so the
    model looks installed to this process without the daemon writing anything
    into the environment it was installed into.

    Asserted against a synthetic distribution under a name nothing else uses,
    because the real `en_core_web_sm` is installed in the gate's environment and
    a test that could not tell the two apart would prove nothing.
    """
    import importlib
    import importlib.metadata
    import zipfile

    site = tmp_path / "site"
    archive = tmp_path / "probe.whl"
    _wheel(archive, distribution="swemux_g2p_probe", version="1.0")
    with zipfile.ZipFile(archive) as opened:
        opened.extractall(site)

    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.distribution("swemux_g2p_probe")
    sys.path.insert(0, str(site))
    try:
        importlib.invalidate_caches()
        assert importlib.metadata.distribution("swemux_g2p_probe").version == "1.0"
        assert importlib.import_module("swemux_g2p_probe").VALUE == 42
    finally:
        sys.path.remove(str(site))
        sys.modules.pop("swemux_g2p_probe", None)
        importlib.invalidate_caches()


def test_g2p_model_installed_asks_the_question_spacy_asks() -> None:
    """Not `find_spec`: spaCy tries `is_package` first, which is distribution metadata.

    A `find_spec` probe would answer a different question and would disagree with
    spaCy exactly where it matters - a package importable without metadata, which
    is what a naive unpack-and-append would produce.
    """
    spacy = pytest.importorskip("spacy", reason="the voice-local extra is not installed")
    from swe_mux.voice_models import G2P_DISTRIBUTION, g2p_model_installed

    assert g2p_model_installed() == spacy.util.is_package(G2P_DISTRIBUTION)


def test_an_absent_g2p_model_is_a_reported_state_and_not_a_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing fetches it on its own; the state says so and the remedy is a press."""
    from swe_mux import voice_models
    from swe_mux.voice_models import SpacyModelStore

    monkeypatch.setattr(voice_models, "g2p_model_installed", lambda: False)
    store = SpacyModelStore(tmp_path)
    assert store.ready() is False
    status = store.status()
    assert status["status"] == "not_downloaded"
    assert status["source"] is None
    assert status["distribution"] == voice_models.G2P_DISTRIBUTION
    assert not (tmp_path / "voice-models" / "spacy").exists()


def test_a_resolvable_g2p_model_says_which_kind_of_present_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`installed` is a fact about the environment, `downloaded` about the data dir."""
    from swe_mux import voice_models
    from swe_mux.voice_models import SpacyModelStore

    monkeypatch.setattr(voice_models, "g2p_model_installed", lambda: True)
    status = SpacyModelStore(tmp_path).status()
    assert status["status"] == "ready"
    assert status["source"] == "installed"
    assert status["downloaded_bytes"] == status["total_bytes"]


def test_a_state_file_that_says_ready_over_a_missing_tree_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted or half-unpacked directory must never read as a working model.

    The same rule `KokoroModelStore` keeps: what is on disk is authoritative for
    "loadable", and a state file that outlived it is a claim, not a reading.
    """
    from swe_mux import voice_models
    from swe_mux.voice_models import G2P_VERSION, SpacyModelStore

    monkeypatch.setattr(voice_models, "g2p_model_installed", lambda: False)
    store = SpacyModelStore(tmp_path)
    store._write_state({"status": "ready", "version": G2P_VERSION})
    assert store.unpacked() is False
    status = store.status()
    assert status["status"] == "error"
    assert status["error"]


def test_unpacking_verifies_the_archive_before_it_replaces_anything(
    tmp_path: Path,
) -> None:
    """A wheel that is not the model must not become the model.

    The payload is hash-pinned, so neither of these can currently happen - which
    is exactly why they are asserted rather than assumed: the guard has to still
    be there the day the pin is bumped or the release asset is republished.
    """
    from swe_mux.voice_models import G2P_DISTRIBUTION, SpacyModelStore, VoiceModelError

    store = SpacyModelStore(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)

    wrong = tmp_path / "wrong.whl"
    payload = _wheel(wrong, distribution="something_else", version="1.0")
    with pytest.raises(VoiceModelError, match="did not contain the package it names"):
        store._unpack(payload)
    assert not store.site.exists()

    escaping = tmp_path / "escaping.whl"
    payload = _wheel(
        escaping,
        distribution=G2P_DISTRIBUTION,
        version="3.8.0",
        extra={"../escaped.py": "raise SystemExit\n"},
    )
    with pytest.raises(VoiceModelError, match="out-of-tree path"):
        store._unpack(payload)
    assert not (tmp_path / "escaped.py").exists()


def test_a_verified_unpack_activates_and_is_reported_as_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state a wheel install ends in: fetched here, resolvable from here."""
    from swe_mux import voice_models
    from swe_mux.voice_models import G2P_DISTRIBUTION, G2P_VERSION, SpacyModelStore

    store = SpacyModelStore(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    payload = _wheel(
        tmp_path / "model.whl", distribution=G2P_DISTRIBUTION, version=G2P_VERSION
    )
    store._unpack(payload)
    store._write_state({"status": "ready", "version": G2P_VERSION})
    assert store.unpacked() is True

    # `activate` short-circuits when the environment already resolves the model,
    # which it does in this gate's own venv. Forcing the other branch is what
    # exercises the `sys.path` entry a wheel install actually depends on.
    resolvable = {"value": False}
    monkeypatch.setattr(voice_models, "g2p_model_installed", lambda: resolvable["value"])
    entry = str(store.site)
    try:
        # The insert happens, and then the post-insert probe decides the answer.
        assert store.activate() is False
        assert entry in sys.path
        resolvable["value"] = True
        assert store.activate() is True
        assert store.status()["source"] == "downloaded"
    finally:
        while entry in sys.path:
            sys.path.remove(entry)


async def test_a_tampered_model_download_is_refused_rather_than_unpacked(
    tmp_path: Path,
) -> None:
    """The pin is only real if the hash is checked; nothing partial reaches disk."""
    from swe_mux.voice_models import SpacyModelStore, VoiceModelError

    class Response:
        status = 200

        async def read(self) -> bytes:
            return b"not the pinned wheel"

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    class Session:
        def get(self, _url: str, **_kwargs: Any) -> Response:
            return Response()

    store = SpacyModelStore(tmp_path)
    with pytest.raises(VoiceModelError, match="failed verification"):
        await store._fetch_wheel(cast(Any, Session()))
    assert not store.site.exists()


async def test_a_model_host_error_is_a_typed_failure(tmp_path: Path) -> None:
    from swe_mux.voice_models import SpacyModelStore, VoiceModelError

    class Response:
        status = 404

        async def read(self) -> bytes:  # pragma: no cover - never reached
            return b""

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    class Session:
        def get(self, _url: str, **_kwargs: Any) -> Response:
            return Response()

    store = SpacyModelStore(tmp_path)
    with pytest.raises(VoiceModelError, match="HTTP 404"):
        await store._fetch_wheel(cast(Any, Session()))
