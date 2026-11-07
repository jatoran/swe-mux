from __future__ import annotations

import re
from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains


def test_recursive_layout_and_command_surfaces_are_wired() -> None:
    root = Path(__file__).parents[1]
    app = localized_source((root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    layout = localized_source((root / "frontend" / "src" / "layout.ts").read_text(encoding="utf-8"))

    assert source_contains(layout, "type PaneSplit")
    assert source_contains(layout, "setSplitRatio")
    assert source_contains(layout, "swapTerminals")
    assert source_contains(app, 'role="separator"')
    assert source_contains(app, "paneNeighborIds")
    # This used to assert `pane.swapNext` was ABSENT from App, which was true and was
    # the bug: the id was registered in `keybindings.py` the whole time, so it was
    # bindable, appeared in the shortcut editor, and did nothing when pressed.
    # Registered ids and implemented commands are now checked against each other in
    # `test_keybinding_registry.py`; what belongs here is that it is implemented.
    assert source_contains(app, "id: 'pane.swapNext'")
    # Scoring is gated on the palette being open, and the gate lives in `commands.ts`
    # so the renderer harness exercises the same function the app does.
    # `commands` became `scopedCommands` and `paletteQuery` became `paletteTerm` when
    # the palette gained its `@`/`#`/`:`/`>` scopes; the gate itself is unchanged and
    # still the thing being asserted.
    assert source_contains(app, "paletteResults(paletteOpen, scopedCommands, paletteTerm)")
    commands = localized_source(
        (root / "frontend" / "src" / "commands.ts").read_text(encoding="utf-8")
    )
    assert source_contains(commands, "return open ? searchCommands(commands, query) : NO_COMMANDS")


def test_normal_ui_flows_do_not_use_browser_native_dialogs() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    source = "\n".join(
        localized_source(path.read_text(encoding="utf-8")) for path in root.glob("*.ts*")
    )

    assert "alert(" not in source
    assert "confirm(" not in source
    # Exactly two deliberate exceptions, both in the note editor and both reached
    # only after the Clipboard API has already failed (an insecure mobile context,
    # where there is no other surface): the manual-copy fallback, and the paste
    # fallback for the same contexts in the other direction. Any third native
    # dialog is a regression — normal flows use in-app surfaces.
    assert source.count("prompt(") == 2
    editor = localized_source((root / "ProjectNoteEditor.tsx").read_text(encoding="utf-8"))
    assert editor.count("prompt(") == 2
    assert source_contains(editor, "window.prompt('Copy the text below:'")
    assert source_contains(editor, "window.prompt('Paste text:')")
    assert not source_contains(source, "Access token required")
    assert not source_contains(source, "mux.token")
    assert source_contains(source, "Create project")
    assert source_contains(source, "Select project folder")
    assert source_contains(source, "/api/fs/roots")
    assert source_contains(source, "/api/fs/list")
    assert source_contains(source, "folderNameFromPath(root)")
    assert source_contains(source, 'aria-label="Manage Projects"')
    assert source_contains(source, "project_files_changed")
    assert source_contains(source, "Select this folder")
    assert not source_contains(source, "Create worktree + terminal")


def test_daemon_and_ui_reload_controls_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))

    # Session-preserving reload surfaces: palette commands, main-menu entries,
    # the restart endpoint, and the blocking wait overlay.
    assert source_contains(app, "id: 'daemon.reload'") and source_contains(app, "id: 'ui.reload'")
    assert app.count("Reload daemon (keep sessions)") >= 2  # main menu + sidebar menu
    assert source_contains(app, "'/api/daemon/restart'")
    assert source_contains(app, "location.reload()")
    assert source_contains(app, "daemonReloading") and source_contains(app, "daemon-reload-layer")
    assert source_contains(css, ".daemon-reload-modal")


