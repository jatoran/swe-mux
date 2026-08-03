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
    assert ".pane-stack>.stack-tabs{display:flex;flex-wrap:nowrap" in css
    assert "mobileWorkspaceProjection" in combined
    assert "mobile-unified-tabs" in combined
    assert (
        ".pane-stack:not(.focused-pane):not(.empty-workspace-pane):not(.mobile-unified-workspace){display:none}"
        in css
    )
    # Pane geometry used to be excluded from mobile by name (`source!=='mobile'`).
    # It is now included by name on one source only — the pane menu — which excludes
    # mobile, the sidebar, and the tab strip in one rule.
    # test_pane_geometry_actions_live_only_on_the_pane_menu owns that invariant.
    assert "contextMenu.source!=='mobile'" not in combined
    assert "contextMenu.source==='mobile'&&mobileMoveRow" in combined
    assert "tabMenu.source==='mobile'" in combined
    # Mobile rail order is a device-local permutation of the projection. It must
    # never write layout: that is the only thing keeping a phone's reordering
    # from rearranging the desktop pane tree for every client.
    assert "MOBILE_TAB_ORDER_KEY" in combined
    app = source("App.tsx")
    move_slot = app.split("const moveMobileTabSlot=")[1].split("const mobileMoveRow=")[0]
    assert "updateLayout" not in move_slot
    assert "api(" not in move_slot
    assert "'mux.mobileTabOrder.v1'" in source("mobileTabOrder.ts")
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
    assert "interactive-widget=resizes-content" in index
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

    assert "openProjectNotes" in app
    assert "openSessionNotes" in app
    assert "Open session note" in app
    assert "openProjectFiles" in app
    assert "openProjectFile" in app
    assert "ProjectResource" in app
    assert "openTab" in app
    assert "splitNoteResource" in app
    assert "moveLeafToSplit" in app
    assert "version: 7" in layout
    assert "showNoteWorkspace" not in app
    assert not (ROOT / "frontend" / "src" / "NotesWorkspace.tsx").exists()
    assert "/api/projects/${project.id}/note" in resource
    assert (
        "/api/projects/${project.id}/session-notes/${encodeURIComponent(resource.id)}" in resource
    )
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


def test_session_note_is_one_click_from_the_pane_bar_and_opens_in_the_anchor_pane() -> None:
    app = source("App.tsx")
    layout = source("layout.ts")
    css = source("style.css")

    # One click from the pane bar, with a state the user can read at a glance.
    assert "note-chip" in app
    assert "noteChipState" in app
    assert "onClick={()=>openSessionNotes(session)}" in app
    assert ".pane-tools .note-chip.written" in css
    assert ".pane-tools .note-chip.open" in css

    # Opening a resource is not a layout command. Every kind lands as a tab in the anchor's
    # pane; nothing splits the workspace on the user's behalf.
    assert "openTab(current,focused,resourceLeaf('note',resourceId))" in app
    # (`layout.ts` still names the retired helper in a comment saying why it is gone.)
    assert "export function placeCompanionLeaf" not in layout
    assert "placeCompanionLeaf(" not in app
    assert "target.kind==='session-note'&&!targetViewId" not in app


def test_notes_tab_indexes_written_notes_without_hosting_an_editor() -> None:
    """Notes are found in the drawer and edited in a pane, never edited in the drawer.

    The drawer unmounts a tab body on every tab switch, which would cost the editor's
    cursor/undo history and detach it from the insert routing that Clipboard and Prompts
    depend on. So the Notes tab is an index: it opens a note into the workspace.
    """
    app = source("App.tsx")
    notes = source("NotesTab.tsx")
    drawer = source("UtilityDrawer.tsx")
    css = source("style.css")

    assert "/api/session-notes" in notes
    assert "project_id=${encodeURIComponent(scopeId)}" in notes
    assert "Filter session notes by project" in notes
    assert "Search session notes" in notes
    assert "All projects" in notes
    assert ".session-note-row" in css and ".notes-tab" in css
    # An index, not an editor: no Continuity editor and no save queue live here.
    assert "ProjectNoteEditor" not in notes
    assert "noteSaveQueue" not in notes
    # The Project note is pinned and unconditional; the focused terminal's note is
    # pinned only when it holds text, which is the "only if one exists" rule.
    assert "note-pin-row" in notes
    assert "focusedNote&&" in notes
    assert "focusedNote={active?.note_exists?" in app

    # The retired modal is gone, and its three entry points now open the drawer tab.
    assert not (ROOT / "frontend" / "src" / "SessionNotesBrowser.tsx").exists()
    assert "SessionNotesBrowser" not in app
    assert "case 'notes':" in drawer
    assert "openNotesBrowser" in app
    assert "id: 'notes.browse'" in app
    assert "runNamedCommand('notes.browse')" in app
    assert "openBrowsedSessionNote" in app

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
    assert "openDrawerTab('files')" in app
    assert "openDrawerTab('notes')" in app

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


