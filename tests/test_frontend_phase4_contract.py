from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def source(name: str) -> str:
    return (ROOT / "frontend" / "src" / name).read_text(encoding="utf-8")


def frontend_source() -> str:
    root = ROOT / "frontend" / "src"
    return "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.ts*"))


def test_mobile_workspace_and_recovery_contracts_remain_available() -> None:
    combined = frontend_source()
    css = source("style.css")
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "mobile-project-name" in combined
    assert "mobile-session-switcher" not in combined
    assert "mobile-new-session" not in combined
    assert ".pane-stack>.stack-tabs-rail>.stack-tabs{display:flex;flex-wrap:nowrap" in css
    assert "mobileWorkspaceProjection" in combined
    assert "mobile-unified-tabs" in combined
    assert (
        ".pane-stack:not(.focused-pane):not(.empty-workspace-pane):not(.mobile-unified-workspace){display:none}"
        in css
    )
    # Mobile menus carry no geometry or ordering controls at all now — the touch
    # `Move tab` row went the way of the desktop ones.
    # test_no_context_menu_reorders_or_reshapes_anything owns that invariant.
    assert "contextMenu.source!=='mobile'" not in combined
    assert "mobileMoveRow" not in combined
    assert "tabMenu.source==='mobile'" in combined
    # The device-local rail permutation went with the row that was its only writer.
    # Orphaning it would have been worse than removing it: a phone that had already
    # saved an order would have stayed pinned to it, with no surface able to change
    # or clear it. Rail order is the layout projection, full stop.
    assert "MOBILE_TAB_ORDER_KEY" not in combined
    assert "mux.mobileTabOrder.v1" not in combined
    assert not (ROOT / "frontend" / "src" / "mobileTabOrder.ts").exists()
    assert "orderMobileTabs" not in combined
    assert "savedOrder" not in combined
    assert "beginWorkspaceTabDrag" in combined
    assert "beginProjectPointerDrag" in combined
    assert "beginSessionPointerDrag" in combined
    assert "showPointerDropIndicator" in combined
    assert 'data-pointer-drop-indicator="split-bottom"' in css
    assert 'data-pointer-drop-indicator="tab-bar"' in css
    assert 'data-pointer-drop-indicator="group-session"' in css
    assert "draggable=" not in source("App.tsx")
    assert "onDragStart=" not in source("App.tsx")
    assert "clipboardImage(Array.from(event.clipboardData.items))" in combined
    assert "host.current.addEventListener('drop', drop)" in combined
    # The soft keyboard overlays the mobile layout and never resizes it. Under the old
    # `resizes-content` the keyboard shrank the layout viewport, which refitted every
    # terminal and resized the real PTY, and shrinking an alternate-screen PTY discards
    # the rows that no longer fit, so every keyboard open permanently ate part of the
    # conversation. Asserted in both directions: the replacement has to be there, and the
    # value that destroyed conversations must not come back. Read off the meta tag rather
    # than the file, which names the abandoned value in the comment that explains it.
    # (`ui.md` §soft keyboard.)
    viewport = re.search(r'<meta name="viewport" content="([^"]*)"', index)
    assert viewport is not None
    assert "interactive-widget=resizes-visual" in viewport.group(1)
    assert "resizes-content" not in viewport.group(1)
    assert "height:var(--app-height,100dvh)" in css
    assert "@media(max-width:760px)" in css
    assert "mux.focus.v1" in combined
    assert "window.addEventListener('pageshow'" in combined
    assert "scheduleFullRedraw()" in combined


def test_projects_are_explicit_session_owners() -> None:
    app = source("App.tsx")
    manager = source("ProjectsManager.tsx")
    types = source("types.ts")

    assert "No Projects shown" in app
    assert "Add project" in manager
    assert "sidebar_visible" in manager
    assert "project_id: targetProject" in app
    assert "Project root<input" in app and "readOnly" in app
    assert "session.project_id" in app
    assert "space_id" not in app
    assert "interface Project" in types
    assert "root:string" in types


def test_project_groups_are_sidebar_only_organization() -> None:
    app = source("App.tsx")
    groups = source("projectGroups.ts")

    assert "/api/project-groups" in app
    assert "Create group" in app
    assert "Ungrouped" in app
    assert "group_id" in app
    assert "buildProjectGroups" in groups
    assert "layout" not in groups