def test_terminal_find_is_inline_and_feature_complete() -> None:
    root = Path(__file__).parents[1]
    pane = localized_source(
        (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")
    )

    assert source_contains(pane, "terminal-find")
    assert source_contains(pane, "findNext")
    assert source_contains(pane, "findPrevious")
    assert source_contains(pane, "caseSensitive")
    assert source_contains(pane, "setFindResult(found ? 'match' : 'missing')")
    assert source_contains(pane, "findResult === 'missing' ? 'missing' : ''")
    assert source_contains(pane, "findResult === 'missing' ? 'no match'")


def test_terminal_clipboard_rail_and_selection_autocopy_are_wired() -> None:
    root = Path(__file__).parents[1]
    pane = localized_source(
        (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")
    )
    rail = localized_source(
        (root / "frontend" / "src" / "commandRail.ts").read_text(encoding="utf-8")
    )
    css = localized_source((root / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(pane, "terminal-action-rail${mobilePinnedSend?' mobile-pinned-send':''}")
    assert source_contains(pane, "Copy reply") and source_contains(pane, "manual-terminal-paste")
    assert source_contains(pane, "/last-reply")
    # Selection auto-copy runs a frame after the gesture ends, never inline: the platform
    # makes its own focus and selection decision as the tap resolves. The deferral is the
    # invariant, and it has survived two refactors of where the call sits - first into an
    # arrow when soft-keyboard restoration was ordered after the copy, then into
    # `endTouchGesture`'s `beforeRestore` hook when that whole sequence was shared with
    # `pointerCancel`. Assert the two halves that carry the invariant rather than any one
    # spelling of the call site: the pointer path hands the copy to the gesture-end
    # sequence, and that sequence runs it inside a frame, ahead of the keyboard repair.
    assert source_contains(pane, "const autoCopySelection=")
    assert source_contains(pane, "endTouchGesture(gesture,") and source_contains(
        pane, ",autoCopySelection)"
    )
    assert source_contains(pane, "requestAnimationFrame(()=>{\n        beforeRestore?.()\n")
    assert pane.index("beforeRestore?.()") < pane.index("restoreSoftKeyboard(restoreKeyboard,")
    assert source_contains(css, ".terminal-action-rail")
    # The rail also sends terminal keys and toggles the on-screen keyboard for read/select
    # mode; it overflows and scrolls horizontally rather than wrapping. The key/item
    # definitions themselves live in commandRail.ts (user-configurable rail).
    assert source_contains(pane, "sendKey") and source_contains(pane, "toggleKeyboard")
    assert (
        source_contains(rail, "\\x03")
        and source_contains(rail, "\\x1b[A")
        and source_contains(rail, "kbd-toggle")
    )
    assert source_contains(pane, "kbd-toggle")
    assert source_contains(css, "overflow-x:auto") and source_contains(
        css, ".terminal-action-rail .kbd-toggle"
    )


def test_successful_clipboard_writes_use_the_shared_interaction_hud() -> None:
    root = Path(__file__).parents[1]
    app = localized_source((root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    hud = localized_source(
        (root / "frontend" / "src" / "InteractionHud.tsx").read_text(encoding="utf-8")
    )
    clipboard = localized_source(
        (root / "frontend" / "src" / "clipboardHistory.ts").read_text(encoding="utf-8")
    )
    pane = localized_source(
        (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((root / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(clipboard, "CLIPBOARD_COPIED_EVENT = 'mux:clipboard-copied'")
    assert source_contains(clipboard, "write.then(() => announceClipboardCopy(text, 'copy')")
    assert source_contains(clipboard, "if (!event.defaultPrevented || suppliedText)")
    assert source_contains(
        hud, "window.addEventListener(CLIPBOARD_COPIED_EVENT, onClipboardCopied)"
    )
    assert source_contains(hud, "Copied to clipboard")
    assert source_contains(hud, 'class="interaction-hud"')
    assert source_contains(app, "<InteractionHud />")
    # Clipboard acknowledgement owns state below App. A copy or cut must not
    # re-render terminals, agent chats, or Continuity editors through the root.
    assert not source_contains(app, "setInteractionHud")
    assert not source_contains(app, "CLIPBOARD_COPIED_EVENT")
    assert source_contains(css, ".interaction-hud") and source_contains(css, "pointer-events:none")
    assert source_contains(css, "right:max(16px,calc(env(safe-area-inset-right) + 12px))")
    # The bottom-right corner it is pinned to is also where a maximised window puts the
    # terminal's command rail, so the offset carries the measured rail clearance.
    assert source_contains(
        css, "bottom:calc(max(16px,env(safe-area-inset-bottom) + 12px) + var(--rail-clearance))"
    )
    # Copy success has one visible owner. The rail keeps selection, paste, upload,
    # and recovery state but no longer duplicates the app-level confirmation.
    assert not source_contains(pane, "showClipboardStatus('Selection copied')")
    assert not source_contains(pane, "showClipboardStatus('Reply copied')")


def test_mobile_terminal_ime_streams_composition_without_xterm_overlay() -> None:
    root = Path(__file__).parents[1]
    pane = localized_source(
        (root / "frontend" / "src" / "TerminalPane.tsx").read_text(encoding="utf-8")
    )
    ime = localized_source(
        (root / "frontend" / "src" / "mobileTerminalIme.ts").read_text(encoding="utf-8")
    )
    css = localized_source((root / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(pane, 'class="mobile-terminal-live-input"')
    assert source_contains(pane, "mobileImeDelta(mobileInputValue,next,mobileLineBreak())")
    assert source_contains(pane, "term.input(data,true)")
    assert source_contains(ime, "TERMINAL_DELETE.repeat")
    assert source_contains(
        css, ".terminal-surface .xterm .composition-view{display:none!important}"
    )


def test_drag_reorder_keeps_native_source_mounted_and_commits_latest_preview() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))

    assert source_contains(app, "{node.children.map(child=>")
    assert source_contains(app, "dragProjectRef.current")
    assert source_contains(app, "dragStackTabRef.current")
    assert source_contains(
        app, "reorderTargetFromContainer(list,current.id,'vertical',pointer.clientY)"
    )
    assert source_contains(
        app, "reorderTargetFromContainer(tabStrip,current.childId,'horizontal',pointer.clientX)"
    )
    # A Project drag resolves its target list from the pointer, across the whole tree,
    # rather than being confined to the list it started in: the same gesture reorders
    # within a Group and moves a Project between Groups. Which list it landed in is read
    # off `data-group-id`, and the drop commits the Group change and the order together.
    assert source_contains(app, "beginProjectPointerDrag(event,project)")
    assert source_contains(app, "const list=projectListAt(tree,pointer.clientY)")
    assert source_contains(app, "const groupId=list.dataset.groupId||null")
    assert source_contains(app, "':scope > [data-group-id]'")
    assert source_contains(app, "commitProjectDrop(project,current)")
    assert source_contains(app, "'PATCH',`/api/projects/${project.id}`,{group_id:drop.groupId}")
    assert source_contains(app, "await commitProjectOrder(drop.previewIds)")
    # A list with no rows to sit beside — an empty Group, or a folded one — is a target in
    # its own right, outlined whole because there is no gap to draw a line in.
    assert source_contains(app, "showPointerDropIndicator(list,'drop-into')")
    assert source_contains(css, '.sidebar-project-list[data-pointer-drop-indicator="drop-into"]')
    assert source_contains(app, "showPointerDropIndicator(targetElement,`insert-${target.side}`)")
    assert source_contains(app, "data-reorder-id={project.id}")
    assert source_contains(app, "data-reorder-id={child.id}")
    assert source_contains(app, "moveLeafToStack")
    assert source_contains(app, "moveLeafToSplit")
    assert source_contains(app, "targetStackId")
    assert source_contains(app, "class={`project-group")
    assert source_contains(css, ".project-group.project-drop-target")
    assert source_contains(css, ".drop-before:after")
    assert source_contains(css, ".drop-after:after")
    assert source_contains(css, ".drop-zone-left")
    assert source_contains(css, ".drop-zone-tabs")

    # Both sidebar lists preview the landing position as a labelled outline of the dragged
    # row over the gap it would take, not as a line at the pointer.
    assert source_contains(app, "dropSlotForRow(targetSection,target.side,rowHeight,project.name)")
    assert source_contains(app, "dropSlotForRow(element,target.side,rowHeight,label)")
    assert source_contains(css, ".mux-drop-slot{")

    # A session drop resolves to a slot between rows or to grouping with one, and lands
    # exactly there rather than being appended to the target's pane.
    assert source_contains(app, "listDropTargetForPoint(")
    assert source_contains(app, "moveTerminalBeside(current,session.id,target.id,target.side)")
    assert source_contains(app, "groupTerminalsInStack(current,target.id,session.id)")

    # The gesture never leaves the Project it started in: only that Project's own session
    # list is consulted, and a release outside it commits nothing.
    assert source_contains(app, "querySelector<HTMLElement>(':scope > .session-list')")
    assert source_contains(app, "DROP_LIST_MARGIN")
    assert not source_contains(css, 'data-pointer-drop-indicator="invalid"')

    # Touch drags cancel touchmove themselves; without it a scrolling sidebar cancels the
    # pointer and a picked-up row goes nowhere.
    assert source_contains(
        app, "window.addEventListener('touchmove',blockTouchScroll,{passive:false})"
    )


def test_the_projects_header_owns_sort_and_fold_for_the_whole_tree() -> None:
    """One title, one sort, and one fold control above the tree.

    Sort was per bucket (a ⇅ on every Group header) on the theory that a hand-arranged
    shortlist and an alphabetical pile should coexist; nobody varied it, so it became a
    single mode applied to every bucket. Collapse-all is new: folding a long sidebar
    used to mean clicking every Project and every Group in turn.
    """
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))
    sort = localized_source((root / "projectSort.ts").read_text(encoding="utf-8"))

    # One mode for every bucket, read straight off the prefs.
    assert source_contains(app, "sortProjects(items,sidebarOrder.projectSort,recentProjectRanks)")
    assert source_contains(app, "setSidebarOrder(setProjectSortMode(sidebarOrder,option.id))")
    assert not source_contains(app, "bucketSortMode") and not source_contains(
        app, "setBucketSortMode"
    )
    assert not source_contains(sort, "bucketSortMode")
    # Placing a Project by hand is what returns Projects to Manual order.
    assert source_contains(app, "setSidebarOrder(setProjectSortMode(sidebarOrder,'custom'))")
    # PROJECTS names the whole tree and owns its controls; it is not a Group header.
    assert source_contains(app, 'class="sidebar-tools sidebar-projects-header"')
    assert source_contains(app, "<strong>PROJECTS</strong>")
    assert app.index('class="sidebar-tools sidebar-projects-header"') < app.index(
        'class="project-tree"'
    )
    assert source_contains(
        app, 'class="sidebar-project-list sidebar-ungrouped-projects" data-group-id=""'
    )
    # One control now, not two: Group placement rides the same mode, so there is no second
    # label to put in the button's title and no second mode to read for its active state.
    # `sectionSort` survives in projectSort.ts only as the migration that reads the retired
    # key off disk once — the type and its option list are gone.
    assert not source_contains(app, "sectionSort")
    assert not source_contains(sort, "SectionSortMode") and not source_contains(
        sort, "SECTION_SORT_OPTIONS"
    )
    assert source_contains(sort, "migrateSectionSort")
    assert source_contains(app, "${projectSortLabel(sidebarOrder.projectSort)}`}")
    assert source_contains(app, "setAllFolded(!allFolded)")
    assert source_contains(app, "setAllCollapsed(displayProjects.map(project=>project.id),folded)")
    assert source_contains(
        app, "setAllBucketsCollapsed(sidebarOrder,displayBuckets.map(bucket=>bucket.id),folded)"
    )
    # Fold-all flips to Expand only once nothing on screen is left to collapse.
    assert source_contains(app, "displayProjects.every(project=>collapsedProjects.has(project.id))")
    assert source_contains(
        app, "displayBuckets.every(bucket=>isBucketCollapsed(sidebarOrder,bucket.id))"
    )
    # Every Group renders, empty ones included: a Group nobody has filled yet is the one
    # that most needs to be on screen, because dragging a Project in is how it gets filled.
    assert not source_contains(app, "displayBuckets.filter(")
    assert not source_contains(app, "projectBuckets")
    # Fold and add/manage are icons, not glyphs. `⊞`/`⊟` is the box-drawing pair for a
    # tree *node*, which nobody reads as a bulk expand/collapse control.
    assert source_contains(app, "{allFolded?<UnfoldMoreIcon/>:<UnfoldLessIcon/>}")
    assert not source_contains(app, "⊞") and not source_contains(app, "⊟")
    assert source_contains(
        app, 'title="Manage Projects" onClick={()=>openProjectsManager()}><CogIcon/>'
    )
    assert source_contains(
        app, "onClick={()=>{openProjectsManager();void createProject()}}><PlusIcon/>"
    )
    # Four controls over content the user is reading: revealed on hover where there is a
    # hover to give, always visible on touch, and never `display`, which would reflow the row.
    assert source_contains(css, "@media (hover:hover) and (pointer:fine){")
    assert source_contains(css, ".sidebar-projects-header .sidebar-tool{opacity:0")
    assert source_contains(css, ".sidebar-projects-header:hover .sidebar-tool,")
    # The header doubles as the section's drag handle; its buttons keep the pointer.
    assert source_contains(app, "beginBucketPointerDrag(event,bucket.id,bucket.name)")
    assert source_contains(app, "data-reorder-id={bucket.id}")
    assert source_contains(app, "'PUT','/api/project-groups/order'")
    assert source_contains(css, ".sidebar-tools .sidebar-sort.active")
    assert source_contains(
        css, '.sidebar-project-bucket[data-pointer-drop-indicator="insert-before"]'
    )
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
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))

    # Right-click anywhere in the section that is not a row of its own, and hold on mobile,
    # which has no right-click to give.
    assert source_contains(app, "openGroupMenu(bucket.id,event.clientX,event.clientY)")
    assert source_contains(
        app, "beginLongPress(event,(x,y)=>{groupHeldRef.current=true;openGroupMenu(bucket.id,x,y)})"
    )
    assert source_contains(
        app, "if((event.target as Element).closest('.project-row,.session-row'))return"
    )
    # The hold fires under the finger, so the click it ends with must not fold the Group.
    assert source_contains(app, "if(groupHeldRef.current){groupHeldRef.current=false;return}")
    # Rename stays on the header as well; sort never comes back to it.
    assert source_contains(app, 'class="bucket-rename" title="Rename group"')
    assert not source_contains(app, 'class="bucket-sort"')

    # Delete is two clicks, and the second one is the only caller of the handler.
    assert source_contains(app, "const deleteGroup=")
    assert source_contains(app, "onClick={()=>setConfirmGroupDeleteId(group.id)}>Delete group…")
    assert source_contains(app, "onClick={()=>void deleteGroup(group)}")
    assert source_contains(app, "'DELETE',`/api/project-groups/${group.id}`")
    # Opening the menu clears any armed confirm, so it cannot be inherited by the next Group.
    assert source_contains(app, "setConfirmGroupDeleteId(null)\n    setGroupMenu({groupId,x,y})")
    # The Projects are what a user fears losing, so the confirm says they do not go.
    assert source_contains(app, "return to the root list.")
    assert source_contains(app, "No folder, session, layout, or history is touched.")
    # Menus are dismiss levels, not a special case of the sidebar's own menu.
    assert source_contains(
        app, "useDismissLevel(() => setGroupMenu(null), !!groupMenu, 'group-menu')"
    )


def test_groups_sort_in_among_root_projects_and_collapse_from_their_header() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))
    sort = localized_source((root / "projectSort.ts").read_text(encoding="utf-8"))

    # Group placement rides the one PROJECTS sort, as a peer of a root Project. As its own
    # setting it could only order Groups among Groups below the whole ungrouped pile, so no
    # mode could lift a Group for the work inside it — the bug this replaced.
    assert source_contains(
        app,
        "sortRootEntries(ungroupedProjects,allBuckets,sidebarOrder.projectSort,recentProjectRanks)",
    )
    assert source_contains(app, "const rootRows=sidebarRootRows(rootEntries)")
    assert source_contains(
        app, "rootEntries.flatMap(entry=>entry.kind==='group'?[entry.bucket]:[])"
    )
    # Reading order interleaves too, or the rail and the numbered shortcuts would disagree
    # with the tree they are drawn from.
    assert source_contains(
        app,
        "displayProjects=rootEntries.flatMap(entry=>entry.kind==='group'?entry.bucket.items:[entry.project])",
    )
    # Each run of root Projects between Groups is its own droppable list.
    assert source_contains(app, "rootRows.map(row=>{")
    assert source_contains(app, "if(row.kind==='root'){")
    assert source_contains(
        app, 'class="sidebar-project-list sidebar-ungrouped-projects" data-group-id=""'
    )
    assert source_contains(app, "key={row.key}")
    # Dragging a Group header returns the one sort to Manual, which is the two-tier tree.
    assert app.count("setSidebarOrder(setProjectSortMode(sidebarOrder,'custom'))") == 2
    # Click folds, drag reorders; the drag swallows the click it ends with.
    assert source_contains(app, "suppressDragClickRef.current===`bucket:${bucket.id}`")
    assert source_contains(app, "setSidebarOrder(toggleBucketCollapsed(sidebarOrder,bucket.id))")
    assert source_contains(
        app, "{!bucketCollapsed&&bucketItems.map(project=>sidebarProjectRow(project))}"
    )
    assert source_contains(
        app,
        "const bucketItems=bucket.items.filter(project=>!sidebarFilter"
        "||sidebarFilter.projects.has(project.id))",
    )
    # An emptied Group keeps its section and says what to do with it — that hint is also
    # the drop target, since a header alone is too thin a strip to aim a dragged row at.
    # The hint is suppressed while a filter is up: it is also the drop target, and every
    # sidebar drag is inert against a tree that is missing rows.
    assert source_contains(
        app,
        '{!bucketCollapsed&&!bucketItems.length&&!sidebarFilter&&<p class="project-list-empty">'
        "Drag a Project here</p>}",
    )
    assert source_contains(css, ".project-list-empty{")
    # A folded Group reports live count *and* the strongest agent state, or an
    # approval waiting inside it would be invisible.
    assert source_contains(app, "projectSetRailStatus(sessions,peerIds,ackedTurns)")
    assert source_contains(css, ".bucket-collapsed-badge.activity-attention")
    assert source_contains(css, ".sidebar-project-bucket>header .bucket-chevron")
    # A Group record is not dated, so the date modes have to borrow a key from the member
    # that leads it rather than being refused, which is what kept Groups out of them before.
    assert source_contains(sort, "export function bucketStamp(")
    assert source_contains(sort, "const oldestFirst = mode === 'created'")


def test_pane_local_tab_rails_and_resizable_collapsible_sidebar_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))
    sidebar_resize = localized_source((root / "sidebarResize.ts").read_text(encoding="utf-8"))

    assert source_contains(app, 'class="app-topbar"')
    assert source_contains(app, 'class="app-identity"')
    workspace_at = app.index("<div class={`workspace")
    assert workspace_at < app.index('<header class="app-topbar">', workspace_at)
    assert not source_contains(app, 'class="top-workspace-tabs"')
    assert source_contains(app, 'className="stack-tabs" wrapperClassName="stack-tabs-rail"')
    assert source_contains(app, "role:'tablist','aria-label':'Workspace tabs'")
    assert source_contains(app, "node.children.map(child=>")
    assert source_contains(sidebar_resize, "SIDEBAR_WIDTH_KEY = 'mux.sidebar.width.v1'")
    assert source_contains(app, "localStorage.getItem(SIDEBAR_WIDTH_KEY)")
    assert source_contains(app, "localStorage.setItem(SIDEBAR_WIDTH_KEY")
    assert source_contains(app, "mux.sidebar.collapsed.v1")
    assert source_contains(app, 'class="sidebar-resizer"')
    assert source_contains(css, ".workspace.sidebar-collapsed")
    assert source_contains(css, ".pane-stack>.stack-tabs-rail>.stack-tabs{display:flex")
    # 34px at chrome scale 1; the row follows `--ui-scale` so the tab strip grows
    # with the tab titles in it (`features/ui.md`, Appearance → chrome scale).
    assert source_contains(css, "grid-template-rows:calc(34px*var(--ui-scale)) minmax(0,1fr)")


def test_mobile_sidebars_use_the_full_selection_and_width_contract() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))

    select_project = app[app.index("const selectProject =") : app.index("const selectSession =")]
    assert source_contains(select_project, "setSidebarOpen(false)")
    assert select_project.index("setSidebarOpen(false)") < select_project.index("if(!remembered")
    # A fixed pixel cap made the drawer look closer to 80% on wide phones and small tablets.
    assert source_contains(css, ".utility-drawer.overlay{")
    overlay_start = css.index(".utility-drawer.overlay{")
    overlay = css[overlay_start : css.index("}", overlay_start)]
    assert source_contains(overlay, "width:90vw")
    assert not source_contains(overlay, "min(")


