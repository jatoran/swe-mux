import assert from 'node:assert/strict'
import test from 'node:test'
import { createDismissStack } from '../src/dismissStack.ts'
import { createBackCoordinator } from '../src/systemBack.ts'
import {
  composeBackTarget,
  createViewHistory,
  VIEW_HISTORY_LIMIT,
  viewBackEnabled,
  type ViewEntry,
} from '../src/viewHistory.ts'

const at = (viewId: string, projectId = 'p1') => ({ projectId, viewId })
const key = (entry: ViewEntry) => `${entry.projectId}/${entry.viewId}`
const allAlive = () => true

/** A history plus the navigator, wired the way `App` wires them. */
function workspace(options: { enabled?: boolean; alive?: (entry: ViewEntry) => boolean } = {}) {
  const history = createViewHistory()
  const visited: string[] = []
  let position = at('a')
  const navigator = {
    enabled: () => options.enabled !== false,
    alive: options.alive ?? allAlive,
    go: (entry: ViewEntry) => {
      visited.push(key(entry))
      // The workspace commits the focus change, which the recorder then observes - the
      // echo every traversal produces (rule 4).
      const previous = position
      position = entry
      history.record(previous, entry)
    },
  }
  return {
    history,
    navigator,
    visited,
    /** A deliberate navigation, as the committed-focus effect would report it. */
    goto: (entry: ViewEntry) => { const previous = position; position = entry; history.record(previous, entry) },
    position: () => position,
  }
}

test('back returns to the view left behind, and consumes it doing so', () => {
  const { history, navigator, goto } = workspace()
  goto(at('b'))
  assert.equal(history.liveDepth(allAlive), 1)

  const taken = history.take(allAlive)
  assert.deepEqual(taken, at('a'))
  assert.equal(history.liveDepth(allAlive), 0, 'a traversal consumes its entry, or back can never leave the app')
  void navigator
})

test('a traversal is not itself a navigation, so back walks out instead of ping-ponging', () => {
  const space = workspace()
  space.goto(at('b'))
  space.goto(at('c'))

  const target = composeBackTarget(createDismissStack(), space.history, space.navigator)
  assert.equal(target.depth(), 2)

  assert.equal(target.pop(), 'popped')
  assert.deepEqual(space.position(), at('b'))
  assert.equal(target.pop(), 'popped')
  assert.deepEqual(space.position(), at('a'))
  // Exhausted rather than cycling: the next press belongs to the platform.
  assert.equal(target.depth(), 0)
  assert.equal(target.pop(), 'empty')
  assert.deepEqual(space.visited, ['p1/b', 'p1/a'])
})

test('flipping between two tabs does not deepen the ring', () => {
  const space = workspace()
  space.goto(at('b'))
  space.goto(at('a'))
  space.goto(at('b'))
  space.goto(at('a'))
  space.goto(at('b'))

  // Two views can only ever be one step apart, however often they were flipped: the one
  // being arrived at leaves the ring, so back never offers to keep you where you are.
  assert.equal(space.history.liveDepth(allAlive), 1)
  assert.deepEqual(space.history.entries(), [at('a')])
})

test('a revisited view moves rather than repeating, so back never lands twice on it', () => {
  const space = workspace()
  space.goto(at('b'))
  space.goto(at('c'))
  space.goto(at('a'))

  assert.deepEqual(space.history.entries().map(key), ['p1/b', 'p1/c'])
  const target = composeBackTarget(createDismissStack(), space.history, space.navigator)
  target.pop(); target.pop()
  assert.deepEqual(space.visited, ['p1/c', 'p1/b'])
  assert.equal(target.depth(), 0)
})

test('the ring is bounded, dropping the oldest view rather than growing', () => {
  const space = workspace()
  for (let index = 0; index <= VIEW_HISTORY_LIMIT + 3; index++) space.goto(at(`view${index}`))

  assert.equal(space.history.liveDepth(allAlive), VIEW_HISTORY_LIMIT)
  const oldest = space.history.entries()[0]
  assert.equal(oldest.viewId, `view${3}`, 'the oldest entries fall off the front')
})

test('a view that no longer exists is skipped when back is pressed, not counted as a step', () => {
  const closed = new Set<string>()
  const space = workspace({ alive: entry => !closed.has(entry.viewId) })
  space.goto(at('b'))
  space.goto(at('c'))
  space.goto(at('d'))
  closed.add('c')
  closed.add('b')

  const target = composeBackTarget(createDismissStack(), space.history, space.navigator)
  assert.equal(target.depth(), 1, 'closed panes are not somewhere back can go')
  assert.equal(target.pop(), 'popped')
  assert.deepEqual(space.visited, ['p1/a'], 'one press crosses the closed panes rather than being swallowed by each')
  assert.equal(target.depth(), 0)
})

