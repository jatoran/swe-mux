import assert from 'node:assert/strict'
import test from 'node:test'
import { DRAWER_TABS, type DrawerTabId } from '../src/drawerTabs.ts'
import {
  canHideDrawerTab,
  drawerTabStructurallyAvailable,
  drawerTabVisible,
  parseHiddenDrawerTabs,
  serializeHiddenDrawerTabs,
  visibleDrawerTabs,
  withDrawerTabHidden,
} from '../src/drawerVisibility.ts'

const every = DRAWER_TABS.map(tab => tab.id)

test('structural absence is a property of the session, not a preference', () => {
  assert.equal(drawerTabStructurallyAvailable('transcript', false), false)
  assert.equal(drawerTabStructurallyAvailable('transcript', true), true)
  // Insight stays available on a shell session: its Timeline segment gates itself and
  // its Findings segment is Project-scoped.
  assert.equal(drawerTabStructurallyAvailable('insight', false), true)
  assert.equal(drawerTabStructurallyAvailable('git', false), true)
})

test('a hidden tab is filtered out of whatever stack holds it', () => {
  const visibility = { hidden: ['git', 'changemap'] as DrawerTabId[], hasTranscript: true }
  assert.equal(drawerTabVisible('git', visibility), false)
  assert.deepEqual(
    visibleDrawerTabs(['clipboard', 'git', 'notes', 'changemap'], visibility),
    ['clipboard', 'notes'],
  )
})

test('an explicitly opened hidden tab is peeked rather than quietly unhidden', () => {
  const hidden: DrawerTabId[] = ['git']
  assert.equal(drawerTabVisible('git', { hidden, hasTranscript: true, peek: 'git' }), true)
  assert.equal(drawerTabVisible('git', { hidden, hasTranscript: true, peek: 'notes' }), false)
  // Peeking never overrides structural absence: a shell session has no transcript to read
  // however the tab was reached.
  assert.equal(
    drawerTabVisible('transcript', { hidden: [], hasTranscript: false, peek: 'transcript' }),
    false,
  )
})

test('the last remaining tab cannot be hidden', () => {
  const allButOne = every.filter(id => id !== 'notes')
  assert.equal(canHideDrawerTab(allButOne, 'notes'), false)
  assert.deepEqual(withDrawerTabHidden(allButOne, 'notes', true), allButOne)
  // Showing one back is always allowed, and a tab already hidden reports hideable so the
  // checklist can render its row enabled rather than trapping the last unchecked box.
  assert.equal(canHideDrawerTab(allButOne, 'git'), true)
  assert.equal(withDrawerTabHidden(allButOne, 'git', false).includes('git'), false)
})

test('toggling is idempotent and never duplicates an id', () => {
  const once = withDrawerTabHidden([], 'git', true)
  assert.deepEqual(withDrawerTabHidden(once, 'git', true), ['git'])
  assert.deepEqual(withDrawerTabHidden(once, 'git', false), [])
})

test('stored sets survive junk, and a set that hides everything is discarded', () => {
  assert.deepEqual(parseHiddenDrawerTabs(null), [])
  assert.deepEqual(parseHiddenDrawerTabs('not json'), [])
  assert.deepEqual(parseHiddenDrawerTabs('{"git":true}'), [])
  assert.deepEqual(parseHiddenDrawerTabs('["git","nope","git",7]'), ['git'])
  // A browser that somehow stored every id would have no tab strip to reach the restore
  // control from, so the value is dropped rather than honoured.
  assert.deepEqual(parseHiddenDrawerTabs(JSON.stringify(every)), [])
})

test('writes are in canonical order, so toggling does not churn the stored value', () => {
  assert.equal(
    serializeHiddenDrawerTabs(['notes', 'clipboard']),
    serializeHiddenDrawerTabs(['clipboard', 'notes']),
  )
  // Canonical is the registry's own order, which puts Notes ahead of Git.
  assert.deepEqual(parseHiddenDrawerTabs(serializeHiddenDrawerTabs(['git', 'notes'])), ['notes', 'git'])
})
