from __future__ import annotations

import re
from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains

ROOT = Path(__file__).parents[1]


def source(name: str) -> str:
    return localized_source((ROOT / "frontend" / "src" / name).read_text(encoding="utf-8"))


def frontend_source() -> str:
    root = ROOT / "frontend" / "src"
    return "\n".join(
        localized_source(path.read_text(encoding="utf-8")) for path in root.glob("*.ts*")
    )


def test_mobile_workspace_and_recovery_contracts_remain_available() -> None:
    combined = frontend_source()
    css = source("style.css")
    index = localized_source((ROOT / "frontend" / "index.html").read_text(encoding="utf-8"))

    assert source_contains(combined, "mobile-project-name")
    assert not source_contains(combined, "mobile-session-switcher")
    assert not source_contains(combined, "mobile-new-session")
    assert source_contains(
        css, ".pane-stack>.stack-tabs-rail>.stack-tabs{display:flex;flex-wrap:nowrap"
    )
    assert source_contains(combined, "mobileWorkspaceProjection")
    assert source_contains(combined, "mobile-unified-tabs")
    assert source_contains(
        css,
        ".pane-stack:not(.focused-pane):not(.empty-workspace-pane)"
        ":not(.mobile-unified-workspace):not(.plugin-popup-stack){display:none}",
    )
    # Mobile menus carry no geometry or ordering controls at all now — the touch
    # `Move tab` row went the way of the desktop ones.
    # test_no_context_menu_reorders_or_reshapes_anything owns that invariant.
    assert not source_contains(combined, "contextMenu.source!=='mobile'")
    assert not source_contains(combined, "mobileMoveRow")
    # `New terminal as tab` was the mobile tab menu's other source-aware row and is
    # gone with it: no context menu spawns a session on any platform, because that is
    # the Run button's job and the Run button is on the mobile rail (`ui.md`).
    # What remains source-aware is the close path — mobile has no per-tab close
    # button, so `Close tab` routes through closeMobileTab to keep neighbour focus.
    assert not source_contains(combined, ">New terminal as tab</button>")
    assert source_contains(
        combined, "if(target.source==='mobile'){closeMobileTab(target.leaf);return}"
    )
    # The device-local rail permutation went with the row that was its only writer.
    # Orphaning it would have been worse than removing it: a phone that had already
    # saved an order would have stayed pinned to it, with no surface able to change
    # or clear it. Rail order is the layout projection, full stop.
    assert not source_contains(combined, "MOBILE_TAB_ORDER_KEY")
    assert not source_contains(combined, "mux.mobileTabOrder.v1")
    assert not (ROOT / "frontend" / "src" / "mobileTabOrder.ts").exists()
    assert not source_contains(combined, "orderMobileTabs")
    assert not source_contains(combined, "savedOrder")
    assert source_contains(combined, "beginWorkspaceTabDrag")
    assert source_contains(combined, "beginProjectPointerDrag")
    assert source_contains(combined, "beginSessionPointerDrag")
    assert source_contains(combined, "showPointerDropIndicator")
    assert source_contains(css, 'data-pointer-drop-indicator="split-bottom"')
    assert source_contains(css, 'data-pointer-drop-indicator="tab-bar"')
    assert source_contains(css, 'data-pointer-drop-indicator="group-session"')
    assert "draggable=" not in source("App.tsx")
    assert "onDragStart=" not in source("App.tsx")
    assert source_contains(combined, "clipboardImage(Array.from(event.clipboardData.items))")
    assert source_contains(combined, "host.current.addEventListener('drop', drop)")
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
    assert source_contains(css, "height:var(--app-height,100dvh)")
    assert source_contains(css, "@media(max-width:760px)")
    assert source_contains(combined, "mux.focus.v1")
    assert source_contains(combined, "window.addEventListener('pageshow'")
    assert source_contains(combined, "scheduleFullRedraw()")


def test_projects_are_explicit_session_owners() -> None:
    app = source("App.tsx")
    manager = source("ProjectsManager.tsx")
    types = source("types.ts")

    assert source_contains(app, "No Projects shown")
    assert source_contains(manager, "Add project")
    assert source_contains(manager, "sidebar_visible")
    assert source_contains(app, "project_id: targetProject")
    assert source_contains(app, "Project root<input") and source_contains(app, "readOnly")
    assert source_contains(app, "session.project_id")
    assert not source_contains(app, "space_id")
    assert source_contains(types, "interface Project")
    assert source_contains(types, "root:string")


def test_project_groups_are_sidebar_only_organization() -> None:
    app = source("App.tsx")
    groups = source("projectGroups.ts")

    assert source_contains(app, "/api/project-groups")
    assert source_contains(app, "Create group")
    assert source_contains(app, "Ungrouped")
    assert source_contains(app, "group_id")
    assert source_contains(groups, "buildProjectGroups")
    assert not source_contains(groups, "layout")


def test_project_resources_share_unified_mixed_view_panes() -> None:
    app = source("App.tsx")
    layout = source("layout.ts")
    resource = source("ProjectResource.tsx")
    css = source("style.css")

    assert source_contains(app, "openNotesBrowser")
    assert source_contains(app, "openBrowsedNote")
    assert source_contains(app, "openProjectFiles")
    assert source_contains(app, "openProjectFile")
    assert source_contains(app, "ProjectResource")
    assert source_contains(app, "openTab")
    assert source_contains(app, "splitNoteResource")
    assert source_contains(app, "moveLeafToSplit")
    assert source_contains(layout, "version: 7")
    assert not source_contains(app, "showNoteWorkspace")
    assert not (ROOT / "frontend" / "src" / "NotesWorkspace.tsx").exists()
    assert source_contains(
        resource, "/api/projects/${project.id}/notes/${encodeURIComponent(resource.id)}"
    )
    assert source_contains(resource, "/api/projects/${project.id}/files")
    assert source_contains(resource, "/api/projects/${project.id}/file")
    assert source_contains(resource, "onOpenFile")
    assert source_contains(resource, "/api/projects/${project.id}/reveal")
    assert source_contains(resource, "/api/projects/${project.id}/ignore")
    assert source_contains(resource, "Open in default explorer")
    assert source_contains(resource, "Add pattern to global ignores")
    assert source_contains(resource, "Add pattern to project ignores")
    assert source_contains(css, ".project-resource")
    assert source_contains(css, ".pane-stack.tab-drop-active")
    assert not source_contains(css, ".notes-workspace-shell.popout")


