import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  DEFAULT_DRAWER_PROJECT_STATE,
  DRAWER_COLLAPSE_WIDTH,
  DRAWER_DEFAULT_WIDTH,
  DRAWER_MIN_WIDTH,
  DRAWER_REOPEN_WIDTH,
  DRAWER_TABS,
  clampDrawerWidth,
  drawerMaximumWidth,
  drawerProjectStateFor,
  drawerTab,
  isNavigatorTab,
  migrateLegacyDrawerTab,
  nextDrawerTab,
  parseDrawerProjectStates,
  parseDrawerTab,
  pruneDrawerProjectStates,
  serializeDrawerProjectStates,
  storedDrawerWidth,
  updateDrawerProjectState,
} from '../src/drawerTabs.ts'
import {
  SIDEBAR_COLLAPSE_WIDTH,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_REOPEN_WIDTH,
  clampSidebarWidth,
  dragCollapsedAtWidth,
} from '../src/sidebarResize.ts'

test('injection surfaces lead, then navigators, then attention surfaces', () => {
  // Order is the argument for the drawer existing: clipboard, session commands, prompts
  // and the prompt queue are all "text into the focused terminal" and belong together.
  // Files and Notes are the second group — project-scoped indexes that open a document
  // into a pane instead of typing into one. Notifications is neither, and stays last.
  // Git closes the Project-scoped block: it reports on the repository behind the Project
  // rather than opening anything into a pane, so it sits with them without being a navigator.
  // Transcript closes the session block: session-scoped like the four before it, but it
  // reads that session back instead of writing into it. Processes closes the Project block
  // for the same shape of reason as Git: Project-scoped, reports rather than opens, and is
  // the watch half of a surface whose acting half stays modal.
  assert.deepEqual(DRAWER_TABS.map(tab => tab.id), ['clipboard', 'commands', 'prompts', 'queue', 'transcript', 'files', 'notes', 'context', 'git', 'processes', 'notifications'])
  assert.deepEqual(DRAWER_TABS.filter(tab => tab.scope === 'session').map(tab => tab.id), ['clipboard', 'commands', 'prompts', 'queue', 'transcript'])
  assert.deepEqual(DRAWER_TABS.filter(tab => tab.scope === 'project').map(tab => tab.id), ['files', 'notes', 'context', 'git', 'processes'])
  assert.deepEqual(DRAWER_TABS.filter(tab => isNavigatorTab(tab.id)).map(tab => tab.id), ['files', 'notes'])
  // The insert group and the navigator group must stay contiguous, so the rail reads as
  // two blocks rather than an arbitrary list.
  const scopes = DRAWER_TABS.map(tab => tab.scope)
  assert.deepEqual(scopes, [...new Set(scopes)].flatMap(scope => scopes.filter(item => item === scope)))
  // Both surfaces render an icon, so the label is only ever an accessible name. It still has
  // to be short and distinct: it is what a screen reader announces for the tab.
  const labels = DRAWER_TABS.map(tab => tab.label)
  assert.equal(new Set(labels).size, labels.length, 'tab labels must be distinct')
  assert.deepEqual(DRAWER_TABS.map(tab => tab.heading), [
    'Clipboard History', 'Commands', 'Prompt Library', 'Prompt Queue', 'Transcript',
    'File Explorer', 'Notes', 'Agent Context', 'Git', 'Processes', 'Alerts',
  ])
  for (const tab of DRAWER_TABS) {
    assert.ok(tab.label.length <= 10, `${tab.id} label is too long to also serve as a name`)
    assert.ok(tab.heading.length > 0, `${tab.id} needs a visible content heading`)
    assert.ok(tab.title.startsWith(tab.label), `${tab.id} title should lead with its label`)
  }
  // The icons live in `railIcons.tsx`, which this module must not import: it stays JSX-free so
  // it runs under plain type-stripping. A contract test checks the map covers every id.
})

