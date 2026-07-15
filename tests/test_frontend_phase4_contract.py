from __future__ import annotations

from pathlib import Path


def frontend_source() -> str:
    root = Path(__file__).parents[1] / "frontend" / "src"
    return "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.ts*"))


def test_mobile_workspace_surfaces_and_touch_contract() -> None:
    root = Path(__file__).parents[1]
    source = frontend_source()
    css = (root / "frontend" / "src" / "style.css").read_text(
        encoding="utf-8"
    )
    index = (root / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "mobile-session-switcher" in source
    assert "mobile-new-session" in source
    assert "mobile-agent-composer" not in source
    assert "mobile-terminal-actions" not in source
    assert "clipboardImage(Array.from(event.clipboardData.items))" in source
    assert "decision.kind === 'browserPaste'" in source
    assert "host.current.addEventListener('drop', drop)" in source
    assert 'class="terminal-image-drop"' in source
    assert "interactive-widget=resizes-content" in index
    assert "--app-height" in source
    assert "height:var(--app-height,100dvh)" in css
    assert "pointerType !== 'touch'" in source
    assert "pointerType === 'touch'" in source
    assert "longPressTimer" in source
    assert "new WheelEvent('wheel'" in source
    assert "min-height:44px" in css
    assert "@media(max-width:760px)" in css
    assert ".stack-tabs{display:none}" in css
    assert ".mobile-toolbar{position:sticky" in css
    assert ".context-menu{z-index:49}" in css
    assert ".sidebar-footer button.menu-trigger{display:flex" in css
    assert 'class="menu-trigger"' in source
    assert ".notes-panel" in css
    assert ".process-panel" in css
    assert ".preview-pane" in css


def test_mobile_focus_clipboard_and_sleep_recovery_are_durable() -> None:
    source = frontend_source()

    assert "mux.focus.v1" in source
    assert "window.history.replaceState" in source
    assert "resolveInitialFocus" in source
    assert "ResilientClipboardProvider" in source
    assert "Prepared terminal clipboard text" in source
    assert "window.addEventListener('pageshow'" in source
    assert "window.addEventListener('online'" in source
    assert "scheduleFullRedraw()" in source
    assert "addon.onContextLoss" in source
    assert "new IntersectionObserver" in source


def test_accessibility_contract_covers_dynamic_workspace_surfaces() -> None:
    source = frontend_source()
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(
        encoding="utf-8"
    )

    assert 'role="dialog"' in source
    assert 'role="menu"' in source
    assert 'role="listbox"' in source
    assert 'aria-live="polite"' in source
    assert 'aria-live="assertive"' in source
    assert "useModalFocus" in source
    assert "prefers-reduced-motion:reduce" in css
    assert "aria-label=\"Focused session\"" in source


def test_history_ungrouped_filter_has_a_distinct_value() -> None:
    source = frontend_source()
    assert "project.project_id || '__ungrouped__'" in source
    assert "historyProject === '__ungrouped__'" in source


def test_phase4_features_have_contextual_navigation_and_live_notifications() -> None:
    source = frontend_source()

    assert "Open space note…" in source
    assert "Agent-run note…" in source
    assert "Processes and previews…" in source
    assert "Usage analytics…" in source
    assert "Automation…" in source
    assert (
        "isAgent(session)?`Agent-run note · ${paneProjectLabel}`:"
        "`Current project note · ${paneProjectLabel}`"
    ) in source
    assert 'title="Processes and previews"' in source
    assert "notificationToast" in source
    assert "openNotifications" in source
    assert "['notification','notification_created'].includes(" in source


def test_notes_keep_annotation_identity_separate_from_live_terminal_actions() -> None:
    source = frontend_source()

    assert "terminalSessionId" in source
    assert "disabled={!terminalSessionId}" in source
    assert "initialKind={target.kind}" in source
    assert "targetKey" in source


def test_notes_have_one_persistent_tabbed_workspace_with_dock_and_popout_modes() -> None:
    source = frontend_source()
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(
        encoding="utf-8"
    )
    workspace = (
        Path(__file__).parents[1] / "frontend" / "src" / "NotesWorkspace.tsx"
    ).read_text(encoding="utf-8")

    assert "session.notesSplit" in source
    assert "Dock selected agent note" in source
    assert "Dock selected space note" in source
    assert "Dock notes workspace" in source
    assert "Pop out notes workspace" in source
    assert "openNoteContext" in source
    assert "onTabContext" in workspace
    assert "Minimize Notes workspace" in workspace
    assert ">−</button>" in workspace
    assert "Close note tab" in source
    assert 'display="pane"' in source
    assert "noteResourceId" in source
    assert "activeWorkspaceNoteIds" in source
    assert "showNoteWorkspace" in source
    assert "pop out" in workspace
    assert "Notes workspace" in workspace
    assert ".note-modal" in css
    assert ".notes-workspace-panel .note-pane" in css
    assert ".notes-workspace-shell.popout" in css
    assert ".space-workspace{flex-direction:column}" in css
    assert "layoutWriteChains" in source
    assert "layoutWriteGeneration" in source


def test_process_fleet_and_sidebar_context_own_global_process_actions() -> None:
    source = frontend_source()
    process_panel = (
        Path(__file__).parents[1] / "frontend" / "src" / "ProcessPanel.tsx"
    ).read_text(encoding="utf-8")

    assert "All processes and previews…" in source
    assert "processes.all" in source
    assert "All Settings…" in source
    assert "PROCESS FLEET" in process_panel
    assert "spaceGroups" in process_panel
    assert "← all processes" in process_panel
    assert "Process fleet…" in source
    assert "/api/processes?session=${encodeURIComponent(sessionId)}" in process_panel
    assert "session query parameter is required" in process_panel
    assert "buildProcessTree" in process_panel
    assert "!snapshot&&!error" in process_panel


def test_notes_use_one_raw_markdown_text_editor() -> None:
    notes = (
        Path(__file__).parents[1] / "frontend" / "src" / "Notes.tsx"
    ).read_text(encoding="utf-8")

    # One raw-markdown editing surface. CodeMirror replaced the textarea because a
    # textarea cannot hang-indent soft-wrapped lines (it has no per-line boxes); the
    # contract itself is unchanged: raw markdown, a single surface, no WYSIWYG and no
    # edit/preview split.
    assert "EditorView" in notes
    assert "wrappedLineIndent" in notes
    assert "placeholder('Write Markdown…')" in notes
    # Tab indents rather than escaping the editor; defaultKeymap keeps CodeMirror's
    # Ctrl-m tab-focus-mode escape hatch so the binding cannot trap keyboard users.
    assert "indentWithTab" in notes
    assert "defaultKeymap" in notes
    assert "contentEditable" not in notes
    assert "execCommand" not in notes
    assert "Search project notes" not in notes
    assert "note?.path || cwd" not in notes
    assert "notes-state-light" in notes
    assert "title={statusTitle}" in notes
    assert "revision::" in notes
    assert ">Edit</button>" not in notes
    assert ">Preview</button>" not in notes
    assert "MarkdownPreview" not in notes


def test_note_ownership_is_explicit_and_space_anchors_are_absent() -> None:
    source = frontend_source()

    assert "APP DATA" in source
    assert "Agent-run note" in source
    assert "Current project note" in source
    assert 'aria-label="Note scope"' not in source
    assert "Project anchor" not in source
    assert "anchor_project_scope_id" not in source


def test_sidebar_notes_follow_durable_owners_and_preserve_the_workspace() -> None:
    source = frontend_source()
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(
        encoding="utf-8"
    )

    assert "noteOwnerSession" in source
    assert "sidebarNoteRow" in source
    assert "savedSpaceNotes" in source
    assert "api<SpaceNoteSummary[]>('GET','/api/space-notes')" in source
    assert 'class="space-note-list"' in source
    assert "layoutSessionForNote" not in source
    assert "hideActiveNotesWorkspace" in source
    assert "showNoteWorkspace(noteLayout,resourceId,mode)" in source
    assert "open pane" not in source
    assert ".sidebar-note-row" in css


def test_sidebar_focus_does_not_reorder_or_rewrite_layout_membership() -> None:
    source = frontend_source()

    assert "stateRank" not in source
    assert "focusedOutsideLayout" in source
    assert "if(isPaned)await updateLayout" in source
    assert "sessionRow(session,'loose')" not in source
    assert "a.created_at-b.created_at" in source


def test_ended_sessions_remain_explicitly_dismissible() -> None:
    source = frontend_source()

    assert "Remove from sidebar" in source
    assert "isEndedSession(session) ? 'Remove session' : 'Kill session'" in source
    assert "Session has already ended" not in source


def test_browser_title_stays_stable_without_attention_count() -> None:
    root = Path(__file__).parents[1]
    source = frontend_source()
    index = (root / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "<title>swe-mux</title>" in index
    assert "document.title" not in source


def test_session_startup_instrumentation_covers_server_and_browser_milestones() -> None:
    source = frontend_source()

    assert "SESSION STARTUP" in source
    assert "startup_timing_ms" in source
    assert "first_prompt" in source
    assert "api_response" in source
    assert "pane_mounted" in source
    assert "socket_open" in source
    assert "replay_ready" in source
    assert 'class="startup-chip"' in source
    assert "/startup-metrics" in source
    assert "client_startup_timing_ms" in source