def test_project_resources_share_unified_mixed_view_panes() -> None:
    app = source("App.tsx")
    layout = source("layout.ts")
    resource = source("ProjectResource.tsx")
    css = source("style.css")

    assert "openNotesBrowser" in app
    assert "openBrowsedNote" in app
    assert "openProjectFiles" in app
    assert "openProjectFile" in app
    assert "ProjectResource" in app
    assert "openTab" in app
    assert "splitNoteResource" in app
    assert "moveLeafToSplit" in app
    assert "version: 7" in layout
    assert "showNoteWorkspace" not in app
    assert not (ROOT / "frontend" / "src" / "NotesWorkspace.tsx").exists()
    assert "/api/projects/${project.id}/notes/${encodeURIComponent(resource.id)}" in resource
    assert "/api/projects/${project.id}/files" in resource
    assert "/api/projects/${project.id}/file" in resource
    assert "onOpenFile" in resource
    assert "/api/projects/${project.id}/reveal" in resource
    assert "/api/projects/${project.id}/ignore" in resource
    assert "Open in default explorer" in resource
    assert "Add pattern to global ignores" in resource
    assert "Add pattern to project ignores" in resource
    assert ".project-resource" in css
    assert ".pane-stack.tab-drop-active" in css
    assert ".notes-workspace-shell.popout" not in css


def test_continuity_find_is_available_from_the_shared_resource_header() -> None:
    resource = source("ProjectResource.tsx")
    drawer = source("UtilityDrawer.tsx")

    assert 'aria-label="Find in this note"' in resource
    assert "onClick={openFind}>⌕</button>" in resource
    assert "autosaved||onSendToAgent" in resource
    # Drawer notes mount the same resource component as workspace tabs, so the
    # header trigger cannot drift between the two surfaces.
    assert "drawer-note:" in drawer
    assert "<ProjectResource" in drawer


def test_note_resource_header_keeps_identity_and_save_state_on_one_row() -> None:
    resource = source("ProjectResource.tsx")
    css = source("style.css")

    assert "class={isNote?'note-resource-heading':undefined}" in resource
    assert resource.count('class="note-resource-separator"') == 2
    assert 'class="note-resource-state">{stateLabel}</span>' in resource
    assert ".project-resource>header>.note-resource-heading{display:flex" in css


def test_note_selection_can_be_consumed_only_after_an_accepted_agent_handoff() -> None:
    resource = source("ProjectResource.tsx")
    picker = source("SendToAgentPicker.tsx")

    assert "isNote&&selected&&snapshot&&!slice.truncated" in resource
    assert "removeSelectionAfterSend" in resource
    assert "const [removeSelection, setRemoveSelection]" in picker
    assert "checked={removeSelection}" in picker
    assert picker.count("finishAccepted()") == 2
    assert "if (result.status === 'done')" in picker


def test_notes_are_decoupled_from_terminals_and_open_in_the_anchor_pane() -> None:
    app = source("App.tsx")
    layout = source("layout.ts")
    assert "note-chip" not in app
    assert "noteChipState" not in app
    assert "openSessionNotes" not in app
    assert "session.note" not in app

    # Opening a resource is not a layout command. Every kind lands as a tab in the anchor's
    # pane; nothing splits the workspace on the user's behalf.
    assert "openTab(current,focused,resourceLeaf('note',resourceId))" in app
    # (`layout.ts` still names the retired helper in a comment saying why it is gone.)
    assert "export function placeCompanionLeaf" not in layout
    assert "placeCompanionLeaf(" not in app
    assert "target.kind==='session-note'&&!targetViewId" not in app