def test_project_rows_reserve_empty_fold_and_always_show_their_controls() -> None:
    """The row's options and Run used to wait for a hover; the operator asked to see them."""
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))

    assert source_contains(app, "const hasSessions=children.length>0")
    assert source_contains(app, 'class="project-chevron project-collapse-spacer"')
    assert source_contains(css, ".project-row .project-collapse-spacer{")
    assert source_contains(css, ".project-row-menu{display:grid!important")
    assert source_contains(css, ".project-row-run{display:grid!important")
    assert not source_contains(css, ".project-row-run{opacity:0")
    assert not source_contains(css, ".project-row-menu{display:none")


def test_collapsed_sidebar_rail_keeps_sidebar_controls_reachable() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))
    accounts = localized_source((root / "ProviderAccounts.tsx").read_text(encoding="utf-8"))
    resources = localized_source((root / "ResourceUsage.tsx").read_text(encoding="utf-8"))

    assert source_contains(app, 'sidebarCollapsed&&<nav class="sidebar-rail"')
    assert source_contains(app, 'title="Menu"') and source_contains(app, 'title="Projects"')
    assert source_contains(app, "<ResourceUsageSummary compact")
    assert source_contains(app, '<AccountSwitcher variant="rail" placement="up"')
    assert source_contains(css, ".sidebar-rail{")
    assert source_contains(css, ".rail-status{")

    # Status sits above the actions, which stay pinned at the very bottom.
    assert app.index('<div class="rail-status">') < app.index('title="Menu"')
    assert app.index('title="Menu"') < app.index('title="Projects"')
    assert source_contains(css, ".rail-status{margin-top:auto")

    # The rail resets the mobile trigger's 42px floor, or it overflows the strip.
    assert source_contains(css, "min-width:28px")
    # Popover direction is independent of the condensed trigger so the rail, which
    # sits at the bottom of the window, still opens upward.
    assert source_contains(accounts, "const opensDown=placement?placement==='down':compact")
    # One chip per provider, each identified by its own mark, narrowed to the weekly window
    # because a square has room for one number. The expanded sidebar keeps the full grid.
    assert source_contains(accounts, "providerQuotaWindows")
    assert source_contains(accounts, "const weekly=quotas[provider]?.weekly||null")
    assert source_contains(accounts, "quotaGridSegments(quotas[provider])")
    # One chip builder shared by the collapsed rail and the mobile toolbar, so the
    # two surfaces cannot drift apart. Only the tooltip differs: the toolbar names every
    # window there, since a phone has no hover and draws only one of them.
    assert source_contains(accounts, "const quotaChip=(provider:ProviderName,title:string)=>")
    assert source_contains(accounts, "quotaChip(provider,weeklyTitle(provider))")
    assert source_contains(accounts, "quotaChip(provider,toolbarTitle(provider))")
    assert not source_contains(accounts, "toolbar-quota") and not source_contains(
        css, "toolbar-quota"
    )
    assert source_contains(accounts, "harnessMark(provider)")
    assert source_contains(css, ".rail-quota .provider-glyph")
    # RAM, not CPU: a fluctuating percentage is not worth a permanent glance.
    assert source_contains(resources, "compactMemoryLabel(combined.memory_bytes)")
    assert source_contains(resources, "resource-usage-compact")


