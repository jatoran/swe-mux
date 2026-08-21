import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import {
  DEFAULT_VOICE_DOCK, canCollapseVoiceDock, canExpandVoiceDock, effectiveVoicePanelMode,
  reduceVoiceDock, voiceBodyVariant, voiceDockPersistable,
} from '../src/voiceDock.ts'
import type { VoiceDockModel } from '../src/voiceDock.ts'

const dir = join(import.meta.dirname, '..', 'src')
const control = readFileSync(join(dir, 'ConversationControl.tsx'), 'utf8')
const panel = readFileSync(join(dir, 'AssistantPanel.tsx'), 'utf8')
const app = readFileSync(join(dir, 'App.tsx'), 'utf8')
const css = readFileSync(join(dir, 'style.css'), 'utf8')

const model = (state: VoiceDockModel['state'], expanded: VoiceDockModel['expanded'] = 'full', borrowed = false): VoiceDockModel =>
  ({ state, expanded, borrowed })

test('the dock steps through three sizes and clamps at both ends', () => {
  assert.equal(reduceVoiceDock(model('chip'), { kind: 'expand' }).state, 'peek')
  assert.equal(reduceVoiceDock(model('peek'), { kind: 'expand' }).state, 'full')
  assert.equal(reduceVoiceDock(model('full'), { kind: 'expand' }).state, 'full')
  assert.equal(reduceVoiceDock(model('full'), { kind: 'collapse' }).state, 'peek')
  assert.equal(reduceVoiceDock(model('peek'), { kind: 'collapse' }).state, 'chip')
  assert.equal(reduceVoiceDock(model('chip'), { kind: 'collapse' }).state, 'chip')
  assert.equal(canExpandVoiceDock('full'), false)
  assert.equal(canCollapseVoiceDock('chip'), false)
})

test('the chip reopens into the size it was collapsed from', () => {
  // A deliberate peek must not come back as a full panel: "out of the way" is a choice,
  // and silently promoting it hands the workspace back to a surface the operator shrank.
  const peeked = reduceVoiceDock(model('full'), { kind: 'collapse' })
  const chipped = reduceVoiceDock(peeked, { kind: 'collapse' })
  assert.equal(chipped.state, 'chip')
  assert.equal(chipped.expanded, 'peek')
  assert.equal(reduceVoiceDock(chipped, { kind: 'toggle' }).state, 'peek')
  // And the toggle is symmetric: from anything open it goes to the chip.
  assert.equal(reduceVoiceDock(model('full'), { kind: 'toggle' }).state, 'chip')
})

test('the microphone may open the dictation draft, and only that', () => {
  // Starting Talk with the dock collapsed would otherwise leave the operator dictating
  // into a surface they cannot see - the draft has no other home. The assistant is
  // deliberately excluded: it speaks its replies, which is what makes leaving it
  // collapsed-and-live the point of the chip.
  const started = reduceVoiceDock(model('chip'), { kind: 'capture', active: true, addressee: 'dictation' })
  assert.equal(started.state, 'full')
  assert.equal(started.borrowed, true)
  const chatStart = reduceVoiceDock(model('chip'), { kind: 'capture', active: true, addressee: 'assistant' })
  assert.deepEqual(chatStart, model('chip'))
  // Nothing already open is resized by capture, in either direction.
  assert.equal(reduceVoiceDock(model('peek'), { kind: 'capture', active: true, addressee: 'dictation' }).state, 'peek')
})

test('stopping the microphone returns only what the microphone borrowed', () => {
  const borrowed = reduceVoiceDock(model('chip'), { kind: 'capture', active: true, addressee: 'dictation' })
  const stopped = reduceVoiceDock(borrowed, { kind: 'capture', active: false, addressee: 'dictation' })
  assert.equal(stopped.state, 'chip')
  assert.equal(stopped.borrowed, false)

  // The load-bearing half of the split: once the operator has touched the dock it is
  // theirs, so stopping capture leaves it exactly where they put it.
  const adjusted = reduceVoiceDock(borrowed, { kind: 'collapse' })
  assert.equal(adjusted.borrowed, false)
  const afterStop = reduceVoiceDock(adjusted, { kind: 'capture', active: false, addressee: 'dictation' })
  assert.deepEqual(afterStop, adjusted)

  // A dock the operator opened themselves survives the microphone entirely.
  const owned = reduceVoiceDock(model('chip'), { kind: 'set', state: 'full' })
  assert.deepEqual(reduceVoiceDock(owned, { kind: 'capture', active: false, addressee: 'dictation' }), owned)
})

test('an open confirmation card raises the dock and never lowers it', () => {
  const raised = reduceVoiceDock(model('chip'), { kind: 'floor', state: 'peek' })
  assert.equal(raised.state, 'peek')
  // Not a loan: a card is not the microphone, so stopping capture must not take the
  // raised dock away while the card is still open.
  assert.equal(raised.borrowed, false)
  // Never a demotion.
  assert.equal(reduceVoiceDock(model('full'), { kind: 'floor', state: 'peek' }).state, 'full')
  // And it leaves the remembered size alone, so the chip still reopens into `full`.
  assert.equal(raised.expanded, 'full')
})

