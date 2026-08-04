from __future__ import annotations

from pathlib import Path


def test_default_and_custom_terminal_creation_keep_split_semantics_explicit() -> None:
    source = (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(
        encoding="utf-8"
    )

    assert "void spawnTerminal()" in source
    assert "New terminal custom…" in source
    assert "profile_id: backend==='shell' ? profileId || undefined : undefined" in source
    # The split-launching variant is palette-only now (no context menu reshapes the
    # pane tree), so the registry entry is the whole surface — its label and its
    # explicit 'horizontal' split argument are what keep the semantics distinct from
    # the unsplit "New terminal custom…" above.
    assert "New custom terminal in selected session split" in source
    assert "openLauncher(commandSession.project_id, 'horizontal')" in source
    assert "spawnTerminal(launcherProject, launcherSplit, launcherProfile)" in source
    assert "backend, project_id: targetProject" in source
    assert "setLayoutMap" in source and "await updateLayout" in source


def test_terminal_creation_is_visible_optimistically_before_the_api_returns() -> None:
    source = (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(
        encoding="utf-8"
    )
    before_request = source.split("const next = await api<Session>('POST', '/api/sessions'", 1)[0]

    assert "pendingTerminal(pendingId,target,backend)" in before_request
    assert "placePendingTerminal(currentLayout,pendingId,placement)" in before_request
    assert "setActiveId(pendingId)" in before_request
    assert "pending-terminal-body" in source


def test_agent_pane_headers_omit_the_working_directory() -> None:
    root = Path(__file__).parents[1]
    source = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "const agentSession=isAgent(session)" in source
    assert "{!agentSession&&<div class=\"pane-path\">{session.cwd}</div>}" in source
    assert "{!agentSession&&<div class={`pane-path ${cwdIsLive?'live':'last-known'}`}" in source
    assert "pane-bar ${agentSession?'agent-pane-bar':''}" in source
    assert ".pane-bar.agent-pane-bar { grid-template-columns:auto minmax(50px,1fr) auto }" in css


def test_desktop_drawer_note_search_precedes_the_agent_action() -> None:
    root = Path(__file__).parents[1]
    source = (root / "frontend" / "src" / "ProjectResource.tsx").read_text(encoding="utf-8")
    css = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert 'class="resource-find"' in source
    assert "@media(min-width:761px)" in css
    assert ".drawer-note-host .resource-actions .resource-find{order:-1" in css
    assert "min-width:31px" in css
    assert "font-size:13px" in css


def test_settings_panel_is_top_aligned_and_viewport_bounded() -> None:
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    viewport_contract = css.split(
        "/* Settings must stay inside the browser viewport at every window height. */", 1
    )[1]
    # 42px at chrome scale 1. The offset tracks `--ui-scale` because the mobile
    # topbar it sits under does; pinning it would overlap or gap at other scales.
    assert "height:calc(100dvh - calc(42px*var(--ui-scale)))" in viewport_contract
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
    src = Path(__file__).parents[1] / "frontend" / "src"
    source = (src / "Settings.tsx").read_text(encoding="utf-8")
    manager = (src / "ProjectsManager.tsx").read_text(encoding="utf-8")

    # Settings keeps only the global list; the per-Project one moved to the Projects
    # registry, which is the single per-Project editor.
    assert source.count("parseIgnorePatternDraft(e.currentTarget.value)") == 1
    assert (
        "project_ignore_patterns:normalizeIgnorePatterns(draft.project_ignore_patterns)" in source
    )
    assert "parseIgnorePatternDraft(event.currentTarget.value)" in manager
    assert "ignore_patterns:normalizeIgnorePatterns(values.ignore_patterns)" in manager
    assert "e.currentTarget.value.split('\\n').map(item=>item.trim()).filter(Boolean)" not in source