def test_notes_tab_manages_the_project_owned_collection() -> None:
    app = source("App.tsx")
    notes = source("NotesTab.tsx")
    drawer = source("UtilityDrawer.tsx")
    css = source("style.css")

    assert "/api/notes" in notes
    assert "/api/projects/${project.id}/notes" in notes
    assert "project_id=${encodeURIComponent(scopeId)}" in notes
    assert "Filter notes by project" in notes
    assert "Search notes" in notes
    assert "All projects" in notes
    assert ".project-note-row" in css and ".notes-subtabs" in css
    assert 'class="notes-new"' in notes
    assert "mode:'rename'" in notes
    assert "onOpenNote" in drawer
    assert 'role="tab"' in notes
    assert "SCRATCHPAD_TAB_ID" in notes
    assert "stableProjectNoteTabs" in notes
    assert 'class="notes-browser"' in notes
    assert "onOpenScratchpad" in drawer
    assert "kind:'global-note',resourceId:'scratchpad'" in app
    assert "id: 'notes.scratchpad'" in app

    # The retired modal is gone, and its three entry points now open the drawer tab.
    assert not (ROOT / "frontend" / "src" / "SessionNotesBrowser.tsx").exists()
    assert "SessionNotesBrowser" not in app
    assert "case 'notes':" in drawer
    assert "openNotesBrowser" in app
    assert "id: 'notes.browse'" in app
    assert "runNamedCommand('notes.browse')" in app
    assert "openBrowsedNote" in app

    # The note selection is remembered separately from temporary editor ownership. Closing
    # the drawer leaves the per-Project selection intact; the editor host is merely unmounted.
    set_open = app[app.index("const setClipboardOpen=") : app.index("const selectDrawerTab=")]
    assert "releaseDrawerNote" not in set_open
    assert "selectedResourceId={props.drawerNoteId}" in drawer
    assert "selected !== 'notes' && renderBody(selected)" in drawer

    # Scope follows how you arrived. Every scope-less entry point (rail, strip, drawer.notes)
    # goes through showDrawerTab and means "this Project"; only the app menu's unscoped
    # notes.browse widens it, via openNotesBrowser, which is not on that path.
    assert "if(tab==='notes')setNotesAllProjects(false)" in app
    assert "setNotesAllProjects(!scope)" in app
    show_tab = app[app.index("const showDrawerTab=") : app.index("const openDrawerTab=")]
    open_tab = app[app.index("const openDrawerTab=") : app.index("const persistDrawerWidth=")]
    assert "setNotesAllProjects" not in open_tab
    assert "setNotesAllProjects(false)" in show_tab


def test_files_is_a_drawer_navigator_rather_than_a_workspace_tab() -> None:
    """Files opens documents into panes, so it costs a drawer tab, not a permanent one.

    The layout used to carry a Files leaf and route every placement rule around it. That
    special-casing is gone with the leaf: a persisted `files:` leaf is pruned on read.
    """
    app = source("App.tsx")
    layout = source("layout.ts")
    drawer = source("UtilityDrawer.tsx")
    tabs = source("drawerTabs.ts")

    assert "'files'" in tabs and "'notes'" in tabs
    assert "case 'files':" in drawer
    assert "resource={{ kind: 'files', id: project.id }}" in drawer
    # Opened, never toggled: a click that names a surface and switches Project must not
    # close the panel it was asking for.
    assert "openDrawerTab('files',project.id)" in app
    assert "openDrawerTab('notes'," in app

    # No Files leaf, and no placement rule that has to dodge one.
    assert "isRetiredFilesLeaf" in layout
    for retired in ("stackHasFiles", "isFilesLeaf", "defaultProjectLayout", "DEFAULT_FILES_RATIO"):
        assert retired not in layout, retired
        assert retired not in app, retired
    # A never-arranged Project is left empty rather than seeded with a Files column.
    assert "seededLayouts" not in app

    # Dragging a file row onto a pane survives the move, but only on desktop: the mobile
    # drawer is an overlay with no visible pane to drop onto.
    assert "onFileDragStart={mobileWorkspace?undefined:" in app


