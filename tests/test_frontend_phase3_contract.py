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
    assert 'aria-label="Manage Projects"' in source
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

    assert "terminal-action-rail${mobilePinnedSend?' mobile-pinned-send':''}" in pane
    assert "Copy reply" in pane and "manual-terminal-paste" in pane
    assert "/last-reply" in pane
    # Selection auto-copy runs a frame after the gesture ends, never inline: the platform
    # makes its own focus and selection decision as the tap resolves. The deferral is the
    # invariant; the call itself moved inside an arrow when soft-keyboard restoration was
    # deliberately ordered after the copy, so do not assert a bare callback reference.
    assert "const autoCopySelection=" in pane
    assert "requestAnimationFrame(()=>{autoCopySelection()" in pane
    assert ".terminal-action-rail" in css
    # The rail also sends terminal keys and toggles the on-screen keyboard for read/select
    # mode; it overflows and scrolls horizontally rather than wrapping. The key/item
    # definitions themselves live in commandRail.ts (user-configurable rail).
    assert "sendKey" in pane and "toggleKeyboard" in pane
    assert "\\x03" in rail and "\\x1b[A" in rail and "kbd-toggle" in rail
    assert "kbd-toggle" in pane
    assert "overflow-x:auto" in css and ".terminal-action-rail .kbd-toggle" in css


