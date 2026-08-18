import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const dir = join(import.meta.dirname, '..', 'src')
const control = readFileSync(join(dir, 'ConversationControl.tsx'), 'utf8')
const css = readFileSync(join(dir, 'style.css'), 'utf8')

const toggle = control.slice(
  control.indexOf('export function ConversationToggle'),
  control.indexOf('export function ConversationSurface'),
)

test('the Talk toggle is lit only while capture is running', () => {
  // The regression this guards is a green base: the button wore the chip in every
  // state, so the one question a microphone control has to answer - "is this
  // listening to me right now?" - could not be answered from the button.
  const base = css.match(/\n\s*\.conversation-talk-toggle\{([^}]+)\}/)
  assert.ok(base, 'missing base rule')
  assert.match(base![1], /--talk-edge:var\(--line\)/)
  assert.match(base![1], /--talk-fill:transparent/)
  assert.match(base![1], /--talk-ink:var\(--muted\)/)
  assert.doesNotMatch(base![1], /green/, 'the resting state must carry no green at all')

  // Green lives on `.active` only, and `.active` is driven by capture actually
  // running rather than by whether Talk is configured.
  const active = css.match(/\n\s*\.conversation-talk-toggle\.active\{([^}]+)\}/)
  assert.ok(active, 'missing active rule')
  assert.match(active![1], /--talk-edge:var\(--green/)
  assert.match(active![1], /--talk-fill:color-mix\(in srgb,var\(--green/)
  assert.match(toggle, /const active=conversation\.phase!=='off'/)
  assert.match(toggle, /\$\{active\?'active':'off'\}/)

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

test('the Talk toggle is a mic glyph that carries its own state, with no label', () => {
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

  // Three states, and only one of them can start listening: off and unconfigured
  // both read as "not listening", so the unconfigured one keeps a mark of its own.
  assert.match(css, /\n\s*\.conversation-talk-toggle\.setup\{--talk-edge-style:dashed\}/)
  assert.match(toggle, /\$\{configured\?'':' setup'\}/)
  // Unconfigured, the toggle goes to the microphone switch itself rather than to the
  // Voice tab, through the shared setting-link routing.
  assert.match(toggle, /onClick=\{configured\?\(\)=>conversation\.toggle\(\):\(\)=>requestSetting\('voice\.stt'\)\}/)
  assert.match(toggle, /aria-pressed=\{configured\?active:undefined\}/)
})
