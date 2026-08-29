import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import { captureFailureNote, DESKTOP_MEDIA_MARKER, desktopMediaReport } from '../src/desktopShell.ts'

const dir = join(import.meta.dirname, '..', 'src')
const read = (name: string) => readFileSync(join(dir, name), 'utf8').replace(/\r\n/g, '\n')

const scope = (value: unknown): Record<string, unknown> => ({ [DESKTOP_MEDIA_MARKER]: value })

test('a browser has no marker, and that absence is the answer', () => {
  assert.equal(desktopMediaReport({}), null)
  assert.equal(desktopMediaReport(scope(undefined)), null)
})

test('a malformed marker is ignored rather than half-believed', () => {
  assert.equal(desktopMediaReport(scope('granted')), null)
  assert.equal(desktopMediaReport(scope({ state: 'sideways' })), null)
  assert.equal(desktopMediaReport(scope({ origin: 'http://127.0.0.1:8765' })), null)
})

test('a well-formed marker is read with every field defaulted', () => {
  const report = desktopMediaReport(scope({ state: 'armed', origin: 'http://127.0.0.1:8765' }))
  assert.deepEqual(report, {
    state: 'armed',
    origin: 'http://127.0.0.1:8765',
    detail: '',
    mechanism: null,
  })
})

test('a browser failure is passed through untouched', () => {
  assert.equal(captureFailureNote('Permission denied', null), 'Permission denied')
})

test('a granted shell sends the reader away from permissions', () => {
  const note = captureFailureNote('Permission denied', {
    state: 'granted', origin: 'http://127.0.0.1:8765', detail: 'granted', mechanism: null,
  })
  assert.match(note, /^Permission denied /)
  assert.match(note, /not a permission refusal/)
})

test('an armed shell says WebView2 never asked, which is a different lead', () => {
  const note = captureFailureNote('Permission denied', {
    state: 'armed', origin: 'http://127.0.0.1:8765', detail: 'ready', mechanism: null,
  })
  assert.match(note, /never asked/)
  assert.match(note, /Windows microphone privacy/)
})

test('a shell that could not install the grant reports its own reason verbatim', () => {
  const note = captureFailureNote('Permission denied', {
    state: 'unsupported', origin: 'http://127.0.0.1:8765',
    detail: 'this WebView2 runtime rejected the microphone permission handler',
    mechanism: null,
  })
  assert.match(note, /rejected the microphone permission handler/)
})

test('a refusal names the origin the shell would not grant', () => {
  const note = captureFailureNote('Permission denied', {
    state: 'refused', origin: 'http://127.0.0.1:8765',
    detail: 'a microphone request from https://example.test was denied', mechanism: null,
  })
  assert.match(note, /example\.test/)
})

test('a state with no sentence to add leaves the message alone', () => {
  const note = captureFailureNote('Permission denied', {
    state: 'unsupported', origin: 'http://127.0.0.1:8765', detail: '', mechanism: null,
  })
  assert.equal(note, 'Permission denied')
})

test('the voice status is re-read rather than latched null on one lost fetch', () => {
  // The whole desktop-shell microphone bug was this single line: a page that
  // lost its one `/api/voice` fetch answered every later press with a claim
  // about a daemon it had never successfully asked.
  const app = read('App.tsx')
  assert.doesNotMatch(app, /\/api\/voice'\)\.then\(setVoiceStatus\)\.catch\(\(\)=>setVoiceStatus\(null\)\)/)
  assert.match(app, /const loadVoiceStatus/)
  // Every consumer goes through the one loader, so none of them can reintroduce
  // the latch.
  assert.equal(app.match(/api<VoiceStatus>\('GET'/g)?.length, 1)
})

test('the reconnect refreshes the voice status unconditionally', () => {
  const app = read('App.tsx')
  const start = app.indexOf('next.onopen')
  const open = app.slice(start, app.indexOf('next.onerror', start))
  assert.match(open, /void loadVoiceStatus\(\)/)
  // Before `if (hadCursor)`, because a page whose first fetch failed has no
  // cursor to resume from and is exactly the page that needs this.
  assert.ok(open.indexOf('loadVoiceStatus') < open.indexOf('if (hadCursor)'))
})

test('a capture failure is shown, not only announced and tooltipped', () => {
  const control = read('ConversationControl.tsx')
  assert.match(control, /class="dictation-failure" role="alert"/)
  assert.match(control, /conversation\.phase==='error'&&!!conversation\.detail/)
  const css = read('style.css')
  assert.match(css, /\.dictation-failure\{/)
})

test('every Talk refusal goes through respond, so it lands in Talk history', () => {
  const control = read('ConversationControl.tsx')
  const from = control.indexOf('  const start=async()=>{')
  // Only the preflight, which is where the refusals live; the capture callbacks
  // built below it report through their own channels.
  const guard = control.slice(from, control.indexOf('unlockPlayback()', from))
  // The old guard used a bare setDetail, which left `talk:error` on screen and
  // nothing at all on disk - no request, no history entry, nothing to read back.
  assert.doesNotMatch(guard, /setPhase\('error'\);setDetail\(/)
  assert.match(guard, /reportFailure\(capability\.reason\)/)
  assert.match(guard, /await refreshStatusRef\.current\(\)/)
})
