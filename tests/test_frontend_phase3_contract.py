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
    # Exactly two deliberate exceptions, both in the note editor and both reached
    # only after the Clipboard API has already failed (an insecure mobile context,
    # where there is no other surface): the manual-copy fallback, and the paste
    # fallback for the same contexts in the other direction. Any third native
    # dialog is a regression — normal flows use in-app surfaces.
    assert source.count("prompt(") == 2
    editor = (root / "ProjectNoteEditor.tsx").read_text(encoding="utf-8")
    assert editor.count("prompt(") == 2
    assert "window.prompt('Copy the text below:'" in editor
    assert "window.prompt('Paste text:')" in editor
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


def test_daemon_and_ui_reload_controls_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")

    # Session-preserving reload surfaces: palette commands, main-menu entries,
    # the restart endpoint, and the blocking wait overlay.
    assert "id: 'daemon.reload'" in app and "id: 'ui.reload'" in app
    assert app.count("Reload daemon (keep sessions)") >= 2  # main menu + sidebar menu
    assert "'/api/daemon/restart'" in app
    assert "location.reload()" in app
    assert "daemonReloading" in app and "daemon-reload-layer" in app
    assert ".daemon-reload-modal" in css


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
    rail = (root / "frontend" / "src" / "commandRail.ts").read_text(encoding="utf-8")
    css = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert 'class="terminal-action-rail"' in pane
    assert "Copy reply" in pane and "manual-terminal-paste" in pane
    assert "/last-reply" in pane
    assert "autoCopySelection" in pane and "requestAnimationFrame(autoCopySelection)" in pane
    assert ".terminal-action-rail" in css
    # The rail also sends terminal keys and toggles the on-screen keyboard for read/select
    # mode; it overflows and scrolls horizontally rather than wrapping. The key/item
    # definitions themselves live in commandRail.ts (user-configurable rail).
    assert "sendKey" in pane and "toggleKeyboard" in pane
    assert "\\x03" in rail and "\\x1b[A" in rail and "kbd-toggle" in rail
    assert "kbd-toggle" in pane
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
    # The bucket id rides along so a hand-placed Project can drop that section's
    # sort back to Manual instead of being re-sorted away on the next render.
    assert "beginProjectPointerDrag(event,project,bucket.id,peerIds)" in app
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


def test_sidebar_sections_carry_a_sort_control_and_reorder_by_their_header() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")
    sort = (root / "projectSort.ts").read_text(encoding="utf-8")

    # Sort is per section, so a Group and the ungrouped remainder can differ.
    assert "bucketSortMode(sidebarOrder,bucket.id)" in app
    assert 'class={`bucket-sort ${sortMode===\'custom\'?\'\':\'active\'}`}' in app
    assert "setSidebarOrder(setBucketSortMode(sidebarOrder,sortMenu.bucketId,option.id))" in app
    # Placing a Project by hand is what returns its section to Manual order.
    assert "setSidebarOrder(setBucketSortMode(sidebarOrder,bucketId,'custom'))" in app
    # The header doubles as the section's drag handle; its buttons keep the pointer.
    assert "beginBucketPointerDrag(event,bucket.id,bucket.name)" in app
    assert "data-reorder-id={bucket.id}" in app
    assert "'PUT','/api/project-groups/order'" in app
    assert ".sidebar-project-bucket>header .bucket-sort" in css
    assert '.sidebar-project-bucket[data-pointer-drop-indicator="insert-before"]' in css
    for mode in ("custom", "activity", "name", "name-desc", "created-desc", "created"):
        assert f"id: '{mode}'" in sort