def test_drawer_tabs_support_icon_and_title_modes_from_one_registry() -> None:
    """Every pane and launcher renders one configured mark from the shared registry.

    `drawerTabs.ts` stays JSX-free so it can be unit-tested under plain type-stripping, which
    is why the icon map lives in `railIcons.tsx` and why this cross-file invariant — every tab
    id has a mark — is checked here rather than there.
    """
    tabs = source("drawerTabs.ts")
    icons = source("railIcons.tsx")
    drawer = source("UtilityDrawer.tsx")
    app = source("App.tsx")
    css = source("style.css")
    rail = source("RailScroller.tsx")

    # Bumped deliberately, not incidentally. Six *labelled* tabs measured ~444px, which
    # overflowed a phone drawer (`min(430px, 92vw)`) into a scrollbar-less scroller that
    # silently parked the last two off-screen. Adding a tab means re-checking that on a
    # phone, which is what this assertion is for — it is a prompt, not a cap.
    #
    # Re-checked at nine (Context): nine 36px touch cells do not fit on a 360px phone.
    # Wrapping made the rail jump to two rows, so the tablist scrolls on one row behind a
    # fade and selection reveals the selected item.
    #
    # Re-checked at ten (Transcript): the tenth cell overflows the same 360px row the ninth
    # already did, and lands in the same scroller, which is the arrangement that made the
    # count stop mattering. What still has to hold is the machinery below it — one row, a
    # fade that says there is more, and scrollIntoView on selection — so a tab is never
    # silently off-screen with nothing to say so.
    #
    # Re-checked at eleven (Processes): same answer as ten, and for the same reason — the
    # eleventh cell is one more 36px stop in a scroller that was already scrolling, so nothing
    # about the header changes. The question a new tab has to pass is therefore no longer
    # "does it fit" but "does this surface belong beside a terminal"; the assertions below are
    # what keep the scroller honest while that stays the actual bar.
    #
    # Re-checked at twelve (Mailbox): it is an application-wide narrow review surface and
    # uses the existing one-row scroller without changing its accessibility contract.
    #
    # Re-checked at thirteen (Agent): it is a session-scoped compact disclosure surface and
    # uses the same one-row scroller and selected-tab reveal contract.
    #
    # Back to twelve: Mailbox left the rail entirely and became the fleet-queue modal. It
    # never answered "does this surface belong beside a terminal" — it has no send button,
    # so nothing in it needed the terminal on screen, and it read as a duplicate of the
    # Queue tab three cells up. A count going *down* is the healthy direction here.
    ids = re.findall(r"\{ id: '([a-z]+)'", tabs)
    assert len(ids) == 12, ids
    tab_css = css[css.index(".drawer-tabs{") : css.index(".drawer-tabs::")]
    assert "flex-wrap:nowrap" in tab_css and "overflow-x:auto" in tab_css
    assert "drawer-chrome" not in drawer
    assert ".drawer-tabs button{position:relative;min-height:34px;flex:1 0 32px" in css
    assert ".drawer-tabs button{min-height:44px;flex-basis:36px;min-width:36px" in css
    assert "activeKey={selected}" in drawer
    assert "querySelector<HTMLElement>('[role=\"tab\"][aria-selected=\"true\"]')" in rail
    assert "revealItem(strip, selected)" in rail
    icon_map = icons[icons.index("DRAWER_TAB_ICONS") :]
    for tab_id in ids:
        assert re.search(rf"^  {tab_id}: \w+Icon,$", icon_map, re.MULTILINE), tab_id

    # No text glyph survives. The configured primary mark is either the short title or icon.
    assert "glyph" not in tabs
    assert "glyph" not in drawer
    assert "props.tabDisplay === 'title'" in drawer
    assert '<span class="drawer-tab-title">{item.label}</span>' in drawer
    assert "utilityRailDisplay==='title'" in app
    assert '<span class="drawer-tab-title">{tab.label}</span>' in app
    assert "<Icon />" in drawer and "<Icon/>" in app

    # Icon-only means the accessible name can no longer come from visible text. Session tabs
    # also name the scope represented by their lower-right dot; Project/app tabs stay unchanged.
    assert (
        "aria-label={`${item.label}${item.scope === 'session' ? ', session scoped' : ''}`}"
        in drawer
    )
    assert "aria-label={`${tab.title}${tab.scope==='session'?'. Session scoped.':''}`}" in app
    assert '.drawer-tabs button[data-scope="session"]:before' in css
    assert '.utility-rail button[data-scope="session"]:before' in css
    assert "width:3px;height:3px" in css
    assert "background:color-mix(in srgb,var(--muted) 52%,var(--panel2));box-shadow:none" in css

    # Sized in CSS on both surfaces: these run a 9-12px font, so `1em` would be unreadable.
    assert ".drawer-tabs button svg{width:17px" in css
    assert ".utility-rail button svg{width:16px" in css
    # Touch has no rail, so the strip is the only tab control and gets a 44px target on the
    # axis it can afford: height. Width is capped by the overlay (see the floors above).
    assert ".drawer-tabs button{min-height:44px;" in css


