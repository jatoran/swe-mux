import assert from 'node:assert/strict'
import test from 'node:test'
import { createDismissStack } from '../src/dismissStack.ts'
import { createBackCoordinator } from '../src/systemBack.ts'

// A history stack of booleans: true marks an entry the coordinator pushed as its sentinel.
function fakeHistory() {
  const entries: boolean[] = [false]
  let index = 0
  const popstates: (() => void)[] = []
  return {
    host: {
      pushSentinel: () => { entries.splice(index + 1); entries.push(true); index = entries.length - 1 },
      back: () => {
        if (index === 0) throw new Error('back past the first entry would leave the app')
        index--
        // The platform delivers popstate asynchronously; the queue lets tests decide when.
        popstates.push(() => undefined)
      },
      onSentinel: () => entries[index] === true,
    },
    /** The user's own back press. */
    userBack: () => { assert.ok(index > 0, 'back past the first entry would leave the app'); index-- },
    depth: () => entries.length,
    at: () => index,
    pendingSelfBacks: () => popstates.length,
    drain: () => { popstates.length = 0 },
  }
}

function harness() {
  const stack = createDismissStack()
  const history = fakeHistory()
  const coordinator = createBackCoordinator(stack, history.host)
  stack.subscribe(coordinator.sync)
  coordinator.sync()
  return { stack, history, coordinator }
}

test('opening the first level arms a single sentinel, and nesting does not add another', () => {
  const { stack, history } = harness()
  assert.equal(history.depth(), 1)

  stack.register({ label: 'history', dismiss: () => undefined })
  assert.equal(history.depth(), 2)
  assert.equal(history.at(), 1)

  // A second level rides the same sentinel.
  stack.register({ label: 'transcript', dismiss: () => undefined })
  assert.equal(history.depth(), 2)
})

test('the system back gesture closes one level instead of leaving the app', () => {
  const { stack, history, coordinator } = harness()
  const closed: string[] = []
  const outer = stack.register({ label: 'history', dismiss: () => closed.push('history') })
  const inner = stack.register({ label: 'transcript', dismiss: () => { closed.push('transcript'); stack.unregister(inner) } })

  history.userBack()
  coordinator.handlePopstate()
  assert.deepEqual(closed, ['transcript'])
  // Still one level deep, so the sentinel is re-armed and the next back is ours too.
  assert.equal(coordinator.state().armed, true)
  assert.equal(history.at(), 1)

  history.userBack()
  coordinator.handlePopstate()
  assert.deepEqual(closed, ['transcript', 'history'])
  stack.unregister(outer)
})

test('the last level closing disarms the sentinel so the next back is not swallowed', () => {
  const { stack, history, coordinator } = harness()
  const id = stack.register({ label: 'history', dismiss: () => undefined })
  assert.equal(history.at(), 1)

  // Closed with the × button rather than by back.
  stack.unregister(id)
  assert.equal(history.at(), 0, 'the sentinel must be consumed, or the next back press does nothing visible')
  assert.equal(coordinator.state().armed, false)

  // That self-inflicted popstate must not be read as a user gesture.
  assert.equal(history.pendingSelfBacks(), 1)
  coordinator.handlePopstate()
  assert.equal(coordinator.state().armed, false)
  history.drain()
})

test('a popstate we caused is never mistaken for a back press against a new overlay', () => {
  const { stack, history, coordinator } = harness()
  const first = stack.register({ label: 'first', dismiss: () => undefined })
  stack.unregister(first)

  // A new overlay opens before the suppressed popstate lands.
  const closed: string[] = []
  stack.register({ label: 'second', dismiss: () => closed.push('second') })
  coordinator.handlePopstate()
  assert.deepEqual(closed, [], 'our own back() must not close the overlay that opened after it')
  assert.equal(coordinator.state().armed, true)
})

test('two closes in flight leave both popstates suppressed', () => {
  const { stack, coordinator } = harness()
  // Each close consumes its own sentinel, so two `back()` calls are outstanding at once.
  const first = stack.register({ label: 'first', dismiss: () => undefined })
  stack.unregister(first)
  const second = stack.register({ label: 'second', dismiss: () => undefined })
  stack.unregister(second)
  assert.equal(coordinator.state().selfInflicted, 2)

  const closed: string[] = []
  stack.register({ label: 'third', dismiss: () => closed.push('third') })
  coordinator.handlePopstate()
  coordinator.handlePopstate()
  assert.deepEqual(closed, [])
})

test('a blocking level absorbs back and keeps the sentinel armed', () => {
  const { stack, history, coordinator } = harness()
  let closed = 0
  stack.register({ label: 'daemon-reload', blocking: true, dismiss: () => { closed++ } })

  history.userBack()
  coordinator.handlePopstate()
  assert.equal(closed, 0)
  assert.equal(coordinator.state().armed, true, 'back must stay intercepted while the app is mid-restart')
  assert.equal(history.at(), 1)
})

test('a popstate with nothing open is left to the platform', () => {
  const { coordinator } = harness()
  assert.equal(coordinator.state().armed, false)
  coordinator.handlePopstate()
  assert.equal(coordinator.state().armed, false)
})

test('an outside navigation off the sentinel drops it rather than moving the user', () => {
  const stack = createDismissStack()
  let backs = 0
  let onSentinel = true
  const coordinator = createBackCoordinator(stack, {
    pushSentinel: () => undefined,
    back: () => { backs++ },
    onSentinel: () => onSentinel,
  })
  stack.subscribe(coordinator.sync)

  const id = stack.register({ label: 'modal', dismiss: () => undefined })
  onSentinel = false
  stack.unregister(id)
  assert.equal(backs, 0, 'stepping back from an entry that is not ours would navigate the user away')
  assert.equal(coordinator.state().armed, false)
})
