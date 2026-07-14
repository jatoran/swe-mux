from __future__ import annotations

from pathlib import Path


def frontend_source() -> str:
    root = Path(__file__).parents[1] / "frontend" / "src"
    return "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.ts*"))


def test_mobile_workspace_surfaces_and_touch_contract() -> None:
    source = frontend_source()
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(
        encoding="utf-8"
    )

    assert "mobile-session-switcher" in source
    assert "pointerType !== 'touch'" in source
    assert "pointerType === 'touch'" in source
    assert "longPressTimer" in source
    assert "min-height:44px" in css
    assert "@media(max-width:760px)" in css
    assert ".notes-panel" in css
    assert ".process-panel" in css
    assert ".preview-pane" in css


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
    assert "Hooks and notifications…" in source
    assert (
        "isAgent(session)?`Agent-run note · ${paneProjectLabel}`:"
        "`Current project note · ${paneProjectLabel}`"
    ) in source
    assert 'title="Processes and previews"' in source
    assert "notificationToast" in source
    assert "openNotifications" in source
    assert "JSON.parse(String(message.data)).type==='notification'" in source


def test_notes_keep_annotation_identity_separate_from_live_terminal_actions() -> None:
    source = frontend_source()

    assert "terminalSessionId" in source
    assert "disabled={!terminalSessionId}" in source
    assert "initialKind={noteTarget.kind}" in source
    assert "targetKey" in source


def test_notes_have_quick_modal_and_persistent_split_pane_modes() -> None:
    source = frontend_source()
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(
        encoding="utf-8"
    )

    assert "session.notesSplit" in source
    assert "note in split" in source
    assert "Open space note in split" in source
    assert 'display="pane"' in source
    assert "noteResourceId" in source
    assert "dock right" in source
    assert "pop out" in source
    assert ".note-modal" in css
    assert ".note-pane.mobile-active" in css


def test_notes_use_one_raw_markdown_text_editor() -> None:
    notes = (
        Path(__file__).parents[1] / "frontend" / "src" / "Notes.tsx"
    ).read_text(encoding="utf-8")

    assert "<textarea" in notes
    assert 'placeholder="Write Markdown…"' in notes
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


def test_sidebar_notes_follow_owners_and_terminal_selection_clears_note_focus() -> None:
    source = frontend_source()
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(
        encoding="utf-8"
    )

    assert "noteOwnerSession" in source
    assert "sidebarNoteRow" in source
    assert "savedSpaceNotes" in source
    assert "api<SpaceNoteSummary[]>('GET','/api/space-notes')" in source
    assert 'class="space-note-list"' in source
    assert "setMobileNoteId(null)" in source
    assert ".sidebar-note-row" in css


def test_sidebar_focus_does_not_reorder_or_rewrite_layout_membership() -> None:
    source = frontend_source()

    assert "stateRank" not in source
    assert "focusedOutsideLayout" in source
    assert "if(isPaned)await updateLayout" in source
    assert "sessionRow(session,'loose')" not in source
    assert "a.created_at-b.created_at" in source