def test_sections_sort_and_collapse_from_the_same_header() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")
    sort = (root / "projectSort.ts").read_text(encoding="utf-8")

    # Section ordering rides the same ⇅ control, one level up, behind a MenuGroup.
    assert 'MenuGroup id="sections"' in app or "MenuGroup id='sections'" in app
    assert "sortBuckets(allBuckets,sidebarOrder.sectionSort,activityStamps)" in app
    assert "setSidebarOrder({...sidebarOrder,sectionSort:option.id})" in app
    # Dragging a section header is what returns the sections to Manual order.
    assert "sectionSort:'custom'" in app
    # Click folds, drag reorders; the drag swallows the click it ends with.
    assert "suppressDragClickRef.current===`bucket:${bucket.id}`" in app
    assert "setSidebarOrder(toggleBucketCollapsed(sidebarOrder,bucket.id))" in app
    assert "{!bucketCollapsed&&bucket.items.map(project =>" in app
    # A folded section reports live count *and* the strongest agent state, or an
    # approval waiting inside it would be invisible.
    assert "projectSetRailStatus(sessions,peerIds,seenActivity)" in app
    assert ".bucket-collapsed-badge.activity-attention" in css
    assert ".sidebar-project-bucket>header .bucket-chevron" in css
    # Sections deliberately carry no date modes: nothing dates a Group.
    assert "created" not in sort.split("SECTION_SORT_OPTIONS")[1].split("]")[0]


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
    # 34px at chrome scale 1; the row follows `--ui-scale` so the tab strip grows
    # with the tab titles in it (`features/ui.md`, Appearance → chrome scale).
    assert "grid-template-rows:calc(34px*var(--ui-scale)) minmax(0,1fr)" in css


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
    # One chip builder shared by the collapsed rail and the mobile toolbar, so the
    # two surfaces cannot drift apart.
    assert "const quotaChip=(provider:ProviderName,form:'rail'|'toolbar')=>" in accounts
    assert "quotaChip(provider,'rail')" in accounts
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
    # The close control floats over the tab's right edge rather than holding a
    # column, so showing it never changes tab width.
    assert ".stack-tab-shell>.tab-close{position:absolute" in css
    assert "@media(hover:hover){.stack-tab-shell:hover>.tab-close" in css
    assert ".stack-tab-shell>.tab-close.confirming" in css
    # Touch never gets it: closing there is the tab's long-press menu, so the
    # mobile tab renders the tab button alone.
    assert "@media(hover:none){.stack-tab-shell>.tab-close{display:none}}" in css
    assert "class=\"stack-tab-shell mobile-unified-tab\"" in app
    mobile_tab_body = app.split("const mobileTab=")[1].split("const mobileUnifiedWorkspace=")[0]
    assert "tab-close" not in mobile_tab_body