test('a stored tab is restored, anything else falls back to clipboard', () => {
  assert.equal(parseDrawerTab('commands'), 'commands')
  assert.equal(parseDrawerTab('notifications'), 'notifications')
  assert.equal(parseDrawerTab(null), 'clipboard')
  assert.equal(parseDrawerTab('processes'), 'processes')
  assert.equal(parseDrawerTab('usage'), 'clipboard')
  assert.equal(drawerTab('prompts').label, 'Prompts')
  // An unknown id must still yield a tab rather than crashing the host.
  assert.equal(drawerTab('nope' as never).id, 'clipboard')
})

test('drawer presentation is isolated per Project', () => {
  let states = updateDrawerProjectState({}, 'p1', { tab: 'files', desktopExpanded: true })
  states = updateDrawerProjectState(states, 'p2', { tab: 'git' })

  assert.deepEqual(drawerProjectStateFor(states, 'p1'), { tab: 'files', desktopExpanded: true })
  assert.deepEqual(drawerProjectStateFor(states, 'p2'), { tab: 'git', desktopExpanded: false })
  assert.equal(drawerProjectStateFor(states, 'p3'), DEFAULT_DRAWER_PROJECT_STATE)

  states = updateDrawerProjectState(states, 'p2', { desktopExpanded: true })
  assert.deepEqual(drawerProjectStateFor(states, 'p1'), { tab: 'files', desktopExpanded: true })
  assert.deepEqual(drawerProjectStateFor(states, 'p2'), { tab: 'git', desktopExpanded: true })
})

test('per-Project drawer persistence round-trips and rejects bad stored shapes', () => {
  let states = updateDrawerProjectState({}, 'p1', { tab: 'notes', desktopExpanded: true })
  states = updateDrawerProjectState(states, 'p2', { tab: 'transcript' })
  assert.deepEqual(parseDrawerProjectStates(serializeDrawerProjectStates(states)), states)
  assert.deepEqual(parseDrawerProjectStates(null), {})
  assert.deepEqual(parseDrawerProjectStates('not json'), {})
  assert.deepEqual(parseDrawerProjectStates('[]'), {})
  assert.deepEqual(
    parseDrawerProjectStates('{"p1":{"tab":"git","desktopExpanded":true},"p2":{"tab":"removed","desktopExpanded":"yes"},"p3":17}'),
    {
      p1: { tab: 'git', desktopExpanded: true },
      p2: { tab: 'clipboard', desktopExpanded: false },
    },
  )
})

test('legacy global tab seeds only the initially active Project', () => {
  const migrated = migrateLegacyDrawerTab({}, 'p1', 'processes')
  assert.deepEqual(drawerProjectStateFor(migrated, 'p1'), { tab: 'processes', desktopExpanded: false })
  assert.equal(drawerProjectStateFor(migrated, 'p2'), DEFAULT_DRAWER_PROJECT_STATE)
  assert.equal(migrateLegacyDrawerTab(migrated, 'p1', 'notes'), migrated, 'migration must not overwrite new state')
  const empty = {}
  assert.equal(migrateLegacyDrawerTab(empty, 'p1', null), empty)
})

test('deleted Projects are pruned from drawer persistence', () => {
  let states = updateDrawerProjectState({}, 'kept', { tab: 'commands' })
  states = updateDrawerProjectState(states, 'gone', { tab: 'queue', desktopExpanded: true })
  assert.deepEqual(pruneDrawerProjectStates(states, ['kept']), {
    kept: { tab: 'commands', desktopExpanded: false },
  })
  const clean = updateDrawerProjectState({}, 'kept', { tab: 'commands' })
  assert.equal(pruneDrawerProjectStates(clean, ['kept']), clean)
})

