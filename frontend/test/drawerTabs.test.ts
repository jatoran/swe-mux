import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  DRAWER_COLLAPSE_WIDTH,
  DRAWER_DEFAULT_WIDTH,
  DRAWER_MIN_WIDTH,
  DRAWER_REOPEN_WIDTH,
  DRAWER_TABS,
  clampDrawerWidth,
  drawerMaximumWidth,
  drawerTab,
  isNavigatorTab,
  storedDrawerWidth,
} from '../src/drawerTabs.ts'
import {
  SIDEBAR_COLLAPSE_WIDTH,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_REOPEN_WIDTH,
  clampSidebarWidth,
  dragCollapsedAtWidth,
  navigationSidebarCommandState,
  storedSidebarWidth,
} from '../src/sidebarResize.ts'

test('navigation sidebar commands target the active responsive presentation',()=>{
  assert.deepEqual(navigationSidebarCommandState(true,true),{mobileOpen:true,desktopCollapsed:null})
  assert.deepEqual(navigationSidebarCommandState(true,false),{mobileOpen:false,desktopCollapsed:null})
  assert.deepEqual(navigationSidebarCommandState(false,true),{mobileOpen:null,desktopCollapsed:false})
  assert.deepEqual(navigationSidebarCommandState(false,false),{mobileOpen:null,desktopCollapsed:true})
})

test('the navigators lead, then the session block, then the remaining Project surfaces', () => {
  // Order is the argument for the drawer existing. Notes and Files lead: they are the
  // two surfaces useful before a single session exists - a fresh workspace has notes to
  // write and files to open while every session tab still has nothing to act on. The
  // session block keeps its internal order (Actions and the prompt queue are both "text
  // into the focused terminal" and belong together; Transcript, Activity, and Agent read
  // that session back instead of writing into it). Git, Processes, and Schedule follow
  // as the Project surfaces that report rather than open, and Notifications - the one
  // application-wide fleet view that earns a permanent tab - stays last.
  //
  // Three former tabs are segments or a section of their neighbours now; the retirement
  // table lives in `drawerLayout.ts` and is covered by `drawerLayout.test.ts`.
  assert.deepEqual(DRAWER_TABS.map(tab => tab.id), ['notes', 'files', 'actions', 'queue', 'transcript', 'activity', 'agent', 'git', 'processes', 'schedule', 'notifications'])
  assert.deepEqual(DRAWER_TABS.filter(tab => tab.scope === 'session').map(tab => tab.id), ['actions', 'queue', 'transcript', 'activity', 'agent'])
  assert.deepEqual(DRAWER_TABS.filter(tab => tab.scope === 'project').map(tab => tab.id), ['notes', 'files', 'git', 'processes', 'schedule'])
  // Alerts is the only app-scoped tab. The fleet queue is app-scoped too but is a modal:
  // it has no send button, so it needs no terminal beside it, and a second queue-shaped
  // tab in the same rail reads as a duplicate of the first.
  assert.deepEqual(DRAWER_TABS.filter(tab => tab.scope === 'app').map(tab => tab.id), ['notifications'])
  assert.deepEqual(DRAWER_TABS.filter(tab => isNavigatorTab(tab.id)).map(tab => tab.id), ['notes', 'files'])
  // The navigator pair, the session block, and the reporting-Project block must each
  // stay contiguous, so the rail reads as blocks rather than an arbitrary list. (The
  // full "each scope is one block" rule retired when the navigators moved to the front:
  // the Project scope now deliberately brackets the session block.)
  const idsInOrder: string[] = DRAWER_TABS.map(tab => tab.id)
  const contiguous = (block: string[]) => {
    const start = idsInOrder.indexOf(block[0])
    assert.deepEqual(idsInOrder.slice(start, start + block.length), block)
  }
  contiguous(['notes', 'files'])
  contiguous(['actions', 'queue', 'transcript', 'activity', 'agent'])
  contiguous(['git', 'processes', 'schedule'])
  // The label is both the accessible name and the visible title-mode mark, so it must stay
  // short and distinct.
  const labels = DRAWER_TABS.map(tab => tab.label)
  assert.equal(new Set(labels).size, labels.length, 'tab labels must be distinct')
  assert.deepEqual(DRAWER_TABS.map(tab => tab.heading), [
    'Notes', 'File Explorer', 'Actions', 'Prompt Queue', 'Transcript', 'Activity', 'Agent',
    'Git', 'Processes', 'Scheduled Runs', 'Alerts',
  ])
  for (const tab of DRAWER_TABS) {
    assert.ok(tab.label.length <= 10, `${tab.id} label is too long to also serve as a name`)
    assert.ok(tab.heading.length > 0, `${tab.id} needs a canonical content heading`)
    assert.ok(tab.title.startsWith(tab.label), `${tab.id} title should lead with its label`)
  }
  // The icons live in `railIcons.tsx`, which this module must not import: it stays JSX-free so
  // it runs under plain type-stripping. A contract test checks the map covers every id.
})

