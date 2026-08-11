import assert from 'node:assert/strict'
import test from 'node:test'
import { createDismissStack, DISMISS_TRACE_LIMIT } from '../src/dismissStack.ts'

test('pop dismisses the most recently opened level, not the first', () => {
  const stack = createDismissStack()
  const closed: string[] = []
  stack.register({ label: 'history', dismiss: () => closed.push('history') })
  const transcript = stack.register({ label: 'transcript', dismiss: () => closed.push('transcript') })

  assert.equal(stack.depth(), 2)
  assert.equal(stack.topLabel(), 'transcript')
  assert.equal(stack.pop(), 'popped')
  assert.deepEqual(closed, ['transcript'])

  // The owner unregisters on unmount; only then does the parent become the top.
  stack.unregister(transcript)
  assert.equal(stack.pop(), 'popped')
  assert.deepEqual(closed, ['transcript', 'history'])
})

test('an empty stack reports empty so callers can fall through to platform back', () => {
  const stack = createDismissStack()
  assert.equal(stack.pop(), 'empty')
  assert.equal(stack.depth(), 0)
  assert.equal(stack.topLabel(), null)
})

test('a level gated off for a child comes back as the dismiss target when the child closes', () => {
  const stack = createDismissStack()
  const order: string[] = []
  // AutomationDashboard's shape: the parent gates itself off while its help panel is up.
  const parent = stack.register({ label: 'automation', dismiss: () => order.push('automation') })
  stack.setActive(parent, false)
  const help = stack.register({ label: 'help', dismiss: () => order.push('help') })
  assert.equal(stack.depth(), 1, 'a gated level is not a dismiss target and does not count')

  stack.pop()
  stack.unregister(help)
  stack.setActive(parent, true)
  assert.equal(stack.topLabel(), 'automation')
  stack.pop()
  assert.deepEqual(order, ['help', 'automation'])
})

test('order follows opening, not registration, so a root that registers up front still stacks correctly', () => {
  const stack = createDismissStack()
  const closed: string[] = []
  // The composition root's shape: every dialog registers at mount, in source order,
  // inactive. Registration order here is the reverse of the order they get opened in.
  const settings = stack.register({ label: 'settings', active: false, dismiss: () => closed.push('settings') })
  const menu = stack.register({ label: 'menu', active: false, dismiss: () => closed.push('menu') })
  const sidebar = stack.register({ label: 'sidebar', active: false, dismiss: () => closed.push('sidebar') })

  stack.setActive(sidebar, true)
  stack.setActive(settings, true)
  stack.setActive(menu, true)

  // Registration order would have popped settings first. Opening order pops the menu.
  assert.equal(stack.topLabel(), 'menu')
  stack.pop()
  stack.setActive(menu, false)
  assert.equal(stack.topLabel(), 'settings')
  stack.pop()
  stack.setActive(settings, false)
  assert.equal(stack.topLabel(), 'sidebar')
  stack.pop()
  assert.deepEqual(closed, ['menu', 'settings', 'sidebar'])
})

test('reopening a level moves it back to the top', () => {
  const stack = createDismissStack()
  const closed: string[] = []
  const first = stack.register({ label: 'first', dismiss: () => closed.push('first') })
  stack.register({ label: 'second', dismiss: () => closed.push('second') })

  // Closing and reopening the older level makes it the newer one.
  stack.setActive(first, false)
  stack.setActive(first, true)
  assert.equal(stack.topLabel(), 'first')
  stack.pop()
  assert.deepEqual(closed, ['first'])
})

test('an inactive level is neither counted nor dismissed', () => {
  const stack = createDismissStack()
  let closed = 0
  stack.register({ label: 'gated', active: false, dismiss: () => { closed++ } })
  assert.equal(stack.depth(), 0)
  assert.equal(stack.pop(), 'empty')
  assert.equal(closed, 0)
})

