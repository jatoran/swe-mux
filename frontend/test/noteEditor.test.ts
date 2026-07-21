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
