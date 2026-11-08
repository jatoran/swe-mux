import assert from 'node:assert/strict'
import test from 'node:test'
import { focusMemoryWith, initialFocusedView, parseFocusMemory, parseViewPreference, reconcileFocusView, installViewUrlReassert, rememberedView, resolveActiveSession, resolveInitialFocus, viewUrl } from '../src/viewState.ts'

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

test('an explicit URL may select a retained ended session', () => {
  const selected = resolveInitialFocus(
    sessions,
    ['default', 'work'],
    { default: ['first'], work: ['wanted', 'ended'] },
    parseViewPreference('?session=ended'),
    { lastProject: 'default', byProject: { default: 'first' }, viewByProject: {} },
  )
  assert.deepEqual(selected, { projectId: 'work', sessionId: 'ended' })
})

test('a followed link is the focused view even where the device remembers another one', () => {
  // The Android report: the link opened the right Project and then showed the tab the
  // device remembered for it, because the remembered view outranked the URL's session.
  const memory = { lastProject: 'work', byProject: { work: 'first-work' }, viewByProject: { work: 'note:plan' } }
  const selected = resolveInitialFocus(
    [...sessions, { id: 'first-work', project_id: 'work', state: 'running' }],
    ['default', 'work'],
    { default: ['first'], work: ['first-work'] },
    parseViewPreference('?session=wanted'),
    memory,
  )
  assert.deepEqual(selected, { projectId: 'work', sessionId: 'wanted' })
  assert.deepEqual(
    initialFocusedView(selected, 'wanted', rememberedView(memory, 'work'), ['first-work', 'wanted', 'note:plan']),
    { viewId: 'wanted', linked: true },
  )
})

test('without a link, the remembered view is restored while the layout still holds it', () => {
  const selected = { projectId: 'work', sessionId: 'wanted' }
  // The app's own URL (a reload) is not a link, so a focused note survives it.
  assert.deepEqual(initialFocusedView(selected, null, 'note:plan', ['wanted', 'note:plan']), { viewId: 'note:plan', linked: false })
  assert.deepEqual(initialFocusedView(selected, null, 'note:gone', ['wanted']), { viewId: 'wanted', linked: false })
  // A link to a session that no longer exists resolved to something else: the old
  // behaviour applies, remembered view included.
  assert.deepEqual(
    initialFocusedView({ projectId: 'work', sessionId: 'first-work' }, 'deleted', 'note:plan', ['first-work', 'note:plan']),
    { viewId: 'note:plan', linked: false },
  )
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

test('an ended selection stays active while an unrelated live sibling exists', () => {
  assert.equal(
    resolveActiveSession(sessions, 'work', 'ended', ['ended'], ['wanted', 'ended']),
    'ended',
  )
})

test('a missing selection falls back to live work before retained ended panes', () => {
  assert.equal(
    resolveActiveSession(sessions, 'work', 'removed', ['ended'], ['ended', 'wanted']),
    'wanted',
  )
  assert.equal(
    resolveActiveSession(sessions.filter(session => session.id !== 'wanted'), 'work', 'removed', ['ended'], ['ended']),
    'ended',
  )
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

// A browser history with the same shape as the real one: a stack of URL entries, where
// replaceState edits the entry we are on and back() steps to the one below it.
function fakeWindow(start: string) {
  const entries = [start]
  let index = 0
  const target = new EventTarget()
  const location = {
    get href() { return `http://localhost${entries[index]}` },
    get pathname() { return new URL(this.href).pathname },
    get search() { return new URL(this.href).search },
    get hash() { return new URL(this.href).hash },
  }
  const history = {
    state: null,
    replaceState: (_state: unknown, _unused: string, url: string) => { entries[index] = url },
  }
  return {
    win: Object.assign(target, { location, history }),
    pushSentinel: () => { entries.splice(index + 1); entries.push(entries[index]); index += 1 },
    back: () => { index -= 1; target.dispatchEvent(new Event('popstate')) },
    url: () => entries[index],
  }
}

test('leaving the back sentinel keeps the URL on what was picked while an overlay was open', () => {
  // The reported shape: open the sidebar search, pick another Project, and the search closing
  // stepped back onto an entry that still named the Project you started on.
  const browser = fakeWindow('/?project=start')
  let selection = { projectId: 'start', sessionId: null as string | null }
  const stop = installViewUrlReassert(() => selection, browser.win)

  browser.pushSentinel()
  selection = { projectId: 'picked', sessionId: 'session-1' }
  browser.win.history.replaceState(null, '', '/?project=picked&session=session-1')
  browser.back()

  assert.equal(browser.url(), '/?project=picked&session=session-1')
  stop()
})

test('re-applying the URL keeps the rest of the address and leaves an unchanged one alone', () => {
  const browser = fakeWindow('/?debug=1&project=one#notes')
  let writes = 0
  const replace = browser.win.history.replaceState
  browser.win.history.replaceState = (state: unknown, unused: string, url: string) => { writes += 1; replace(state, unused, url) }
  const stop = installViewUrlReassert(() => ({ projectId: 'one', sessionId: null }), browser.win)

  browser.pushSentinel()
  browser.back()
  assert.equal(writes, 0, 'nothing moved, so nothing is written')

  stop()
  const after = installViewUrlReassert(() => ({ projectId: 'two', sessionId: 's' }), browser.win)
  browser.pushSentinel()
  browser.back()
  assert.equal(browser.url(), '/?debug=1&project=two&session=s#notes')
  after()
})

test('before anything is focused there is nothing to re-apply', () => {
  const browser = fakeWindow('/?project=start')
  const stop = installViewUrlReassert(() => null, browser.win)
  browser.pushSentinel()
  browser.win.history.replaceState(null, '', '/?project=other')
  browser.back()
  assert.equal(browser.url(), '/?project=start')
  stop()
})