test('a blocking level absorbs the pop rather than letting it fall through', () => {
  const stack = createDismissStack()
  let closed = 0
  stack.register({ label: 'history', dismiss: () => { closed++ } })
  stack.register({ label: 'daemon-reload', blocking: true, dismiss: () => { closed++ } })

  assert.equal(stack.pop(), 'blocked')
  assert.equal(closed, 0)
  // Still counted, so the history sentinel stays armed and back keeps being intercepted.
  assert.equal(stack.depth(), 2)
})

test('a repeated pop is inert until the stack changes shape', () => {
  const stack = createDismissStack()
  let closed = 0
  stack.register({ label: 'modal', dismiss: () => { closed++ } })

  assert.equal(stack.pop(), 'popped')
  // Back pressed twice before the modal unmounted must not also close what is behind it.
  assert.equal(stack.pop(), 'pending')
  assert.equal(closed, 1)
})

test('a guarded dismiss that opens a confirmation makes the next pop close the confirmation', () => {
  const stack = createDismissStack()
  const closed: string[] = []
  let confirmId = 0
  // Settings' shape: closing dirty settings opens a Save/Discard decision instead of closing.
  stack.register({
    label: 'settings',
    dismiss: () => {
      closed.push('settings-attempt')
      confirmId = stack.register({ label: 'save-discard', dismiss: () => closed.push('save-discard') })
    },
  })

  assert.equal(stack.pop(), 'popped')
  assert.equal(stack.depth(), 2)
  // The confirmation registered, which cleared the pending guard, so back now reaches it.
  assert.equal(stack.pop(), 'popped')
  assert.deepEqual(closed, ['settings-attempt', 'save-discard'])
  stack.unregister(confirmId)
})

test('unregistering out of order removes the right level', () => {
  const stack = createDismissStack()
  const closed: string[] = []
  const outer = stack.register({ label: 'outer', dismiss: () => closed.push('outer') })
  stack.register({ label: 'inner', dismiss: () => closed.push('inner') })

  // A parent can unmount first (a route change closing everything under it).
  stack.unregister(outer)
  assert.equal(stack.depth(), 1)
  assert.equal(stack.topLabel(), 'inner')
  stack.pop()
  assert.deepEqual(closed, ['inner'])
})

test('unregistering an unknown id is a no-op so double-unmount is safe', () => {
  const stack = createDismissStack()
  const id = stack.register({ label: 'modal', dismiss: () => undefined })
  stack.unregister(id)
  stack.unregister(id)
  assert.equal(stack.depth(), 0)
})

test('subscribers see every structural change, so the history sentinel can track depth', () => {
  const stack = createDismissStack()
  const depths: number[] = []
  const unsubscribe = stack.subscribe(() => depths.push(stack.depth()))

  const id = stack.register({ label: 'modal', dismiss: () => undefined })
  stack.setActive(id, false)
  stack.setActive(id, true)
  stack.unregister(id)
  assert.deepEqual(depths, [1, 0, 1, 0])

  unsubscribe()
  stack.register({ label: 'after', dismiss: () => undefined })
  assert.equal(depths.length, 4)
})

test('setActive to the current value does not notify', () => {
  const stack = createDismissStack()
  let notifications = 0
  const id = stack.register({ label: 'modal', dismiss: () => undefined })
  stack.subscribe(() => { notifications++ })
  stack.setActive(id, true)
  assert.equal(notifications, 0)
  stack.setActive(id, false)
  assert.equal(notifications, 1)
})

test('the trace ring records outcomes and stays bounded', () => {
  let clock = 0
  const stack = createDismissStack(() => ++clock)
  const id = stack.register({ label: 'history', dismiss: () => undefined })
  stack.pop()
  const events = stack.trace()
  assert.deepEqual(events.map(event => event.action), ['register', 'pop'])
  assert.equal(events[1].result, 'popped')
  assert.equal(events[1].label, 'history')
  assert.equal(events[0].at, 1)
  stack.unregister(id)

  for (let index = 0; index < DISMISS_TRACE_LIMIT + 10; index++) {
    stack.unregister(stack.register({ label: `churn-${index}`, dismiss: () => undefined }))
  }
  assert.equal(stack.trace().length, DISMISS_TRACE_LIMIT)
})
