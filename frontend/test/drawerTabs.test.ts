import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  DEFAULT_DRAWER_PROJECT_STATE,
  DRAWER_DEFAULT_WIDTH,
  DRAWER_MAX_WIDTH,
  DRAWER_MIN_WIDTH,
  DRAWER_TABS,
  clampDrawerWidth,
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
  for (const tab of DRAWER_TABS) {
    assert.ok(tab.label.length <= 10, `${tab.id} label is too long to also serve as a name`)
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
  assert.match(app, /const clipboardOpen=mobileWorkspace\?mobileDrawerOpen:activeDrawerState\.desktopExpanded/)
  assert.match(app, /if\(mobileWorkspace\)\{\s*const open=.*?setMobileDrawerOpen\(open\).*?return\s*\}/s)
  assert.doesNotMatch(app, /setClipboardOpenState/)
  assert.ok(app.includes("openDrawerTab('files',project.id)"), 'cross-Project Files actions must name their target')
  assert.ok(app.includes("openDrawerTab('notes',targetProject)"), 'cross-Project Notes actions must name their target')
  assert.ok(app.includes("openDrawerTab('queue',session?.project_id||projectId)"), 'cross-Project Queue actions must name their target')
})

test('dock width is bounded and survives junk in localStorage', () => {
  assert.equal(clampDrawerWidth(120), DRAWER_MIN_WIDTH)
  assert.equal(clampDrawerWidth(9000), DRAWER_MAX_WIDTH)
  assert.equal(clampDrawerWidth(420), 420)
  assert.equal(clampDrawerWidth(Number.NaN), DRAWER_DEFAULT_WIDTH)
  assert.equal(storedDrawerWidth('440'), 440)
  assert.equal(storedDrawerWidth(null), DRAWER_DEFAULT_WIDTH)
  assert.equal(storedDrawerWidth('not-a-number'), DRAWER_DEFAULT_WIDTH)
  assert.equal(storedDrawerWidth('0'), DRAWER_DEFAULT_WIDTH)
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
