import assert from 'node:assert/strict'
import test from 'node:test'
import { QueueDraftSaver, type QueueDraftTransport } from '../src/queueDraftSaver.ts'
import type { QueueConstraints, QueueMessage } from '../src/queueApi.ts'

// The autosave behind the Queue pane's draft rows. Every test here is a claim about what
// reaches the daemon, because the whole reason this replaced a Save button is that a
// half-written message must survive a gesture nobody thought of as "discard".

type Call =
  | { kind: 'create'; body: string; constraints?: QueueConstraints }
  | { kind: 'update'; messageId: string; revision: number; body: string }

const message = (id: string, revision: number, body: string): QueueMessage => ({
  id,
  target_session_id: 'session',
  target_agent_run_id: null,
  target_backend: null,
  target_label: null,
  project_id: null,
  position: 1,
  state: 'draft',
  body,
  revision,
  sender_kind: 'user',
  sender_id: null,
  sender_label: null,
  origin_session_id: null,
  correlation_id: null,
  chain_depth: 0,
  origin: null,
  constraints: null,
  blocked_reasons: null,
  stranded_reason: null,
  cancel_kind: null,
  retargeted_from: null,
  created_at: 0,
  updated_at: 0,
  edited_at: null,
  armed_at: null,
  sent_at: null,
})

/** A transport that records what it was asked to write and can be made slow. */
function recorder(options: { delayMs?: number; failFirstUpdateWithRevision?: number } = {}) {
  const calls: Call[] = []
  let revision = 0
  let conflicts = 0
  const wait = () => (options.delayMs
    ? new Promise<void>(resolve => setTimeout(resolve, options.delayMs))
    : Promise.resolve())
  const transport: QueueDraftTransport = {
    async create(_sessionId, body, opts) {
      calls.push({ kind: 'create', body, constraints: opts.constraints })
      await wait()
      revision = 1
      return message('m1', revision, body)
    },
    async update(messageId, rev, body) {
      calls.push({ kind: 'update', messageId, revision: rev, body })
      await wait()
      if (options.failFirstUpdateWithRevision !== undefined && conflicts === 0) {
        conflicts += 1
        const error = new Error('the message changed since you last saw it') as Error & {
          detail?: Record<string, unknown>
        }
        error.detail = { code: 'revision_conflict', revision: options.failFirstUpdateWithRevision }
        throw error
      }
      revision = rev + 1
      return message(messageId, revision, body)
    },
  }
  return { calls, transport }
}

const seed = { sessionId: 'session', messageId: '', revision: 0, body: '', constraints: null }

test('an empty draft is never sent to the daemon', async () => {
  // `+` has to be free. A draft nobody typed into must leave no row behind, which means
  // it must never have existed - and the daemon refuses an empty body anyway.
  const { calls, transport } = recorder()
  const saver = new QueueDraftSaver(transport, 0)
  saver.open('draft:1', seed)
  saver.edit('draft:1', { body: '   ' })
  await saver.flush('draft:1')
  assert.deepEqual(calls, [])
  assert.equal(saver.state('draft:1')?.status, 'idle')
})

test('the first non-empty body creates the item and later ones patch it', async () => {
  const { calls, transport } = recorder()
  const saver = new QueueDraftSaver(transport, 0)
  saver.open('draft:1', seed)
  saver.edit('draft:1', { body: 'first' })
  await saver.flush('draft:1')
  saver.edit('draft:1', { body: 'first and second' })
  await saver.flush('draft:1')
  assert.deepEqual(calls, [
    { kind: 'create', body: 'first', constraints: undefined },
    { kind: 'update', messageId: 'm1', revision: 1, body: 'first and second' },
  ])
  const state = saver.state('draft:1')
  assert.equal(state?.messageId, 'm1')
  assert.equal(state?.status, 'saved')
  assert.equal(state?.dirty, false)
})

test('mid-turn asked for before the item existed rides the create', async () => {
  // The constraint cannot be a PATCH on an item that does not exist yet, and forgetting it
  // would silently downgrade a message the person marked urgent.
  const { calls, transport } = recorder()
  const saver = new QueueDraftSaver(transport, 0)
  saver.open('draft:1', seed)
  saver.edit('draft:1', { constraints: { delivery: 'now' } })
  saver.edit('draft:1', { body: 'urgent' })
  await saver.flush('draft:1')
  assert.deepEqual(calls, [{ kind: 'create', body: 'urgent', constraints: { delivery: 'now' } }])
})

test('a save already in flight does not lose the keystrokes typed over it', async () => {
  // The debounce fires, the request opens, and the person keeps typing. Nothing else would
  // pick those characters up, so `flush` has to run again until it converges.
  const { calls, transport } = recorder({ delayMs: 5 })
  const saver = new QueueDraftSaver(transport, 0)
  saver.open('draft:1', seed)
  saver.edit('draft:1', { body: 'half' })
  const inFlight = saver.flush('draft:1')
  saver.edit('draft:1', { body: 'half a sentence' })
  await inFlight
  const final = await saver.flush('draft:1')
  assert.equal(final?.dirty, false)
  assert.equal(calls.at(-1)?.body, 'half a sentence')
})