test('registry lookup falls back safely for an unknown tab', () => {
  assert.equal(drawerTab('actions').label, 'Actions')
  assert.equal(drawerTab('nope' as never).id, 'notes')
})

test('App restores desktop state per Project without persisting mobile visibility', () => {
  const app = readFileSync(join(import.meta.dirname, '..', 'src', 'App.tsx'), 'utf8')
  assert.match(app, /const activeDrawerPresentation=projectId[\s\S]*?drawerProjectPresentationFor\(drawerProjectPresentations,projectId,drawerLayout\)/)
  assert.match(app, /const clipboardOpen=mobileWorkspace\?mobileDrawerOpen:\(drawerResizeOpen\?\?activeDrawerPresentation\.desktop_expanded\)/)
  assert.match(app, /if\(mobileWorkspace\)\{\s*const open=.*?setMobileDrawerOpen\(open\).*?return\s*\}/s)
  assert.doesNotMatch(app, /setClipboardOpenState/)
  assert.ok(app.includes("openDrawerTab('files',project.id)"), 'cross-Project Files actions must name their target')
  assert.ok(app.includes("openDrawerTab('notes',targetProject)"), 'cross-Project Notes actions must name their target')
  assert.ok(app.includes("openDrawerTab('queue',session?.project_id||projectId)"), 'cross-Project Queue actions must name their target')
  assert.ok(app.includes('setDrawerNoteClaimRequest({token,projectId,resourceId:drawerNoteId})'), 'spoken Notes navigation must claim the selected note')
  assert.ok(app.includes('noteTargetClaimToken={drawerNoteClaimRequest?.projectId===projectId'), 'the claim must be scoped to the current Project and selected note')
})

test('headered bodies keep compact headings while content-first bodies start with their rails', () => {
  const host = readFileSync(join(import.meta.dirname, '..', 'src', 'UtilityDrawer.tsx'), 'utf8')
  const css = readFileSync(join(import.meta.dirname, '..', 'src', 'style.css'), 'utf8')
  assert.ok(host.includes('class={`drawer-body drawer-body-${selected}`}'))
  assert.ok(host.includes('<h2 class="drawer-panel-title" title={active.title}>{heading}</h2>'))
  assert.ok(host.includes('!contentFirst && <div class="drawer-pane-heading">'))
  assert.ok(host.includes('<DrawerSegmentControl'))
  assert.match(css, /\.drawer-panel-title\{[^}]*border:[^}]*background:/)
  assert.ok(css.includes('padding-left:calc(var(--drawer-panel-title-width) + 13px)'), 'existing top chrome must make room for the heading')
})

test('both tab-icon surfaces mark session scope without using notification badges', () => {
  const host = readFileSync(join(import.meta.dirname, '..', 'src', 'UtilityDrawer.tsx'), 'utf8')
  const app = readFileSync(join(import.meta.dirname, '..', 'src', 'App.tsx'), 'utf8')
  const css = readFileSync(join(import.meta.dirname, '..', 'src', 'style.css'), 'utf8')
  assert.ok(host.includes('data-scope={item.scope}'))
  assert.ok(app.includes('data-scope={tab.scope}'))
  assert.match(css, /button\[data-scope="session"\]:before[^}]*width:3px[^}]*height:3px[^}]*border-radius:50%/)
  assert.doesNotMatch(css, /button\[data-scope="session"\]:before[^}]*\.drawer-badge/)
})

test('Notes exposes one revision-safe action model through inline and pointer menus', () => {
  const notes = readFileSync(join(import.meta.dirname, '..', 'src', 'NotesTab.tsx'), 'utf8')
  const css = readFileSync(join(import.meta.dirname, '..', 'src', 'style.css'), 'utf8')
  assert.ok(notes.includes("await api('DELETE',`/api/projects/${note.project_id}/notes/"))
  assert.ok(notes.includes('{revision:note.revision}'))
  assert.ok(notes.includes("confirming?'delete?':'×'"), 'inline delete must expose its second step')
  assert.ok(notes.includes('onContextMenu={event=>openContextMenu(note,event)}'))
  assert.ok(notes.includes('const LONG_PRESS_MS=550'))
  assert.ok(notes.includes("'Confirm delete':'Delete note'"), 'context delete must expose the same second step')
  assert.ok(notes.includes("setTitlePrompt({mode:'create',title:'Untitled note'})"))
  assert.match(css, /\.project-note-row\{[^}]*grid-template-columns:/)
})