def test_drawer_tabs_are_icon_only_from_one_shared_icon_set() -> None:
    """Nine icon tabs stay reachable in one horizontally scrolling phone row.

    `drawerTabs.ts` stays JSX-free so it can be unit-tested under plain type-stripping, which
    is why the icon map lives in `railIcons.tsx` and why this cross-file invariant — every tab
    id has a mark — is checked here rather than there.
    """
    tabs = source("drawerTabs.ts")
    icons = source("railIcons.tsx")
    drawer = source("UtilityDrawer.tsx")
    app = source("App.tsx")
    css = source("style.css")

    # Bumped deliberately, not incidentally. Six *labelled* tabs measured ~444px, which
    # overflowed a phone drawer (`min(430px, 92vw)`) into a scrollbar-less scroller that
    # silently parked the last two off-screen. Adding a tab means re-checking that on a
    # phone, which is what this assertion is for — it is a prompt, not a cap.
    #
    # Re-checked at nine (Context): nine 36px touch cells do not fit beside the 30px close
    # target on a 360px phone. Wrapping made the drawer header jump to two rows, so the
    # tablist now scrolls on one row behind a fade; the close target sits outside that
    # scroller and selection calls scrollIntoView.
    ids = re.findall(r"\{ id: '([a-z]+)'", tabs)
    assert len(ids) == 9, ids
    tab_css = css[css.index(".drawer-tabs{") : css.index(".drawer-tabs::")]
    assert "flex-wrap:nowrap" in tab_css and "overflow-x:auto" in tab_css
    assert ".drawer-tabs-shell:after" in css
    assert ".drawer-tabs-shell>.drawer-close" in css
    assert ".drawer-tabs button{position:relative;min-height:34px;flex:1 0 32px" in css
    assert ".drawer-tabs button{min-height:44px;flex-basis:36px;min-width:36px" in css
    assert "scrollIntoView({ block: 'nearest', inline: 'nearest' })" in drawer
    icon_map = icons[icons.index("DRAWER_TAB_ICONS") :]
    for tab_id in ids:
        assert re.search(rf"^  {tab_id}: \w+Icon,$", icon_map, re.MULTILINE), tab_id

    # No text glyph survives on either surface, and neither renders a label.
    assert "glyph" not in tabs
    assert "glyph" not in drawer
    # The label is the accessible name only; it is never rendered as a child of the button.
    assert ">{item.label}" not in drawer and "{item.label}<" not in drawer
    assert "<Icon />" in drawer and "<Icon/>" in app

    # Icon-only means the accessible name can no longer come from visible text.
    assert "aria-label={item.label}" in drawer
    assert "aria-label={tab.title}" in app

    # Sized in CSS on both surfaces: these run a 9-12px font, so `1em` would be unreadable.
    assert ".drawer-tabs button svg{width:17px" in css
    assert ".utility-rail button svg{width:16px" in css
    # Touch has no rail, so the strip is the only tab control and gets a 44px target on the
    # axis it can afford: height. Width is capped by the overlay (see the floors above).
    assert ".drawer-tabs button{min-height:44px;" in css


def test_drawer_tabs_are_user_arrangeable_and_the_order_persists() -> None:
    """One arrangement, two surfaces, one drag contract, stored on the daemon.

    The strip and the rail are two renderings of one control, so they render one order and
    share one drag handler; a per-surface order would let them disagree about what "third"
    means. The order is server-persisted (unlike drawer width and last-used tab, which are
    genuinely per-device) so a phone inherits what a desktop arranged.
    """
    app = source("App.tsx")
    drawer = source("UtilityDrawer.tsx")
    settings = source("deviceSettings.ts")
    order = source("drawerTabOrder.ts")
    css = source("style.css")

    # One ordered list feeds both surfaces, and one handler reorders from either.
    assert "tabs={orderedDrawerTabs}" in app
    assert "orderedDrawerTabs.map(tab=>{" in app
    assert "props.tabs.map(item => {" in drawer
    assert "onTabDragStart={beginDrawerTabDrag}" in app
    assert "onPointerDown={event=>beginDrawerTabDrag(event,tab.id)}" in app
    assert "props.onTabDragStart(event, item.id)" in drawer

    # The app's pointer-drag contract, not native DnD: no `draggable` attribute, refs plus one
    # DOM indicator during the move, commit on pointer-up.
    assert "beginPointerDrag(event,drawerTab(id).label" in app
    assert "draggable" not in drawer
    assert 'data-reorder-id={item.id}' in drawer and "data-reorder-id={tab.id}" in app
    assert "reorderTargetFromContainer(container,id,axis" in app
    assert ".drawer-tabs button[data-pointer-drop-indicator" in css
    assert ".utility-rail button[data-pointer-drop-indicator" in css
    # The drag's pointer-up also clicks the tab it started from; both surfaces suppress it.
    assert app.count("suppressDragClickRef.current===`drawer-tab:") == 2

    # Server-persisted in one canonical bucket, like the command rail and the file tree.
    assert "'drawerTabs'" in settings
    assert "const DRAWER_TAB_PROFILE: SettingsProfile = 'desktop'" in settings
    assert "saveDrawerTabOrder" in app and "loadDrawerTabOrder" in app
    # Another device editing the order is the same event as the cache first loading.
    assert "window.addEventListener('mux:settings-changed',adopt)" in app

    # Arranging can never lose a tab, and persistent state a drag can scramble needs a way back.
    assert "export function normalizeDrawerTabOrder" in order
    assert "id: 'drawer.resetTabs'" in app


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


def test_worktrees_are_not_a_first_class_frontend_surface() -> None:
    app = source("App.tsx")
    assert "worktreeCreate" not in app
    assert "manageWorktrees" not in app
    assert "Create worktree" not in app


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
    assert "time_basis" in history
    assert "Time: last message" in history
    assert "timestampLabel(message.ts)" in history
    assert "Started {timestampLabel(historyStart(entry))}" in history
