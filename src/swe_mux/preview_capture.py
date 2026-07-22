from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

# Server-side headless screenshot of a session-owned loopback preview. Optional:
# requires the `preview-capture` extra (Playwright + a Chromium download). When
# absent the caller reports a typed "unavailable" state, like every other
# optional integration — it never raises into a terminal path.

INSTALL_HINT = "uv sync --extra preview-capture && uv run playwright install chromium"


def _configure_browsers_path() -> None:
    """Point Playwright at the standard per-user browser cache.

    A PyInstaller-frozen desktop build otherwise resolves browsers to a
    `.local-browsers` directory inside the bundle, which is empty. `playwright
    install` puts Chromium in the OS cache (`%LOCALAPPDATA%\\ms-playwright` on
    Windows), so point there unless the user set the path explicitly.
    """
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cache = Path(local) / "ms-playwright"
        if cache.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cache)

# Preview-pane viewport labels → capture widths. Height is a sensible default;
# full_page is off so the shot matches what the pane frames.
VIEWPORT_WIDTHS = {"mobile": 390, "tablet": 834, "responsive": 1280}


class PreviewCaptureUnavailable(RuntimeError):
    """Raised when the optional Playwright capture backend is not installed."""


def capture_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


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

    Raises PreviewCaptureUnavailable if the backend is missing, or a RuntimeError
    with a bounded message on failure.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - exercised via capture_available
        raise PreviewCaptureUnavailable(
            f"preview capture needs the optional Playwright backend: {INSTALL_HINT}"
        ) from exc
    _configure_browsers_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width = max(240, min(width, 3840))
    height = max(240, min(height, 4320))
    clip_rect = _sanitize_clip(clip, width, height) if clip else None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
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
