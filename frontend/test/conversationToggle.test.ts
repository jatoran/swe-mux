import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const dir = join(import.meta.dirname, '..', 'src')
// Normalized to LF: the slices below cut on literal "\n" anchors, and a checkout
// through autocrlf reads back CRLF, which turns a scoped slice into the rest of
// the file without failing anything.
const read = (name: string) => readFileSync(join(dir, name), 'utf8').replace(/\r\n/g, '\n')
const control = read('ConversationControl.tsx')
const css = read('style.css')

const toggle = control.slice(
  control.indexOf('export function VoiceControl'),
  control.indexOf('/**\n * The one voice surface'),
)

test('the voice control is lit by capture and by nothing else', () => {
  // Two regressions are guarded here at once. The first is a green base: the button
  // wore the chip in every state, so the one question a microphone control has to
  // answer - "is this listening to me right now?" - could not be answered from it.
  // The second is newer and is what merging the panel chip into this button risks:
  // the panel being open must not light it either, or the lit state means two
  // things and therefore nothing.
  const base = css.match(/\n\s*\.conversation-talk-toggle\{([^}]+)\}/)
  assert.ok(base, 'missing base rule')
  assert.match(base![1], /--talk-edge:var\(--line\)/)
  assert.match(base![1], /--talk-fill:transparent/)
  assert.match(base![1], /--talk-ink:var\(--muted\)/)
  assert.doesNotMatch(base![1], /green/, 'the resting state must carry no green at all')

  const active = css.match(/\n\s*\.conversation-talk-toggle\.active\{([^}]+)\}/)
  assert.ok(active, 'missing active rule')
  assert.match(active![1], /--talk-edge:var\(--green/)
  assert.match(active![1], /--talk-fill:color-mix\(in srgb,var\(--green/)
  assert.match(toggle, /const active=conversation\.phase!=='off'/)
  assert.match(toggle, /\$\{active\?'active':'off'\}/)

  // The open panel gets a treatment of its own, and it is colourless. Both of the
  // non-capture states also stand down when capture is live, so nothing can repaint
  // a listening microphone.
  const open = css.match(/\n\s*\.conversation-talk-toggle\.dock-open:not\(\.active\)\{([^}]+)\}/)
  assert.ok(open, 'the open-panel state needs its own rule')
  assert.doesNotMatch(open![1], /green/)
  const pending = css.match(/\n\s*\.conversation-talk-toggle\.pending:not\(\.active\)\{([^}]+)\}/)
  assert.ok(pending, 'the waiting-card state needs its own rule')
  assert.doesNotMatch(pending![1], /green/)

  // The desktop header needs its own copy of the box because `.app-identity button`
  // is a generic icon-button reset that outranks the class. Both copies must read
  // the same variables, or one state drifts on one surface only.
  for (const rule of [
    /\n\s*\.app-identity \.conversation-talk-toggle\{([^}]+)\}/,
    /\n\s*\.mobile-toolbar>\.conversation-talk-toggle\{([^}]+)\}/,
  ]) {
    const match = css.match(rule)
    assert.ok(match, `missing host rule ${rule}`)
    assert.doesNotMatch(match![1], /green/, 'a host copy must not hard-code the lit colours')
  }
  const header = css.match(/\n\s*\.app-identity \.conversation-talk-toggle\{([^}]+)\}/)!
  assert.match(header[1], /border:1px var\(--talk-edge-style\) var\(--talk-edge\)/)
  assert.match(header[1], /background:var\(--talk-fill\)/)
  assert.match(header[1], /color:var\(--talk-ink\)/)
})

test('the voice control is a mic glyph that carries its own state, with no label', () => {
  // The slash is the entire "off" signal now that no word accompanies it, so it has
  // to be bound to the same flag as the highlight and not to some separate prop.
  assert.match(toggle, /<MicIcon slashed=\{!active\}\/>/)
  assert.doesNotMatch(toggle, /<span>/, 'the button carries no text')
  assert.match(control, /function MicIcon\(\{slashed\}:\{slashed:boolean\}\)/)
  assert.match(control, /\{slashed&&<path d="M4 3\.5l16 17"\/>\}/)

  // Square on both surfaces: a min-width sized for a word left the icon off-centre.
  for (const rule of [
    /\n\s*\.conversation-talk-toggle\{([^}]+)\}/,
    /\n\s*\.mobile-toolbar>\.conversation-talk-toggle\{([^}]+)\}/,
  ]) {
    const match = css.match(rule)
    assert.ok(match)
    const width = match![1].match(/(?:^|;)width:calc\((\d+)px\*var\(--ui-scale\)\)/)
    const height = match![1].match(/(?:^|;)height:calc\((\d+)px\*var\(--ui-scale\)\)/)
    assert.ok(width && height, `missing explicit box in ${rule}`)
    assert.equal(width![1], height![1])
  }

  // Three states, and only one of them can be listening: off and unconfigured both
  // read as "not listening", so the unconfigured one keeps a mark of its own.
  assert.match(css, /\n\s*\.conversation-talk-toggle\.setup\{--talk-edge-style:dashed\}/)
  assert.match(toggle, /\$\{configured\?'':' setup'\}/)
  // Unconfigured, reaching for capture goes to the microphone switch itself rather
  // than to the Voice tab, through the shared setting-link routing.
  assert.match(toggle, /if\(!configured\)\{requestSetting\('voice\.stt'\);return\}/)
  // Pressed is capture; expanded is the panel. A screen reader is told the two
  // states separately, exactly as the colour and the badge show them separately.
  assert.match(toggle, /aria-pressed=\{configured\?active:undefined\}/)
  assert.match(toggle, /aria-expanded=\{!collapsed\}/)
})

test('the control carries what is waiting behind a collapsed panel', () => {
  // It is the only way back to a dock that is still streaming, speaking, and opening
  // cards, so it has to say that something is there. A count outranks a dot because a
  // confirmation card expires and an unread reply does not.
  assert.match(toggle, /pendingActions>0\n\s*\?<i class="voice-dock-badge">/)
  assert.match(toggle, /unseen\?<i class="voice-dock-dot" aria-hidden="true"\/>:null/)
  // The badge is absolutely positioned, so the button has to be its containing block.
  const base = css.match(/\n\s*\.conversation-talk-toggle\{([^}]+)\}/)!
  assert.match(base[1], /position:relative/)
  for (const mark of ['.voice-dock-badge', '.voice-dock-dot']) {
    const rule = css.match(new RegExp(`\\n\\s*\\${mark}\\{([^}]+)\\}`))
    assert.ok(rule, `missing ${mark} rule`)
    assert.match(rule![1], /position:absolute/)
  }
})