def test_continuity_find_is_available_from_the_shared_resource_header() -> None:
    resource = source("ProjectResource.tsx")
    drawer = source("UtilityDrawer.tsx")

    assert source_contains(resource, 'aria-label="Find in this note"')
    assert source_contains(resource, "onClick={openFind}>⌕</button>")
    assert source_contains(resource, "autosaved||onSendToAgent")
    # Drawer notes mount the same resource component as workspace tabs, so the
    # header trigger cannot drift between the two surfaces.
    assert source_contains(drawer, "drawer-note:")
    assert source_contains(drawer, "<ProjectResource")


def test_autosave_resource_header_keeps_identity_and_save_state_on_one_row() -> None:
    """Identity and save state share one row, and the state is a light with a name.

    Three files have to agree and none of them imports the others: the markup names
    a class, the stylesheet lays it out, and `resourceSaveIndicator` decides the
    tone. A change to one is invisible to the other two, which is what this file
    exists to catch.

    The row is keyed on `autosaved` rather than on being a note, because what earns
    the compact treatment is having a save state to show at all. A resource that
    does not autosave keeps its state as plain text.
    """
    resource = source("ProjectResource.tsx")
    css = source("style.css")

    assert source_contains(resource, "class={autosaved?'autosave-resource-heading':undefined}")
    # A bare coloured dot has no accessible name, so the tone always ships with one.
    assert source_contains(resource, "aria-label={`Save status: ${saveIndicator.label}`}")
    assert source_contains(resource, "class={`resource-save-indicator ${saveIndicator.tone}`}")
    # Non-autosaving resources still say their state in words.
    assert source_contains(resource, ":<span>{stateLabel}</span>")
    # One row, and the light keeps its size when the title is long enough to
    # ellipsize. Matched against the rule's selector list rather than an exact
    # string, so grouping the selector with its neighbours stays free.
    assert source_contains(css, ".project-resource>header>.autosave-resource-heading{display:flex")
    assert source_contains(css, ".autosave-resource-heading>strong{min-width:0}")
    unshrinkable = [
        selectors
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        if source_contains(body, "flex:none")
    ]
    assert any(
        source_contains(group, ".autosave-resource-heading>.resource-save-indicator")
        for group in unshrinkable
    ), "the save light is allowed to shrink, so a long title can squeeze it away"


def test_every_save_indicator_tone_has_a_colour() -> None:
    """A tone with no rule renders as the neutral default and reads as "saved-ish".

    `resourceSaveIndicator` is the only thing that mints tones and the stylesheet is
    the only thing that colours them, so adding a tone without a rule is a silent
    downgrade: an error state that looks idle. `pending` is deliberately the base
    style, which is why it is excluded rather than required to have its own rule.
    """
    indicator = source("resourceSaveIndicator.ts")
    css = source("style.css")

    declared = set(re.findall(r"tone: '([a-z]+)'", indicator))
    assert declared, "no tones declared; this guard would assert nothing"
    assert "pending" in declared, "the neutral base tone is expected to exist"
    assert source_contains(css, ".resource-save-indicator{")
    for tone in sorted(declared - {"pending"}):
        assert f".resource-save-indicator.{tone}{{background:" in css, (
            f"tone {tone!r} has no colour rule, so it renders as the neutral default"
        )


def test_note_selection_can_be_consumed_only_after_an_accepted_agent_handoff() -> None:
    resource = source("ProjectResource.tsx")
    picker = source("SendToAgentPicker.tsx")

    assert source_contains(resource, "isNote&&selected&&snapshot&&!slice.truncated")
    assert source_contains(resource, "removeSelectionAfterSend")
    assert source_contains(picker, "const [removeSelection, setRemoveSelection]")
    assert source_contains(picker, "checked={removeSelection}")
    assert picker.count("finishAccepted()") == 2
    assert source_contains(picker, "if (result.status === 'done')")


def test_notes_are_decoupled_from_terminals_and_open_in_the_anchor_pane() -> None:
    app = source("App.tsx")
    layout = source("layout.ts")
    assert not source_contains(app, "note-chip")
    assert not source_contains(app, "noteChipState")
    assert not source_contains(app, "openSessionNotes")
    assert not source_contains(app, "session.note")

    # Opening a resource is not a layout command. Every kind lands as a tab in the anchor's
    # pane; nothing splits the workspace on the user's behalf.
    assert source_contains(app, "openTab(current,focused,resourceLeaf('note',resourceId))")
    # (`layout.ts` still names the retired helper in a comment saying why it is gone.)
    assert not source_contains(layout, "export function placeCompanionLeaf")
    assert not source_contains(app, "placeCompanionLeaf(")
    assert not source_contains(app, "target.kind==='session-note'&&!targetViewId")


def test_notes_tab_manages_the_project_owned_collection() -> None:
    app = source("App.tsx")
    notes = source("NotesTab.tsx")
    drawer = source("UtilityDrawer.tsx")
    css = source("style.css")

    assert source_contains(notes, "/api/notes")
    assert source_contains(notes, "/api/projects/${project.id}/notes")
    assert source_contains(notes, "project_id=${encodeURIComponent(scopeId)}")
    assert source_contains(notes, "Filter notes by project")
    assert source_contains(notes, "Search notes")
    assert source_contains(notes, "All projects")
    assert source_contains(css, ".project-note-row") and source_contains(css, ".notes-subtabs")
    assert source_contains(notes, 'class="notes-new"')
    assert source_contains(notes, "mode:'rename'")
    assert source_contains(drawer, "onOpenNote")
    assert source_contains(notes, 'role="tab"')
    assert source_contains(notes, "SCRATCHPAD_TAB_ID")
    assert source_contains(notes, "stableProjectNoteTabs")
    assert source_contains(notes, 'class="notes-browser"')
    assert source_contains(drawer, "onOpenScratchpad")
    assert source_contains(app, "kind:'global-note',resourceId:'scratchpad'")
    assert source_contains(app, "id: 'notes.scratchpad'")

    # The retired modal is gone, and its three entry points now open the drawer tab.
    assert not (ROOT / "frontend" / "src" / "SessionNotesBrowser.tsx").exists()
    assert not source_contains(app, "SessionNotesBrowser")
    assert source_contains(drawer, "case 'notes':")
    assert source_contains(app, "openNotesBrowser")
    assert source_contains(app, "id: 'notes.browse'")
    assert source_contains(app, "runNamedCommand('notes.browse')")
    assert source_contains(app, "openBrowsedNote")

    # The note selection is remembered separately from temporary editor ownership. Closing
    # the drawer leaves the per-Project selection intact; the editor host is merely unmounted.
    set_open = app[app.index("const setClipboardOpen=") : app.index("const selectDrawerTab=")]
    assert not source_contains(set_open, "releaseDrawerNote")
    assert source_contains(drawer, "selectedResourceId={props.drawerNoteId}")
    # Notes is dispatched outside the segmented body so its editor is never unmounted by a
    # tab switch. Files joined it there for the same reason once it began hosting editors too.
    assert source_contains(
        drawer,
        "selected !== 'notes' && selected !== 'files' "
        "&& renderSegmentedBody(stack.id, selected, segment)",
    )

    # Scope follows how you arrived. Every scope-less entry point (rail, strip, drawer.notes)
    # goes through showDrawerTab and means "this Project"; only the app menu's unscoped
    # notes.browse widens it, via openNotesBrowser, which is not on that path.
    assert source_contains(app, "if(tab==='notes')setNotesAllProjects(false)")
    assert source_contains(app, "setNotesAllProjects(!scope)")
    show_tab = app[app.index("const showDrawerTab=") : app.index("const openDrawerTab=")]
    open_tab = app[app.index("const openDrawerTab=") : app.index("const persistDrawerWidth=")]
    assert not source_contains(open_tab, "setNotesAllProjects")
    assert source_contains(show_tab, "setNotesAllProjects(false)")


