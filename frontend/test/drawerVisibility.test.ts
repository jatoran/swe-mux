import assert from 'node:assert/strict'
import test from 'node:test'
import { DRAWER_TABS, type DrawerTabId } from '../src/drawerTabs.ts'
import {
  DEFAULT_HIDDEN_DRAWER_TABS,
  canHideDrawerTab,
  defaultHiddenDrawerTabs,
  drawerTabStructurallyAvailable,
  drawerTabVisible,
  parseHiddenDrawerTabs,
  serializeHiddenDrawerTabs,
  visibleDrawerTabs,
  withDrawerTabHidden,
} from '../src/drawerVisibility.ts'

const every = DRAWER_TABS.map(tab => tab.id)

test('the shipped default hides Processes and nothing else', () => {
  // Processes is not made redundant by the Resources dialog - a modal covers the terminal,
  // and this tab pins the focused session beside it - but it answers a question asked
  // rarely enough not to spend a rail slot on by default.
  assert.deepEqual([...DEFAULT_HIDDEN_DRAWER_TABS], ['processes'])
  // Alerts is deliberately not hidden: it is the only tab that draws an unread badge, so
  // hiding it would remove the one glanceable "something needs you" signal.
  assert.equal(DEFAULT_HIDDEN_DRAWER_TABS.includes('notifications'), false)
  // Every shipped default must name a registered tab, or it silently hides nothing.
  for (const id of DEFAULT_HIDDEN_DRAWER_TABS) assert.ok(every.includes(id), id)
})

test('the terminal tier default puts away the agent machinery and keeps the rest', () => {
  const hidden = defaultHiddenDrawerTabs('terminal')
  const visible = every.filter(id => !hidden.includes(id))
  // A pure-terminal install should not draw eleven tabs; what remains is what a
  // terminal-first user still owns.
  assert.deepEqual(visible, ['notes', 'files', 'actions', 'git', 'notifications'])
  // The badge rule holds for every tier: the one "something needs you" surface stays.
  assert.equal(hidden.includes('notifications'), false)
  // A default may never hide everything - the restore control lives on the tab strip.
  assert.ok(hidden.length < every.length)
  for (const id of hidden) assert.ok(every.includes(id), id)
})

test('deterministic also puts Activity away; the unchosen and automations tiers keep the shipped default', () => {
  // Activity is fed by the model-backed layer the deterministic tier keeps off, so
  // drawing it there is mostly drawing the three kinds of empty. The empty tier - an
  // install predating the chooser, or a skipped first run - deliberately keeps the
  // pre-tier default so nothing shifts under an existing device.
  assert.deepEqual(defaultHiddenDrawerTabs('deterministic'), ['activity', 'processes'])
  for (const tier of ['', 'automations'] as const) {
    assert.deepEqual(defaultHiddenDrawerTabs(tier), [...DEFAULT_HIDDEN_DRAWER_TABS], tier)
  }
})

test('structural absence is a property of the session, not a preference', () => {
  assert.equal(drawerTabStructurallyAvailable('transcript', false), false)
  assert.equal(drawerTabStructurallyAvailable('transcript', true), true)
  // Activity and Agent stay available on a shell session. Their segments gate themselves
  // (`drawerSegments.ts`) and `resolveDrawerSegment` falls back to one that does not need a
  // transcript or an agent harness, so a shell session still reaches Activity's findings
  // and Agent's instruction files. Gating the whole tab would hide surfaces that work.
  assert.equal(drawerTabStructurallyAvailable('activity', false), true)
  assert.equal(drawerTabStructurallyAvailable('agent', false), true)
  assert.equal(drawerTabStructurallyAvailable('git', false), true)
})

test('a hidden tab is filtered out of whatever stack holds it', () => {
  const visibility = { hidden: ['git', 'processes'] as DrawerTabId[], hasTranscript: true }
  assert.equal(drawerTabVisible('git', visibility), false)
  assert.deepEqual(
    visibleDrawerTabs(['actions', 'git', 'notes', 'processes'], visibility),
    ['actions', 'notes'],
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
  // `null` is "never chosen", which is the only moment the shipped default applies. An
  // *empty* stored set is a choice - the user showed everything - and must stay empty.
  assert.deepEqual(parseHiddenDrawerTabs(null), [...DEFAULT_HIDDEN_DRAWER_TABS])
  assert.deepEqual(parseHiddenDrawerTabs('[]'), [])
  assert.deepEqual(parseHiddenDrawerTabs('not json'), [])
  assert.deepEqual(parseHiddenDrawerTabs('{"git":true}'), [])
  assert.deepEqual(parseHiddenDrawerTabs('["git","nope","git",7]'), ['git'])
  // A browser that somehow stored every id would have no tab strip to reach the restore
  // control from, so the value is dropped rather than honoured.
  assert.deepEqual(parseHiddenDrawerTabs(JSON.stringify(every)), [])
})

test('writes are in canonical order, so toggling does not churn the stored value', () => {
  assert.equal(
    serializeHiddenDrawerTabs(['notes', 'actions']),
    serializeHiddenDrawerTabs(['actions', 'notes']),
  )
  // Canonical is the registry's own order, which puts Notes ahead of Git.
  assert.deepEqual(parseHiddenDrawerTabs(serializeHiddenDrawerTabs(['git', 'notes'])), ['notes', 'git'])
})
