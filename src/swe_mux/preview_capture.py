from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .host_platform import IS_MACOS, IS_WINDOWS

# Server-side headless screenshot of a session-owned loopback preview. Optional:
# requires the `preview-capture` extra (Playwright + a Chromium download). When
# absent the caller reports a typed "unavailable" state, like every other
# optional integration — it never raises into a terminal path.
#
# The two halves fail *separately* and a fresh install can be in either state, so
# they are never collapsed into one "unavailable": the Python package can be
# absent, or the package can be present with no browser binary under it. Those
# need different commands, and a report that does not say which one you are in
# sends the operator to run the wrong one. This is the same discipline the Agent
# Environment drawer applies to an empty MCP catalog (`design/features/
# agent-environment.md`): an absent capability must say which kind of absent.
#
# Nothing here downloads a browser. `playwright install chromium` is ~150 MB over
# the network, and a daemon that fetches it because someone pressed Capture is
# exactly the silent first-use cost this reporting exists to remove.

log = logging.getLogger(__name__)

# The three states a capture backend can be in. There is no "unknown": every
# input is a local import or a local filesystem read, both of which answer.
CAPTURE_STATES = ("ready", "extra_missing", "browser_missing")

EXTRA_INSTALL_COMMAND = "uv sync --extra preview-capture"
BROWSER_INSTALL_COMMAND = "uv run playwright install chromium"
INSTALL_HINT = f"{EXTRA_INSTALL_COMMAND} && {BROWSER_INSTALL_COMMAND}"

# The packaged desktop app does not carry Playwright: `preview-capture` is
# deliberately outside `DISTRIBUTED_EXTRAS` (`packaging/license_audit.py`), so a
# `uv sync` against the source tree cannot reach the frozen bundle's own
# interpreter. Saying so is the actionable answer there; printing a command that
# provably will not help is worse than printing none.
FROZEN_EXTRA_DETAIL = (
    "the packaged desktop app does not bundle Playwright, so preview capture is "
    "unavailable in this build"
)
FROZEN_EXTRA_REMEDY = (
    f"Run swe-mux from a source checkout and install the extra there: {INSTALL_HINT}"
)

# Playwright's own on-disk layout under a browsers root: `<root>/chromium-1148/
# chrome-win/chrome.exe` and its per-host siblings, plus the separate headless
# shell build `playwright install chromium` also lays down from 1.49 onward.
# Probing the file rather than launching the browser is what keeps this report
# free: a launch costs a process and several hundred milliseconds, and would run
# on a machine we already know has nothing to launch.
_CHROMIUM_EXECUTABLES = (
    Path("chrome-win") / "chrome.exe",
    Path("chrome-win") / "headless_shell.exe",
    Path("chrome-linux") / "chrome",
    Path("chrome-linux") / "headless_shell",
    Path("chrome-mac") / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
    Path("chrome-mac") / "headless_shell",
)

# Substrings Playwright puts in the launch error when the browser binary is
# missing. The filesystem probe below can be wrong in one direction (a browsers
# root we did not think to look in), and this is the correction: a launch that
# fails for this reason is reclassified as `browser_missing` rather than reported
# as a generic capture failure the operator cannot act on.
_MISSING_BROWSER_MARKERS = (
    "executable doesn't exist",
    "playwright install",
    "browser has not been installed",
)


def _playwright_cache_candidates() -> list[Path]:
    """Where `playwright install` puts browsers, per host.

    These are Playwright's own documented locations, not guesses: Windows uses
    `%LOCALAPPDATA%\\ms-playwright`, macOS `~/Library/Caches/ms-playwright`, and
    Linux `$XDG_CACHE_HOME/ms-playwright` falling back to `~/.cache/ms-playwright`.
    Getting this wrong is silent - capture simply reports unavailable on a machine
    that has Chromium installed.
    """
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        return [Path(local) / "ms-playwright"] if local else []
    if IS_MACOS:
        return [Path.home() / "Library" / "Caches" / "ms-playwright"]
    cache_home = os.environ.get("XDG_CACHE_HOME")
    roots = [Path(cache_home)] if cache_home else []
    roots.append(Path.home() / ".cache")
    return [root / "ms-playwright" for root in roots]


def _configure_browsers_path() -> None:
    """Point Playwright at the standard per-user browser cache.

    A PyInstaller-frozen desktop build otherwise resolves browsers to a
    `.local-browsers` directory inside the bundle, which is empty. `playwright
    install` puts Chromium in the OS cache, so point there unless the user set
    the path explicitly.
    """
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    for cache in _playwright_cache_candidates():
        if cache.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cache)
            return


def _package_local_browsers() -> Path | None:
    """Playwright's in-package browser directory, used when the path is set to `0`.

    `PLAYWRIGHT_BROWSERS_PATH=0` is Playwright's documented way of saying "keep
    the browsers inside the installed package", so a scan of the OS cache would
    find nothing on a machine that is correctly installed. Resolving it from the
    imported module is a read of where the package actually is, not a guess.
    """
    try:
        import playwright
    except ImportError:  # pragma: no cover - callers import first
        return None
    location = getattr(playwright, "__file__", None)
    if not location:
        return None
    return Path(location).parent / "driver" / "package" / ".local-browsers"


def _browser_roots() -> list[Path]:
    explicit = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots: list[Path] = []
    if explicit and explicit != "0":
        roots.append(Path(explicit))
    else:
        roots.extend(_playwright_cache_candidates())
    package_local = _package_local_browsers()
    if package_local is not None:
        roots.append(package_local)
    return roots


