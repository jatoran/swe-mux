import assert from 'node:assert/strict'
import test from 'node:test'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, reconcileFocusView, rememberedView, resolveInitialFocus, viewUrl } from '../src/viewState.ts'

const sessions = [
  { id: 'first', project_id: 'default', state: 'running' },
  { id: 'wanted', project_id: 'work', state: 'idle' },
  { id: 'ended', project_id: 'work', state: 'exited' },
]

test('URL session wins and carries its actual project', () => {
  const selected = resolveInitialFocus(
    sessions,
    ['default', 'work'],
    { default: ['first'], work: ['wanted'] },
    parseViewPreference('?project=default&session=wanted'),
    { lastProject: 'default', byProject: { default: 'first' }, viewByProject: {} },
  )
  assert.deepEqual(selected, { projectId: 'work', sessionId: 'wanted' })
})

test('per-device focus wins before the first visible session', () => {
  const selected = resolveInitialFocus(
    sessions,
    ['default', 'work'],
    { default: ['first'], work: ['wanted'] },
    { projectId: 'work', sessionId: null },
    parseFocusMemory('{"lastProject":"default","byProject":{"work":"wanted"}}'),
  )
  assert.deepEqual(selected, { projectId: 'work', sessionId: 'wanted' })
  assert.deepEqual(focusMemoryWith({ lastProject: null, byProject: {}, viewByProject: {} }, 'work', 'wanted'), {
    lastProject: 'work', byProject: { work: 'wanted' }, viewByProject: {},
  })
})

test('focus memory persists and restores the focused view, and clears it when absent', () => {
  const withNote = focusMemoryWith({ lastProject: null, byProject: {}, viewByProject: {} }, 'work', 'wanted', 'note:work')
  assert.deepEqual(withNote, { lastProject: 'work', byProject: { work: 'wanted' }, viewByProject: { work: 'note:work' } })
  assert.equal(rememberedView(withNote, 'work'), 'note:work')
  assert.equal(rememberedView(withNote, 'default'), null)
  // A later focus with no view id clears the remembered view for that project.
  const cleared = focusMemoryWith(withNote, 'work', 'wanted', null)
  assert.equal(rememberedView(cleared, 'work'), null)
})

test('parse tolerates legacy focus memory without a view map', () => {
  const parsed = parseFocusMemory('{"lastProject":"work","byProject":{"work":"wanted"}}')
  assert.deepEqual(parsed, { lastProject: 'work', byProject: { work: 'wanted' }, viewByProject: {} })
  assert.equal(rememberedView(parsed, 'work'), null)
})

test('view URL preserves unrelated parameters and hash without navigation history noise', () => {
  assert.equal(
    viewUrl('http://localhost/?debug=1#notes', 'work', 'wanted'),
    '/?debug=1&project=work&session=wanted#notes',
  )
})

// A pane the daemon spawned for us (history resume, branch, second opinion): we know its
// id from the response, but the layout carrying it is one refresh behind.
const awaiting = (overrides: Partial<Parameters<typeof reconcileFocusView>[0]> = {}) =>
  reconcileFocusView({
    requested: 'resumed',
    focused: 'resumed',
    hasRoot: true,
    holdsRequested: false,
    holdsFocused: false,
    firstPaneActive: 'old-tab',
    ...overrides,
  })

test('a requested view survives the refresh it is waiting for', () => {
  // The bug: the request is dropped, the fallback wins, and the resumed session opens
  // behind the tab the History browser was opened from.
  assert.deepEqual(awaiting(), { focus: 'resumed', keepRequest: true })
  // Refresh lands and the leaf exists. Focus applies and the request is spent.
  assert.deepEqual(
    awaiting({ holdsRequested: true, holdsFocused: true }),
    { focus: 'resumed', keepRequest: false },
  )
})

test('a pending request outlives more than one refresh', () => {
  // `refresh()` is deduplicated: the one we await can have been snapshotted before the
  // spawn committed, so the leaf arrives on the *next* one. A request that expired after
  // a single round would lose the race intermittently, which is the worst kind.
  const first = awaiting()
  assert.equal(first.keepRequest, true)
  const second = awaiting({ focused: first.focus })
  assert.equal(second.keepRequest, true)
  assert.deepEqual(
    awaiting({ focused: second.focus, holdsRequested: true, holdsFocused: true }),
    { focus: 'resumed', keepRequest: false },
  )
})

test('a deliberate choice made while waiting outranks the pending request', () => {
  // Clicking another tab (or switching project) while the spawn is in flight is a real
  // decision; the arriving pane must not yank focus back out from under it.
  assert.deepEqual(
    awaiting({ focused: 'clicked', holdsFocused: true }),
    { focus: 'clicked', keepRequest: false },
  )
  // Focus landing on something that is *not* in this layout is reconciliation noise, not
  // a choice, so the request stands.
  assert.deepEqual(
    awaiting({ focused: 'stale', holdsFocused: false }),
    { focus: 'stale', keepRequest: true },
  )
})

test('with no request pending, focus reconciles exactly as before', () => {
  const settled = (overrides: Partial<Parameters<typeof reconcileFocusView>[0]> = {}) =>
    reconcileFocusView({
      requested: null,
      focused: 'note',
      hasRoot: true,
      holdsRequested: false,
      holdsFocused: true,
      firstPaneActive: 'terminal',
      ...overrides,
    })
  // A focused view that still exists is left alone, even when another pane is active.
  assert.deepEqual(settled(), { focus: 'note', keepRequest: false })
  // A focus naming nothing falls back to the first pane's own active tab.
  assert.deepEqual(settled({ holdsFocused: false }), { focus: 'terminal', keepRequest: false })
  // An empty project focuses nothing at all.
  assert.deepEqual(
    settled({ hasRoot: false, holdsFocused: false, firstPaneActive: null }),
    { focus: null, keepRequest: false },
  )
})
