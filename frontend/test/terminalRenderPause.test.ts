import assert from 'node:assert/strict'
import test from 'node:test'
import { terminalRenderControl } from '../src/terminalRenderPause.ts'

test('the control drives xterm\'s own intersection handler in both directions', () => {
  const entries: { isIntersecting: boolean; intersectionRatio: number; self: unknown }[] = []
  const original = function (this: unknown, entry: { isIntersecting: boolean; intersectionRatio: number }) {
    entries.push({ ...entry, self: this })
  }
  const service = { _handleIntersectionChange: original }
  const control = terminalRenderControl({ _core: { _renderService: service } })
  assert.equal(control.available, true)
  assert.equal(control.pause(), true)
  // The pause must be sticky: the browser's IntersectionObserver delivers its own
  // entries asynchronously, and a visibility:hidden box geometrically intersects, so
  // an unshadowed delivery would silently unpause the pane. The shadowed handler
  // must translate any delivery into not-intersecting.
  service._handleIntersectionChange({ isIntersecting: true, intersectionRatio: 1 })
  assert.equal(control.resume(), true)
  assert.equal(service._handleIntersectionChange, original, 'resume must restore the real handler')
  service._handleIntersectionChange.call(service, { isIntersecting: true, intersectionRatio: 1 })
  assert.deepEqual(
    entries.map(({ isIntersecting }) => isIntersecting),
    [false, false, true, true],
  )
  // Called as a method: xterm's handler reads renderer state off `this`.
  assert.ok(entries.every(entry => entry.self === service))
})

test('missing internals degrade to a no-op, never a throw', () => {
  for (const term of [undefined, null, {}, { _core: {} }, { _core: { _renderService: {} } }]) {
    const control = terminalRenderControl(term)
    assert.equal(control.available, false)
    assert.equal(control.pause(), false)
    assert.equal(control.resume(), false)
  }
})

test('a handler that throws reports failure instead of breaking the pane', () => {
  const control = terminalRenderControl({
    _core: {
      _renderService: {
        _handleIntersectionChange() { throw new Error('renderer disposed') },
      },
    },
  })
  assert.equal(control.available, true)
  assert.equal(control.pause(), false)
})
