import assert from 'node:assert/strict'
import test from 'node:test'
import { captureUnavailableNote } from '../src/previewCapture.ts'

// Phase 11 W9: a machine with neither half of the capture backend has to be told
// which half it is missing. These simulate each state rather than requiring it,
// because the machine running the gate may have both installed.

test('a missing extra and a missing browser read as different problems', () => {
  const noExtra = captureUnavailableNote({
    available: false,
    state: 'extra_missing',
    reason: 'the optional preview-capture extra (Playwright) is not installed',
    remedy: 'uv sync --extra preview-capture && uv run playwright install chromium',
  })
  const noBrowser = captureUnavailableNote({
    available: false,
    state: 'browser_missing',
    reason: 'Playwright is installed but no Chromium browser binary was found',
    remedy: 'uv run playwright install chromium',
  })

  assert.notEqual(noExtra, noBrowser)
  assert.match(noExtra, /not installed/)
  assert.match(noExtra, /Run: uv sync --extra preview-capture/)
  assert.match(noBrowser, /no browser to render with/)
  assert.match(noBrowser, /Run: uv run playwright install chromium/)
  // Telling someone who already has Playwright to install Playwright is the exact
  // failure the split exists to prevent.
  assert.ok(!noBrowser.includes('uv sync'))
})

test('no remedy says so instead of printing an empty command', () => {
  // The packaged desktop app ships no Playwright, so `uv sync` against the source
  // tree cannot reach its interpreter. The old copy rendered "Enable with: " with
  // nothing after it whenever the hint was absent.
  const note = captureUnavailableNote({
    available: false,
    state: 'extra_missing',
    reason: 'the packaged desktop app does not bundle Playwright',
    remedy: null,
  })

  assert.match(note, /No command on this machine enables it\./)
  assert.ok(!note.includes('Run: '))
})

test('an unstated state still produces an actionable sentence', () => {
  // A daemon older than this client returns no `state`. Reading that as the
  // commonest absence beats rendering "undefined" at the operator.
  const note = captureUnavailableNote({ available: false, remedy: 'uv sync --extra preview-capture' })

  assert.match(note, /Preview capture is not installed\./)
  assert.match(note, /Run: uv sync --extra preview-capture/)
})
