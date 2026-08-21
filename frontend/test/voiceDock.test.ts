import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import {
  DEFAULT_VOICE_DOCK, canCollapseVoiceDock, canExpandVoiceDock, effectiveVoicePanelMode,
  isVoicePanelMode, reduceVoiceDock, voiceAddressee, voiceBodyVariant, voiceDockPersistable,
} from '../src/voiceDock.ts'
import type { VoiceDockModel } from '../src/voiceDock.ts'

const dir = join(import.meta.dirname, '..', 'src')
// Normalized to LF: these assertions slice on literal "\n" anchors, and a
// working copy checked out through autocrlf (any fresh merge checkout on
// Windows) reads back CRLF, which silently breaks an indexOf bound into -1 and
// turns a scoped slice into "the rest of the file".
const read = (name: string) => readFileSync(join(dir, name), 'utf8').replace(/\r\n/g, '\n')
const control = read('ConversationControl.tsx')
const readTab = read('VoiceReadTab.tsx')
const panel = read('AssistantPanel.tsx')
const app = read('App.tsx')
const css = read('style.css')

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

test('a body is drawn only when the dock is open and it is the selected one', () => {
  assert.equal(voiceBodyVariant('chip', 'chat', 'chat'), 'hidden')
  assert.equal(voiceBodyVariant('peek', 'chat', 'chat'), 'peek')
  assert.equal(voiceBodyVariant('full', 'chat', 'chat'), 'full')
  assert.equal(voiceBodyVariant('full', 'dictation', 'chat'), 'hidden')
  assert.equal(voiceBodyVariant('full', 'dictation', 'dictation'), 'full')
  assert.equal(voiceBodyVariant('full', 'read', 'read'), 'full')
  assert.equal(voiceBodyVariant('full', 'read', 'chat'), 'hidden')
  // With capture off there is no draft to dictate into, so the dock shows the assistant.
  assert.equal(effectiveVoicePanelMode('dictation', false), 'chat')
  assert.equal(effectiveVoicePanelMode('dictation', true), 'dictation')
  // Read aloud's panel needs no microphone and says nothing about one, so capture
  // never moves it in either direction.
  assert.equal(effectiveVoicePanelMode('read', false), 'read')
  assert.equal(effectiveVoicePanelMode('read', true), 'read')
  assert.ok(isVoicePanelMode('read'))
  assert.ok(!isVoicePanelMode('tts'))
})