def test_successful_clipboard_writes_use_the_shared_interaction_hud() -> None:
    root = Path(__file__).parents[1]
    app = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    hud = (root / "frontend" / "src" / "InteractionHud.tsx").read_text(encoding="utf-8")
    clipboard = (root / "frontend" / "src" / "clipboardHistory.ts").read_text(encoding="utf-8")
    pane = (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")
    css = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "CLIPBOARD_COPIED_EVENT = 'mux:clipboard-copied'" in clipboard
    assert "write.then(() => announceClipboardCopy(text, 'copy')" in clipboard
    assert "if (!event.defaultPrevented || suppliedText)" in clipboard
    assert "window.addEventListener(CLIPBOARD_COPIED_EVENT, onClipboardCopied)" in hud
    assert "Copied to clipboard" in hud
    assert 'class="interaction-hud"' in hud
    assert "<InteractionHud />" in app
    # Clipboard acknowledgement owns state below App. A copy or cut must not
    # re-render terminals, agent chats, or Continuity editors through the root.
    assert "setInteractionHud" not in app
    assert "CLIPBOARD_COPIED_EVENT" not in app
    assert ".interaction-hud" in css and "pointer-events:none" in css
    assert "right:max(16px,calc(env(safe-area-inset-right) + 12px))" in css
    assert "bottom:max(16px,calc(env(safe-area-inset-bottom) + 12px))" in css
    # Copy success has one visible owner. The rail keeps selection, paste, upload,
    # and recovery state but no longer duplicates the app-level confirmation.
    assert "showClipboardStatus('Selection copied')" not in pane
    assert "showClipboardStatus('Reply copied')" not in pane


def test_mobile_terminal_ime_streams_composition_without_xterm_overlay() -> None:
    root = Path(__file__).parents[1]
    pane = (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")
    ime = (root / "frontend" / "src" / "mobileTerminalIme.ts").read_text(encoding="utf-8")
    css = (root / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert 'class="mobile-terminal-live-input"' in pane
    assert "mobileImeDelta(mobileInputValue,next,mobileLineBreak())" in pane
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
    assert "reorderTargetFromContainer(list,current.id,'vertical',pointer.clientY)" in app
    assert (
        "reorderTargetFromContainer(tabStrip,current.childId,'horizontal',pointer.clientX)" in app
    )
    # A Project drag resolves its target list from the pointer, across the whole tree,
    # rather than being confined to the list it started in: the same gesture reorders
    # within a Group and moves a Project between Groups. Which list it landed in is read
    # off `data-group-id`, and the drop commits the Group change and the order together.
    assert "beginProjectPointerDrag(event,project)" in app
    assert "const list=projectListAt(tree,pointer.clientY)" in app
    assert "const groupId=list.dataset.groupId||null" in app
    assert "':scope > [data-group-id]'" in app
    assert "commitProjectDrop(project,current)" in app
    assert "'PATCH',`/api/projects/${project.id}`,{group_id:drop.groupId}" in app
    assert "await commitProjectOrder(drop.previewIds)" in app
    # A list with no rows to sit beside — an empty Group, or a folded one — is a target in
    # its own right, outlined whole because there is no gap to draw a line in.
    assert "showPointerDropIndicator(list,'drop-into')" in app
    assert '.sidebar-project-list[data-pointer-drop-indicator="drop-into"]' in css
    assert "showPointerDropIndicator(targetElement,`insert-${target.side}`)" in app
    assert "data-reorder-id={project.id}" in app
    assert "data-reorder-id={child.id}" in app
    assert "moveLeafToStack" in app
    assert "moveLeafToSplit" in app
    assert "targetStackId" in app
    assert "class={`project-group" in app
    assert ".project-group.project-drop-target" in css
    assert ".drop-before:after" in css
    assert ".drop-after:after" in css
    assert ".drop-zone-left" in css
    assert ".drop-zone-tabs" in css

    # Both sidebar lists preview the landing position as a labelled outline of the dragged
    # row over the gap it would take, not as a line at the pointer.
    assert "dropSlotForRow(targetSection,target.side,rowHeight,project.name)" in app
    assert "dropSlotForRow(element,target.side,rowHeight,label)" in app
    assert ".mux-drop-slot{" in css

    # A session drop resolves to a slot between rows or to grouping with one, and lands
    # exactly there rather than being appended to the target's pane.
    assert "listDropTargetForPoint(" in app
    assert "moveTerminalBeside(current,session.id,target.id,target.side)" in app
    assert "groupTerminalsInStack(current,target.id,session.id)" in app

    # The gesture never leaves the Project it started in: only that Project's own session
    # list is consulted, and a release outside it commits nothing.
    assert "querySelector<HTMLElement>(':scope > .session-list')" in app
    assert "DROP_LIST_MARGIN" in app
    assert 'data-pointer-drop-indicator="invalid"' not in css

    # Touch drags cancel touchmove themselves; without it a scrolling sidebar cancels the
    # pointer and a picked-up row goes nowhere.
    assert "window.addEventListener('touchmove',blockTouchScroll,{passive:false})" in app


def test_the_projects_header_owns_sort_and_fold_for_the_whole_tree() -> None:
    """One title, one sort, and one fold control above the tree.

    Sort was per bucket (a ⇅ on every Group header) on the theory that a hand-arranged
    shortlist and an alphabetical pile should coexist; nobody varied it, so it became a
    single mode applied to every bucket. Collapse-all is new: folding a long sidebar
    used to mean clicking every Project and every Group in turn.
    """
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")
    sort = (root / "projectSort.ts").read_text(encoding="utf-8")

    # One mode for every bucket, read straight off the prefs.
    assert "sortProjects(items,sidebarOrder.projectSort,recentProjectRanks)" in app
    assert "setSidebarOrder(setProjectSortMode(sidebarOrder,option.id))" in app
    assert "bucketSortMode" not in app and "setBucketSortMode" not in app
    assert "bucketSortMode" not in sort
    # Placing a Project by hand is what returns Projects to Manual order.
    assert "setSidebarOrder(setProjectSortMode(sidebarOrder,'custom'))" in app
    # PROJECTS names the whole tree and owns its controls; it is not a Group header.
    assert 'class="sidebar-tools sidebar-projects-header"' in app
    assert '<strong>PROJECTS</strong>' in app
    assert app.index('class="sidebar-tools sidebar-projects-header"') < app.index(
        'class="project-tree"'
    )
    assert 'class="sidebar-project-list sidebar-ungrouped-projects" data-group-id=""' in app
    # One control now, not two: Group placement rides the same mode, so there is no second
    # label to put in the button's title and no second mode to read for its active state.
    # `sectionSort` survives in projectSort.ts only as the migration that reads the retired
    # key off disk once — the type and its option list are gone.
    assert "sectionSort" not in app
    assert "SectionSortMode" not in sort and "SECTION_SORT_OPTIONS" not in sort
    assert "migrateSectionSort" in sort
    assert "${projectSortLabel(sidebarOrder.projectSort)}`}" in app
    assert "setAllFolded(!allFolded)" in app
    assert "setAllCollapsed(displayProjects.map(project=>project.id),folded)" in app
    assert (
        "setAllBucketsCollapsed(sidebarOrder,displayBuckets.map(bucket=>bucket.id),folded)"
        in app
    )
    # Fold-all flips to Expand only once nothing on screen is left to collapse.
    assert "displayProjects.every(project=>collapsedProjects.has(project.id))" in app
    assert "displayBuckets.every(bucket=>isBucketCollapsed(sidebarOrder,bucket.id))" in app
    # Every Group renders, empty ones included: a Group nobody has filled yet is the one
    # that most needs to be on screen, because dragging a Project in is how it gets filled.
    assert "displayBuckets.filter(" not in app
    assert "projectBuckets" not in app
    # Fold and add/manage are icons, not glyphs. `⊞`/`⊟` is the box-drawing pair for a
    # tree *node*, which nobody reads as a bulk expand/collapse control.
    assert "{allFolded?<UnfoldMoreIcon/>:<UnfoldLessIcon/>}" in app
    assert "⊞" not in app and "⊟" not in app
    assert 'title="Manage Projects" onClick={()=>openProjectsManager()}><CogIcon/>' in app
    assert "onClick={()=>{openProjectsManager();void createProject()}}><PlusIcon/>" in app
    # Four controls over content the user is reading: revealed on hover where there is a
    # hover to give, always visible on touch, and never `display`, which would reflow the row.
    assert "@media (hover:hover) and (pointer:fine){" in css
    assert ".sidebar-projects-header .sidebar-tool{opacity:0" in css
    assert ".sidebar-projects-header:hover .sidebar-tool," in css
    # The header doubles as the section's drag handle; its buttons keep the pointer.
    assert "beginBucketPointerDrag(event,bucket.id,bucket.name)" in app
    assert "data-reorder-id={bucket.id}" in app
    assert "'PUT','/api/project-groups/order'" in app
    assert ".sidebar-tools .sidebar-sort.active" in css
    assert '.sidebar-project-bucket[data-pointer-drop-indicator="insert-before"]' in css
    for mode in ("custom", "activity", "name", "name-desc", "created-desc", "created"):
        assert f"id: '{mode}'" in sort


def test_a_group_is_renamed_folded_and_deleted_from_its_own_context_menu() -> None:
    """A Group's three actions live behind a right-click, and delete asks first.

    The header carried a `×` for delete once, a pixel from the fold toggle, and removing it
    left no delete path at all: reassigning every Project one at a time still leaves the
    empty Group on screen, because empty Groups render. So delete came back — in a context
    menu, behind a confirm that says what survives it, rather than as a header button.
    Rename and fold are mirrored there too; both still work from the header.
    """
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")

    # Right-click anywhere in the section that is not a row of its own, and hold on mobile,
    # which has no right-click to give.
    assert "openGroupMenu(bucket.id,event.clientX,event.clientY)" in app
    assert (
        "beginLongPress(event,(x,y)=>{groupHeldRef.current=true;openGroupMenu(bucket.id,x,y)})"
        in app
    )
    assert "if((event.target as Element).closest('.project-row,.session-row'))return" in app
    # The hold fires under the finger, so the click it ends with must not fold the Group.
    assert "if(groupHeldRef.current){groupHeldRef.current=false;return}" in app
    # Rename stays on the header as well; sort never comes back to it.
    assert 'class="bucket-rename" title="Rename group"' in app
    assert 'class="bucket-sort"' not in app

    # Delete is two clicks, and the second one is the only caller of the handler.
    assert "const deleteGroup=" in app
    assert "onClick={()=>setConfirmGroupDeleteId(group.id)}>Delete group…" in app
    assert "onClick={()=>void deleteGroup(group)}" in app
    assert "'DELETE',`/api/project-groups/${group.id}`" in app
    # Opening the menu clears any armed confirm, so it cannot be inherited by the next Group.
    assert "setConfirmGroupDeleteId(null)\n    setGroupMenu({groupId,x,y})" in app
    # The Projects are what a user fears losing, so the confirm says they do not go.
    assert "return to the root list." in app
    assert "No folder, session, layout, or history is touched." in app
    # Menus are dismiss levels, not a special case of the sidebar's own menu.
    assert "useDismissLevel(() => setGroupMenu(null), !!groupMenu, 'group-menu')" in app


def test_groups_sort_in_among_root_projects_and_collapse_from_their_header() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")
    sort = (root / "projectSort.ts").read_text(encoding="utf-8")

    # Group placement rides the one PROJECTS sort, as a peer of a root Project. As its own
    # setting it could only order Groups among Groups below the whole ungrouped pile, so no
    # mode could lift a Group for the work inside it — the bug this replaced.
    assert (
        "sortRootEntries(ungroupedProjects,allBuckets,sidebarOrder.projectSort,recentProjectRanks)"
        in app
    )
    assert "const rootRows=sidebarRootRows(rootEntries)" in app
    assert "rootEntries.flatMap(entry=>entry.kind==='group'?[entry.bucket]:[])" in app
    # Reading order interleaves too, or the rail and the numbered shortcuts would disagree
    # with the tree they are drawn from.
    assert (
        "displayProjects=rootEntries.flatMap(entry=>entry.kind==='group'?entry.bucket.items:[entry.project])"
        in app
    )
    # Each run of root Projects between Groups is its own droppable list.
    assert "rootRows.map(row=>{" in app
    assert "if(row.kind==='root'){" in app
    assert 'class="sidebar-project-list sidebar-ungrouped-projects" data-group-id=""' in app
    assert "key={row.key}" in app
    # Dragging a Group header returns the one sort to Manual, which is the two-tier tree.
    assert app.count("setSidebarOrder(setProjectSortMode(sidebarOrder,'custom'))") == 2
    # Click folds, drag reorders; the drag swallows the click it ends with.
    assert "suppressDragClickRef.current===`bucket:${bucket.id}`" in app
    assert "setSidebarOrder(toggleBucketCollapsed(sidebarOrder,bucket.id))" in app
    assert "{!bucketCollapsed&&bucketItems.map(project=>sidebarProjectRow(project))}" in app
    assert (
        "const bucketItems=bucket.items.filter(project=>!sidebarFilter"
        "||sidebarFilter.projects.has(project.id))" in app
    )
    # An emptied Group keeps its section and says what to do with it — that hint is also
    # the drop target, since a header alone is too thin a strip to aim a dragged row at.
    # The hint is suppressed while a filter is up: it is also the drop target, and every
    # sidebar drag is inert against a tree that is missing rows.
    assert (
        "{!bucketCollapsed&&!bucketItems.length&&!sidebarFilter&&<p class=\"project-list-empty\">"
        "Drag a Project here</p>}" in app
    )
    assert ".project-list-empty{" in css
    # A folded Group reports live count *and* the strongest agent state, or an
    # approval waiting inside it would be invisible.
    assert "projectSetRailStatus(sessions,peerIds,ackedTurns)" in app
    assert ".bucket-collapsed-badge.activity-attention" in css
    assert ".sidebar-project-bucket>header .bucket-chevron" in css
    # A Group record is not dated, so the date modes have to borrow a key from the member
    # that leads it rather than being refused, which is what kept Groups out of them before.
    assert "export function bucketStamp(" in sort
    assert "const oldestFirst = mode === 'created'" in sort


def test_pane_local_tab_rails_and_resizable_collapsible_sidebar_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")

    assert 'class="app-topbar"' in app
    assert 'class="app-identity"' in app
    workspace_at = app.index("<div class={`workspace")
    assert workspace_at < app.index('<header class="app-topbar">', workspace_at)
    assert 'class="top-workspace-tabs"' not in app
    assert 'className="stack-tabs" itemLabel="workspace tabs"' in app
    assert "role:'tablist','aria-label':'Workspace tabs'" in app
    assert "node.children.map(child=>" in app
    assert "mux.sidebar.width.v1" in app
    assert "mux.sidebar.collapsed.v1" in app
    assert 'class="sidebar-resizer"' in app
    assert ".workspace.sidebar-collapsed" in css
    assert ".pane-stack>.stack-tabs-rail>.stack-tabs{display:flex" in css
    # 34px at chrome scale 1; the row follows `--ui-scale` so the tab strip grows
    # with the tab titles in it (`features/ui.md`, Appearance → chrome scale).
    assert "grid-template-rows:calc(34px*var(--ui-scale)) minmax(0,1fr)" in css


def test_mobile_sidebars_use_the_full_selection_and_width_contract() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")

    select_project = app[
        app.index("const selectProject =") : app.index("const selectSession =")
    ]
    assert "setSidebarOpen(false)" in select_project
    assert select_project.index("setSidebarOpen(false)") < select_project.index("if(!remembered")
    # A fixed pixel cap made the drawer look closer to 80% on wide phones and small tablets.
    assert ".utility-drawer.overlay{" in css
    overlay_start = css.index(".utility-drawer.overlay{")
    overlay = css[overlay_start : css.index("}", overlay_start)]
    assert "width:90vw" in overlay
    assert "min(" not in overlay


def test_project_rows_reserve_empty_fold_and_hover_run_cells() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")

    assert "const hasSessions=children.length>0" in app
    assert 'class="project-chevron project-collapse-spacer"' in app
    assert ".project-row .project-collapse-spacer{" in css
    assert ".project-row-run{opacity:0;visibility:hidden;pointer-events:none" in css
    assert ".project-group:hover>.project-row .project-row-run" in css
    assert ".project-group:focus-within>.project-row .project-row-run" in css


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
    # One chip per provider, each identified by its own mark, narrowed to the weekly window
    # because a square has room for one number. The expanded sidebar keeps the full grid.
    assert "providerQuotaWindows" in accounts
    assert "const weekly=quotas[provider]?.weekly||null" in accounts
    assert "quotaGridSegments(quotas[provider])" in accounts
    # One chip builder shared by the collapsed rail and the mobile toolbar, so the
    # two surfaces cannot drift apart. Only the tooltip differs: the toolbar names every
    # window there, since a phone has no hover and draws only one of them.
    assert "const quotaChip=(provider:ProviderName,title:string)=>" in accounts
    assert "quotaChip(provider,weeklyTitle(provider))" in accounts
    assert "quotaChip(provider,toolbarTitle(provider))" in accounts
    assert "toolbar-quota" not in accounts and "toolbar-quota" not in css
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


def test_no_context_menu_reorders_or_reshapes_anything() -> None:
    """Split, stack, dissolve and move-tab are gone from the session and tab menus.

    On every source and every platform, mobile included. They used to render on the
    sidebar row, the tab, the pane's own ⋯ menu, and (as a rail permutation) the touch
    long-press menu, where the direction rows and their buttons pushed Rename and Kill
    past the fold in a menu opened to act on one session. Layout is now drag (direct
    manipulation) or the command palette, and the mobile rail is simply the projection's
    order.
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
        assert "directionRow('Move tab:'" not in menu
        assert "mobileMoveRow" not in menu
        assert "runNamedCommand('session.groupStack')" not in menu
        assert "runNamedCommand('stack.dissolve')" not in menu
        assert "runNamedCommand('session.customSplit')" not in menu
    # These existed only to serve those rows; nothing may reintroduce a caller without
    # also reintroducing the menu entry this test forbids.
    assert "splitExistingLeaf" not in app
    assert "moveMobileTabSlot" not in app
    assert app.count("moveTabDirection") == 2  # the definition, and the palette command
    # Removed from the menus, not from the app: these are the routes that keep them
    # usable, and each is bindable because it is a registry entry. pane.moveTab* was
    # added with this change so moving a tab between panes keeps a keyboard route.
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
    assert "id: `pane.moveTab${option.id[0].toUpperCase()}${option.id.slice(1)}`" in app


def test_projects_manager_and_shared_directional_tab_actions_are_wired() -> None:
    root = Path(__file__).parents[1]
    app = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    manager = (root / "frontend" / "src" / "ProjectsManager.tsx").read_text(encoding="utf-8")
    layout = (root / "frontend" / "src" / "layout.ts").read_text(encoding="utf-8")

    assert "sidebar_visible" in manager
    assert "Configured Projects keep their notes, files, settings, and history" in manager
    # The editor header names the Project once. The opaque short id it used to lead
    # with named nothing a human can act on: every selector a person or an agent uses
    # (sidebar, pickers, the MCP `project` argument) takes the name.
    assert "<span>PROJECT</span><h3>{selected.name}</h3>" in manager
    assert "selected.id.slice(0,8)" not in manager
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
    assert 'title="Manage Projects" onClick={()=>openProjectsManager()}' in app
    assert 'title="Projects" onClick={()=>openProjectsManager()}' in app
    # Only the resource menu still drives layout from a menu — a Project note or an
    # opened file has no tab to drag until it is already in a pane, so removing it there
    # would leave no way in. The session/tab menus lost theirs, Move tab included; see
    # test_no_desktop_context_menu_touches_the_pane_tree.
    assert "directionRow('Open in split:'" in app
    assert "directionRow('Move tab:'" not in app
    assert "splitNoteResource(noteMenu.resourceId" in app
    # Still the availability oracle for the direction rows and now for pane.moveTab*.
    assert "export function paneNeighborIds" in layout
    assert "paneNeighborIds(activeLayout, leaf.id)[option.id]" in app
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
