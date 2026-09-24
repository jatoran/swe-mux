import assert from 'node:assert/strict'
import test from 'node:test'
import { RECOVERY_PARAM, RecoveryStore, normalizeReason, recoveryMessage } from '../src/rendererRecovery.ts'

// The desktop shell reloads a crashed or hung page at `/?mux_recovered=<reason>`. The tab
// layout is shared by every device, so without a paused state the reload restores the
// Preview that froze the page (2026-09-24) and freezes it again.

test('an ordinary load pauses nothing and leaves the address alone', () => {
  const store = new RecoveryStore()
  assert.equal(store.consume('http://127.0.0.1:8765/?project=p1'), null)
  assert.equal(store.active, false)
  assert.equal(store.previewPaused('preview-a'), false)
  assert.equal(store.current(), null)
})

test('a recovery load pauses every preview and strips only its own parameter', () => {
  const store = new RecoveryStore()
  const clean = store.consume(`http://127.0.0.1:8765/?project=p1&${RECOVERY_PARAM}=renderer_hung#x`)
  assert.equal(clean, '/?project=p1#x')
  assert.equal(store.previewPaused('preview-a'), true)
  assert.equal(store.previewPaused('preview-b'), true)
  assert.deepEqual(store.current(), { reason: 'renderer_hung', message: recoveryMessage('renderer_hung') })
})

test('loading one preview resumes that preview only', () => {
  const store = new RecoveryStore()
  store.consume(`http://127.0.0.1:8765/?${RECOVERY_PARAM}=renderer_exited`)
  let notified = 0
  const unsubscribe = store.subscribe(() => { notified += 1 })
  store.resume('preview-a')
  assert.equal(store.previewPaused('preview-a'), false)
  assert.equal(store.previewPaused('preview-b'), true)
  assert.equal(notified, 1)
  unsubscribe()
  store.resume('preview-b')
  assert.equal(notified, 1)
})

test('loading all previews resumes them and retires the notice', () => {
  const store = new RecoveryStore()
  store.consume(`http://127.0.0.1:8765/?${RECOVERY_PARAM}=operator_reload`)
  store.resumeAll(['preview-a', 'preview-b'])
  assert.equal(store.previewPaused('preview-a'), false)
  assert.equal(store.previewPaused('preview-b'), false)
  assert.equal(store.current(), null)
  // A preview opened later in this page's life is still held: the page is still the
  // one that came up in safe mode.
  assert.equal(store.previewPaused('preview-c'), true)
})

test('dismissing the notice keeps previews paused', () => {
  const store = new RecoveryStore()
  store.consume(`http://127.0.0.1:8765/?${RECOVERY_PARAM}=renderer_hung`)
  store.dismiss()
  assert.equal(store.current(), null)
  assert.equal(store.previewPaused('preview-a'), true)
})

test('an arbitrary reason is reduced to a word and read as a manual safe mode', () => {
  assert.equal(normalizeReason('<img src=x>'), 'imgsrcx')
  assert.equal(normalizeReason('!!!'), 'manual')
  assert.equal(recoveryMessage('manual'), 'swe-mux was opened in safe mode.')
  assert.notEqual(recoveryMessage('renderer_exited'), recoveryMessage('renderer_hung'))
})
