from __future__ import annotations

from pathlib import Path


def test_default_and_custom_terminal_creation_keep_split_semantics_explicit() -> None:
    source = (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(
        encoding="utf-8"
    )

    assert "void spawnTerminal()" in source
    assert "New terminal custom…" in source
    assert "profile_id: profileId || undefined" in source
    assert "New terminal custom in split…" in source
    assert "openLauncher(commandSession.project_id, 'horizontal')" in source
    assert "spawnTerminal(launcherProject, launcherSplit, launcherProfile)" in source
    assert "backend: 'shell', project_id: targetProject" in source
    assert "setLayoutMap" in source and "await updateLayout" in source


def test_terminal_creation_is_visible_optimistically_before_the_api_returns() -> None:
    source = (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(
        encoding="utf-8"
    )
    before_request = source.split("const next = await api<Session>('POST', '/api/sessions'", 1)[0]

    assert "pendingTerminal(pendingId,target)" in before_request
    assert "placePendingTerminal(currentLayout,pendingId,placement)" in before_request
    assert "setActiveId(pendingId)" in before_request
    assert "pending-terminal-body" in source


def test_settings_panel_is_top_aligned_and_viewport_bounded() -> None:
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    viewport_contract = css.split(
        "/* Settings must stay inside the browser viewport at every window height. */", 1
    )[1]
    assert "height:calc(100dvh - 42px)" in viewport_contract
    assert "align-items:flex-start" in viewport_contract
    assert "overflow:hidden" in viewport_contract
    assert "overflow-y:auto" in viewport_contract


def test_settings_drafts_keep_the_active_tab_and_guard_every_close_path() -> None:
    source = (Path(__file__).parents[1] / "frontend" / "src" / "Settings.tsx").read_text(
        encoding="utf-8"
    )

    assert "},[initialSection])" in source
    assert "},[draft,initialSection])" not in source
    assert "event.target===event.currentTarget&&requestClose()" in source
    assert "event.stopImmediatePropagation()" in source
    assert 'role="alertdialog"' in source
    assert "Save your changes?" in source
    assert "discardAndLeave" in source
    assert "saveAndLeave" in source
    assert "dirty?'Save changes':'Saved'" in source
    assert "requestClose('usage')" in source
    assert "requestClose('automation')" in source


def test_project_ignore_editors_preserve_newlines_until_save() -> None:
    source = (Path(__file__).parents[1] / "frontend" / "src" / "Settings.tsx").read_text(
        encoding="utf-8"
    )

    assert source.count("parseIgnorePatternDraft(e.currentTarget.value)") == 2
    assert (
        "project_ignore_patterns:normalizeIgnorePatterns(draft.project_ignore_patterns)" in source
    )
    assert "ignore_patterns:normalizeIgnorePatterns(projectValues.ignore_patterns)" in source
    assert "e.currentTarget.value.split('\\n').map(item=>item.trim()).filter(Boolean)" not in source
