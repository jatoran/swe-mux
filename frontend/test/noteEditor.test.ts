import assert from 'node:assert/strict'
import test from 'node:test'
import { insertEditorTab } from '../src/editorText.ts'
import {
  NoteSaveQueue,
  fileSaveTarget,
  noteQueueKey,
  noteSaveTarget,
  type NoteSaveAck,
  type NoteSaveState,
  type ResourceSaveTarget,
} from '../src/noteSaveQueue.ts'

type Deferred = { resolve: (ack: NoteSaveAck) => void; reject: (error: unknown) => void }

function makeTransport() {
  const calls: { url: string; text: string; revision: string }[] = []
  const deferreds: Deferred[] = []
  const transport = (target: ResourceSaveTarget, text: string, revision: string) => {
    calls.push({ url: target.url, text, revision })
    return new Promise<NoteSaveAck>((resolve, reject) => deferreds.push({ resolve, reject }))
  }
  return { transport, calls, deferreds }
}

const noteTarget = noteSaveTarget('p1', null)

const tick = () => new Promise(resolve => setImmediate(resolve))

test('Tab inserts a literal tab and replaces the active selection', () => {
  assert.deepEqual(insertEditorTab('hello world', 5, 5), { text: 'hello\t world', caret: 6 })
  assert.deepEqual(insertEditorTab('hello world', 0, 5), { text: '\t world', caret: 1 })
})

test('save queue commits text with the storage revision and advances it on ack', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  const states: NoteSaveState[] = []
  queue.subscribe(key, state => states.push(state))
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'hello')
  queue.flush(key)
  assert.deepEqual(calls, [{ url: '/api/projects/p1/note', text: 'hello', revision: 'rev0' }])
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.getState(key).storageRevision, 'rev1')
  assert.equal(queue.getState(key).status, 'saved')
})

test('only the newest pending snapshot is sent while one save is in flight', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'A')
  queue.flush(key) // A now in flight against rev0
  queue.submit(key, 'B') // queued behind the in-flight save
  queue.submit(key, 'C') // supersedes B; newest wins
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  queue.flush(key)
  assert.deepEqual(calls.map(call => call.text), ['A', 'C'])
  assert.equal(calls[1].revision, 'rev1') // second save uses the acked revision
})

test('a storage conflict keeps local text, blocks auto-save, and overwrite re-commits it', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'mine')
  queue.flush(key)
  deferreds[0].reject(Object.assign(new Error('note changed externally'), { status: 409 }))
  await tick()
  const conflicted = queue.getState(key)
  assert.equal(conflicted.status, 'conflict')
  assert.equal(conflicted.storageRevision, 'rev0') // unchanged: never adopt stale server revision
  assert.ok(conflicted.banner)
  // Further typing is retained but not auto-sent while blocked (no 409 loop).
  queue.submit(key, 'mine2')
  queue.flush(key)
  assert.equal(calls.length, 1)
  // Resolve by adopting the on-disk revision and overwriting with local text.
  queue.overwrite(key, 'rev5')
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[1], { url: '/api/projects/p1/note', text: 'mine2', revision: 'rev5' })
})

test('reset adopts a fresh revision and clears conflict/pending state', () => {
  const { transport } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'x')
  queue.reset(key, noteTarget, 'rev9')
  const state = queue.getState(key)
  assert.equal(state.storageRevision, 'rev9')
  assert.equal(state.status, 'idle')
  assert.equal(state.banner, null)
})

test('live follow is allowed only for a different remote revision while locally clean', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  queue.reset(key, noteTarget, 'rev0')
  assert.equal(queue.canFollowRemote(key, 'rev1'), true)
  assert.equal(queue.canFollowRemote(key, 'rev0'), false)

  queue.submit(key, 'local edit')
  assert.equal(queue.canFollowRemote(key, 'rev1'), false)
  queue.flush(key)
  assert.equal(queue.canFollowRemote(key, 'rev1'), false)
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.canFollowRemote(key, 'rev1'), false)
  assert.equal(queue.canFollowRemote(key, 'rev2'), true)
})

test('session notes retain their storage identity through queued saves', () => {
  const { transport, calls } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('project', 'session-note:terminal')
  queue.reset(key, noteSaveTarget('project', 'terminal'), 'rev0')
  queue.submit(key, 'session context')
  queue.flush(key)
  assert.deepEqual(calls, [{ url: '/api/projects/project/session-notes/terminal', text: 'session context', revision: 'rev0' }])
})

