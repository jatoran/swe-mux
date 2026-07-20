from __future__ import annotations

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


def test_worktrees_are_not_a_first_class_frontend_surface() -> None:
    app = source("App.tsx")
    assert "worktreeCreate" not in app
    assert "manageWorktrees" not in app
    assert "Create worktree" not in app


def test_process_fleet_groups_by_project() -> None:
    panel = source("ProcessPanel.tsx")

    assert "projectProcessGroups" in panel
    assert "session?.project_id" in panel
    assert "All projects and sessions" in panel
    assert "PROCESS FLEET" in panel
    assert "buildProcessTree" in panel


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
