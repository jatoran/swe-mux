import assert from 'node:assert/strict'
import test from 'node:test'
import {
  conversationTargetAvailable,
  effectiveConversationTarget,
  resolveConversationTarget,
  toggleConversationTargetPin,
  type VoiceSessionCandidate,
} from '../src/conversationTarget.ts'
import type { EditorHandle } from '../src/insertTarget.ts'
import { forgetEditorFocus, noteEditorFocus, subscribeInsertTarget } from '../src/insertTarget.ts'

const live = new Set(['agent-a', 'agent-b'])
const sessions: VoiceSessionCandidate[] = ['agent-a', 'agent-b'].map(id => ({
  id,
  label: `Agent · ${id}`,
  available: () => live.has(id),
  agentRunId: () => `${id}-run`,
  voiceMode: () => null,
  voiceContent: () => null,
}))

test('conversation target follows terminal focus and falls back to the focused agent', () => {
  assert.equal(resolveConversationTarget({ kind: 'terminal', sessionId: 'agent-b', at: 2 }, sessions, 'agent-a')?.id, 'agent-b')
  assert.equal(resolveConversationTarget(null, sessions, 'agent-a')?.id, 'agent-a')
  live.delete('agent-b')
  assert.equal(resolveConversationTarget({ kind: 'terminal', sessionId: 'agent-b', at: 3 }, sessions, 'agent-a')?.id, 'agent-a')
  live.add('agent-b')
})

test('a named text surface outranks the terminal fallback only while mounted', () => {
  let inserted = ''
  const editor: EditorHandle = { insertText: text => { inserted = text }, isConnected: true }
  const focused = {
    kind: 'editor' as const,
    editor,
    surface: { id: 'global:scratchpad', kind: 'scratchpad' as const, label: 'Scratchpad' },
    at: 5,
  }
  const target = resolveConversationTarget(focused, sessions, 'agent-a')
  assert.equal(target?.kind, 'text')
  assert.equal(target?.label, 'Scratchpad')
  if (target?.kind === 'text') target.editor.insertText('draft')
  assert.equal(inserted, 'draft')

  editor.isConnected = false
  assert.equal(resolveConversationTarget(focused, sessions, 'agent-a')?.id, 'agent-a')
})

test('pin keeps the exact sink until the user returns to focus-following mode', () => {
  const first = resolveConversationTarget({ kind: 'terminal', sessionId: 'agent-a', at: 1 }, sessions, null)
  const second = resolveConversationTarget({ kind: 'terminal', sessionId: 'agent-b', at: 2 }, sessions, null)
  const pinned = toggleConversationTargetPin(null, first)
  assert.equal(pinned?.id, 'agent-a')
  assert.equal(second?.id, 'agent-b')
  assert.equal(effectiveConversationTarget(second, pinned)?.id, 'agent-a')
  assert.equal(toggleConversationTargetPin(pinned, second), null)
  assert.equal(conversationTargetAvailable(pinned), true)
})

test('a pinned text sink becomes unavailable instead of following a replacement handle', () => {
  const oldEditor: EditorHandle = { insertText: () => {}, isConnected: true }
  const nextEditor: EditorHandle = { insertText: () => {}, isConnected: true }
  const oldTarget = resolveConversationTarget({
    kind: 'editor', editor: oldEditor,
    surface: { id: 'queue:agent-a', kind: 'queue', label: 'Queue · agent-a' }, at: 1,
  }, sessions, null)
  const nextTarget = resolveConversationTarget({
    kind: 'editor', editor: nextEditor,
    surface: { id: 'queue:agent-b', kind: 'queue', label: 'Queue · agent-b' }, at: 2,
  }, sessions, null)
  const pinned = toggleConversationTargetPin(null, oldTarget)
  oldEditor.isConnected = false
  assert.equal(effectiveConversationTarget(nextTarget, pinned)?.id, 'queue:agent-a')
  assert.equal(conversationTargetAvailable(pinned), false)
})

test('a focused text surface republishes a resolved label change', () => {
  const editor: EditorHandle = { insertText: () => {}, isConnected: true }
  const labels: string[] = []
  const unsubscribe = subscribeInsertTarget(target => {
    if (target?.kind === 'editor' && target.surface) labels.push(target.surface.label)
  })
  noteEditorFocus(editor, { id: 'note:one', kind: 'note', label: 'Note · loading' })
  noteEditorFocus(editor, { id: 'note:one', kind: 'note', label: 'Decision log · Project' })
  forgetEditorFocus(editor)
  unsubscribe()
  assert.deepEqual(labels, ['Note · loading', 'Decision log · Project'])
})