def test_files_is_a_drawer_navigator_rather_than_a_workspace_tab() -> None:
    """Files hosts what it opens, so it costs a drawer tab, not a permanent one.

    The layout used to carry a Files leaf and route every placement rule around it. That
    special-casing is gone with the leaf: a persisted `files:` leaf is pruned on read.
    """
    app = source("App.tsx")
    layout = source("layout.ts")
    drawer = source("UtilityDrawer.tsx")
    tabs = source("drawerTabs.ts")

    assert source_contains(tabs, "'files'") and source_contains(tabs, "'notes'")
    assert source_contains(drawer, "case 'files':")
    assert source_contains(drawer, "resource={{ kind: 'files', id: project.id }}")
    # Opened, never toggled: a click that names a surface and switches Project must not
    # close the panel it was asking for.
    assert source_contains(app, "openDrawerTab('files',project.id)")
    assert source_contains(app, "openDrawerTab('notes',")

    # No Files leaf, and no placement rule that has to dodge one.
    assert source_contains(layout, "isRetiredFilesLeaf")
    for retired in ("stackHasFiles", "isFilesLeaf", "defaultProjectLayout", "DEFAULT_FILES_RATIO"):
        assert retired not in layout, retired
        assert retired not in app, retired
    # A never-arranged Project is left empty rather than seeded with a Files column.
    assert not source_contains(app, "seededLayouts")

    # Dragging a file row onto a pane survives the move, but only on desktop: the mobile
    # drawer is an overlay with no visible pane to drop onto.
    assert source_contains(app, "onFileDragStart={mobileWorkspace?undefined:")


def test_the_files_tab_hosts_the_files_it_opens() -> None:
    """A picked file opens in the Files tab, and its pane placement stays one gesture away.

    Two claims here are cross-file and cannot be reached from either side alone.

    The first is a correctness rule and is why this is not merely an ergonomic change. A file
    is edited through the module-scoped save queue a note is, so two mounted editors on one
    file discard one side's text with no conflict the daemon can see. The drawer therefore
    claims a file from its pane exactly as it claims a note, and only the *showing* chip is
    mounted - the other open chips are rail entries with no editor at all.

    The second is why the state is device-local rather than layout state: `project.layout` is
    persisted server-side and shared, so the old behaviour let a phone rearrange the desktop's
    panes just by browsing files.
    """
    app = source("App.tsx")
    drawer = source("UtilityDrawer.tsx")
    resource = source("ProjectResource.tsx")
    files = source("drawerFiles.ts")

    # The Files tab hosts editors now, so its body is dispatched outside the segmented switch
    # and kept mounted, exactly as the Notes host is and for the same two failures.
    assert source_contains(drawer, "const renderFilesHost")
    assert source_contains(drawer, "{filesHere && renderFilesHost(selected === 'files')}")
    assert source_contains(drawer, "resource={{ kind: 'file', id: activeDrawerFile }}")

    # Device-local, per Project, and never written into the shared layout.
    assert source_contains(files, "mux.drawer.files.v1")
    assert source_contains(app, "DRAWER_FILES_KEY") and source_contains(app, "serializeDrawerFiles")
    assert source_contains(app, "pruneDrawerFiles")
    for layout_writer in ("updateLayout", "resourceLeaf", "openTab"):
        assert layout_writer not in files, layout_writer

    # One live editor per file per browser: the pane leaf stands down while the drawer shows
    # it, the same way a claimed note's leaf does.
    assert source_contains(
        app, "isDrawerFileOwned(drawerFiles,activeProject.id,identity.id,clipboardOpen)"
    )
    assert source_contains(app, "workspace-leaf-placeholder note-in-drawer")

    # The pane placement is named rather than implied, because it stopped being the default.
    assert source_contains(resource, "type FileOpenIntent='default'|'pane'")
    assert source_contains(resource, "event.ctrlKey||event.metaKey?'pane':'default'")
    assert source_contains(resource, "Open in a pane")
    assert "onOpenInPane" in source("DrawerFilesRail.tsx")

    # Unsaved work is a refusal the cap honours, not a warning it prints afterwards.
    assert source_contains(app, "keep:unsavedFilePaths(targetProject)")
    assert source_contains(resource, "export function unsavedFilePaths")


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
    # Re-checked at thirteen (Timeline): it is a compact session-scoped reader and uses the
    # same one-row scroller and selected-tab reveal contract.
    # Back to twelve: Actions combines the former Commands and Prompts tabs without changing
    # the one-row scroller or selected-tab reveal contract.
    # Re-checked at thirteen (Change Map, Phase 7.9): a session-scoped compact reader like
    # Insight and Transcript, using the same one-row scroller and selected-tab reveal contract.
    # It is the thirteenth 36px stop in a scroller that was already scrolling below ten, so the
    # header machinery is unchanged; the bar it passes is "does this surface belong beside a
    # terminal", and a per-session code change map read against the focused session does.
    # Re-checked at fourteen (Schedule): a Project-scoped surface that sits with Processes and
    # is the fourteenth 36px stop in a scroller that has been scrolling since ten, so the
    # header machinery below is unchanged and the assertions here still hold. On the actual
    # bar - "does this surface belong beside a terminal" - it does: what it offers is pause
    # this, run it now, and is last night's scheduled session still open, and the last of
    # those is only answerable with the workspace on screen. Its own geometry is pinned
    # separately by `frontend/test/renderer/schedule-layout.spec.ts`, because the drawer
    # column is the narrowest surface in the app and this tab is the densest row in it.
    #
    # Back to eleven (the drawer consolidation): Clipboard became a *section* of Actions,
    # Change Map became a segment of Activity (renamed from Insight), and Agent Context
    # became Agent's Instructions segment. A count going down is the healthy direction, and
    # the rule that decided each one is the same bar as always, applied per surface: a
    # low-frequency *inspection* surface can afford one more click, and an *injection*
    # surface cannot - which is why Clipboard is a co-visible section rather than a segment,
    # and why the Queue, which has a send button, was left alone.
    # Nothing about the header machinery changes: eleven 36px stops is still a scroller that
    # has been scrolling since ten.
    ids = re.findall(r"\{ id: '([a-z]+)'", tabs)
    assert len(ids) == 11, ids
    tab_css = css[css.index(".drawer-tabs{") : css.index(".drawer-tabs::")]
    assert source_contains(tab_css, "flex-wrap:nowrap") and source_contains(
        tab_css, "overflow-x:auto"
    )
    assert not source_contains(drawer, "drawer-chrome")
    assert source_contains(
        css, ".drawer-tabs button{position:relative;min-height:34px;flex:1 0 32px"
    )
    assert source_contains(
        css, ".drawer-tabs button{min-height:44px;flex-basis:36px;min-width:36px"
    )
    assert source_contains(drawer, "activeKey={selected}")
    assert source_contains(
        rail, 'querySelector<HTMLElement>(\'[role="tab"][aria-selected="true"]\')'
    )
    assert source_contains(rail, "revealItem(strip, selected)")
    icon_map = icons[icons.index("DRAWER_TAB_ICONS") :]
    for tab_id in ids:
        assert re.search(rf"^  {tab_id}: \w+Icon,$", icon_map, re.MULTILINE), tab_id
    # And nothing left over: a mark for a retired tab is dead weight that reads as a tab
    # somebody forgot to register.
    assert len(re.findall(r"^  \w+: \w+Icon,$", icon_map, re.MULTILINE)) == len(ids)

    # Segments and sections are registered beside the tabs and generate their own commands,
    # which is what keeps a folded-in surface as reachable by name as it was as a tab.
    segments = source("drawerSegments.ts")
    assert source_contains(segments, "export const DRAWER_SEGMENTS")
    assert source_contains(app, "...DRAWER_SEGMENTS.map((segment): Command => ({")
    assert source_contains(drawer, "<DrawerSegmentControl")

    # No text glyph survives. The configured primary mark is either the short title or icon.
    assert not source_contains(tabs, "glyph")
    assert not source_contains(drawer, "glyph")
    assert source_contains(drawer, "props.tabDisplay === 'title'")
    assert source_contains(drawer, '<span class="drawer-tab-title">{item.label}</span>')
    assert source_contains(app, "utilityRailDisplay==='title'")
    assert source_contains(app, '<span class="drawer-tab-title">{tab.label}</span>')
    assert source_contains(drawer, "<Icon />") and source_contains(app, "<Icon/>")

    # Icon-only means the accessible name can no longer come from visible text. Session tabs
    # also name the scope represented by their lower-right dot; Project/app tabs stay unchanged.
    # A badged tab (Alerts, Queue) names its count too: `aria-label` replaces the button's
    # content, so a count left only in the pill is one a screen reader never hears.
    assert source_contains(
        drawer,
        "aria-label={`${item.label}${item.scope === 'session' ? ', session scoped' : ''}"
        "${badge ? `, ${badge.label}` : ''}`}",
    )
    assert source_contains(
        app,
        "aria-label={`${tab.title}${tab.scope==='session'?'. Session scoped.':''}"
        "${badge?` ${badge.label}.`:''}`}",
    )
    assert source_contains(css, '.drawer-tabs button[data-scope="session"]:before')
    assert source_contains(css, '.utility-rail button[data-scope="session"]:before')
    assert source_contains(css, "width:3px;height:3px")
    assert source_contains(
        css, "background:color-mix(in srgb,var(--muted) 52%,var(--panel2));box-shadow:none"
    )

    # Sized in CSS on both surfaces: these run a 9-12px font, so `1em` would be unreadable.
    assert source_contains(css, ".drawer-tabs button svg{width:17px")
    assert source_contains(css, ".utility-rail button svg{width:16px")
    # Touch has no rail, so the strip is the only tab control and gets a 44px target on the
    # axis it can afford: height. Width is capped by the overlay (see the floors above).
    assert source_contains(css, ".drawer-tabs button{min-height:44px;")