test('back crosses Projects, restoring the one the view belongs to', () => {
  const space = workspace()
  space.goto(at('other', 'p2'))

  const target = composeBackTarget(createDismissStack(), space.history, space.navigator)
  assert.equal(target.pop(), 'popped')
  assert.deepEqual(space.position(), at('a', 'p1'))
})

test('an open overlay always answers first, whatever the ring holds', () => {
  const space = workspace()
  space.goto(at('b'))
  const dismiss = createDismissStack()
  const closed: string[] = []
  const id = dismiss.register({ label: 'settings', dismiss: () => { closed.push('settings'); dismiss.unregister(id) } })

  const target = composeBackTarget(dismiss, space.history, space.navigator)
  assert.equal(target.depth(), 2, 'the overlay and the view behind it are both places back can go')
  target.pop()
  assert.deepEqual(closed, ['settings'])
  assert.deepEqual(space.visited, [], 'the workspace under an overlay must not move while it is covered')

  target.pop()
  assert.deepEqual(space.visited, ['p1/a'])
})

test('with the ring disabled, back reports nothing to do rather than stepping views', () => {
  const space = workspace({ enabled: false })
  space.goto(at('b'))

  const target = composeBackTarget(createDismissStack(), space.history, space.navigator)
  assert.equal(target.depth(), 0, 'a desktop sentinel armed against views would trap the browser Back button')
  assert.equal(target.pop(), 'empty')
  assert.deepEqual(space.visited, [])
  // Recording continues regardless, so a phone rotating across the breakpoint and back
  // finds its history intact rather than wiped.
  assert.equal(space.history.liveDepth(allAlive), 1)
})

test('a restore that changes nothing does not eat the next real navigation', () => {
  const history = createViewHistory()
  history.record(at('a'), at('b'))
  // Taken but never restored - the workspace refused, so no echo ever arrives.
  assert.deepEqual(history.take(allAlive), at('a'))

  history.record(at('b'), at('c'))
  assert.deepEqual(history.entries(), [at('b')], 'the pending echo marker must not swallow a genuine move')
})

test('a position naming no view is not somewhere back can return to', () => {
  const history = createViewHistory()
  history.record({ projectId: 'p1', viewId: null }, at('a'))
  assert.equal(history.liveDepth(allAlive), 0)
})

test('the history sentinel arms and disarms against the ring, not just overlays', () => {
  const space = workspace()
  const dismiss = createDismissStack()
  const target = composeBackTarget(dismiss, space.history, space.navigator)

  let sentinels = 0
  let onSentinel = false
  const coordinator = createBackCoordinator(target, {
    pushSentinel: () => { sentinels++; onSentinel = true },
    back: () => { sentinels--; onSentinel = false },
    onSentinel: () => onSentinel,
  })
  target.subscribe(coordinator.sync)
  coordinator.sync()
  assert.equal(coordinator.state().armed, false, 'nothing to go back to yet')

  space.goto(at('b'))
  assert.equal(coordinator.state().armed, true)
  assert.equal(sentinels, 1)

  // The platform consumed the sentinel to deliver this.
  onSentinel = false
  coordinator.handlePopstate()
  assert.deepEqual(space.visited, ['p1/a'])
  assert.equal(coordinator.state().armed, false, 'the ring is empty, so the next press leaves the app')
  assert.equal(sentinels, 1, 'the consumed sentinel is not re-pushed against an empty ring')
})

test('a deeper ring re-arms so every step back is still ours', () => {
  const space = workspace()
  space.goto(at('b'))
  space.goto(at('c'))
  const target = composeBackTarget(createDismissStack(), space.history, space.navigator)

  let onSentinel = false
  const pushes: number[] = []
  const coordinator = createBackCoordinator(target, {
    pushSentinel: () => { pushes.push(1); onSentinel = true },
    back: () => { onSentinel = false },
    onSentinel: () => onSentinel,
  })
  target.subscribe(coordinator.sync)
  coordinator.sync()
  assert.equal(pushes.length, 1, 'one sentinel, however deep the ring')

  onSentinel = false
  coordinator.handlePopstate()
  assert.equal(coordinator.state().armed, true, 'a view remains, so back stays ours')
  assert.equal(pushes.length, 2)
})

test('the switch defaults on and is only off when explicitly false', () => {
  assert.equal(viewBackEnabled({}), true)
  assert.equal(viewBackEnabled({ mobile_back_view_history: true }), true)
  assert.equal(viewBackEnabled({ mobile_back_view_history: false }), false)
})