test('App restores desktop state per Project without persisting mobile visibility', () => {
  const app = readFileSync(join(import.meta.dirname, '..', 'src', 'App.tsx'), 'utf8')
  assert.match(app, /const activeDrawerState=projectId\?drawerProjectStateFor\(drawerProjectStates,projectId\):unscopedDrawerState/)
  assert.match(app, /const clipboardOpen=mobileWorkspace\?mobileDrawerOpen:\(drawerResizeOpen\?\?activeDrawerState\.desktopExpanded\)/)
  assert.match(app, /if\(mobileWorkspace\)\{\s*const open=.*?setMobileDrawerOpen\(open\).*?return\s*\}/s)
  assert.doesNotMatch(app, /setClipboardOpenState/)
  assert.ok(app.includes("openDrawerTab('files',project.id)"), 'cross-Project Files actions must name their target')
  assert.ok(app.includes("openDrawerTab('notes',targetProject)"), 'cross-Project Notes actions must name their target')
  assert.ok(app.includes("openDrawerTab('queue',session?.project_id||projectId)"), 'cross-Project Queue actions must name their target')
})

test('each drawer body shows a compact heading with the rail tooltip description', () => {
  const host = readFileSync(join(import.meta.dirname, '..', 'src', 'UtilityDrawer.tsx'), 'utf8')
  const css = readFileSync(join(import.meta.dirname, '..', 'src', 'style.css'), 'utf8')
  assert.ok(host.includes('class={`drawer-body drawer-body-${tab}`}'))
  assert.ok(host.includes('<h2 class="drawer-panel-title" title={active.title}>{active.heading}</h2>'))
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
  assert.ok(notes.includes("await api('DELETE',`/api/projects/${note.project_id}/session-notes/"))
  assert.ok(notes.includes('{revision:note.revision}'))
  assert.ok(notes.includes("confirming?'delete?':'×'"), 'inline delete must expose its second step')
  assert.ok(notes.includes('onContextMenu={event=>openContextMenu(note,event)}'))
  assert.ok(notes.includes('const LONG_PRESS_MS=550'))
  assert.ok(notes.includes("'Confirm delete':'Delete session note'"), 'context delete must expose the same second step')
  assert.ok(notes.includes("projectNoteBytes===null?'size …':sizeLabel(projectNoteBytes)"))
  assert.match(css, /\.project-note-shell\{[^}]*border:[^}]*box-shadow:/)
})

test('dock width has no fixed maximum and preserves a minimum workspace', () => {
  assert.equal(clampDrawerWidth(120), DRAWER_MIN_WIDTH)
  assert.equal(clampDrawerWidth(9000), 9000)
  assert.equal(clampDrawerWidth(9000, 1468), 1468)
  assert.equal(clampDrawerWidth(420, Number.NaN), 420)
  assert.equal(clampDrawerWidth(420), 420)
  assert.equal(clampDrawerWidth(Number.NaN), DRAWER_DEFAULT_WIDTH)
  assert.equal(drawerMaximumWidth(1920, 258), 1468)
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
  assert.ok(app.includes('drawerResizeOpen??activeDrawerState.desktopExpanded'))
  assert.ok(app.includes('setDrawerResizeOpen(dragOpen)'))
  assert.ok(app.includes('setClipboardOpen(dragOpen)'))
  assert.ok(app.includes("localStorage.setItem('mux.sidebar.collapsed.v1',String(dragCollapsed))"))
  assert.match(css, /workspace\.drawer-open\{[^}]*minmax\(150px,1fr\)/)
  assert.match(css, /workspace\.sidebar-collapsed\.drawer-open\{[^}]*minmax\(150px,1fr\)/)
})

test('tab cycling wraps in both directions', () => {
  assert.equal(nextDrawerTab('clipboard', 1), 'commands')
  assert.equal(nextDrawerTab('prompts', 1), 'queue')
  assert.equal(nextDrawerTab('queue', 1), 'transcript')
  assert.equal(nextDrawerTab('transcript', 1), 'files')
  assert.equal(nextDrawerTab('notes', 1), 'context')
  assert.equal(nextDrawerTab('context', 1), 'git')
  assert.equal(nextDrawerTab('notifications', 1), 'clipboard')
  assert.equal(nextDrawerTab('clipboard', -1), 'notifications')
  assert.equal(nextDrawerTab('prompts', -2), 'clipboard')
})