def test_session_tab_context_menu_omits_redundant_focus_and_detach_actions() -> None:
    app = (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "source: 'sidebar'|'tab'|'pane'" in app
    assert "openSessionMenu(session,event.clientX,event.clientY,'tab')" in app
    assert (
        "contextMenu.source==='sidebar'&&<button onClick={() => runNamedCommand('session.open')}>"
        in app
    )
    assert "id: 'pane.detach'" not in app
    assert "runNamedCommand('pane.detach')" not in app


def test_no_session_or_tab_context_menu_reshapes_the_pane_tree() -> None:
    """Split / stack / dissolve are gone from every context menu, on every source.

    They used to render on the sidebar row, the tab, and the pane's own ⋯ menu, where
    five direction rows and two buttons pushed Rename and Kill past the fold in a menu
    opened to act on one session. Layout is now drag (direct manipulation) or the
    command palette. `Move tab` stays: it reorders the strip you are looking at rather
    than reshaping the tree.
    """
    app = (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(
        encoding="utf-8"
    )
    session_menu = app[app.index("{contextMenu &&") : app.index("{projectMenu &&")]
    tab_menu = app[app.index("{tabMenu&&") : app.index("Close tab</button>")]

    for menu in (session_menu, tab_menu):
        assert "Open in split:" not in menu
        assert "New terminal in split:" not in menu
        assert "Stack with focused terminal" not in menu
        assert "Dissolve tab stack into splits" not in menu
        assert "New terminal custom in split" not in menu
        assert "runNamedCommand('session.groupStack')" not in menu
        assert "runNamedCommand('stack.dissolve')" not in menu
        assert "runNamedCommand('session.customSplit')" not in menu
        # The one directional row that survives, and the reason the helper is still here.
        assert "directionRow('Move tab:'" in menu
    # `splitExistingLeaf` existed only to serve those rows; nothing may reintroduce a
    # caller without also reintroducing the menu entry this test forbids.
    assert "splitExistingLeaf" not in app
    # Removed from the menus, not from the app: these are the routes that keep them
    # usable, and each is bindable because it is a registry entry.
    for command in (
        "session.openSplitHorizontal",
        "session.openSplitVertical",
        "pane.splitHorizontal",
        "pane.splitVertical",
        "session.groupStack",
        "stack.dissolve",
        "session.customSplit",
    ):
        assert f"id: '{command}'" in app or f"id:'{command}'" in app


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
    # openProjectsManager takes an optional focus (which Project, which tab), so
    # every plain trigger must call it rather than hand it the click event.
    assert 'class="project-trigger" onClick={()=>openProjectsManager()}' in app
    assert "directionRow('Move tab:'" in app
    # Only the resource menu still splits from a menu — a Project note or an opened file
    # has no tab to drag until it is already in a pane, so removing it there would leave
    # no way in. The session/tab menus lost theirs; see
    # test_no_session_or_tab_context_menu_reshapes_the_pane_tree.
    assert "directionRow('Open in split:'" in app
    assert "splitNoteResource(noteMenu.resourceId" in app
    assert "export function paneNeighborIds" in layout
    assert "Swap pane with next" not in app
    assert (
        "Open project note…</button>"
        not in app[app.index("{contextMenu &&") : app.index("{projectMenu &&")]
    )


def test_event_stream_resumes_from_the_last_seen_sequence() -> None:
    """Reconnect catch-up is only meaningful with a cursor.

    Without one the daemon cannot know what this client missed, and its no-cursor
    default served the oldest retained page — history the client already had, and
    none of the gap.
    """
    app = (
        Path(__file__).parents[1] / "frontend" / "src" / "App.tsx"
    ).read_text(encoding="utf-8")

    assert "lastEventSeq" in app
    assert "?after_seq=${lastEventSeq.current}" in app
    assert "openWebSocket(`/events${resume}`)" in app
    # A gap wider than the replay window must trigger a full refresh, not be
    # mistaken for "caught up".
    assert "'events_gap'" in app


def test_layout_refresh_defers_to_an_in_flight_layout_write() -> None:
    """A GET snapshotted before a layout PATCH committed must not clobber it.

    Overwriting optimistic state snapped a just-dropped tab back, and a second
    drag in that window based itself on the clobbered layout and then won the
    write — silently reverting the first move for every client.
    """
    app = (
        Path(__file__).parents[1] / "frontend" / "src" / "App.tsx"
    ).read_text(encoding="utf-8")

    assert app.count("if(layoutWriteChains.current[project.id]!==undefined)continue") == 2


def test_terminal_pane_clears_per_session_state_on_a_session_switch() -> None:
    """The pane is reused unkeyed across stack-tab switches.

    Without an explicit reset, "Copy reply" copies (and records into clipboard
    history) the previous session's reply.
    """
    pane = (
        Path(__file__).parents[1] / "frontend" / "src" / "TerminalPane.tsx"
    ).read_text(encoding="utf-8")

    reset = pane[pane.index("setLastReply('')") : pane.index("},[session.id])")]
    for setter in (
        "setPreparedClipboard('')",
        "setManualClipboard(false)",
        "setSelectionText('')",
        "setFindQuery('')",
        "setFindResult('')",
    ):
        assert setter in reset, setter
    # An empty last-reply response must clear the value rather than leave the
    # previous session's text in place.
    assert "if(!disposed)setLastReply(result.text||'')" in pane
    # A renderer change has to reach panes whose other props are stable.
    assert "a.rendererPreference === b.rendererPreference" in pane