def test_workspace_tabs_have_inline_close_controls_with_terminal_confirmation() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "style.css").read_text(encoding="utf-8"))

    assert source_contains(app, "const closeTab=(child:PaneLeaf,label:string,session?:Session)=>")
    assert source_contains(
        app, "if(terminal){if(session&&!session.pending)requestKill(session);return}"
    )
    assert source_contains(
        app, "if(child.kind==='note'){void removeWorkspaceNote(projectId,child.id);return}"
    )
    assert source_contains(app, "removeLeaf(latest,child.kind,child.id)")
    assert source_contains(app, "{confirming?'✓':'×'}</button>")
    assert source_contains(app, "terminal&&(!session||!!session.pending)")
    # The close control floats over the tab's right edge rather than holding a
    # column, so showing it never changes tab width.
    assert source_contains(css, ".stack-tab-shell>.tab-close{position:absolute")
    assert source_contains(css, "@media(hover:hover){.stack-tab-shell:hover>.tab-close")
    assert source_contains(css, ".stack-tab-shell>.tab-close.confirming")
    # Touch never gets it: closing there is the tab's long-press menu, so the
    # mobile tab renders the tab button alone.
    assert source_contains(css, "@media(hover:none){.stack-tab-shell>.tab-close{display:none}}")
    assert source_contains(app, 'class="stack-tab-shell mobile-unified-tab"')
    mobile_tab_body = app.split("const mobileTab=")[1].split("const mobileUnifiedWorkspace=")[0]
    assert not source_contains(mobile_tab_body, "tab-close")


