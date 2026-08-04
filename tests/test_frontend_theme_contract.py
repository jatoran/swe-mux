from __future__ import annotations

import re
from pathlib import Path

from swe_mux.config import contrast_ratio


def test_every_builtin_theme_defines_readable_xterm_ansi_and_ui_states() -> None:
    root = Path(__file__).parents[1]
    source = (root / "frontend" / "src" / "theme.ts").read_text(encoding="utf-8")
    css = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")
    required = {
        "background",
        "foreground",
        "cursor",
        "selectionBackground",
        "black",
        "brightBlack",
        "red",
        "brightRed",
        "green",
        "brightGreen",
        "yellow",
        "brightYellow",
        "blue",
        "brightBlue",
        "magenta",
        "brightMagenta",
        "cyan",
        "brightCyan",
        "white",
        "brightWhite",
    }
    for name in ("dark", "light", "solarized-dark", "tokyo-night"):
        pattern = rf"(?:^|\n)\s*['\"]?{re.escape(name)}['\"]?:\s*\{{([^}}]+)\}}"
        block = re.search(pattern, source)
        assert block, f"missing {name} xterm theme"
        fields = set(re.findall(r"(\w+):", block.group(1)))
        assert required <= fields
        background = re.search(r"background:'(#[0-9a-fA-F]{6})'", block.group(1))
        foreground = re.search(r"foreground:'(#[0-9a-fA-F]{6})'", block.group(1))
        assert background and foreground
        assert contrast_ratio(background.group(1), foreground.group(1)) >= 4.5

    for selector in (
        "button:focus-visible",
        ".state-dot.working",
        ".state-dot.awaiting",
        ".state-dot.crashed",
        "selectionBackground",
    ):
        assert selector in css or selector in source


def test_scrollbars_use_compact_theme_aware_shared_chrome() -> None:
    css = (
        Path(__file__).parents[1] / "frontend" / "src" / "style.css"
    ).read_text(encoding="utf-8")

    assert "scrollbar-color:color-mix(in srgb,var(--muted) 48%,transparent) transparent" in css
    assert "scrollbar-width:thin" in css
    assert "*::-webkit-scrollbar { width:7px;height:7px }" in css
    assert "*::-webkit-scrollbar-track { background:transparent }" in css
    assert "border:2px solid transparent" in css
    assert "background-clip:content-box" in css
    assert "*::-webkit-scrollbar-thumb:hover" in css
    assert "*::-webkit-scrollbar-thumb:active" in css