def test_hidden_drawer_tabs_stay_a_render_filter_with_a_way_back() -> None:
    """Putting a tab away must never edit the layout, and must never be a one-way door."""
    app = source("App.tsx")
    drawer = source("UtilityDrawer.tsx")
    settings = source("Settings.tsx")
    visibility = source("drawerVisibility.ts")

    # Persistence is the hidden set alone. Nothing here may reach for the layout writers:
    # normalization re-inserts a missing tab at its *canonical* position, so removing one
    # from the tree would discard wherever the user dragged it.
    assert source_contains(visibility, "DRAWER_HIDDEN_KEY = 'mux.drawer.hidden.v1'")
    assert source_contains(
        app, "storeDrawerValue(DRAWER_HIDDEN_KEY,serializeHiddenDrawerTabs(next))"
    )
    assert not source_contains(visibility, "moveDrawerTab")
    assert not source_contains(visibility, "serializeDrawerLayout")

    # The way back is on the strip the tab was hidden from, not only in Settings.
    assert source_contains(app, 'id="drawer-visible-tabs"')
    assert source_contains(
        app,
        "label={`Panels · ${DRAWER_TABS.length-hiddenDrawerTabs.length} of ${DRAWER_TABS.length}`}",
    )
    assert source_contains(app, ">Show all</button>")
    assert source_contains(settings, 'class="drawer-tab-visibility"')

    # An empty pane is dropped rather than drawn under a fallback heading, and a drawer
    # with nothing left carries its own control back.
    assert source_contains(drawer, "node.tabs.some(tabAvailable) ? renderStack(node) : null")
    assert source_contains(drawer, "renderEmptyDrawer()")
    assert source_contains(drawer, ">Choose panels…</button>")