def test_drawer_tabs_use_recursive_device_local_layout_and_pane_dragging() -> None:
    """Pane rails edit one recursive local layout while the outer rail remains a mirror."""
    app = source("App.tsx")
    drawer = source("UtilityDrawer.tsx")
    layout = source("drawerLayout.ts")
    order = source("drawerTabOrder.ts")
    css = source("style.css")

    # The recursive layout feeds pane rails. The depth-first projection feeds the launcher.
    assert "layout={drawerLayout}" in app
    assert "presentation={activeDrawerPresentation}" in app
    assert (
        "drawerLauncherTabs.filter("
        "tab=>tab.id!=='transcript'||hasHarnessTranscript(active?.backend)).map(tab=>{"
    ) in app
    assert "stack.tabs.filter(tabAvailable).map((id, index, visibleTabs) => {" in drawer
    assert "renderNode(layout.root)" in drawer
    assert "onTabDragStart={beginDrawerTabDrag}" in app
    assert "props.onTabDragStart(event, id)" in drawer
    assert "onPointerDown={event=>beginDrawerTabDrag(event,tab.id)}" not in app

    # The app pointer-drag contract keeps a prospective tree in refs and commits once.
    assert "beginPointerDrag(event,drawerTab(id).label" in app
    assert "draggable" not in drawer
    assert "data-reorder-id={id}" in drawer
    assert "dragDrawerLayoutRef.current=moveDrawerTabToStack" in app
    assert "dragDrawerLayoutRef.current=edge" in app
    assert "commitDrawerLayout(next,id)" in app
    assert ".drawer-tabs button[data-pointer-drop-indicator" in css
    assert ".drawer-pane[data-pointer-drop-indicator" in css
    assert "suppressDragClickRef.current===`drawer-tab:${tab}`" in app

    # Legacy flat order is input only. Recursive state has its own device-local key.
    assert "mux.drawer.layout.v1" in layout
    assert "loadDrawerTabOrder()" in app
    assert "loadSettings().then" in app
    assert "!drawerLegacySettingsReady" in app
    assert "localStorage.getItem(DRAWER_LAYOUT_KEY)===null" in app
    assert "saveDrawerTabOrder" not in app
    assert "mux:settings-changed',adopt" not in app

    # Normalization preserves singleton ownership and reset restores the canonical stack.
    assert "export function normalizeDrawerLayout" in layout
    assert "export function normalizeDrawerTabOrder" in order
    assert "id: 'drawer.resetLayout'" in app


def test_drawer_tab_display_is_live_and_searchable() -> None:
    app = source("App.tsx")
    settings = source("Settings.tsx")
    drawer = source("UtilityDrawer.tsx")
    css = source("style.css")

    assert "drawer_tab_display?:'icon'|'title'" in app
    assert "utility_rail_display?:'icon'|'title'" in app
    assert "setDrawerTabDisplay(config.drawer_tab_display==='title'?'title':'icon')" in app
    assert "setUtilityRailDisplay(config.utility_rail_display==='title'?'title':'icon')" in app
    assert "Drawer tabs<select" in settings
    assert "Right rail<select" in settings
    assert '<option value="icon">Icons</option>' in settings
    assert '<option value="title">Titles</option>' in settings
    assert "tabDisplay={drawerTabDisplay}" in app
    assert "utilityRailDisplay==='title'" in app
    assert "const utilityRailWidth=utilityRailDisplay==='title'?112:40" in app
    assert "props.tabDisplay === 'title'" in drawer
    assert "surface:'tabs'|'rail'" in app
    assert "Collapse utility drawer" in app
    assert "drawer-display-menu" in app
    assert ".drawer-display-menu{z-index:70}" in css
    assert "onTab(id, id === selected)" in drawer
    assert "beginTabLongPress" in drawer
    assert "drawer-chrome" not in drawer
    assert "--utility-rail-width" in app
    assert "var(--utility-rail-width,40px)" in css


def test_recursive_drawer_exposes_tab_and_separator_accessibility() -> None:
    drawer = source("UtilityDrawer.tsx")

    assert "role:'tablist'" in drawer
    assert 'role="tab"' in drawer
    assert 'role="tabpanel"' in drawer
    assert "aria-controls={panelDomId(stack.id)}" in drawer
    assert "aria-labelledby={tabDomId(stack.id, selected)}" in drawer
    assert "tabIndex={id === selected ? 0 : -1}" in drawer
    assert "event.key !== 'ArrowLeft' && event.key !== 'ArrowRight'" in drawer
    assert "aria-valuenow={Math.round(node.ratio * 100)}" in drawer
    assert "onDblClick={() => updateRatio(0.5)}" in drawer
    assert "onPointerDown={projection ? beginTabLongPress" in drawer
    assert "mobile ? renderStack(mobileStack, focusedTab) : renderNode(layout.root)" in drawer
    assert 'aria-live="polite"' in drawer