def installed_chromium() -> Path | None:
    """The Chromium binary `playwright install chromium` laid down, or None."""
    for root in _browser_roots():
        try:
            builds = sorted(root.glob("chromium*-*"), reverse=True)
        except OSError:
            continue
        for build in builds:
            for relative in _CHROMIUM_EXECUTABLES:
                candidate = build / relative
                try:
                    if candidate.is_file():
                        return candidate
                except OSError:
                    continue
    return None


@dataclass(frozen=True, slots=True)
class CaptureCapability:
    """Which of the three capture states this install is in, and how to leave it.

    `detail` names the kind of absence in a sentence; `remedy` is the exact
    command to run, or None where no command on this machine would help (the
    frozen desktop build). `browser_path` is evidence for `ready`: the binary
    that was actually found, so a "ready" claim is checkable rather than asserted.
    """

    state: str
    detail: str
    remedy: str | None = None
    browser_path: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "remedy": self.remedy,
            "browser_path": self.browser_path,
        }


class PreviewCaptureUnavailable(RuntimeError):
    """Raised when the optional Playwright capture backend is not installed.

    Carries the capability that explains *which* half is missing, so a launch
    that discovers a missing browser reports the same typed state the pre-check
    would have reported.
    """

    def __init__(self, capability: CaptureCapability) -> None:
        super().__init__(
            capability.detail
            if capability.remedy is None
            else f"{capability.detail}: {capability.remedy}"
        )
        self.capability = capability


def _extra_missing() -> CaptureCapability:
    if getattr(sys, "frozen", False):
        return CaptureCapability(
            state="extra_missing", detail=FROZEN_EXTRA_DETAIL, remedy=FROZEN_EXTRA_REMEDY
        )
    return CaptureCapability(
        state="extra_missing",
        detail="the optional preview-capture extra (Playwright) is not installed",
        remedy=INSTALL_HINT,
    )


def _browser_missing() -> CaptureCapability:
    roots = ", ".join(str(root) for root in _browser_roots()) or "no known browser cache"
    return CaptureCapability(
        state="browser_missing",
        detail=(
            "Playwright is installed but no Chromium browser binary was found "
            f"(looked in: {roots})"
        ),
        remedy=BROWSER_INSTALL_COMMAND,
    )


def capture_capability() -> CaptureCapability:
    """Report the capture backend's state without downloading or launching anything.

    Two local reads: can Playwright be imported, and is there a Chromium binary
    under a browsers root. Both are cheap and repeatable, so an operator who runs
    the remedy sees the state change on the next press without a daemon restart.
    """
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        capability = _extra_missing()
    else:
        _configure_browsers_path()
        executable = installed_chromium()
        capability = (
            CaptureCapability(
                state="ready",
                detail="Playwright and a Chromium browser binary are installed",
                browser_path=str(executable),
            )
            if executable is not None
            else _browser_missing()
        )
    log.debug(
        "preview capture capability state=%s",
        capability.state,
        extra={"state": capability.state, "browser_path": capability.browser_path},
    )
    return capability


# Preview-pane viewport labels → capture widths. Height is a sensible default;
# full_page is off so the shot matches what the pane frames.
VIEWPORT_WIDTHS = {"mobile": 390, "tablet": 834, "responsive": 1280}


def _is_missing_browser_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _MISSING_BROWSER_MARKERS)


async def capture_loopback(
    url: str,
    out_path: Path,
    *,
    width: int = 1280,
    height: int = 800,
    clip: dict[str, float] | None = None,
    timeout_ms: int = 15000,
) -> Path:
    """Screenshot a loopback URL to `out_path`.

    With `clip` ({x, y, width, height} in page CSS pixels) only that rectangle is
    captured — the region-select path. The clip is rendered at the same viewport
    the browser showed, so coordinates line up with what the user selected.

    Raises PreviewCaptureUnavailable if either half of the backend is missing, or
    a RuntimeError with a bounded message on failure.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - exercised via capture_capability
        raise PreviewCaptureUnavailable(_extra_missing()) from exc
    _configure_browsers_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width = max(240, min(width, 3840))
    height = max(240, min(height, 4320))
    clip_rect = _sanitize_clip(clip, width, height) if clip else None
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as exc:
            # A browsers root we did not think to scan makes the pre-check say
            # "ready" on a machine with nothing to launch. Playwright's own error
            # is the authority, so it is promoted to the same typed state rather
            # than surfacing as an unactionable capture failure.
            if _is_missing_browser_error(exc):
                raise PreviewCaptureUnavailable(_browser_missing()) from exc
            raise
        try:
            page = await browser.new_page(viewport={"width": width, "height": height})
            # "load", not "networkidle": a Vite/HMR dev server keeps an HMR
            # WebSocket open forever, so networkidle never fires and goto times out.
            await page.goto(url, wait_until="load", timeout=timeout_ms)
            # Give a rendering beat for hydration/first paint after load.
            await page.wait_for_timeout(400)
            if clip_rect is not None:
                await page.screenshot(path=str(out_path), clip=cast(Any, clip_rect))
            else:
                await page.screenshot(path=str(out_path), full_page=False)
        finally:
            await browser.close()
    log.info(
        "preview capture wrote %s", out_path, extra={"url": url, "region": bool(clip_rect)}
    )
    return out_path


def _sanitize_clip(clip: dict[str, float], width: int, height: int) -> dict[str, float] | None:
    """Clamp a selection rectangle inside the viewport; None if it is degenerate."""
    try:
        x = max(0.0, min(float(clip["x"]), float(width)))
        y = max(0.0, min(float(clip["y"]), float(height)))
        w = max(1.0, min(float(clip["width"]), float(width) - x))
        h = max(1.0, min(float(clip["height"]), float(height) - y))
    except (KeyError, TypeError, ValueError):
        return None
    if w < 4 or h < 4:
        return None
    return {"x": x, "y": y, "width": w, "height": h}
