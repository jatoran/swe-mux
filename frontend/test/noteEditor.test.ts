import assert from 'node:assert/strict'
import test from 'node:test'
import { insertEditorTab, prefersPlainMobileEditor } from '../src/editorText.ts'
import {
  NoteSaveQueue,
  noteQueueKey,
  type NoteSaveAck,
  type NoteSaveState,
} from '../src/noteSaveQueue.ts'

type Deferred = { resolve: (ack: NoteSaveAck) => void; reject: (error: unknown) => void }

function makeTransport() {
  const calls: { projectId: string; markdown: string; revision: string; sessionNoteId?:string }[] = []
  const deferreds: Deferred[] = []
  const transport = (projectId: string, markdown: string, revision: string, sessionNoteId?:string|null) => {
    calls.push({...{ projectId, markdown, revision },...(sessionNoteId?{sessionNoteId}:{})})
    return new Promise<NoteSaveAck>((resolve, reject) => deferreds.push({ resolve, reject }))
  }
  return { transport, calls, deferreds }
}

const tick = () => new Promise(resolve => setImmediate(resolve))

test('coarse pointers and narrow screens use the native mobile note editor', () => {
  assert.equal(prefersPlainMobileEditor(true, false), true)
  assert.equal(prefersPlainMobileEditor(false, true), true)
  assert.equal(prefersPlainMobileEditor(false, false), false)
})

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
  queue.reset(key, 'p1', 'rev0')
  queue.submit(key, 'hello')
  queue.flush(key)
  assert.deepEqual(calls, [{ projectId: 'p1', markdown: 'hello', revision: 'rev0' }])
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.getState(key).storageRevision, 'rev1')
  assert.equal(queue.getState(key).status, 'saved')
})

test('only the newest pending snapshot is sent while one save is in flight', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  queue.reset(key, 'p', 'rev0')
  queue.submit(key, 'A')
  queue.flush(key) // A now in flight against rev0
  queue.submit(key, 'B') // queued behind the in-flight save
  queue.submit(key, 'C') // supersedes B; newest wins
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  queue.flush(key)
  assert.deepEqual(calls.map(call => call.markdown), ['A', 'C'])
  assert.equal(calls[1].revision, 'rev1') // second save uses the acked revision
})

test('a storage conflict keeps local text, blocks auto-save, and overwrite re-commits it', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  queue.reset(key, 'p', 'rev0')
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
  assert.deepEqual(calls[1], { projectId: 'p', markdown: 'mine2', revision: 'rev5' })
})

test('reset adopts a fresh revision and clears conflict/pending state', () => {
  const { transport } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  queue.reset(key, 'p', 'rev0')
  queue.submit(key, 'x')
  queue.reset(key, 'p', 'rev9')
  const state = queue.getState(key)
  assert.equal(state.storageRevision, 'rev9')
  assert.equal(state.status, 'idle')
  assert.equal(state.banner, null)
})

test('live follow is allowed only for a different remote revision while locally clean', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  queue.reset(key, 'p', 'rev0')
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
  queue.reset(key, 'project', 'rev0', 'terminal')
  queue.submit(key, 'session context')
  queue.flush(key)
  assert.deepEqual(calls, [{projectId:'project',markdown:'session context',revision:'rev0',sessionNoteId:'terminal'}])
})
