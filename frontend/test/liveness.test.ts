import assert from 'node:assert/strict'
import test from 'node:test'
import {
  ATTEMPT_SETTLE_MS,
  HANDSHAKE_TIMEOUT_MS,
  LIVENESS_POLL_MS,
  RESUME_SLEEP_MS,
  retryDelay,
  shouldForceReconnect,
  watchLiveness,
  watchResume,
  type ConnectionPhase,
  type DocumentLike,
  type LivenessSignal,
  type WindowLike,
} from '../src/liveness.ts'

type Harness = {
  doc: DocumentLike
  win: WindowLike
  now: () => number
  advance: (ms: number) => void
  fire: (type: string, event?: { persisted?: boolean }) => void
  tickPoll: () => void
  hide: () => void
  show: () => void
}

function harness(startHidden = false): Harness {
  let clock = 1_000_000
  const listeners = new Map<string, ((event: { persisted?: boolean }) => void)[]>()
  let poll: (() => void) | null = null
  const add = (type: string, listener: (event: { persisted?: boolean }) => void) => {
    listeners.set(type, [...(listeners.get(type) || []), listener])
  }
  const remove = (type: string, listener: (event: { persisted?: boolean }) => void) => {
    listeners.set(type, (listeners.get(type) || []).filter(item => item !== listener))
  }
  const doc: DocumentLike = { hidden: startHidden, addEventListener: add, removeEventListener: remove }
  const win: WindowLike = {
    addEventListener: add,
    removeEventListener: remove,
    setInterval: (handler, ms) => { assert.equal(ms, LIVENESS_POLL_MS); poll = handler; return 1 },
    clearInterval: () => { poll = null },
  }
  const fire = (type: string, event: { persisted?: boolean } = {}) => {
    for (const listener of [...(listeners.get(type) || [])]) listener(event)
  }
  return {
    doc,
    win,
    now: () => clock,
    advance: ms => { clock += ms },
    fire,
    tickPoll: () => poll?.(),
    hide: () => { doc.hidden = true; fire('visibilitychange') },
    show: () => { doc.hidden = false; fire('visibilitychange') },
  }
}

type FakeSocket = {
  phase: ConnectionPhase
  attemptStartedAt: number | null
  nextAttemptAt: number | null
}

function watched(harnessed: Harness, socket: FakeSocket): { signals: LivenessSignal[]; stop: () => void } {
  const signals: LivenessSignal[] = []
  const stop = watchLiveness({
    phase: () => socket.phase,
    attemptStartedAt: () => socket.attemptStartedAt,
    nextAttemptAt: () => socket.nextAttemptAt,
    reconnect: signal => {
      signals.push(signal)
      socket.phase = 'connecting'
      socket.attemptStartedAt = harnessed.now()
      socket.nextAttemptAt = null
    },
    now: harnessed.now,
    doc: harnessed.doc,
    win: harnessed.win,
  })
  return { signals, stop }
}

test('retry backoff grows exponentially and stays capped', () => {
  assert.equal(retryDelay(0), 1000)
  assert.equal(retryDelay(1), 2000)
  assert.equal(retryDelay(3), 8000)
  assert.equal(retryDelay(4), 10000)
  assert.equal(retryDelay(99), 10000)
  assert.equal(retryDelay(-1), 1000)
})

test('a connection that was never attempted is always started', () => {
  assert.equal(shouldForceReconnect({ phase: 'closed', attemptStartedAt: null, nextAttemptAt: null, staleBefore: null, now: 100 }), true)
})

test('a handshake still inside its deadline is left alone', () => {
  assert.equal(shouldForceReconnect({
    phase: 'connecting', attemptStartedAt: 1000, nextAttemptAt: null, staleBefore: null,
    now: 1000 + HANDSHAKE_TIMEOUT_MS - 1,
  }), false)
})

test('a handshake past its deadline is stalled and gets replaced', () => {
  // The whole point of the module: a stalled handshake fires neither close nor error.
  assert.equal(shouldForceReconnect({
    phase: 'connecting', attemptStartedAt: 1000, nextAttemptAt: null, staleBefore: null,
    now: 1000 + HANDSHAKE_TIMEOUT_MS,
  }), true)
})

test('a burst of resume signals cannot tear down the attempt it just started', () => {
  assert.equal(shouldForceReconnect({
    phase: 'connecting', attemptStartedAt: 1000, nextAttemptAt: null, staleBefore: 1000,
    now: 1000 + ATTEMPT_SETTLE_MS - 1,
  }), false)
})

test('a closed socket waits for its own backoff, then is taken over', () => {
  const closed = { phase: 'closed' as ConnectionPhase, attemptStartedAt: 1000, staleBefore: null }
  assert.equal(shouldForceReconnect({ ...closed, nextAttemptAt: 9000, now: 5000 }), false)
  assert.equal(shouldForceReconnect({ ...closed, nextAttemptAt: 9000, now: 9000 }), true)
  // No timer pending at all: nothing else is going to reconnect this.
  assert.equal(shouldForceReconnect({ ...closed, nextAttemptAt: null, now: 5000 }), true)
})

