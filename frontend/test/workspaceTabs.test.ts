import assert from 'node:assert/strict'
import test from 'node:test'
import { isFocusTraversalKey } from '../src/keys.ts'
import type { PaneStack } from '../src/layout.ts'
import { relativeStackTab } from '../src/workspaceTabs.ts'

const stack: PaneStack = {
  type: 'stack', id: 'pane-a', active_child_id: 'note-a', children: [
    { type: 'leaf', kind: 'terminal', id: 'term-a' },
    { type: 'leaf', kind: 'note', id: 'note-a' },
    { type: 'leaf', kind: 'preview', id: 'preview-a' },
  ],
}

test('workspace tab cycling follows pane order and wraps in both directions', () => {
  assert.equal(relativeStackTab(stack, 'term-a', 1)?.id, 'note-a')
  assert.equal(relativeStackTab(stack, 'preview-a', 1)?.id, 'term-a')
  assert.equal(relativeStackTab(stack, 'term-a', -1)?.id, 'preview-a')
  assert.equal(relativeStackTab(stack, 'note-a', -1)?.id, 'term-a')
})

test('workspace tab cycling falls back to the active child and ignores singleton panes', () => {
  assert.equal(relativeStackTab(stack, 'missing', 1)?.id, 'preview-a')
  assert.equal(relativeStackTab({...stack, children: [stack.children[0]]}, 'term-a', 1), null)
  assert.equal(relativeStackTab(null, 'term-a', 1), null)
})

test('only unmodified Tab and Shift+Tab are focus traversal keys', () => {
  const key = (overrides: Partial<KeyboardEvent> = {}) => ({
    key: 'Tab', ctrlKey: false, altKey: false, metaKey: false, ...overrides,
  } as KeyboardEvent)
  assert.equal(isFocusTraversalKey(key()), true)
  assert.equal(isFocusTraversalKey(key({shiftKey: true})), true)
  assert.equal(isFocusTraversalKey(key({ctrlKey: true})), false)
  assert.equal(isFocusTraversalKey(key({ctrlKey: true, shiftKey: true})), false)
  assert.equal(isFocusTraversalKey(key({metaKey: true})), false)
  assert.equal(isFocusTraversalKey(key({altKey: true})), false)
})