test('only the dictation draft is ever the addressee', () => {
  // The draft has no surface but its own, so speech may only land there while it is
  // the body on screen. Every other body - the assistant, and the read-aloud panel,
  // which is a control surface with no conversation behind it - leaves the assistant
  // as the addressee. That is the shipped chat rule generalized, not a new one, and
  // the dock's header states it rather than redirecting speech silently.
  assert.equal(voiceAddressee('dictation', true), 'dictation')
  assert.equal(voiceAddressee('dictation', false), 'assistant')
  assert.equal(voiceAddressee('chat', true), 'assistant')
  assert.equal(voiceAddressee('read', true), 'assistant')
  assert.match(control, /talkActive&&!dictating&&<span class="dictation-mic-note"/)
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

test('one top-bar control: click is the panel, ctrl+click is capture', () => {
  // This deliberately REVERSES the rule that shipped with the dock, which was that the
  // panel chip and the microphone are separate buttons with separate jobs. In use that
  // read as two voice buttons whose difference had to be remembered, on the row with the
  // least space in the app. The separation survives as the modifier rather than as the
  // button, and the assertion below is what stops it collapsing into one ambiguous tap.
  const toggle = control.slice(
    control.indexOf('export function VoiceControl'),
    control.indexOf("/**\n * The one voice surface"),
  )
  assert.ok(toggle.length > 0 && toggle.includes('conversation-talk-toggle'))
  // Unmodified is the panel, because it is the common action.
  assert.match(toggle, /if\(event\.ctrlKey\|\|event\.metaKey\)\{toggleCapture\(\);return\}\n\s*onToggleDock\(\)/)
  // Capture is reached by the modifier and by a hold - the phone has no ctrl key, so
  // the same 550 ms hold the rest of the app uses is the touch route to it.
  assert.match(toggle, /const toggleCapture=\(\)=>\{/)
  assert.match(toggle, /heldRef\.current=true\n\s*toggleCapture\(\)/)
  assert.match(control, /const CAPTURE_HOLD_MS=550/)
  // A hold that fired must not also open the panel when the finger lifts.
  assert.match(toggle, /if\(heldRef\.current\)\{heldRef\.current=false;return\}/)
  // Exactly one voice control in each top bar, and the second button is gone.
  for (const bar of ['mobile-toolbar', 'app-identity']) {
    assert.ok(app.includes(bar), `missing ${bar}`)
  }
  assert.equal(app.split('<VoiceControl').length - 1, 2)
  assert.doesNotMatch(app, /VoiceDockChip/)
  assert.doesNotMatch(control, /VoiceDockChip/)
})

test('the panel owns the primary capture control, and the size steps are separate', () => {
  // Ctrl+click does not exist on touch, so the in-modal microphone is the capture
  // control there and the top-bar hold is the shortcut. It starts as well as stops:
  // the header used to carry only a `stop mic`, which meant the panel could release
  // the microphone but never take it.
  assert.match(control, /class=\{`voice-dock-mic \$\{talkActive\?'active':'off'\}/)
  assert.match(control, /onClick=\{captureConfigured\?\(\)=>conversation\.toggle\(\):\(\)=>requestSetting\('voice\.stt'\)\}/)
  assert.match(control, /<MicIcon slashed=\{!talkActive\}\/>/)
  assert.doesNotMatch(control, />stop mic</)
  // And it is lit by capture alone, like the top-bar control it mirrors.
  const mic = css.match(/\n\s*\.voice-dock-mic\{([^}]+)\}/)
  assert.ok(mic, 'missing dock mic rule')
  assert.doesNotMatch(mic[1], /green/)
  assert.match(css, /\n\s*\.voice-dock-mic\.active\{[^}]*--talk-edge:var\(--green/)
  // The size steps stay their own two buttons at the other end of the header, and
  // neither of them reaches capture.
  assert.match(control, /onClick=\{\(\)=>onDock\('collapse'\)\}/)
  assert.match(control, /onClick=\{\(\)=>onDock\('expand'\)\}/)
})

test('read aloud is a third body in the panel, mounted once and hidden rather than dropped', () => {
  // Same rule as the assistant body above: the panel holds the clip list it has
  // fetched and its subscription to clip events, so a remount on every tab switch
  // would refetch the whole list to render the same rows.
  assert.match(control, /<div class="voice-dock-body voice-dock-read" hidden=\{read\?undefined:true\}>\{readView\}<\/div>/)
  assert.equal(app.split('<VoiceReadTab').length - 1, 1)
  assert.match(control, /onClick=\{\(\)=>onMode\('read'\)\}>tts</)
  // The master switch keeps exactly one owner. The tab may turn it on through the
  // standard gate - a grant only ever turns something on - and links to Settings for
  // turning it off, rather than carrying a second copy of the switch.
  assert.match(readTab, /<GrantGate\n\s*ids=\{\['voice\.tts'\]\}/)
  assert.match(readTab, /<SettingLink target="voice\.tts"/)
  assert.doesNotMatch(readTab, /type="checkbox"/)
  // And the per-session control left the pane bar, so it is answered once rather than
  // once per drawn pane.
  assert.doesNotMatch(app, /tts:\{voiceMode/)
  assert.doesNotMatch(app, /tts:setup/)
})

test('the global clip list is ordered by when each reply arrived', () => {
  // Synthesis order is exactly wrong for a held backlog: clips are made in whatever
  // order engine slots and summary calls free up, so it puts an hour-old update above
  // the reply that just landed. `source_ts` is captured at generation time for this.
  assert.match(readTab, /typeof clip\.source_ts === 'number' \? clip\.source_ts : clip\.created_at/)
  // The daemon's synthesis state and this device's playback state are separate fields
  // and stay separate: a clip is `ready` on the daemon forever, while `played` is true
  // on the laptop and false on the phone.
  assert.match(readTab, /if \(clip\.status === 'synthesizing'\) return 'synthesizing'/)
  assert.match(readTab, /return device \|\| 'ready'/)
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
