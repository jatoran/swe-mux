from __future__ import annotations

from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains


def test_default_and_custom_terminal_creation_keep_split_semantics_explicit() -> None:
    source = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    )

    assert source_contains(source, "void spawnTerminal()")
    assert source_contains(source, "New terminal custom…")
    # A launch profile now exists for agent harnesses too, so the browser no longer
    # gates `profile_id` on `shell`. The daemon refuses a profile whose own backend
    # does not match, which is the check the gate used to stand in for.
    assert source_contains(source, "profile_id: profileId || undefined")
    assert not source_contains(source, "profile_id: backend==='shell'")
    # The split-launching variant is palette-only now (no context menu reshapes the
    # pane tree), so the registry entry is the whole surface — its label and its
    # explicit 'horizontal' split argument are what keep the semantics distinct from
    # the unsplit "New terminal custom…" above.
    assert source_contains(source, "New custom terminal in selected session split")
    assert source_contains(source, "openLauncher(commandSession.project_id, 'horizontal')")
    assert source_contains(source, "spawnTerminal(launcherProject, launcherSplit, launcherProfile)")
    assert source_contains(source, "backend, project_id: targetProject")
    assert source_contains(source, "setLayoutMap") and source_contains(source, "await updateLayout")


def test_terminal_creation_is_visible_optimistically_before_the_api_returns() -> None:
    source = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    )
    before_request = source.split("const next = await api<Session>('POST', '/api/sessions'", 1)[0]

    assert source_contains(before_request, "pendingTerminal(pendingId,target,backend)")
    assert source_contains(
        before_request, "placePendingTerminal(currentLayout,pendingId,placement)"
    )
    assert source_contains(before_request, "setActiveId(pendingId)")
    assert source_contains(source, "pending-terminal-body")


def test_agent_pane_headers_use_the_configurable_cwd_metric() -> None:
    root = Path(__file__).parents[1]
    source = localized_source((root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    topbar = localized_source(
        (root / "frontend" / "src" / "sessionTopbarConfig.ts").read_text(encoding="utf-8")
    )

    assert source_contains(source, "const agentSession=isAgent(session)")
    assert source_contains(source, "<SessionTopbar session={session}")
    assert source_contains(topbar, "{kind:'metric',id:'cwd',mode:'notable'}")
    # Pending terminals still use their fixed pre-session header and omit cwd for agents.
    assert source_contains(source, '{!agentSession&&<div class="pane-path">{session.cwd}</div>}')


def test_pane_headers_name_the_session_instead_of_restating_its_state() -> None:
    """The header's first field is the session name, bounded so it cannot crowd the controls.

    State is already on the tab, the sidebar row, and the terminal being read; the name is the
    field those surfaces crop. The track is `fit-content()` rather than `auto` because an `auto`
    track takes its max-content size before the flexible track expands, so a sentence-length
    generated title would take the cwd's space and squeeze the pane tools to their floor.
    """
    root = Path(__file__).parents[1]
    source = localized_source((root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((root / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(source, "const paneTitle=sessionName(session)||id")
    assert source_contains(
        source, '<span class="pane-title" title={paneTitleHint}>{paneTitle}</span>'
    )
    # No status text in the bar; the reading it replaced survives only in the tooltip.
    assert not source_contains(source, "pane-state")
    assert source_contains(source, "sessionStatus(session),")
    # Faults keep a marker, because an agent header draws no path chip to report them - but
    # the predicate lives in `sessionStatus.ts`, where it is unit-tested against a healthy
    # session. Deciding it inline here is what made the marker fire on all 17 live sessions.
    assert source_contains(source, 'class="pane-fault"')
    assert source_contains(source, "const paneFaults=sessionFaults(session)")
    assert source_contains(css, ".session-topbar .pane-identity{min-width:0;max-width:35cqw")
    assert source_contains(css, ".pane-title { min-width:0;overflow:hidden;text-overflow:ellipsis")


def test_desktop_drawer_note_search_precedes_the_agent_action() -> None:
    root = Path(__file__).parents[1]
    source = localized_source(
        (root / "frontend" / "src" / "ProjectResource.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((root / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(source, 'class="resource-find"')
    assert source_contains(
        css,
        ':where(:root[data-workspace-layout="desktop"]) '
        ".utility-drawer .drawer-note-host .resource-actions .resource-find{order:-1",
    )
    assert source_contains(css, "min-width:31px")
    assert source_contains(css, "font-size:13px")


def test_settings_panel_is_top_aligned_and_viewport_bounded() -> None:
    css = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(encoding="utf-8")
    )

    viewport_contract = css.split(
        "/* Settings must stay inside the browser viewport at every window height. */", 1
    )[1]
    # 42px at chrome scale 1. The offset tracks `--ui-scale` because the mobile
    # topbar it sits under does; pinning it would overlap or gap at other scales.
    assert source_contains(viewport_contract, "height:calc(100dvh - calc(42px*var(--ui-scale)))")
    assert source_contains(viewport_contract, "align-items:flex-start")
    assert source_contains(viewport_contract, "overflow:hidden")
    assert source_contains(viewport_contract, "overflow-y:auto")


def test_settings_drafts_keep_the_active_tab_and_guard_every_close_path() -> None:
    source = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "Settings.tsx").read_text(
            encoding="utf-8"
        )
    )

    assert source_contains(source, "},[initialSection])")
    assert not source_contains(source, "},[draft,initialSection])")
    assert source_contains(source, "event.target===event.currentTarget&&requestClose()")
    assert source_contains(source, "event.stopImmediatePropagation()")
    assert source_contains(source, 'role="alertdialog"')
    assert source_contains(source, "Save your changes?")
    assert source_contains(source, "discardAndLeave")
    assert source_contains(source, "saveAndLeave")
    assert source_contains(source, "dirty?'Save changes':'Saved'")
    assert source_contains(source, "requestClose('usage')")
    assert source_contains(source, "requestClose('automation')")


def test_project_ignore_editors_preserve_newlines_until_save() -> None:
    src = Path(__file__).parents[1] / "frontend" / "src"
    source = localized_source((src / "Settings.tsx").read_text(encoding="utf-8"))
    manager = localized_source((src / "ProjectsManager.tsx").read_text(encoding="utf-8"))

    # Settings keeps only the global list; the per-Project one moved to the Projects
    # registry, which is the single per-Project editor.
    assert source.count("parseIgnorePatternDraft(e.currentTarget.value)") == 1
    assert source_contains(
        source, "project_ignore_patterns:normalizeIgnorePatterns(draft.project_ignore_patterns)"
    )
    assert source_contains(manager, "parseIgnorePatternDraft(event.currentTarget.value)")
    # The registry normalizes on the way out, not while typing, and that step belongs to
    # the delta the panel sends: `projectConfig.ts` is the single place that decides what
    # a draft is worth in the file, so the panel itself must not normalize at all.
    delta = localized_source((src / "projectConfig.ts").read_text(encoding="utf-8"))
    assert source_contains(delta, "normalizeIgnorePatterns(value as string[])")
    assert not source_contains(manager, "normalizeIgnorePatterns")
    assert not source_contains(
        source, "e.currentTarget.value.split('\\n').map(item=>item.trim()).filter(Boolean)"
    )