def test_menu_scope_follows_the_menu_that_opened_the_surface() -> None:
    """The app menu browses every Project; a Project row browses that Project."""
    app = source("App.tsx")
    panel = source("ProcessPanel.tsx")

    main_menu = app[app.index('aria-label="swe-mux menu"') : app.index('class="sidebar-scrim"')]
    # The lead block carries no heading: it is the app's general-purpose surfaces,
    # not a "browse projects" section. Nothing that acts on a single Project lives
    # in the app menu at all — that belongs to the Project's own context menu.
    assert "BROWSE ALL PROJECTS" not in main_menu
    assert "CURRENT PROJECT" not in main_menu
    # The app menu opens each browser unscoped, never its project-scoped variant.
    for command in ("history.open", "notes.browse", "processes.all", "prompts.open"):
        assert f"runNamedCommand('{command}')" in main_menu
    for scoped in (
        "history.openProject",
        "processes.project",
        "prompts.openProject",
        "observations.open",
        "project.settings",
    ):
        assert scoped not in main_menu

    project_menu = app[
        app.index("aria-label={`Project actions for") : app.index('aria-label="Sidebar actions"')
    ]
    assert "BROWSE THIS PROJECT" in project_menu
    for scoped in ("history.openProject", "processes.project", "prompts.openProject"):
        assert f"runNamedCommand('{scoped}')" in project_menu
    assert "openNotesBrowser(target)" in project_menu

    # Scope is a visible, clearable control rather than a hidden mode.
    assert "process-scope-select" in panel
    assert "initialProjectId" in panel
    # History is a global overlay (not a per-project pane), opened with an
    # optional scope that pre-filters its own clearable project picker.
    assert "const showHistory = (scope:Project|null=null)" in app
    assert "{historyOpen&&<HistoryBrowser" in app


def test_dialog_layers_stack_above_persistent_chrome() -> None:
    """Chrome that paints over a dialog swallows taps on the dialog's own header.

    The mobile toolbar sat above the Projects registry layer, so `+ Add project`
    and its close button rendered but could not be tapped on a phone.
    """
    css = source("style.css")
    layers = {
        name: int(value) for name, value in re.findall(r"\.([a-z-]+)\s*\{[^}]*?z-index:(\d+)", css)
    }
    chrome = ["mobile-toolbar", "mobile-nav-toggle", "app-topbar", "context-menu"]
    dialogs = [
        "modal-layer",
        "prompt-library-layer",
        "projects-manager-layer",
        "process-layer",
        "project-registry-dialog-layer",
        "folder-picker-layer",
    ]
    missing = [name for name in chrome + dialogs if name not in layers]
    assert not missing, f"z-index layers not found in the stylesheet: {missing}"
    ceiling = max(layers[name] for name in chrome)
    below = {name: layers[name] for name in dialogs if layers[name] <= ceiling}
    assert not below, f"dialog layers must sit above chrome (>{ceiling}): {below}"
    # A dialog opened from the registry has to stack over it.
    assert layers["project-registry-dialog-layer"] > layers["projects-manager-layer"]
    assert layers["folder-picker-layer"] > layers["project-registry-dialog-layer"]


def test_worktrees_stay_out_of_navigation_but_can_launch_from_project_run() -> None:
    app = source("App.tsx")
    assert "worktreeCreate" not in app
    assert "manageWorktrees" not in app
    assert "Create worktree" not in app
    run_menu = source("ProjectRunMenu.tsx")
    assert "New worktree session" in run_menu
    assert "'/api/git/worktrees'" in run_menu
    assert "onWorktreeCreated(result.path,worktree.backend)" in run_menu
    assert "'/api/git/worktrees/session'" in app
    assert "pendingTerminal(pendingId,target,backend,{" in app
    assert "label:'Setting up worktree…'" in app
    assert "placement:null" in app
    assert "if(pending.projectId!==project.id||!pending.placement)continue" in app
    assert "selectPendingTerminal(current,session.id)" in app
    assert "setActiveId(current=>current===pendingId?next.id:current)" in app
    assert "normalizeWorktreeBranchInput(value)" in run_menu
    assert "api<{worktree_root?:string}>('GET','/api/config')" in run_menu
    assert "timeoutMs:35*60*1000" in app
    assert "worktree-my-change" in run_menu
    assert "setup output is in the session scrollback" in app
    settings = source("Settings.tsx")
    assert "<h3>Git and worktrees</h3>" in settings
    assert "change('worktree_root'" in settings
    projects = source("ProjectsManager.tsx")
    assert "Worktree setup command" in projects
    assert "setup_command" in projects


