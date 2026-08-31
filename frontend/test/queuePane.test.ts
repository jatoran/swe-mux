import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

// Source-shape assertions, in the style of `tabContextMenu.test.ts`: the rules below are
// about *where* one behaviour lives, which no rendered assertion can pin down.

const src = (name: string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')
const queuePane = src('QueuePane.tsx')
const app = src('App.tsx')

test('delete exists once and is drawn once', () => {
  // It used to be drawn twice - inline and again in the tray - which put two copies of a
  // destructive control on screen together the moment the tray was open. One helper, one
  // call site, and the tray no longer carries a copy.
  assert.equal(queuePane.split('deleteQueueMessage(message.id)').length - 1, 1)
  assert.match(queuePane, /const deleteButton = \(message: QueueMessage \| null, busy: boolean\)/)
  assert.equal(queuePane.split('deleteButton(message, busy').length - 1, 1)

  const helper = queuePane.slice(
    queuePane.indexOf('const deleteButton ='),
    queuePane.indexOf('const overflow ='),
  )
  // Same guards the expanded delete always had: not mid-delivery, not while busy, and
  // arm-then-confirm through the one shared `deleteConfirmId`.
  assert.match(helper, /if \(message\?\.state === 'delivering'\) return null/)
  assert.match(helper, /disabled=\{busy\}/)
  assert.match(helper, /deleteConfirmId === message\.id/)
  assert.match(helper, /setDeleteConfirmId\(message\.id\)/)
  // A draft autosave never created has nothing to confirm about: no daemon has its text.
  assert.match(helper, /onClick=\{discardDraft\}/)
})

test('there is one writing surface and it is a queue row', () => {
  // Two text fields with different rules - a footer composer that staged and autosaved
  // nothing, and a row editor behind an explicit Save - sharing a 300px column is what
  // made "the thing I typed disappeared" possible in two different ways.
  assert.ok(!queuePane.includes('queue-composer'), 'the composer footer should be gone')
  assert.equal(queuePane.split('<textarea').length - 1, 1)
  assert.match(queuePane, /class="queue-compose-bar"/)
  assert.match(queuePane, /const startDraft = \(\)/)
})

test('the editor claims focus only where a keyboard is already there', () => {
  // Focusing a field on a soft-keyboard device is a layout change, not a convenience: the
  // keyboard rises over the list the tab was opened to read. `+` is now what the token
  // reaches, because a draft row is where composing happens.
  assert.match(queuePane, /if \(!openRequestToken \|\| hasSoftKeyboard\(\)\) return/)
  assert.match(queuePane, /import \{ hasSoftKeyboard \} from '\.\/deviceSettings'/)
  const opener = queuePane.slice(queuePane.indexOf('if (!openRequestToken || hasSoftKeyboard()) return'))
  assert.match(opener.slice(0, 200), /startDraft\(\)/)
})

test('nothing acts on a body the daemon has not got yet', () => {
  // A debounce makes it possible to arm or send the *previous* body. Both paths flush
  // first, which is the invariant an explicit Save button used to provide for free.
  const arm = queuePane.slice(
    queuePane.indexOf('const armFromEditor ='),
    queuePane.indexOf('const sendFromRow ='),
  )
  assert.match(arm, /await queueDraftSaver\.flush\(current\.key\)/)
  const send = queuePane.slice(
    queuePane.indexOf('const sendFromRow ='),
    queuePane.indexOf('const armFromRow ='),
  )
  assert.match(send, /isEditing && key \? await queueDraftSaver\.flush\(key\) : null/)
  // And the send quotes the revision the *save* returned, not the one the last fetch
  // reported: an autosaved edit advances it, so the fetched one is a certain refusal.
  assert.match(send, /saved\?\.revision \?\? message\.revision/)
})

test('leaving the pane saves the open editor instead of dropping it', () => {
  // Moving focus to another session retargets the whole pane and unmounts this one; the
  // drawer being swiped shut does the same. Neither is a decision to discard anything.
  assert.match(queuePane, /void queueDraftSaver\.flush\(key\)\.then\(\(\) => queueDraftSaver\.close\(key\)\)/)
  assert.match(queuePane, /onBlur=\{\(\) => \{ void queueDraftSaver\.flush\(editing\.key\) \}\}/)
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