test('two writes for one key never overlap', async () => {
  // Two PATCHes racing on one item is a self-inflicted revision conflict whose loser is
  // always the newer text.
  let open = 0
  let maxOpen = 0
  const transport: QueueDraftTransport = {
    async create(_sessionId, body) {
      open += 1; maxOpen = Math.max(maxOpen, open)
      await new Promise<void>(resolve => setTimeout(resolve, 3))
      open -= 1
      return message('m1', 1, body)
    },
    async update(messageId, revision, body) {
      open += 1; maxOpen = Math.max(maxOpen, open)
      await new Promise<void>(resolve => setTimeout(resolve, 3))
      open -= 1
      return message(messageId, revision + 1, body)
    },
  }
  const saver = new QueueDraftSaver(transport, 0)
  saver.open('draft:1', seed)
  saver.edit('draft:1', { body: 'a' })
  const first = saver.flush('draft:1')
  saver.edit('draft:1', { body: 'ab' })
  const second = saver.flush('draft:1')
  await Promise.all([first, second])
  assert.equal(maxOpen, 1)
})

test('clearing a saved item keeps the text the daemon already has', async () => {
  // The daemon refuses an empty body outright, so retrying one is a loop that reddens the
  // status line and deletes nothing. Say so once and keep the last good text.
  const { calls, transport } = recorder()
  const saver = new QueueDraftSaver(transport, 0)
  saver.open('draft:1', seed)
  saver.edit('draft:1', { body: 'something' })
  await saver.flush('draft:1')
  saver.edit('draft:1', { body: '' })
  await saver.flush('draft:1')
  assert.equal(calls.length, 1)
  assert.equal(saver.state('draft:1')?.status, 'empty')
})

test('a revision conflict re-anchors on the daemon and retries once', async () => {
  // The only way to reach one is a second surface editing the same item, and the newest
  // keystrokes are still the ones the person meant.
  const { calls, transport } = recorder({ failFirstUpdateWithRevision: 9 })
  const saver = new QueueDraftSaver(transport, 0)
  saver.open('msg:m1', { ...seed, messageId: 'm1', revision: 1, body: 'old' })
  saver.edit('msg:m1', { body: 'new' })
  await saver.flush('msg:m1')
  assert.deepEqual(calls, [
    { kind: 'update', messageId: 'm1', revision: 1, body: 'new' },
    { kind: 'update', messageId: 'm1', revision: 9, body: 'new' },
  ])
  assert.equal(saver.state('msg:m1')?.status, 'saved')
})

test('a failure is reported rather than retried forever', async () => {
  const transport: QueueDraftTransport = {
    create: async () => { throw new Error('daemon is down') },
    update: async () => { throw new Error('daemon is down') },
  }
  const saver = new QueueDraftSaver(transport, 0)
  saver.open('draft:1', seed)
  saver.edit('draft:1', { body: 'text' })
  const state = await saver.flush('draft:1')
  assert.equal(state?.status, 'error')
  assert.match(state?.error ?? '', /daemon is down/)
  assert.equal(state?.dirty, true)
})

test('flushAll saves every open editor', async () => {
  // The teardown path: `pagehide`, and the pane unmounting under a drawer swipe.
  const { calls, transport } = recorder()
  const saver = new QueueDraftSaver(transport, 1000)
  saver.open('draft:1', seed)
  saver.open('msg:m2', { ...seed, messageId: 'm2', revision: 4, body: 'before' })
  saver.edit('draft:1', { body: 'one' })
  saver.edit('msg:m2', { body: 'after' })
  await saver.flushAll()
  assert.deepEqual(calls.map(call => call.body).sort(), ['after', 'one'])
})

test('closing an entry writes nothing', async () => {
  // `close` is the discard path and the retire-after-flush path; callers that mean to save
  // call `flush` first, and a discard must not smuggle a write out on its way past.
  const { calls, transport } = recorder()
  const saver = new QueueDraftSaver(transport, 1000)
  saver.open('draft:1', seed)
  saver.edit('draft:1', { body: 'never mind' })
  saver.close('draft:1')
  await new Promise<void>(resolve => setTimeout(resolve, 20))
  assert.deepEqual(calls, [])
  assert.equal(saver.state('draft:1'), null)
})

test('subscribers hear the create, which is how the pane learns the id', async () => {
  const { transport } = recorder()
  const saver = new QueueDraftSaver(transport, 0)
  const seen: string[] = []
  saver.subscribe((key, state) => { if (state.messageId) seen.push(`${key}:${state.messageId}`) })
  saver.open('draft:1', seed)
  saver.edit('draft:1', { body: 'hello' })
  await saver.flush('draft:1')
  assert.ok(seen.includes('draft:1:m1'))
})