def test_session_tab_context_menu_omits_redundant_focus_and_detach_actions() -> None:
    app = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    )

    assert source_contains(app, "source: 'sidebar'|'tab'|'pane'")
    assert source_contains(app, "openSessionMenu(session,event.clientX,event.clientY,'tab')")
    # `Open in focused pane` is gone from the menu on every source, sidebar included.
    # It was the sidebar's own compensation for a menu opened *instead of* clicking the
    # row - but clicking the row is what opens the session, from the same list the menu
    # was opened on, so the row only said so a second time. The palette keeps
    # `session.open`, which is what makes it bindable.
    session_menu = app[app.index("{contextMenu &&") : app.index("{projectMenu &&")]
    assert not source_contains(session_menu, "runNamedCommand('session.open')")
    assert source_contains(app, "{ id: 'session.open'")
    # Scoped to the menu rather than to the whole file. `pane.detach` staying out of
    # this menu is the contract; the command existing is not only allowed but
    # required - `TerminalPane` has a "Detach pane" button that calls it, and for as
    # long as nothing implemented it that button silently did nothing.
    assert not source_contains(session_menu, "runNamedCommand('pane.detach')")
    assert source_contains(app, "id: 'pane.detach'")


def test_no_context_menu_reorders_or_reshapes_anything() -> None:
    """Split, stack, dissolve and move-tab are gone from the session and tab menus.

    On every source and every platform, mobile included. They used to render on the
    sidebar row, the tab, the pane's own ⋯ menu, and (as a rail permutation) the touch
    long-press menu, where the direction rows and their buttons pushed Rename and Kill
    past the fold in a menu opened to act on one session. Layout is now drag (direct
    manipulation) or the command palette, and the mobile rail is simply the projection's
    order.
    """
    app = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    )
    session_menu = app[app.index("{contextMenu &&") : app.index("{projectMenu &&")]
    tab_menu = app[app.index("{tabMenu &&") : app.index("Close tab</button>")]

    for menu in (session_menu, tab_menu):
        assert not source_contains(menu, "Open in split:")
        assert not source_contains(menu, "New terminal in split:")
        assert not source_contains(menu, "Stack with focused terminal")
        assert not source_contains(menu, "Dissolve tab stack into splits")
        assert not source_contains(menu, "New terminal custom in split")
        assert not source_contains(menu, "directionRow('Move tab:'")
        assert not source_contains(menu, "mobileMoveRow")
        assert not source_contains(menu, "runNamedCommand('session.groupStack')")
        assert not source_contains(menu, "runNamedCommand('stack.dissolve')")
        assert not source_contains(menu, "runNamedCommand('session.customSplit')")
    # These existed only to serve those rows; nothing may reintroduce a caller without
    # also reintroducing the menu entry this test forbids.
    assert not source_contains(app, "splitExistingLeaf")
    assert not source_contains(app, "moveMobileTabSlot")
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
    assert source_contains(
        app, "id: `pane.moveTab${option.id[0].toUpperCase()}${option.id.slice(1)}`"
    )