def test_drawer_tabs_use_recursive_device_local_layout_and_pane_dragging() -> None:
    """Pane rails edit one recursive local layout while the outer rail remains a mirror."""
    app = source("App.tsx")
    drawer = source("UtilityDrawer.tsx")
    layout = source("drawerLayout.ts")
    order = source("drawerTabOrder.ts")
    css = source("style.css")

    # The recursive layout feeds pane rails. The depth-first projection feeds the launcher.
    assert source_contains(app, "layout={drawerLayout}")
    assert source_contains(app, "presentation={renderedDrawerPresentation}")
    # One predicate answers "is this tab drawn" for the launcher rail and every pane
    # strip alike, so a hidden tab cannot survive in one of them. `drawerVisibility.ts`
    # owns the rule; neither surface may re-derive it.
    assert source_contains(
        app,
        "drawerLauncherTabs.filter(tab=>drawerTabVisible(tab.id,"
        "{hidden:hiddenDrawerTabs,hasTranscript:hasHarnessTranscript(active?.backend)}))",
    )
    assert source_contains(
        drawer, "visibleDrawerTabs(stack.tabs, visibility).map((id, index, visibleTabs) => {"
    )
    assert source_contains(drawer, "renderNode(layout.root)")
    assert source_contains(app, "onTabDragStart={beginDrawerTabDrag}")
    assert source_contains(drawer, "props.onTabDragStart(event, id)")
    assert not source_contains(app, "onPointerDown={event=>beginDrawerTabDrag(event,tab.id)}")

    # The app pointer-drag contract keeps a prospective tree in refs and commits once.
    assert source_contains(app, "beginPointerDrag(event,drawerTab(id).label")
    assert not source_contains(drawer, "draggable")
    assert source_contains(drawer, "data-reorder-id={id}")
    assert source_contains(app, "dragDrawerLayoutRef.current=moveDrawerTabToStack")
    assert source_contains(app, "dragDrawerLayoutRef.current=edge")
    assert source_contains(app, "commitDrawerLayout(next,id)")
    assert source_contains(css, ".drawer-tabs button[data-pointer-drop-indicator")
    assert source_contains(css, ".drawer-pane[data-pointer-drop-indicator")
    assert source_contains(app, "suppressDragClickRef.current===`drawer-tab:${tab}`")

    # Legacy flat order is input only. Recursive state has its own device-local key.
    assert source_contains(layout, "mux.drawer.layout.v1")
    assert source_contains(app, "loadDrawerTabOrder()")
    assert source_contains(app, "loadSettings().then")
    assert source_contains(app, "!drawerLegacySettingsReady")
    assert source_contains(app, "localStorage.getItem(DRAWER_LAYOUT_KEY)===null")
    assert not source_contains(app, "saveDrawerTabOrder")
    assert not source_contains(app, "mux:settings-changed',adopt")

    # Normalization preserves singleton ownership and reset restores the canonical stack.
    assert source_contains(layout, "export function normalizeDrawerLayout")
    assert source_contains(order, "export function normalizeDrawerTabOrder")
    assert source_contains(app, "id: 'drawer.resetLayout'")


def test_drawer_tab_display_is_live_and_searchable() -> None:
    app = source("App.tsx")
    settings = source("Settings.tsx")
    drawer = source("UtilityDrawer.tsx")
    css = source("style.css")

    assert source_contains(app, "drawer_tab_display?:'icon'|'title'")
    assert source_contains(app, "utility_rail_display?:'icon'|'title'")
    assert source_contains(
        app, "setDrawerTabDisplay(config.drawer_tab_display==='title'?'title':'icon')"
    )
    assert source_contains(
        app, "setUtilityRailDisplay(config.utility_rail_display==='title'?'title':'icon')"
    )
    # Both controls are the app's own `Dropdown` rather than a native `<select>`; the choices
    # moved from `<option>` children into an `options` prop with them.
    assert source_contains(settings, "Drawer tabs<Dropdown")
    assert source_contains(settings, "Right rail<Dropdown")
    assert source_contains(settings, "{value:'icon',label:'Icons'}")
    assert source_contains(settings, "{value:'title',label:'Titles'}")
    assert source_contains(app, "tabDisplay={drawerTabDisplay}")
    assert source_contains(app, "utilityRailDisplay==='title'")
    assert source_contains(app, "const utilityRailWidth=utilityRailDisplay==='title'?112:40")
    assert source_contains(drawer, "props.tabDisplay === 'title'")
    assert source_contains(app, "surface:'tabs'|'rail'")
    assert source_contains(app, "Collapse utility drawer")
    assert source_contains(app, "drawer-display-menu")
    assert source_contains(css, ".drawer-display-menu{z-index:70}")
    assert source_contains(drawer, "onTab(id, id === selected)")
    assert source_contains(drawer, "beginTabLongPress")
    assert not source_contains(drawer, "drawer-chrome")
    assert source_contains(app, "--utility-rail-width")
    assert source_contains(css, "var(--utility-rail-width,40px)")


def test_recursive_drawer_exposes_tab_and_separator_accessibility() -> None:
    drawer = source("UtilityDrawer.tsx")

    assert source_contains(drawer, "role:'tablist'")
    assert source_contains(drawer, 'role="tab"')
    assert source_contains(drawer, 'role="tabpanel"')
    assert source_contains(drawer, "aria-controls={panelDomId(stack.id)}")
    assert source_contains(drawer, "aria-labelledby={tabDomId(stack.id, selected)}")
    assert source_contains(drawer, "tabIndex={id === selected ? 0 : -1}")
    assert source_contains(drawer, "event.key !== 'ArrowLeft' && event.key !== 'ArrowRight'")
    assert source_contains(drawer, "aria-valuenow={Math.round(node.ratio * 100)}")
    assert source_contains(drawer, "onDblClick={() => updateRatio(0.5)}")
    assert source_contains(drawer, "onPointerDown={projection")
    assert source_contains(
        drawer, "beginTabLongPress(event, id); props.onProjectionTabReorder?.(event, id)"
    )
    assert source_contains(
        drawer, "? (mobile ? renderStack(mobileStack, focusedTab) : renderNode(layout.root))"
    )
    assert source_contains(drawer, 'aria-live="polite"')


