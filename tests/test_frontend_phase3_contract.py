from __future__ import annotations

import re
from pathlib import Path


def test_recursive_layout_and_command_surfaces_are_wired() -> None:
    root = Path(__file__).parents[1]
    app = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    layout = (root / "frontend" / "src" / "layout.ts").read_text(encoding="utf-8")

    assert "type PaneSplit" in layout
    assert "setSplitRatio" in layout
    assert "swapTerminals" in layout
    assert 'role="separator"' in app
    assert "paneNeighborIds" in app
    assert "pane.swapNext" not in app
    assert "searchCommands(commands, paletteQuery)" in app


def test_normal_ui_flows_do_not_use_browser_native_dialogs() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.ts*"))

    assert "alert(" not in source
    assert "confirm(" not in source
    assert "prompt(" not in source
    assert "Access token required" not in source
    assert "mux.token" not in source
    assert "Create project" in source
    assert "Select project folder" in source
    assert "/api/fs/roots" in source
    assert "/api/fs/list" in source
    assert "folderNameFromPath(root)" in source
    assert 'class="project-trigger"' in source
    assert "project_files_changed" in source
    assert "Select this folder" in source
    assert "Create worktree + terminal" not in source


def test_terminal_find_is_inline_and_feature_complete() -> None:
    root = Path(__file__).parents[1]
    pane = (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")

    assert "terminal-find" in pane
    assert "findNext" in pane
    assert "findPrevious" in pane
    assert "caseSensitive" in pane
    assert "setFindResult(found ? 'match' : 'no match')" in pane


def test_terminal_clipboard_rail_and_selection_autocopy_are_wired() -> None:
    root = Path(__file__).parents[1]
    pane = (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")
    css = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert 'class="terminal-action-rail"' in pane
    assert "Copy reply" in pane and "manual-terminal-paste" in pane
    assert "/last-reply" in pane
    assert "autoCopySelection" in pane and "requestAnimationFrame(autoCopySelection)" in pane
    assert ".terminal-action-rail" in css
    # The rail also sends terminal keys and toggles the on-screen keyboard for read/select
    # mode; it overflows and scrolls horizontally rather than wrapping.
    assert "sendKey" in pane and "toggleKeyboard" in pane
    assert "\\x03" in pane and "\\x1b[A" in pane and "kbd-toggle" in pane
    assert "overflow-x:auto" in css and ".terminal-action-rail .kbd-toggle" in css


def test_mobile_terminal_ime_streams_composition_without_xterm_overlay() -> None:
    root = Path(__file__).parents[1]
    pane = (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")
    ime = (root / "frontend" / "src" / "mobileTerminalIme.ts").read_text(encoding="utf-8")
    css = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert 'class="mobile-terminal-live-input"' in pane
    assert "mobileImeDelta(mobileInputValue,next)" in pane
    assert "term.input(data,true)" in pane
    assert "TERMINAL_DELETE.repeat" in ime
    assert ".terminal-surface .xterm .composition-view{display:none!important}" in css


def test_drag_reorder_keeps_native_source_mounted_and_commits_latest_preview() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")

    assert "{node.children.map(child=>" in app
    assert "dragProjectRef.current" in app
    assert "dragStackTabRef.current" in app
    assert "reorderTargetFromContainer(bucket,current.id,'vertical',pointer.clientY)" in app
    assert (
        "reorderTargetFromContainer(tabStrip,current.childId,'horizontal',pointer.clientX)" in app
    )
    assert "beginProjectPointerDrag(event,project,peerIds)" in app
    assert "showPointerDropIndicator(targetElement,`insert-${target.side}`)" in app
    assert "data-reorder-id={project.id}" in app
    assert "data-reorder-id={child.id}" in app
    assert "moveLeafToStack" in app
    assert "moveLeafToSplit" in app
    assert "targetStackId" in app
    assert "class={`project-group" in app
    assert "dragSessionTargetRef.current={stackId,projectId:targetProjectId}" in app
    assert ".project-group.project-drop-target" in css
    assert ".drop-before:after" in css
    assert ".drop-after:after" in css
    assert ".drop-zone-left" in css
    assert ".drop-zone-tabs" in css


def test_pane_local_tab_rails_and_resizable_collapsible_sidebar_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")

    assert 'class="app-topbar"' in app
    assert 'class="app-identity"' in app
    workspace_at = app.index("<div class={`workspace")
    assert workspace_at < app.index('<header class="app-topbar">', workspace_at)
    assert 'class="top-workspace-tabs"' not in app
    assert 'class="stack-tabs" role="tablist" aria-label="Workspace tabs"' in app
    assert "node.children.map(child=>" in app
    assert "mux.sidebar.width.v1" in app
    assert "mux.sidebar.collapsed.v1" in app
    assert 'class="sidebar-resizer"' in app
    assert ".workspace.sidebar-collapsed" in css
    assert ".pane-stack>.stack-tabs{display:flex}" in css
    assert "grid-template-rows:34px minmax(0,1fr)" in css


def test_collapsed_sidebar_rail_keeps_sidebar_controls_reachable() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")
    accounts = (root / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    resources = (root / "ResourceUsage.tsx").read_text(encoding="utf-8")

    assert 'sidebarCollapsed&&<nav class="sidebar-rail"' in app
    assert 'title="Menu"' in app and 'title="Projects"' in app
    assert "<ResourceUsageSummary compact" in app
    assert '<AccountSwitcher variant="rail" placement="up"' in app
    assert ".sidebar-rail{" in css
    assert ".rail-status{" in css

    # Status sits above the actions, which stay pinned at the very bottom.
    assert app.index('<div class="rail-status">') < app.index('title="Menu"')
    assert app.index('title="Menu"') < app.index('title="Projects"')
    assert ".rail-status{margin-top:auto" in css

    # The rail resets the mobile trigger's 42px floor, or it overflows the strip.
    assert "min-width:28px" in css
    # Popover direction is independent of the condensed trigger so the rail, which
    # sits at the bottom of the window, still opens upward.
    assert "const opensDown=placement?placement==='down':compact" in accounts
    # One chip per provider, each identified by its own mark, showing weekly usage.
    assert "providerWeeklyUsage" in accounts
    assert "const railChip=(provider:ProviderName)=>" in accounts
    assert "providerGlyph(provider)" in accounts
    assert ".rail-quota .provider-glyph" in css
    # RAM, not CPU: a fluctuating percentage is not worth a permanent glance.
    assert "compactMemoryLabel(combined.memory_bytes)" in resources
    assert "resource-usage-compact" in resources


def test_workspace_tabs_have_inline_close_controls_with_terminal_confirmation() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")

    assert "const closeTab=(child:PaneLeaf,label:string,session?:Session)=>" in app
    assert "if(terminal){if(session&&!session.pending)requestKill(session);return}" in app
    assert "if(child.kind==='note'){void removeWorkspaceNote(projectId,child.id);return}" in app
    assert "removeLeaf(latest,child.kind,child.id)" in app
    assert "{confirming?'✓':'×'}</button>" in app
    assert "terminal&&(!session||!!session.pending)" in app
    assert (
        ".stack-tab-shell>.tab-close{width:22px;min-width:22px;max-width:22px;flex:0 0 22px" in css
    )
    assert ".stack-tab-shell>.tab-close.confirming" in css


def test_session_tab_context_menu_omits_redundant_focus_and_detach_actions() -> None:
    app = (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "source: 'sidebar'|'tab'|'pane'" in app
    assert "openSessionMenu(session,event.clientX,event.clientY,'tab')" in app
    assert (
        "contextMenu.source==='sidebar'&&<button onClick={() => runNamedCommand('session.open')}>"
        in app
    )
    assert (
        "contextMenu.source==='sidebar'&&<button "
        "disabled={!activeId||activeId===contextMenu.session.id}" in app
    )
    assert "id: 'pane.detach'" not in app
    assert "runNamedCommand('pane.detach')" not in app


def test_projects_manager_and_shared_directional_tab_actions_are_wired() -> None:
    root = Path(__file__).parents[1]
    app = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    manager = (root / "frontend" / "src" / "ProjectsManager.tsx").read_text(encoding="utf-8")
    layout = (root / "frontend" / "src" / "layout.ts").read_text(encoding="utf-8")

    assert "sidebar_visible" in manager
    assert "Configured Projects keep their notes, files, settings, and history" in manager
    assert (
        "visibleProjects = orderedProjects.filter(project => project.sidebar_visible !== false)"
        in app
    )
    assert app.count('class="modal-layer project-registry-dialog-layer"') == 2
    # Adding a Project is reachable in one step from the empty-sidebar menu, above
    # the registry entry, rather than only through the registry.
    assert "id: 'project.add'" in app
    assert "runNamedCommand('project.add')}}>Add project…" in app
    assert app.index("runNamedCommand('project.add')") < app.index(
        "runNamedCommand('project.create')}}>Manage projects…"
    )
    # Ordering, not literal depths: the exact values must stay free to move above
    # persistent chrome. test_dialog_layers_stack_above_persistent_chrome owns the
    # full invariant.
    style = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")
    depths = {
        name: int(value)
        for name, value in re.findall(r"\.([a-z-]+)\s*\{[^}]*?z-index:(\d+)", style)
    }
    assert depths["project-registry-dialog-layer"] > depths["projects-manager-layer"]
    assert 'class="project-trigger" onClick={openProjectsManager}' in app
    assert "directionRow('Move tab:'" in app
    assert "directionRow('Open in split:'" in app
    assert "directionRow('New terminal in split:'" in app
    assert "export function paneNeighborIds" in layout
    assert "Swap pane with next" not in app
    assert (
        "Open project note…</button>"
        not in app[app.index("{contextMenu &&") : app.index("{projectMenu &&")]
    )
