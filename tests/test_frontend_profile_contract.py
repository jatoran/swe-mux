from __future__ import annotations

from pathlib import Path


def test_default_and_custom_terminal_creation_keep_split_semantics_explicit() -> None:
    source = (
        Path(__file__).parents[1] / "frontend" / "src" / "App.tsx"
    ).read_text(encoding="utf-8")

    assert "void spawnTerminal()" in source
    assert "New terminal custom…" in source
    assert "profile_id: profileId || undefined" in source
    assert "New terminal custom in split…" in source
    assert (
        "openLauncher(commandSession.space_id, 'horizontal',workingCwd(commandSession))"
        in source
    )
    assert "spawnTerminal(launcherSpace, cwd, launcherSplit, launcherProfile)" in source
    assert "setLayoutMap" in source and "await updateLayout" in source


def test_settings_panel_is_top_aligned_and_viewport_bounded() -> None:
    css = (
        Path(__file__).parents[1] / "frontend" / "src" / "style.css"
    ).read_text(encoding="utf-8")

    viewport_contract = css.split(
        "/* Settings must stay inside the browser viewport at every window height. */", 1
    )[1]
    assert "height:calc(100dvh - 42px)" in viewport_contract
    assert "align-items:flex-start" in viewport_contract
    assert "overflow:hidden" in viewport_contract
    assert "overflow-y:auto" in viewport_contract
