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

    assert "Open space notes…" in source
    assert "Open session note…" in source
    assert "Processes and previews…" in source
    assert "Usage analytics…" in source
    assert "Hooks and notifications…" in source
    assert 'title="Session note"' in source
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

    assert "Open session note in split" in source
    assert "Open space note in split" in source
    assert 'display="pane"' in source
    assert "noteResourceId" in source
    assert "dock right" in source
    assert "pop out" in source
    assert ".note-modal" in css
    assert ".note-pane.mobile-active" in css