def test_menu_scope_follows_the_menu_that_opened_the_surface() -> None:
    """The app menu browses every Project; a Project row browses that Project."""
    app = source("App.tsx")
    view = source("ProcessFleetView.tsx")
    panel = source("ResourcesModal.tsx")

    main_menu = app[app.index('aria-label="swe-mux menu"') : app.index('class="sidebar-scrim"')]
    # The lead block carries no heading: it is the app's general-purpose surfaces,
    # not a "browse projects" section. Nothing that acts on a single Project lives
    # in the app menu at all — that belongs to the Project's own context menu.
    assert not source_contains(main_menu, "BROWSE ALL PROJECTS")
    assert not source_contains(main_menu, "CURRENT PROJECT")
    # The app menu opens each browser unscoped, never its project-scoped variant.
    # Processes, bandwidth, storage, and fleet activity are one `Resources` row, so the menu
    # names the dialog rather than four of its segments; `processes.all` survives as a
    # command and as the sidebar menu's row, and still opens the dialog on Processes.
    #
    # `usage.open` is a row again, and a separate one. Spend was a segment of Resources,
    # where the surface named for money had no total on its first screen and three of its
    # six tabs measured neither a token nor a dollar. A subject nobody finds by guessing
    # which meter it was filed under is what a menu row is for.
    for command in (
        "history.open",
        "notes.browse",
        "resources.open",
        "usage.open",
        "prompts.open",
    ):
        assert f"runNamedCommand('{command}')" in main_menu
    for retired_row in ("networkUsage.open", "storageUsage.open", "fleetActivity.open"):
        assert f"runNamedCommand('{retired_row}')" not in main_menu
    for scoped in (
        "history.openProject",
        "processes.project",
        "prompts.openProject",
        "project.settings",
    ):
        assert scoped not in main_menu

    project_menu = app[
        app.index("aria-label={`Project actions for") : app.index('aria-label="Sidebar actions"')
    ]
    # History survives as the prefiltered surface with nowhere else to be reached from.
    assert source_contains(project_menu, "runNamedCommand('history.openProject')")
    # Notes, Processes, the fleet queue, Browse files, and Prompt library each left the
    # Project menu. Every one already opens scoped to the selected Project from the drawer
    # or another nearby surface, so the menu row duplicated a route one click away. They
    # keep their palette commands.
    for elsewhere in (
        "openNotesBrowser(target)",
        "processes.project",
        "queue.fleetProject",
        "prompts.openProject",
    ):
        assert elsewhere not in project_menu
    assert not source_contains(project_menu, "openProjectFiles(")
    for command in (
        "notes.browseProject",
        "processes.project",
        "project.files",
        "prompts.openProject",
    ):
        assert f"id: '{command}'" in app or f"id:'{command}'" in app
    # The category headings went with them: two headings over three rows apiece in a menu
    # of nine spent a fifth of its height saying what each row's own icon now says.
    assert not source_contains(project_menu, "BROWSE THIS PROJECT")
    assert not source_contains(project_menu, 'context-subtitle">PROJECT<')

    # Scope is a visible, clearable control rather than a hidden mode, on every shell that
    # draws the surface — including the drawer tab, which opens scoped to the active Project.
    assert source_contains(view, "process-scope-select")
    # A `Dropdown` now, so the clearing row is an option object rather than an `<option>`.
    assert source_contains(view, "{ value: '', label: 'All projects' }")
    assert source_contains(panel, "initialProjectId")
    assert "projectScope={scope}" in source("ProcessesTab.tsx")
    # And the drawer tab is not made redundant by the dialog's Processes segment: a modal
    # covers the terminal, so the tab is what answers "what is *this* session running"
    # beside it. It ships hidden by default rather than removed.
    assert "focusedSessionId" in source("ProcessesTab.tsx")
    assert "'processes'" in source("drawerVisibility.ts")
    # History is a global overlay (not a per-project pane), opened with an
    # optional scope that pre-filters its own clearable project picker.
    assert source_contains(app, "const showHistory = (scope:Project|null=null)")
    assert source_contains(app, "{historyOpen&&<HistoryBrowser")


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
    assert not source_contains(app, "worktreeCreate")
    assert not source_contains(app, "manageWorktrees")
    assert not source_contains(app, "Create worktree")
    run_menu = source("ProjectRunMenu.tsx")
    assert source_contains(run_menu, "New worktree session")
    # Both phases of the launch are App's, so the optimistic pane can exist before the checkout
    # does. The form only collects the draft and renders the creation failure it gets back.
    assert not source_contains(run_menu, "'/api/git/worktrees'")
    assert source_contains(
        run_menu, "onWorktreeLaunch({path,branch,startPoint:worktree.startPoint.trim(),"
    )
    assert source_contains(app, "'/api/git/worktrees'")
    assert source_contains(app, "'/api/git/worktrees/session'")
    assert source_contains(app, "pendingTerminal(pendingId,target,backend,{")
    assert source_contains(app, "label:'Creating worktree…'")
    assert source_contains(app, "pending_label:'Setting up worktree…'")
    # A worktree launch owns a real leaf in the focused pane like every other launch, because an
    # unpanned view is drawn nowhere at all on a phone.
    assert source_contains(
        app, "const optimisticLayout=placePendingTerminal(currentLayout,pendingId,placement)"
    )
    # The refresh pass that replays pending placements moved into the layout planner, which keeps
    # a launch the operator is watching as the pane's active tab across a long bootstrap.
    planner = source("fleetLayouts.ts")
    assert source_contains(
        planner, "if (pending.projectId !== project.id || !pending.placement) continue"
    )
    watched = "const watched = joinAnchor.projectId === project.id && joinAnchor.viewId === id"
    assert watched in planner
    assert source_contains(app, "selectPendingTerminal(current,session.id)")
    assert source_contains(app, "setActiveId(current=>current===pendingId?next.id:current)")
    assert source_contains(run_menu, "normalizeWorktreeBranchInput(value)")
    assert source_contains(run_menu, "api<{worktree_root?:string}>('GET','/api/config')")
    assert source_contains(app, "timeoutMs:35*60*1000")
    assert source_contains(run_menu, "worktree-my-change")
    assert source_contains(app, "setup output is in the session scrollback")
    settings = source("Settings.tsx")
    assert source_contains(settings, "<h3>Git and worktrees</h3>")
    assert source_contains(settings, "change('worktree_root'")
    projects = source("ProjectsManager.tsx")
    assert source_contains(projects, "Worktree setup command")
    assert source_contains(projects, "setup_command")


def test_run_menu_leads_with_the_action_title_and_stays_a_menu_on_a_phone() -> None:
    run_menu = source("ProjectRunMenu.tsx")
    css = source("style.css")

    # A row shows the title and the run shape. The description is agent-facing prose
    # laid out `flex:none`, so it took the row's whole width and ellipsised the name a
    # human picks by - worst on package scripts, whose description is the script body.
    assert source_contains(run_menu, "<strong>{action.label}</strong>{hint&&<em>{hint}</em>}")
    assert source_contains(
        run_menu,
        "const hint=[action.steps.length>1?`${action.steps.length} terminals`:'',"
        "action.inputs?.length?'asks for input':''].filter(Boolean).join(' · ')",
    )
    # It is still reachable, on the row's tooltip and above the inputs form.
    assert source_contains(
        run_menu, "title={[action.description,...action.steps.map(step=>step.command)]"
    )

    # On a phone the menu is a menu, not the screen: a Project with many actions
    # scrolls inside a bounded box. `.project-run-menu` already sets `overflow:auto`,
    # and fitScrollingMenuInViewport lifts a bottom-anchored box back on-screen.
    assert source_contains(
        css,
        ".project-run-menu{width:min(calc(300px*var(--ui-scale)),calc(100vw - 28px));"
        "max-height:min(58dvh,calc(100dvh - 12px))}",
    )
    assert not source_contains(css, "left:6px!important")