test('markdown files queue-save to the project file endpoint with their path', () => {
  const { transport, calls } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('project', 'file:docs/readme.md')
  queue.reset(key, fileSaveTarget('project', 'docs/readme.md'), 'rev0')
  queue.submit(key, '# hi')
  queue.flush(key)
  assert.deepEqual(calls, [{ url: '/api/projects/project/file', text: '# hi', revision: 'rev0' }])
  const body = fileSaveTarget('project', 'docs/readme.md').body('# hi', 'rev0')
  assert.deepEqual(body, { path: 'docs/readme.md', text: '# hi', revision: 'rev0' })
})

test('the unload beacon sends the newest snapshot even while a save is in flight', async () => {
  // While a PUT is in flight the newest text lives only in entry.pending, and
  // that PUT is a plain non-keepalive fetch the unload can abort. Skipping the
  // entry meant guaranteed loss of everything typed during the save.
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'saved so far')
  queue.flush(key) // in flight
  queue.submit(key, 'typed while saving')

  const beacons: { url: string; body: unknown; keepalive: boolean }[] = []
  const realFetch = globalThis.fetch
  globalThis.fetch = ((url: string, init: RequestInit) => {
    beacons.push({
      url,
      body: JSON.parse(String(init.body)),
      keepalive: Boolean(init.keepalive),
    })
    return Promise.resolve(new Response('{}'))
  }) as typeof fetch
  try {
    queue.beaconFlushAll()
  } finally {
    globalThis.fetch = realFetch
  }

  assert.equal(beacons.length, 1)
  assert.equal(beacons[0].url, '/api/projects/p1/note')
  assert.equal(beacons[0].keepalive, true)
  assert.deepEqual(beacons[0].body, { markdown: 'typed while saving', revision: 'rev0' })
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
})

test('the unload beacon skips entries with nothing pending and blocked entries', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const clean = noteQueueKey('p1', 'clean')
  const blocked = noteQueueKey('p1', 'blocked')
  queue.reset(clean, noteTarget, 'rev0')
  queue.reset(blocked, noteTarget, 'rev0')
  queue.submit(blocked, 'conflicted')
  queue.flush(blocked)
  deferreds[0].resolve({ revision: 'rev9', status: 'conflict' })
  await tick()

  const beacons: string[] = []
  const realFetch = globalThis.fetch
  globalThis.fetch = ((url: string) => {
    beacons.push(url)
    return Promise.resolve(new Response('{}'))
  }) as typeof fetch
  try {
    queue.beaconFlushAll()
  } finally {
    globalThis.fetch = realFetch
  }
  assert.deepEqual(beacons, [])
})

// `pendingText` is what makes moving a note between the drawer and a pane lossless. The two
// hosts are mutually exclusive, so a move unmounts one editor and mounts another against this
// same entry, and the arriving editor's GET can be a debounce behind what was typed.

test('pendingText is null for an unknown note and for a settled one', () => {
  const { transport } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  assert.equal(queue.pendingText(noteQueueKey('p1', 'never-touched')), null)
  queue.reset(key, noteTarget, 'rev0')
  assert.equal(queue.pendingText(key), null)
})

test('pendingText reports typing that has not committed yet', () => {
  const { transport } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'half a sentence')
  assert.equal(queue.pendingText(key), 'half a sentence')
})

test('pendingText still reports a snapshot a running save is carrying', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'in flight')
  queue.flush(key)
  // `start` moves the text out of `pending` for the duration of the request. Without the
  // in-flight snapshot the arriving editor would see a settled note and adopt the daemon's
  // copy, which is exactly the text this PUT is about to replace.
  assert.equal(queue.pendingText(key), 'in flight')
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.pendingText(key), null)
})

test('pendingText prefers newer typing over the snapshot in flight', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'first')
  queue.flush(key)
  queue.submit(key, 'second')
  assert.equal(queue.pendingText(key), 'second')
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.pendingText(key), 'second')
})

test('a failed save leaves its text recoverable through pendingText', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  queue.reset(key, noteTarget, 'rev0')
  queue.submit(key, 'offline edit')
  queue.flush(key)
  deferreds[0].reject(new Error('the daemon did not respond in time.'))
  await tick()
  assert.equal(queue.pendingText(key), 'offline edit')
})
