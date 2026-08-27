import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { scrollbackRepaintNeeded } from '../src/terminalHealth.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

test('a wrapped-ring replay is recognized by its missing scrollback', () => {
  // The broken state: an OMP/Codex replay of pure live-region repaint traffic
  // parses to less than one screen of scrollback.
  assert.equal(scrollbackRepaintNeeded(true, 'normal', 0, 48), true)
  assert.equal(scrollbackRepaintNeeded(true, 'normal', 47, 48), true)
  // A healthy transcript replay yields screens of scrollback.
  assert.equal(scrollbackRepaintNeeded(true, 'normal', 48, 48), false)
  assert.equal(scrollbackRepaintNeeded(true, 'normal', 2000, 48), false)
  // Alternate-screen TUIs (Claude) never have scrollback; keying on it would
  // request a repaint forever.
  assert.equal(scrollbackRepaintNeeded(true, 'alternate', 0, 48), false)
  // A harness that never rewrites scrollback keeps its transcript in the ring
  // as ordinary lines, so an empty parse means an actually-empty session.
  assert.equal(scrollbackRepaintNeeded(false, 'normal', 0, 48), false)
  // Degenerate grid: rows=0 must not make every pane demand a repaint.
  assert.equal(scrollbackRepaintNeeded(true, 'normal', 0, 0), true)
  assert.equal(scrollbackRepaintNeeded(true, 'normal', 1, 0), false)
})

test('the repaint request fires after replay parse and again on first reveal', () => {
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  // finishReplay runs only after every replay write's parse callback drained, so the
  // buffer judgement there is trustworthy; a hidden warm-mount attach suppresses the
  // request, and the reveal path re-checks the retained judgement.
  const finish = source.slice(
    source.indexOf('const finishReplay = () => {'),
    source.indexOf('const handleMessage'),
  )
  assert.match(finish, /maybeRequestScrollbackRepaint\(\)/)
  const reveal = source.slice(
    source.indexOf('paneVisibilityRef.current = (nowVisible: boolean) => {'),
    source.indexOf('// Chromium device emulation'),
  )
  assert.match(reveal, /maybeRequestScrollbackRepaint\(\)/)
})

test('one request per parsed buffer: the flag resets only where the buffer does', () => {
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  const request = source.slice(
    source.indexOf('const maybeRequestScrollbackRepaint = () => {'),
    source.indexOf('const finishReplay'),
  )
  assert.match(request, /if \(scrollbackRepaintRequested \|\| replaying \|\| paneIsHidden\(\)\) return/)
  // `backendRef.current`, not the captured prop: this request lives in the terminal's
  // construction effect, which never re-runs on a promotion, so a pane spawned as a
  // shell would never ask for the repaint its agent needs.
  assert.match(request, /scrollbackRepaintNeeded\(repaintsScrollback\(backendRef\.current\)/)
  assert.match(request, /\{ type: 'repaint' \}/)
  // The reset must sit in the same branch as term.reset(): a re-parsed buffer earns
  // a fresh judgement, and nothing else may re-arm the request.
  const replayStart = source.slice(
    source.indexOf("if (frame.type === 'replay_start')"),
    source.indexOf("if (frame.type === 'replay_end')"),
  )
  assert.match(replayStart, /term\.reset\(\)[\s\S]*scrollbackRepaintRequested = false/)
  const assignments = source.split('scrollbackRepaintRequested = false').length - 1
  const declarations = source.split('let scrollbackRepaintRequested = false').length - 1
  assert.equal(declarations, 1, 'exactly one declaration')
  assert.equal(assignments - declarations, 1, 'exactly one reset site')
})

test('repair events reach the daemon durably, not only the opt-in ring buffer', () => {
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  const report = source.slice(
    source.indexOf('const reportRepair = ('),
    source.indexOf('const renderDiagnostic'),
  )
  assert.match(report, /diagnoseRender\?\.\(phase, detail\)/)
  assert.match(report, /\{ type: 'client_diagnostic', phase, detail \}/)
  // Every repair layer reports through reportRepair so its production firing rate is
  // measurable; the sweep's deferred/confirmed chatter stays local-only.
  for (const phase of [
    'write_pipeline_dead',
    'surface_drift_repair',
    'viewport_fit_drift_repair',
    'viewport_fit_resumed',
    'surface_repair_resumed',
    'scrollback_repaint_requested',
    'webgl_render_error',
  ]) {
    assert.match(source, new RegExp(`reportRepair\\('${phase}'`), `${phase} must report durably`)
  }
})