test('dock width has no fixed maximum and preserves a minimum workspace', () => {
  assert.equal(clampDrawerWidth(120), DRAWER_MIN_WIDTH)
  assert.equal(clampDrawerWidth(9000), 9000)
  assert.equal(clampDrawerWidth(9000, 1468), 1468)
  assert.equal(clampDrawerWidth(420, Number.NaN), 420)
  assert.equal(clampDrawerWidth(420), 420)
  assert.equal(clampDrawerWidth(Number.NaN), DRAWER_DEFAULT_WIDTH)
  assert.equal(drawerMaximumWidth(1920, 258), 1468)
  assert.equal(drawerMaximumWidth(1920, 258, 112), 1396)
  assert.equal(drawerMaximumWidth(761, 484), DRAWER_MIN_WIDTH, 'drawer minimum wins when fixed chrome exhausts the viewport')
  assert.equal(storedDrawerWidth('440'), 440)
  assert.equal(storedDrawerWidth('9000'), 9000)
  assert.equal(storedDrawerWidth(null), DRAWER_DEFAULT_WIDTH)
  assert.equal(storedDrawerWidth('not-a-number'), DRAWER_DEFAULT_WIDTH)
  assert.equal(storedDrawerWidth('0'), DRAWER_DEFAULT_WIDTH)
})

test('desktop sidebar drags collapse reversibly beyond their minimum widths', () => {
  assert.equal(clampSidebarWidth(100), SIDEBAR_MIN_WIDTH)
  assert.equal(clampSidebarWidth(900), SIDEBAR_MAX_WIDTH)
  assert.equal(clampSidebarWidth(Number.NaN), SIDEBAR_DEFAULT_WIDTH)
  assert.equal(storedSidebarWidth('333'), 333)
  assert.equal(storedSidebarWidth('0'), SIDEBAR_DEFAULT_WIDTH)
  assert.equal(storedSidebarWidth(null), SIDEBAR_DEFAULT_WIDTH)

  assert.equal(dragCollapsedAtWidth(SIDEBAR_COLLAPSE_WIDTH + 1, false, SIDEBAR_COLLAPSE_WIDTH, SIDEBAR_REOPEN_WIDTH), false)
  assert.equal(dragCollapsedAtWidth(SIDEBAR_COLLAPSE_WIDTH, false, SIDEBAR_COLLAPSE_WIDTH, SIDEBAR_REOPEN_WIDTH), true)
  assert.equal(dragCollapsedAtWidth(SIDEBAR_REOPEN_WIDTH - 1, true, SIDEBAR_COLLAPSE_WIDTH, SIDEBAR_REOPEN_WIDTH), true)
  assert.equal(dragCollapsedAtWidth(SIDEBAR_REOPEN_WIDTH, true, SIDEBAR_COLLAPSE_WIDTH, SIDEBAR_REOPEN_WIDTH), false)
  assert.equal(dragCollapsedAtWidth(Number.NaN, true, SIDEBAR_COLLAPSE_WIDTH, SIDEBAR_REOPEN_WIDTH), true)

  assert.equal(dragCollapsedAtWidth(DRAWER_COLLAPSE_WIDTH, false, DRAWER_COLLAPSE_WIDTH, DRAWER_REOPEN_WIDTH), true)
  assert.equal(dragCollapsedAtWidth(DRAWER_REOPEN_WIDTH - 1, true, DRAWER_COLLAPSE_WIDTH, DRAWER_REOPEN_WIDTH), true)
  assert.equal(dragCollapsedAtWidth(DRAWER_REOPEN_WIDTH, true, DRAWER_COLLAPSE_WIDTH, DRAWER_REOPEN_WIDTH), false)
})

test('App previews drag-collapse but persists only the final state', () => {
  const app = readFileSync(join(import.meta.dirname, '..', 'src', 'App.tsx'), 'utf8')
  const css = readFileSync(join(import.meta.dirname, '..', 'src', 'style.css'), 'utf8')
  assert.ok(app.includes('drawerResizeOpen??activeDrawerPresentation.desktop_expanded'))
  assert.ok(app.includes('setDrawerResizeOpen(dragOpen)'))
  assert.ok(app.includes('setClipboardOpen(dragOpen)'))
  assert.ok(app.includes("localStorage.setItem('mux.sidebar.collapsed.v1',String(dragCollapsed))"))
  assert.match(css, /workspace\.drawer-open\{[^}]*minmax\(150px,1fr\)/)
  assert.match(css, /workspace\.sidebar-collapsed\.drawer-open\{[^}]*minmax\(150px,1fr\)/)
})
