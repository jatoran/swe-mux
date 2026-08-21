import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

// Source-shape assertions, in the style of `tabContextMenu.test.ts`: the two rules below
// are about *where* one behaviour lives, which no rendered assertion can pin down.

const src = (name: string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')
const queuePane = src('QueuePane.tsx')
const app = src('App.tsx')

test('delete exists once and is drawn twice', () => {
  // Two copies of a destructive control is exactly where behaviour drifts, so the row's
  // delete and the overflow's delete are the same function called twice.
  assert.equal(queuePane.split('deleteQueueMessage(message.id)').length - 1, 1)
  assert.match(queuePane, /const deleteButton = \(message: QueueMessage, busy: boolean, compact: boolean\)/)
  assert.equal(queuePane.split('deleteButton(message, busy').length - 1, 2)

  const helper = queuePane.slice(
    queuePane.indexOf('const deleteButton ='),
    queuePane.indexOf('const overflow ='),
  )
  // Same guards as the expanded delete always had: not mid-delivery, not while busy, and
  // arm-then-confirm through the one shared `deleteConfirmId`.
  assert.match(helper, /if \(message\.state === 'delivering'\) return null/)
  assert.match(helper, /disabled=\{busy\}/)
  assert.match(helper, /deleteConfirmId === message\.id/)
  assert.match(helper, /setDeleteConfirmId\(message\.id\)/)
})

test('the composer claims focus only where a keyboard is already there', () => {
  // Focusing a field on a soft-keyboard device is a layout change, not a convenience: the
  // keyboard rises over the list the tab was opened to read.
  assert.match(queuePane, /if \(openRequestToken && !hasSoftKeyboard\(\)\) composerRef\.current\?\.focus\(\)/)
  assert.match(queuePane, /import \{ hasSoftKeyboard \} from '\.\/deviceSettings'/)
})

test('revealing a queue is not a request to compose in it', () => {
  // `queued_behind` and `not_due` open the Queue to say where an already-written message
  // went. Bumping the open token for them put a caret in the composer for a message the
  // user had just finished writing.
  const opener = app.slice(
    app.indexOf('const openQueueForSession ='),
    app.indexOf('const openTranscriptForSession ='),
  )
  assert.match(opener, /const openQueueForSession = async \(sessionId: string, compose = true\)/)
  assert.match(opener, /if \(compose\) setQueueOpenToken/)
  assert.equal(app.split('openQueueForSession(sid, false)').length - 1, 2)
})