def test_process_fleet_groups_sessions_and_daemon_infrastructure() -> None:
    view = source("ProcessFleetView.tsx")
    panel = source("ResourcesModal.tsx")
    fleet = source("processFleet.ts")

    assert source_contains(view, "projectProcessGroups")
    # A session's Project comes from the live session first and the sample second, so a
    # session moved between Projects re-files immediately instead of on the next sample.
    assert source_contains(
        fleet, "sessionById.get(group.session_id)?.project_id || group.project_id"
    )
    assert source_contains(panel, "All projects, sessions, and swe-mux infrastructure")
    assert source_contains(panel, "PROCESS::FLEET")
    # Drilling into one session retitles the frame, so the heading always says which of the
    # two scopes is on screen rather than leaving the fleet's title over one session's trees.
    assert source_contains(panel, "selectedSessionId ? 'Session processes' : 'System'")
    assert source_contains(view, "buildProcessTree")
    assert source_contains(view, "renderDaemonGroup")
    # The runtime keeps its own group. Its heading dropped the `swe-mux::` prefix that the
    # session-group heading immediately below it already spelled out.
    assert source_contains(view, "daemon + infrastructure")
    assert source_contains(view, "swe-mux runtime")
    assert source_contains(view, "daemon-owned child not attributed to a terminal session")
    # ...and it is excluded by a Project scope, because it belongs to no Project.
    assert source_contains(view, "if (!daemon || selectedSessionId || projectScope) return null")
    # Totals follow what is on screen: the daemon's own figures plus the runtime when
    # unscoped, recounted from the visible rows when not.
    assert source_contains(view, "fleetViewTotals(snapshot, sessionGroups, scoped)")


def test_accessibility_and_startup_instrumentation_remain_intact() -> None:
    combined = frontend_source()
    css = source("style.css")

    assert source_contains(combined, 'role="dialog"')
    assert source_contains(combined, 'role="menu"')
    assert source_contains(combined, 'aria-live="assertive"')
    assert source_contains(combined, "useModalFocus")
    assert source_contains(css, "prefers-reduced-motion:reduce")
    assert source_contains(combined, "startup_timing_ms")
    assert source_contains(combined, "client_startup_timing_ms")
    assert source_contains(combined, "/startup-metrics")


def test_browser_title_stays_stable_without_attention_count() -> None:
    combined = frontend_source()
    index = localized_source((ROOT / "frontend" / "index.html").read_text(encoding="utf-8"))

    assert source_contains(index, "<title>swe-mux</title>")
    assert not source_contains(combined, "document.title")


def test_the_transcript_tab_reads_and_only_reads() -> None:
    """The drawer's one write-nothing session surface, and the rules that keep it so.

    Every other session-scoped tab exists to put text into an agent. Mixing that into
    the surface meant for reviewing what already happened is how a stray tap becomes a
    message nobody wrote, so the absence of an insert path is a contract rather than an
    omission — including the `onDone` every sibling takes, which on mobile would close
    the drawer after each copy and end the reading.

    The contract bounds *what a tap can reach*, not how many buttons the tab has. Copy,
    select, and the per-message read-aloud markers all leave the conversation, the PTY,
    and the session exactly as they were, which is why per-message playback could join
    them while branching could not (`design/features/ui.md`, `design/features/voice.md`).
    What the tab may be *handed* is bounded here for the same reason: the session it
    reads, and display flags. `readAloud` is `tts_enabled`, and it decides only whether
    the markers are drawn at all.
    """
    app = source("App.tsx")
    topbar = source("sessionTopbarConfig.ts")
    drawer = source("UtilityDrawer.tsx")
    tab = source("TranscriptTab.tsx")
    view = source("transcriptView.ts")

    mount = re.search(r"<TranscriptTab ([^>]*)/>", drawer)
    assert mount is not None, "the drawer must mount the transcript tab"
    assert set(re.findall(r"(\w+)=", mount.group(1))) == {"session", "readAloud"}
    for injection in ("onInsert", "onDone", "onSend"):
        assert injection not in tab, injection
    # Match a session-input endpoint, not a module such as ./inputComposition.ts.
    assert not re.search(r"/api/sessions/[^\s\"\x27`]+/input", tab)
    # The one request this tab makes that is not a read asks for audio for a message it
    # is already showing. It cannot reach the conversation, and it names the message
    # rather than "the latest reply" — which is what lets the daemon answer with an
    # existing clip instead of synthesizing a second copy (`design/features/voice.md`).
    posts = set(re.findall(r"api<[^>]*>\('POST', `([^`]+)`", tab))
    assert posts == {"/api/sessions/${sessionId}/voice/generate"}, posts
    assert source_contains(tab, "message_id: message.message_id")

    # Copy and playback both leave the agent alone, and agent replies run to kilobytes,
    # so they stay out of the clipboard ring rather than evicting the snippets it
    # exists to hand back.
    assert source_contains(tab, "withoutClipboardCapture")
    assert "suppressDepth > 0" in source("clipboardHistory.ts")

    # The pane-header route is shared by desktop panes and the mobile projection. It
    # follows the same focus-first contract as Queue, is available only for harnesses
    # with transcripts, and stays adjacent to Queue in source/render order.
    opener_start = app.index("const openTranscriptForSession =")
    opener = app[opener_start : app.index("/** Pop one target's queue", opener_start)]
    assert source_contains(opener, "if (session) await selectSession(session)")
    assert source_contains(opener, "openDrawerTab('transcript',session?.project_id||projectId)")
    tools_start = app.index("renderAction={(actionId:SessionTopbarActionId)=>")
    tools = app[tools_start : app.index("menu={<button", tools_start)]
    assert source_contains(tools, "hasHarnessTranscript(session.backend)")
    assert source_contains(tools, "deliversHarnessPrompts(session.backend)")
    assert source_contains(tools, "openQueueForSession(id)")
    assert source_contains(tools, "openTranscriptForSession(id)")
    default_start = topbar.index("export function defaultSessionTopbarConfig")
    default = topbar[default_start : topbar.index("const METRIC_IDS", default_start)]
    assert default.index("action',id:'drawer:queue'") < default.index(
        "action',id:'drawer:transcript'"
    )

    # The drawer unmounts a tab body on every tab switch, so anything that has to
    # survive that cannot be component state. The scroll place is the whole reason this
    # module exists separately from the component.
    assert source_contains(view, "rememberTranscriptScroll") and source_contains(
        view, "recallTranscriptScroll"
    )
    assert source_contains(view, "let scrollMemory")
    assert not source_contains(view, "useState")

    # Refreshed after the observer consumes a user message, while the agent appends, and
    # when the assistant turn ends. A timer would re-read the conversation to learn
    # nothing most of the time; the only scheduled read is the loader's backoff retry of
    # a read that came back unfinished (`transcriptLoader.ts`).
    assert source_contains(tab, "TURN_ENDED_EVENT") and "turn_ended" in source("App.tsx")
    assert source_contains(tab, "TRANSCRIPT_CHANGED_EVENT") and "transcript_message" in source(
        "App.tsx"
    )
    assert source_contains(tab, "TRANSCRIPT_APPENDED_EVENT") and "transcript_appended" in source(
        "App.tsx"
    )
    assert not source_contains(tab, "setInterval")
    assert "setInterval" not in source("transcriptLoader.ts")
    assert "session.regenerateTitle" in source("App.tsx")
    assert "/title/regenerate" in source("App.tsx")

    # A reader already scrolled up is not carried along by an arriving message; it is
    # offered as a button instead. Yanking the column mid-sentence every time an agent
    # speaks is what makes a live log unreadable.
    assert source_contains(tab, "isPinnedToBottom")
    assert source_contains(tab, "transcript-jump") and ".transcript-jump{" in source("style.css")

    # Search is a local lens over the already loaded conversation. Copy-all remains the
    # complete conversation, while each message's copy control sticks only within that row.
    assert source_contains(tab, 'type="search"') and source_contains(tab, "transcriptMatchesQuery")
    assert source_contains(tab, "transcriptSearchParts")
    assert source_contains(tab, "transcript-copy-anchor")
    assert ".transcript-copy-anchor{position:sticky" in source("style.css")

    # Show-more is an explicit reading preference. It follows the session and run
    # across drawer unmounts, but the bounded registry contains no transcript text.
    assert source_contains(tab, "TRANSCRIPT_EXPANSION_KEY")
    assert source_contains(tab, "setTranscriptMessageExpanded")
    assert source_contains(view, "TRANSCRIPT_EXPANSION_MAX_ENTRIES = 500")
    assert (
        source_contains(view, "sessionId: string")
        and source_contains(view, "runId: string")
        and source_contains(view, "messageId: string")
    )

    # One message is one message whether it is being read live or out of history, so
    # both surfaces stamp it through the same helper rather than each carrying its own
    # date formatting. `transcriptView.ts` owns the behavior (unit-tested in
    # `frontend/test/transcriptView.test.ts`); what is pinned here is the wiring, which
    # is what silently drifts when a reader gains a feature the other does not.
    history = source("HistoryBrowser.tsx")
    for surface in (tab, history):
        assert source_contains(surface, "transcriptTimestampLabel(message.ts)")
        assert source_contains(surface, "transcriptTimestampIso(message.ts)")
        assert source_contains(surface, "TranscriptToolCalls")
        assert source_contains(surface, "showToolCalls")
    tool_calls = source("TranscriptToolCalls.tsx")
    # One formatter for an input History carries and one the reader reads on demand.
    assert source_contains(
        tool_calls, "transcriptToolInputText(lazy ? inputs.get(call.id) : call.input)"
    )
    assert source_contains(tool_calls, '<details class="transcript-tool-call"')
    # An input is drawn only while its call is open: a long session's tool calls toggled
    # on used to lay out every input as preformatted text.
    assert source_contains(tool_calls, "{isOpen && (")
    # Deliberately not the same formatter the history *list* uses: a row with no
    # timestamp still owes the reader an explanation, while a message stamp that
    # cannot be rendered is simply omitted (`{stamp&&<time…>}`).
    assert source_contains(history, "'timestamp unavailable'")
    assert not source_contains(view, "'timestamp unavailable'")


