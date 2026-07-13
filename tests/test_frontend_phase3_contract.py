from __future__ import annotations

from pathlib import Path


def test_recursive_layout_and_command_surfaces_are_wired() -> None:
    root = Path(__file__).parents[1]
    app = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    layout = (root / "frontend" / "src" / "layout.ts").read_text(encoding="utf-8")

    assert "type PaneSplit" in layout
    assert "setSplitRatio" in layout
    assert "swapTerminals" in layout
    assert "role=\"separator\"" in app
    assert "pane.swapNext" in app
    assert "searchCommands(commands, paletteQuery)" in app


def test_normal_ui_flows_do_not_use_browser_native_dialogs() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.ts*"))

    assert "alert(" not in source
    assert "confirm(" not in source
    assert "prompt(" not in source
    assert "Access token required" not in source
    assert "mux.token" not in source
    assert "Create worktree + terminal" in source


def test_terminal_find_is_inline_and_feature_complete() -> None:
    root = Path(__file__).parents[1]
    pane = (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")

    assert "terminal-find" in pane
    assert "findNext" in pane
    assert "findPrevious" in pane
    assert "caseSensitive" in pane
    assert "setFindResult(found ? 'match' : 'no match')" in pane
