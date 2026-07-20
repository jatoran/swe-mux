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
    assert "contextMenu.source!=='mobile'" in combined
    assert "tabMenu.source==='mobile'" in combined
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
    assert "version:6" in layout
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


def test_session_note_is_one_click_from_the_pane_bar_and_opens_beside_the_terminal() -> None:
    app = source("App.tsx")
    layout = source("layout.ts")
    css = source("style.css")

    # One click from the pane bar, with a state the user can read at a glance.
    assert "note-chip" in app
    assert "noteChipState" in app
    assert "onClick={()=>openSessionNotes(session)}" in app
    assert ".pane-tools .note-chip.written" in css
    assert ".pane-tools .note-chip.open" in css

    # A session note accompanies its terminal instead of covering it.
    assert "export function placeCompanionLeaf" in layout
    assert "placeCompanionLeaf" in app
    assert "target.kind==='session-note'&&!targetViewId" in app


def test_session_notes_browser_lists_written_notes_and_filters_by_project() -> None:
    app = source("App.tsx")
    browser = source("SessionNotesBrowser.tsx")
    css = source("style.css")

    assert "/api/session-notes" in browser
    assert "project_id=${encodeURIComponent(projectId)}" in browser
    assert "Filter session notes by project" in browser
    assert "Search session notes" in browser
    assert "All projects" in browser
    assert ".session-notes-modal" in css

    # Reachable from the project context menu, the main menu, and the palette.
    assert "SessionNotesBrowser" in app
    assert "setSessionNotesScope(target.id)" in app
    assert "id: 'notes.browse'" in app
    assert "runNamedCommand('notes.browse')" in app
    assert "openBrowsedSessionNote" in app


def test_menu_scope_follows_the_menu_that_opened_the_surface() -> None:
    """The app menu browses every Project; a Project row browses that Project."""
    app = source("App.tsx")
    panel = source("ProcessPanel.tsx")

    main_menu = app[app.index('aria-label="swe-mux menu"') : app.index('class="sidebar-scrim"')]
    assert "BROWSE ALL PROJECTS" in main_menu
    # The app menu opens each browser unscoped, never its project-scoped variant.
    for command in ("history.open", "notes.browse", "processes.all", "prompts.open"):
        assert f"runNamedCommand('{command}')" in main_menu
    for scoped in ("history.openProject", "processes.project", "prompts.openProject"):
        assert scoped not in main_menu

    project_menu = app[
        app.index("aria-label={`Project actions for") : app.index('aria-label="Sidebar actions"')
    ]
    assert "BROWSE THIS PROJECT" in project_menu
    for scoped in ("history.openProject", "processes.project", "prompts.openProject"):
        assert f"runNamedCommand('{scoped}')" in project_menu
    assert "setSessionNotesScope(target.id)" in project_menu

    # Scope is a visible, clearable control rather than a hidden mode.
    assert "process-scope-select" in panel
    assert "initialProjectId" in panel
    assert "const showHistory = async (scope:Project|null=null)" in app


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
        "history-layer",
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