test('a healthy open socket is never disturbed', () => {
  assert.equal(shouldForceReconnect({
    phase: 'open', attemptStartedAt: 1000, nextAttemptAt: null, staleBefore: null, now: 500_000,
  }), false)
})

test('an open socket older than a resume is treated as dead', () => {
  // readyState still says OPEN, but the daemon dropped it while the phone slept.
  assert.equal(shouldForceReconnect({
    phase: 'open', attemptStartedAt: 1000, nextAttemptAt: null, staleBefore: 60_000, now: 60_100,
  }), true)
})

test('an open socket that postdates the resume is kept', () => {
  assert.equal(shouldForceReconnect({
    phase: 'open', attemptStartedAt: 60_500, nextAttemptAt: null, staleBefore: 60_000, now: 90_000,
  }), false)
})

test('waking after a long sleep replaces the socket the browser still calls open', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'open', attemptStartedAt: fake.now(), nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  fake.hide()
  fake.advance(30 * 60 * 1000)
  fake.show()
  assert.deepEqual(signals, ['visible'])
  stop()
})

test('a brief hide leaves a working socket connected', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'open', attemptStartedAt: fake.now(), nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  fake.hide()
  fake.advance(RESUME_SLEEP_MS - 1)
  fake.show()
  assert.deepEqual(signals, [])
  stop()
})

test('the visible poll rescues a handshake that hung on resume', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'open', attemptStartedAt: fake.now(), nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  fake.hide()
  fake.advance(10 * 60 * 1000)
  fake.show()
  assert.deepEqual(signals, ['visible'])
  // That attempt never completes: no close, no error, nothing to react to. The poll is
  // the only thing left, and it must not give up before the deadline or churn after it.
  fake.advance(LIVENESS_POLL_MS)
  fake.tickPoll()
  assert.deepEqual(signals, ['visible'])
  fake.advance(HANDSHAKE_TIMEOUT_MS)
  fake.tickPoll()
  assert.deepEqual(signals, ['visible', 'poll'])
  stop()
})

test('the resume burst produces exactly one attempt', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'open', attemptStartedAt: fake.now(), nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  fake.hide()
  fake.advance(10 * 60 * 1000)
  fake.show()
  fake.fire('focus')
  fake.fire('online')
  fake.tickPoll()
  assert.deepEqual(signals, ['visible'])
  stop()
})

test('coming back online forces a fresh socket over the old route', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'open', attemptStartedAt: fake.now(), nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  fake.advance(ATTEMPT_SETTLE_MS)
  fake.fire('online')
  assert.deepEqual(signals, ['online'])
  stop()
})

test('a back/forward-cache restore reconnects', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'open', attemptStartedAt: fake.now(), nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  fake.advance(ATTEMPT_SETTLE_MS)
  fake.fire('pageshow', { persisted: true })
  assert.deepEqual(signals, ['pageshow'])
  stop()
})

test('a non-persisted pageshow leaves a healthy socket alone', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'open', attemptStartedAt: fake.now(), nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  fake.advance(ATTEMPT_SETTLE_MS)
  fake.fire('pageshow', { persisted: false })
  assert.deepEqual(signals, [])
  stop()
})

test('the poll stays quiet while the page is hidden', () => {
  const fake = harness(true)
  const socket: FakeSocket = { phase: 'closed', attemptStartedAt: 0, nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  fake.advance(60_000)
  fake.tickPoll()
  assert.deepEqual(signals, [])
  stop()
})

test('a disabled connection is left closed instead of re-checked forever', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'closed', attemptStartedAt: 0, nextAttemptAt: null }
  const signals: LivenessSignal[] = []
  const stop = watchLiveness({
    phase: () => socket.phase,
    attemptStartedAt: () => socket.attemptStartedAt,
    nextAttemptAt: () => socket.nextAttemptAt,
    enabled: () => false,
    reconnect: signal => signals.push(signal),
    now: fake.now,
    doc: fake.doc,
    win: fake.win,
  })
  fake.tickPoll()
  fake.fire('online')
  assert.deepEqual(signals, [])
  stop()
})

test('disposing the watcher stops every signal', () => {
  const fake = harness()
  const socket: FakeSocket = { phase: 'closed', attemptStartedAt: 0, nextAttemptAt: null }
  const { signals, stop } = watched(fake, socket)
  stop()
  fake.hide()
  fake.advance(10 * 60 * 1000)
  fake.show()
  fake.fire('online')
  fake.tickPoll()
  assert.deepEqual(signals, [])
})

test('watchResume reports the resume marker only after a real suspension', () => {
  const fake = harness()
  const events: { signal: LivenessSignal; stale: boolean }[] = []
  const stop = watchResume(event => events.push({ signal: event.signal, stale: event.staleBefore !== null }), {
    now: fake.now, doc: fake.doc, win: fake.win,
  })
  fake.tickPoll()
  fake.hide()
  fake.advance(RESUME_SLEEP_MS - 1)
  fake.show()
  fake.hide()
  fake.advance(RESUME_SLEEP_MS)
  fake.show()
  assert.deepEqual(events, [
    { signal: 'poll', stale: false },
    { signal: 'visible', stale: false },
    { signal: 'visible', stale: true },
  ])
  stop()
})
