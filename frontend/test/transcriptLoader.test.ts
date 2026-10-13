import assert from 'node:assert/strict'
import test from 'node:test'
import { TRANSCRIPT_RETRY_DELAYS_MS, TranscriptLoader, type TranscriptTimers } from '../src/transcriptLoader.ts'
import type { SessionTranscript, TranscriptMessage } from '../src/transcriptView.ts'

const at = (ordinal: number): TranscriptMessage => ({
  message_id: `offset:${ordinal}`, ordinal, role: 'assistant', text: `m${ordinal}`, preceding_tool_calls: 0,
})

const reading = (over: Partial<SessionTranscript> = {}): SessionTranscript => ({
  session_id: 's1', backend: 'claude', messages: [], hidden: 0, truncated: false, reason: null,
  generation: 'g.0', seq: 1, total: 0, has_older: false, page: 'tail', delta: false, replace_from: null,
  ...over,
})

/** A daemon whose answers the test hands out one at a time, in the order asked. */
function daemon() {
  const asked: string[] = []
  const waiting: Array<(answer: SessionTranscript | Error) => void> = []
  return {
    asked,
    fetcher: (query: string) => {
      asked.push(query)
      return new Promise<SessionTranscript>((resolve, reject) => {
        waiting.push(answer => (answer instanceof Error ? reject(answer) : resolve(answer)))
      })
    },
    async answer(value: SessionTranscript | Error) {
      const next = waiting.shift()
      assert.ok(next, 'nothing was asked')
      next(value)
      // Let the loader's continuation, and any follow-up it starts, run.
      for (let turn = 0; turn < 5; turn++) await Promise.resolve()
    },
    get pending() { return waiting.length },
  }
}

function clock() {
  const scheduled: Array<{ delay: number; run: () => void; cancelled: boolean }> = []
  const timers: TranscriptTimers = {
    set: (run, delay) => { const entry = { delay, run, cancelled: false }; scheduled.push(entry); return entry },
    clear: handle => { (handle as { cancelled: boolean }).cancelled = true },
  }
  return {
    timers,
    scheduled,
    fire() {
      const due = scheduled.filter(entry => !entry.cancelled)
      scheduled.length = 0
      for (const entry of due) entry.run()
    },
  }
}

const params = (query: string) => Object.fromEntries(new URLSearchParams(query))

test('triggers that arrive while a read is in flight become one follow-up', async () => {
  const server = daemon()
  const updates: SessionTranscript[] = []
  const loader = new TranscriptLoader(server.fetcher, reading => { if (reading) updates.push(reading) })
  loader.refresh()
  // A reconnect replaying forty turn ends.
  for (let index = 0; index < 40; index++) loader.refresh()
  assert.equal(server.asked.length, 1)
  await server.answer(reading({ messages: [at(1)], seq: 3 }))
  // Exactly one more, sent after the first answer and so asking from it.
  assert.equal(server.asked.length, 2)
  assert.deepEqual(params(server.asked[1]), { since: '3', generation: 'g.0', oldest: 'offset:1' })
  await server.answer(reading({ page: 'delta', seq: 4, replace_from: 'offset:2', messages: [at(2)] }))
  assert.equal(server.asked.length, 2)
  assert.deepEqual(loader.current?.messages.map(item => item.ordinal), [1, 2])
  assert.equal(updates.length, 2)
  loader.dispose()
})

test('a delta that cannot be joined is answered by asking for the tail', async () => {
  const server = daemon()
  const loader = new TranscriptLoader(server.fetcher, () => {}, reading({ messages: [at(1)], seq: 3 }))
  loader.refresh()
  await server.answer(reading({ page: 'delta', generation: 'g.1', replace_from: 'offset:1', messages: [at(1)] }))
  assert.equal(server.asked.length, 2)
  assert.deepEqual(params(server.asked[1]), { limit: '200' })
  // The column kept what it had while the tail was on its way.
  assert.deepEqual(loader.current?.messages.map(item => item.ordinal), [1])
  await server.answer(reading({ generation: 'g.1', messages: [at(1), at(2)] }))
  assert.deepEqual(loader.current?.messages.map(item => item.ordinal), [1, 2])
  loader.dispose()
})

test('a read the daemon has not finished is retried, backing off, until it lands', async () => {
  const server = daemon()
  const time = clock()
  const loader = new TranscriptLoader(server.fetcher, () => {}, null, time.timers)
  loader.refresh()
  await server.answer(reading({ reason: 'indexing', messages: [] }))
  assert.deepEqual(time.scheduled.map(entry => entry.delay), [TRANSCRIPT_RETRY_DELAYS_MS[0]])
  time.fire()
  await server.answer(reading({ reason: 'indexing', messages: [] }))
  assert.deepEqual(time.scheduled.map(entry => entry.delay), [TRANSCRIPT_RETRY_DELAYS_MS[1]])
  time.fire()
  await server.answer(reading({ messages: [at(1)] }))
  // Landed: nothing more is scheduled, and the backoff starts over next time.
  assert.equal(time.scheduled.filter(entry => !entry.cancelled).length, 0)
  assert.equal(loader.current?.messages.length, 1)
  loader.dispose()
})

test('a failed request is reported and retried, and the reading is kept', async () => {
  const server = daemon()
  const time = clock()
  const errors: string[] = []
  const held = reading({ messages: [at(1)] })
  const loader = new TranscriptLoader(server.fetcher, (_reading, error) => errors.push(error), held, time.timers)
  loader.refresh()
  await server.answer(new Error('GET failed'))
  assert.deepEqual(errors, ['GET failed'])
  assert.equal(loader.current, held)
  assert.equal(time.scheduled.length, 1)
  loader.dispose()
  assert.ok(time.scheduled[0].cancelled, 'disposing cancels the retry')
})

test('older pages load one at a time and say whether they added anything', async () => {
  const server = daemon()
  const loader = new TranscriptLoader(server.fetcher, () => {}, reading({ messages: [at(5)], has_older: true }))
  const first = loader.loadOlder()
  const second = loader.loadOlder()
  assert.equal(server.asked.length, 1)
  assert.deepEqual(params(server.asked[0]), { limit: '200', before: 'offset:5' })
  await server.answer(reading({ page: 'older', has_older: false, messages: [at(4)] }))
  assert.equal(await first, true)
  assert.equal(await second, true)
  assert.deepEqual(loader.current?.messages.map(item => item.ordinal), [4, 5])
  // Nothing above now: no request.
  assert.equal(await loader.loadOlder(), false)
  assert.equal(server.asked.length, 1)
  loader.dispose()
})

test('the whole conversation is walked back page by page', async () => {
  const server = daemon()
  const loader = new TranscriptLoader(server.fetcher, () => {}, reading({ messages: [at(3)], has_older: true }))
  const whole = loader.loadAll()
  await server.answer(reading({ page: 'older', has_older: true, messages: [at(2)] }))
  await server.answer(reading({ page: 'older', has_older: false, messages: [at(1)] }))
  assert.equal(await whole, true)
  assert.deepEqual(loader.current?.messages.map(item => item.ordinal), [1, 2, 3])
  assert.equal(server.pending, 0)
  loader.dispose()
})

test('a disposed loader neither asks nor reports', async () => {
  const server = daemon()
  let updates = 0
  const loader = new TranscriptLoader(server.fetcher, () => { updates++ })
  loader.refresh()
  loader.dispose()
  await server.answer(reading({ messages: [at(1)] }))
  loader.refresh()
  assert.equal(updates, 0)
  assert.equal(server.asked.length, 1)
})