def test_projects_manager_and_shared_directional_tab_actions_are_wired() -> None:
    root = Path(__file__).parents[1]
    app = localized_source((root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    manager = localized_source(
        (root / "frontend" / "src" / "ProjectsManager.tsx").read_text(encoding="utf-8")
    )
    creator = localized_source(
        (root / "frontend" / "src" / "ProjectCreateDialog.tsx").read_text(encoding="utf-8")
    )
    layout = localized_source((root / "frontend" / "src" / "layout.ts").read_text(encoding="utf-8"))

    assert source_contains(manager, "sidebar_visible")
    assert source_contains(
        manager, "Configured Projects keep their notes, files, settings, and history"
    )
    # The editor header names the Project once. The opaque short id it used to lead
    # with named nothing a human can act on: every selector a person or an agent uses
    # (sidebar, pickers, the MCP `project` argument) takes the name.
    assert source_contains(manager, "<span>PROJECT</span><h3>{selected.name}</h3>")
    assert not source_contains(manager, "selected.id.slice(0,8)")
    assert source_contains(
        app,
        "visibleProjects = orderedProjects.filter(project => project.sidebar_visible !== false)",
    )
    # Project creation owns its dialog; Group creation still lives in App.
    assert source_contains(app, "projectCreateOpen&&<ProjectCreateDialog")
    assert app.count('class="modal-layer project-registry-dialog-layer"') == 1
    assert creator.count('class="modal-layer project-registry-dialog-layer"') == 1
    # Adding a Project is reachable in one step from the empty-sidebar menu, above
    # the registry entry, rather than only through the registry.
    assert source_contains(app, "id: 'project.add'")
    # Each sidebar-menu row is an icon plus its own label element, so the label is
    # asserted apart from the handler rather than as one contiguous string.
    assert source_contains(app, "runNamedCommand('project.add')}}><span class=\"menu-row-icon\"")
    assert source_contains(app, '<span class="menu-row-label">Add project…</span>')
    assert app.index("runNamedCommand('project.add')") < app.index(
        "runNamedCommand('project.create')}}>"
    )
    # Ordering, not literal depths: the exact values must stay free to move above
    # persistent chrome. test_dialog_layers_stack_above_persistent_chrome owns the
    # full invariant.
    style = localized_source((root / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))
    depths = {
        name: int(value)
        for name, value in re.findall(r"\.([a-z-]+)\s*\{[^}]*?z-index:(\d+)", style)
    }
    assert depths["project-registry-dialog-layer"] > depths["projects-manager-layer"]
    # openProjectsManager takes an optional focus (which Project, which tab), so
    # every plain trigger must call it rather than hand it the click event.
    assert source_contains(app, 'title="Manage Projects" onClick={()=>openProjectsManager()}')
    assert source_contains(app, 'title="Projects" onClick={()=>openProjectsManager()}')
    # Only the resource menu still drives layout from a menu — a Project note or an
    # opened file has no tab to drag until it is already in a pane, so removing it there
    # would leave no way in. The session/tab menus lost theirs, Move tab included; see
    # test_no_desktop_context_menu_touches_the_pane_tree.
    assert source_contains(app, "directionRow('Open in split:'")
    assert not source_contains(app, "directionRow('Move tab:'")
    assert source_contains(app, "splitNoteResource(noteMenu.resourceId")
    # Still the availability oracle for the direction rows and now for pane.moveTab*.
    assert source_contains(layout, "export function paneNeighborIds")
    assert source_contains(app, "paneNeighborIds(activeLayout, leaf.id)[option.id]")
    assert not source_contains(app, "Swap pane with next")
    assert not source_contains(
        app[app.index("{contextMenu &&") : app.index("{projectMenu &&")],
        "Open project note…</button>",
    )


def test_event_stream_resumes_from_the_last_seen_sequence() -> None:
    """Reconnect catch-up is only meaningful with a cursor.

    Without one the daemon cannot know what this client missed, and its no-cursor
    default served the oldest retained page — history the client already had, and
    none of the gap.
    """
    app = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    )

    assert source_contains(app, "lastEventSeq")
    assert source_contains(app, "?after_seq=${lastEventSeq.current}")
    assert source_contains(app, "openWebSocket(`/events${resume}`)")
    # A gap wider than the replay window must trigger a full refresh, not be
    # mistaken for "caught up".
    assert source_contains(app, "'events_gap'")


def test_layout_refresh_defers_to_an_in_flight_layout_write() -> None:
    """A GET snapshotted before a layout PATCH committed must not clobber it.

    Overwriting optimistic state snapped a just-dropped tab back, and a second
    drag in that window based itself on the clobbered layout and then won the
    write — silently reverting the first move for every client.

    Both halves of the rule now live behind `layoutWriter.hasPendingWrite`: the
    revision is not adopted (`adoptRevisions`) and the layout is not reconciled
    (the planner's `hasPendingLayoutWrite`).
    """
    frontend = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((frontend / "App.tsx").read_text(encoding="utf-8"))
    writer = localized_source((frontend / "layoutWriter.ts").read_text(encoding="utf-8"))

    assert source_contains(app, "layoutWriter.adoptRevisions(nextProjects)")
    assert source_contains(app, "hasPendingLayoutWrite: layoutWriter.hasPendingWrite")
    assert source_contains(writer, "if (chains[project.id] !== undefined) continue")
    assert source_contains(writer, "hasPendingWrite: projectId => chains[projectId] !== undefined")


def test_terminal_pane_clears_per_session_state_on_a_session_switch() -> None:
    """The pane is reused unkeyed across stack-tab switches.

    Without an explicit reset, "Copy reply" copies (and records into clipboard
    history) the previous session's reply.
    """
    pane = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "TerminalPane.tsx").read_text(
            encoding="utf-8"
        )
    )

    reset = pane[pane.index("    exitArrange()") : pane.index("},[session.id])")]
    for setter in (
        "setPreparedClipboard('')",
        "setManualClipboard(false)",
        "setClipboardStatus('')",
        "setFindQuery('')",
        "setFindResult('')",
    ):
        assert setter in reset, setter
    # The toast carries the selection readout as well as the copy and paste confirmations,
    # so its pending dismissal is part of the reset: left running, the outgoing session's
    # timer blanks a message the incoming one has just put up.
    assert source_contains(reset, "clipboardStatusTimerRef.current=null")
    assert source_contains(reset, "clipboardStatusKindRef.current='clipboard'")
    # Copy has no text cache now. Its request guard sees identity changes on
    # render, before the session-switch effect runs; async races are tested in
    # frontend/test/replyCopy.test.ts.
    assert source_contains(pane, "replyCopyRef.current.observe(session)")
    assert source_contains(pane, "replyCopyRef.current.load(session")
    assert not source_contains(pane, "setLastReply")
    # A renderer change has to reach panes whose other props are stable. The memo moved
    # to its own module so its rules could be unit-tested rather than only grepped
    # (`frontend/test/terminalPaneInputBackend.test.ts`); the wiring assertion follows it.
    memo = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "terminalPaneMemo.ts").read_text(
            encoding="utf-8"
        )
    )
    assert source_contains(memo, "a.rendererPreference === b.rendererPreference")
    assert source_contains(pane, "memo(TerminalPaneImpl, terminalPanePropsEqual)")