def test_process_fleet_groups_sessions_and_daemon_infrastructure() -> None:
    panel = source("ProcessPanel.tsx")

    assert "projectProcessGroups" in panel
    assert "session?.project_id" in panel
    assert "All projects, sessions, and swe-mux infrastructure" in panel
    assert "PROCESS FLEET" in panel
    assert "buildProcessTree" in panel
    assert "renderDaemonGroup" in panel
    assert "swe-mux::daemon + infrastructure" in panel
    assert "daemon-owned child not attributed to a terminal session" in panel
    assert "combinedResourceTotals(snapshot)" in panel


def test_accessibility_and_startup_instrumentation_remain_intact() -> None:
    combined = frontend_source()
    css = source("style.css")

    assert 'role="dialog"' in combined
    assert 'role="menu"' in combined
    assert 'aria-live="assertive"' in combined
    assert "useModalFocus" in combined
    assert "prefers-reduced-motion:reduce" in css
    assert "startup_timing_ms" in combined
    assert "client_startup_timing_ms" in combined
    assert "/startup-metrics" in combined


def test_browser_title_stays_stable_without_attention_count() -> None:
    combined = frontend_source()
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "<title>swe-mux</title>" in index
    assert "document.title" not in combined


def test_the_transcript_tab_reads_and_only_reads() -> None:
    """The drawer's one inert session surface, and the three rules that keep it inert.

    Every other session-scoped tab exists to put text into an agent. Mixing that into
    the surface meant for reviewing what already happened is how a stray tap becomes a
    message nobody wrote, so the absence of an insert path is a contract rather than an
    omission — including the `onDone` every sibling takes, which on mobile would close
    the drawer after each copy and end the reading.
    """
    app = source("App.tsx")
    drawer = source("UtilityDrawer.tsx")
    tab = source("TranscriptTab.tsx")
    view = source("transcriptView.ts")

    assert "<TranscriptTab session={session} />" in drawer
    for injection in ("onInsert", "onDone", "onSend", "/input"):
        assert injection not in tab, injection

    # Copy is this tab's only verb and agent replies run to kilobytes, so they stay out
    # of the clipboard ring rather than evicting the snippets it exists to hand back.
    assert "withoutClipboardCapture" in tab
    assert "suppressDepth > 0" in source("clipboardHistory.ts")

    # The pane-header route is shared by desktop panes and the mobile projection. It
    # follows the same focus-first contract as Queue, is available only for harnesses
    # with transcripts, and stays adjacent to Queue in source/render order.
    opener_start = app.index("const openTranscriptForSession =")
    opener = app[opener_start : app.index("/** Pop one target's queue", opener_start)]
    assert "if (session) await selectSession(session)" in opener
    assert "openDrawerTab('transcript',session?.project_id||projectId)" in opener
    tools_start = app.index('<div class="pane-tools">')
    tools = app[tools_start : app.index('</div>', tools_start)]
    assert "hasHarnessTranscript(session.backend)" in tools
    assert 'class="pane-tool-label transcript-chip"' in tools
    assert "openQueueForSession(session.id)" in tools
    assert "openTranscriptForSession(session.id)" in tools
    assert tools.index("openQueueForSession(session.id)") < tools.index(
        "openTranscriptForSession(session.id)"
    )

    # The drawer unmounts a tab body on every tab switch, so anything that has to
    # survive that cannot be component state. The scroll place is the whole reason this
    # module exists separately from the component.
    assert "rememberTranscriptScroll" in view and "recallTranscriptScroll" in view
    assert "let scrollMemory" in view
    assert "useState" not in view

    # Refreshed after the observer consumes a user message and when the assistant turn
    # ends. A timer would re-read a whole transcript to learn nothing most of the time.
    assert "TURN_ENDED_EVENT" in tab and "turn_ended" in source("App.tsx")
    assert "TRANSCRIPT_CHANGED_EVENT" in tab and "transcript_message" in source("App.tsx")
    assert "setInterval" not in tab
    assert "session.regenerateTitle" in source("App.tsx")
    assert "/title/regenerate" in source("App.tsx")

    # A reader already scrolled up is not carried along by an arriving message; it is
    # offered as a button instead. Yanking the column mid-sentence every time an agent
    # speaks is what makes a live log unreadable.
    assert "isPinnedToBottom" in tab
    assert "transcript-jump" in tab and ".transcript-jump{" in source("style.css")

    # Search is a local lens over the already loaded conversation. Copy-all remains the
    # complete conversation, while each message's copy control sticks only within that row.
    assert 'type="search"' in tab and "transcriptMatchesQuery" in tab
    assert "transcriptSearchParts" in tab
    assert "transcript-copy-anchor" in tab
    assert ".transcript-copy-anchor{position:sticky" in source("style.css")

    # Show-more is an explicit reading preference. It follows the session and run
    # across drawer unmounts, but the bounded registry contains no transcript text.
    assert "TRANSCRIPT_EXPANSION_KEY" in tab
    assert "setTranscriptMessageExpanded" in tab
    assert "TRANSCRIPT_EXPANSION_MAX_ENTRIES = 500" in view
    assert "sessionId: string" in view and "runId: string" in view and "messageId: string" in view

    # One message is one message whether it is being read live or out of history, so
    # both surfaces stamp it through the same helper rather than each carrying its own
    # date formatting. `transcriptView.ts` owns the behavior (unit-tested in
    # `frontend/test/transcriptView.test.ts`); what is pinned here is the wiring, which
    # is what silently drifts when a reader gains a feature the other does not.
    history = source("HistoryBrowser.tsx")
    for surface in (tab, history):
        assert "transcriptTimestampLabel(message.ts)" in surface
        assert "transcriptTimestampIso(message.ts)" in surface
    # Deliberately not the same formatter the history *list* uses: a row with no
    # timestamp still owes the reader an explanation, while a message stamp that
    # cannot be rendered is simply omitted (`{stamp&&<time…>}`).
    assert "'timestamp unavailable'" in history
    assert "'timestamp unavailable'" not in view