test('only the operator’s own dock is persisted', () => {
  assert.equal(voiceDockPersistable(model('full')), true)
  assert.equal(voiceDockPersistable(model('full', 'full', true)), false)
  assert.equal(DEFAULT_VOICE_DOCK.state, 'chip')
})

test('a body is drawn only when the dock is open and it is the addressee', () => {
  assert.equal(voiceBodyVariant('chip', 'chat', 'chat'), 'hidden')
  assert.equal(voiceBodyVariant('peek', 'chat', 'chat'), 'peek')
  assert.equal(voiceBodyVariant('full', 'chat', 'chat'), 'full')
  assert.equal(voiceBodyVariant('full', 'dictation', 'chat'), 'hidden')
  assert.equal(voiceBodyVariant('full', 'dictation', 'dictation'), 'full')
  // With capture off there is no draft to dictate into, so the dock shows the assistant.
  assert.equal(effectiveVoicePanelMode('dictation', false), 'chat')
  assert.equal(effectiveVoicePanelMode('dictation', true), 'dictation')
})

test('collapsing the dock hides it in CSS rather than unmounting the conversation', () => {
  // The regression this pins is the announce loop's client-side cut: `announcedRef` in
  // AssistantPanel is per-instance, so a remount is indistinguishable from a device that
  // has never seen an open card and speaks its line again. Every size and addressee
  // change must therefore be a rendering change, never a mount change.
  assert.match(css, /\n\s*\.voice-dock\.chip\{display:none\}/)
  assert.match(control, /<div class="voice-dock-body voice-dock-chat" hidden=\{chat\?undefined:true\}>\{assistantView\}<\/div>/)
  assert.doesNotMatch(control, /\{chat&&assistantView\}/)
  // `hidden` is a render in the panel too - the early return still returns an element.
  assert.match(panel, /if \(variant === 'hidden'\) return <div class="assistant-panel hidden-variant" hidden \/>/)
  // Exactly one mount site in the whole app.
  assert.equal(app.split('<AssistantPanel').length - 1, 1)
  // And it is not behind a conditional the way the two old placements were.
  assert.match(app, /<div class="voice-dock-anchor">\s*\n\s*<VoiceDock/)
})

test('the dock control and the microphone control are separate buttons', () => {
  const chip = control.slice(control.indexOf('export function VoiceDockChip'), control.indexOf('function ChatIcon'))
  // The chip moves the dock and nothing else: no capture call anywhere in it.
  assert.match(chip, /onClick=\{onToggle\}/)
  assert.doesNotMatch(chip, /conversation\./)
  // The talk toggle moves capture and nothing else: no dock state anywhere in it. Cut at
  // the chip's own doc comment rather than its `export`, or the prose about the dock in
  // that comment lands inside the slice.
  const toggle = control.slice(
    control.indexOf('export function ConversationToggle'),
    control.indexOf("/**\n * The voice dock's collapsed state"),
  )
  assert.ok(toggle.length > 0 && toggle.includes('conversation-talk-toggle'))
  assert.doesNotMatch(toggle, /dock/i)
  // Both live in both top bars, beside each other.
  for (const bar of ['mobile-toolbar', 'app-identity']) {
    assert.ok(app.includes(bar), `missing ${bar}`)
  }
  assert.equal(app.split('<VoiceDockChip').length - 1, 2)
  // The dock's own header carries the two size steps, and its only capture control is
  // labelled as such rather than doubling as a close button.
  assert.match(control, /onClick=\{\(\)=>onDock\('collapse'\)\}/)
  assert.match(control, /onClick=\{\(\)=>onDock\('expand'\)\}/)
  assert.match(control, /onClick=\{\(\)=>conversation\.stop\(\)\}>stop mic</)
})

test('the dock floats from a zero-height anchor instead of taking a workspace track', () => {
  // Same contract as the pane's read-aloud strip: a surface that takes a track changes a
  // pane's row count, which resizes the PTY under a live agent and reflows scrollback
  // that does not come back when the panel closes.
  // Column-anchored: the indented copies inside the mobile media block are overrides, and
  // matching one of those instead would pass while the base rule said anything at all.
  const anchor = css.match(/\n\.voice-dock-anchor\{([^}]+)\}/)
  assert.ok(anchor, 'missing anchor rule')
  assert.match(anchor![1], /position:relative/)
  assert.match(anchor![1], /height:0/)
  const dock = css.match(/\n\.voice-dock\{([^}]+)\}/)
  assert.ok(dock, 'missing dock rule')
  assert.match(dock![1], /position:absolute/)
  // The old fixed layer and the pane-hosted placement are both gone.
  assert.doesNotMatch(css, /\.conversation-layer/)
  assert.doesNotMatch(app, /placement="pane"/)
})

test('the peek row carries open cards and no composer', () => {
  const peek = panel.slice(panel.indexOf("if (variant === 'peek')"), panel.indexOf('return <div class="assistant-panel">'))
  assert.match(peek, /openActions\.map/)
  // A countdown the operator cannot act on is the one thing peek may not do.
  assert.doesNotMatch(peek, /assistant-input/)
  assert.doesNotMatch(peek, /assistant-log/)
  const talkPeek = control.slice(control.indexOf("{talkVariant==='peek'"), control.indexOf('<div class="voice-dock-body voice-dock-chat"'))
  assert.doesNotMatch(talkPeek.slice(0, talkPeek.indexOf(':<>')), /<textarea/)
})
