import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  TERMINAL_RENDER_DIAGNOSTICS_KEY,
  diagnosticsOptIn,
  isWebglRenderError,
} from '../src/terminalRenderDiagnostics.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

test('render diagnostics can be opted into outside a dev build', () => {
  // The faults this instrumentation exists for happen on the frozen desktop app, which is
  // a production build. Gating it on `import.meta.env.DEV` alone made the one build that
  // needs the evidence the one build that cannot produce it.
  assert.equal(diagnosticsOptIn(() => '1'), true)
  assert.equal(diagnosticsOptIn(() => 'true'), true)
  assert.equal(diagnosticsOptIn(() => null), false)
  assert.equal(diagnosticsOptIn(() => '0'), false)
  assert.equal(diagnosticsOptIn(() => ''), false)
  // Read under exactly one key, since the user has to type it by hand to turn this on.
  let asked: string | null = null
  diagnosticsOptIn((key: string) => { asked = key; return null })
  assert.equal(asked, 'mux:terminal-render-diagnostics')
  assert.equal(TERMINAL_RENDER_DIAGNOSTICS_KEY, 'mux:terminal-render-diagnostics')
})

test('a webgl render fault is recognised from its message or its stack', () => {
  assert.equal(isWebglRenderError({ message: 'x', error: new Error('at WebglRenderer._updateModel') } as ErrorEvent), true)
  assert.equal(isWebglRenderError({ message: 'boom in @xterm/addon-webgl', error: null } as unknown as ErrorEvent), true)
  assert.equal(isWebglRenderError({ message: 'unrelated', error: new Error('nope') } as ErrorEvent), false)
})

test('the webgl drawing buffer is preserved, because the renderer only repaints damage', () => {
  // WebglRenderer._updateModel skips every cell whose code/fg/bg/ext match its model, so a
  // frame re-uploads only what changed and the rest is expected to still be in the drawing
  // buffer. With preserveDrawingBuffer false the browser may discard that buffer once the
  // canvas stops being composited — which is what `.pane-warm` (display:none) makes every
  // background tab — and the pane comes back with only the changed cells drawn. Selecting
  // text repaints the cells under it, which is how this presents: "it draws once I
  // highlight it". No event reports a discarded buffer, so no repair can be keyed on one.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  assert.match(source, /new WebglAddon\(true\)/)
  assert.ok(!/new WebglAddon\(\)/.test(source))
})

test('the surface confirmation repaints without re-running the fit', () => {
  // A fit is term.resize plus a pseudoconsole resize plus the CLI repainting everything it
  // is showing (EXPENSIVE_VIEWPORT_PASS_MS). Repeating that to chase a lost paint would put
  // back the per-frame cost the viewport scheduler exists to avoid; only the two calls that
  // put pixels back are worth repeating.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  const body = source.slice(
    source.indexOf('const runSurfaceRedraw'),
    source.indexOf('const armSurfaceConfirmation'),
  )
  assert.ok(body.length > 0, 'runSurfaceRedraw not found')
  assert.match(body, /clearTextureAtlas\(\)/)
  assert.match(body, /redrawVisibleTerminal\(term, host\.current\)/)
  assert.ok(!body.includes('measureFit('), 'the confirmation must not refit')
  assert.ok(!body.includes('applyGeometry('), 'the confirmation must not resize the PTY')
})