def test_history_filters_fit_narrow_split_panes() -> None:
    css = source("style.css")
    history = source("HistoryBrowser.tsx")

    assert ".history-workspace { container-type:inline-size" in css
    assert ".history-search>* { min-width:0;max-width:100% }" in css
    assert (
        ".history-search { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px }"
        in css
    )
    assert "@container (max-width:620px)" in css
    # The time filter chooses which of the two stamps a row is ranked by, so the row
    # has to show both for the choice to mean anything.
    assert "time_basis" in history
    assert "Time: last message" in history
    assert "Started {timestampLabel(historyStart(entry))}" in history
    assert "Last {entry.last_message_role" in history


def test_daemon_spawned_panes_request_focus_rather_than_setting_it() -> None:
    """A pane the daemon creates is focused through the pending-request path.

    These flows learn the new leaf's id from their response but learn where it sits
    only on the next layout refresh. A bare `setFocusedViewId` in that gap is read as
    stale focus by the reconciliation effect and replaced with the pane's current tab,
    so the resumed/branched session opens *behind* the tab it was started from. The
    ordering itself is `reconcileFocusView` in `viewState.ts`, unit-tested there; what
    is pinned here is that the call sites actually go through it.
    """
    app = source("App.tsx")

    for flow in ("resumeHistoryEntry", "branchSession", "resumeSession", "confirmSecondOpinion"):
        body = re.search(rf"const {flow} = ?async[^\n]*\n(.*?)\n  \}}\n", app, re.DOTALL)
        assert body, f"{flow} is no longer a recognisable handler"
        assert "requestFocusView(" in body.group(1), f"{flow} must request focus, not set it"
        assert "setFocusedViewId(" not in body.group(1), (
            f"{flow} sets focus directly, which the layout refresh will undo"
        )

    # The request is held in a ref and consulted by the reconciliation effect, or it
    # would be dropped by the very render it exists to survive.
    assert (
        "const requestFocusView=(id:string)=>"
        "{pendingFocusId.current=id;setFocusedViewId(id)}"
    ) in app
    assert "requested:pendingFocusId.current" in app
    assert "if(!keepRequest)pendingFocusId.current=null" in app