def test_history_filters_fit_narrow_split_panes() -> None:
    css = source("style.css")
    history = source("HistoryBrowser.tsx")

    assert source_contains(css, ".history-workspace { container-type:inline-size")
    assert source_contains(css, ".history-search>* { min-width:0;max-width:100% }")
    # A wrapping row of controls sized to their own widest option, not equal columns:
    # `Dropdown` already reserves that width, so a four-item list was taking half the
    # sidebar. The floor keeps a short list from collapsing and is capped at the
    # container, so a narrow sidebar wraps instead of overflowing.
    assert source_contains(
        css, ".history-search { display:flex;flex-wrap:wrap;align-items:center;gap:4px }"
    )
    assert source_contains(css, "flex:0 1 auto;min-width:min(7rem,100%);max-width:100%")
    assert not source_contains(css, ".history-search { display:grid")
    assert source_contains(css, "@container (max-width:620px)")
    # The control chooses which of the two stamps a row is ranked by, so the row has to
    # show both for the choice to mean anything. It is named as a sort because it now is
    # one: it used to scope only the date range, so toggling it changed nothing at all
    # unless a range happened to be set (`design/features/history.md`).
    assert source_contains(history, "time_basis")
    assert source_contains(history, "Sort: last activity")
    assert source_contains(history, "Sort: session started")
    # And it opens on last activity, which is what "the most recent conversations" means.
    assert source_contains(history, "useState<'started'|'last_message'>('last_message')")
    assert source_contains(history, "Started {timestampLabel(historyStart(entry))}")
    assert source_contains(history, "Last {entry.last_message_role")


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

    # `runBranch` rather than `branchSession`: the latter now decides between opening
    # the point picker and branching on the click, and it is `runBranch` that actually
    # spawns and therefore owes the focus request - reached both directly and from the
    # picker's confirm.
    # `confirmSecondOpinion` was a fourth flow until the cross-vendor review lost its
    # frontend (`test_frontend_control_plane_contract.py`); a handler that does not exist
    # cannot owe a focus request.
    # The rule is about *which* id, not about which setter appears. A flow may focus a
    # placeholder leaf directly, because the client put that leaf in the layout in the
    # same tick and there is no gap for reconciliation to read as stale - that is how the
    # History resume shows a pane from the press rather than from the answer. What must
    # never be set directly is an id the daemon just handed back.
    for flow in ("resumeHistoryEntry", "runBranch", "resumeSession"):
        body = re.search(rf"const {flow} = ?async[^\n]*\n(.*?)\n  \}}\n", app, re.DOTALL)
        assert body, f"{flow} is no longer a recognisable handler"
        assert "requestFocusView(" in body.group(1), f"{flow} must request focus, not set it"
        for target in re.findall(r"setFocusedViewId\(([^\n]*)", body.group(1)):
            assert source_contains(target, "pendingId"), (
                f"{flow} sets focus on {target.strip()}, which the layout refresh will undo; "
                "only a placeholder this flow placed itself may be focused directly"
            )

    # The request is held in a ref and consulted by the reconciliation effect, or it
    # would be dropped by the very render it exists to survive.
    assert source_contains(
        app, "const requestFocusView=(id:string)=>{pendingFocusId.current=id;setFocusedViewId(id)}"
    )
    assert source_contains(app, "requested:pendingFocusId.current")
    assert source_contains(app, "if(!keepRequest)pendingFocusId.current=null")
