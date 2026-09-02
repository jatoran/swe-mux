import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applyDaemonProbe, DAEMON_PROBE_INTERVAL_MS, DAEMON_STALL_MISSES, daemonStallText,
  INITIAL_DAEMON_LIVENESS, pauseDaemonProbes, probeDaemon, resumeDaemonProbes, stalledSince,
  stallSeconds, watchDaemonLiveness,
  type DaemonLivenessState, type DaemonProbeOutcome,
} from '../src/daemonLiveness.ts'

/**
 * The daemon-stall indicator, both halves: the pure state machine that decides
 * when "not responding" is true, and the scheduler that feeds it.
 *
 * What is pinned is the threshold contract - two consecutive misses to show,
 * one success to clear, the clock stamped at the first miss - and the two ways
 * the scheduler could quietly lie: probing a hidden tab (which the design says
 * it never does), and letting a probe from before a hide/show cycle report on
 * the measurement that replaced it.
 */

const t0 = 1_756_000_000_000

const run = (outcomes: DaemonProbeOutcome[], start = t0, step = DAEMON_PROBE_INTERVAL_MS) =>
  outcomes.reduce<DaemonLivenessState>(
    (state, outcome, index) => applyDaemonProbe(state, outcome, start + index * step),
    INITIAL_DAEMON_LIVENESS,
  )

test('one miss is a blip and two are a stall', () => {
  assert.equal(DAEMON_STALL_MISSES, 2)
  const one = run(['missed'])
  assert.equal(one.stalled, false)
  assert.equal(stalledSince(one), null)
  const two = run(['missed', 'missed'])
  assert.equal(two.stalled, true)
  assert.equal(two.misses, 2)
  // Three is still one stall, not a new one.
  const three = run(['missed', 'missed', 'missed'])
  assert.equal(three.stalled, true)
  assert.equal(stalledSince(three), stalledSince(two))
})

test('the clock starts at the first miss, not at the one that crossed the threshold', () => {
  // The daemon had already stopped answering when the first probe left; the
  // second miss is confirmation, and a clock started there would under-report
  // the silence by a whole interval.
  const state = run(['missed', 'missed'])
  assert.equal(state.missedSince, t0)
  assert.equal(stalledSince(state), t0)
})

test('the first success clears the stall and every miss before it', () => {
  const recovered = run(['missed', 'missed', 'missed', 'ok'])
  assert.equal(recovered.stalled, false)
  assert.equal(recovered.misses, 0)
  assert.equal(recovered.missedSince, null)
  assert.equal(stalledSince(recovered), null)
  // A success between two misses is a reset, so alternating slow probes never
  // accumulate into a banner.
  const alternating = run(['missed', 'ok', 'missed', 'ok', 'missed'])
  assert.equal(alternating.stalled, false)
  assert.equal(alternating.misses, 1)
  assert.equal(alternating.missedSince, t0 + 4 * DAEMON_PROBE_INTERVAL_MS)
})

test('a healthy daemon produces no state churn', () => {
  // The hook publishes on identity, so a steady stream of successes must hand
  // back the same object rather than a fresh equal one per probe.
  const first = applyDaemonProbe(INITIAL_DAEMON_LIVENESS, 'ok', t0)
  assert.equal(first, INITIAL_DAEMON_LIVENESS)
  assert.equal(applyDaemonProbe(first, 'ok', t0 + 4000), first)
})

test('pausing drops the observation rather than freezing it', () => {
  const stalled = run(['missed', 'missed'])
  const paused = pauseDaemonProbes(stalled)
  assert.equal(paused.paused, true)
  assert.equal(paused.stalled, false)
  assert.equal(paused.misses, 0)
  assert.equal(stalledSince(paused), null)
  // Nothing is measured while paused, so an outcome that arrives anyway is ignored.
  assert.equal(applyDaemonProbe(paused, 'missed', t0), paused)
  // Resuming starts from zero: a stall that carried on through the hidden
  // stretch is re-found by the next two probes rather than assumed.
  const resumed = resumeDaemonProbes(paused)
  assert.equal(resumed.paused, false)
  assert.equal(resumed.misses, 0)
  assert.equal(applyDaemonProbe(resumed, 'missed', t0 + 60_000).stalled, false)
  // Idempotent on both sides.
  assert.equal(pauseDaemonProbes(paused), paused)
  assert.equal(resumeDaemonProbes(resumed), resumed)
})

test('the sentence names the elapsed seconds and promises only what holds', () => {
  assert.equal(stallSeconds(t0, t0 + 12_400), 12)
  // Clock skew or a clamped timestamp must not print a negative duration.
  assert.equal(stallSeconds(t0, t0 - 5_000), 0)
  const text = daemonStallText(t0, t0 + 12_400)
  assert.equal(text, 'swe-mux daemon is not responding (12s). Sessions keep running; the UI will catch up on its own.')
  // No instruction, because there is nothing correct to do: a reload during a
  // stall hangs on its first request.
  assert.doesNotMatch(text, /reload|restart|refresh|click|press/i)
})

// ---------------------------------------------------------------- the scheduler

type Pending = { handler: () => void; ms: number }

function fakeTimers() {
  const pending = new Map<number, Pending>()
  let next = 1
  return {
    pending,
    host: {
      setTimeout: (handler: () => void, ms: number) => { const id = next++; pending.set(id, { handler, ms }); return id },
      clearTimeout: (id: number) => { pending.delete(id) },
    },
    /** Fire every timer that is due, in order. */
    fire() {
      const due = [...pending.entries()]
      pending.clear()
      for (const [, entry] of due) entry.handler()
    },
  }
}

function fakeDocument(hidden = false) {
  const listeners = new Set<(event: { persisted?: boolean }) => void>()
  const doc = {
    hidden,
    addEventListener: (type: string, listener: (event: { persisted?: boolean }) => void) => {
      if (type === 'visibilitychange') listeners.add(listener)
    },
    removeEventListener: (type: string, listener: (event: { persisted?: boolean }) => void) => {
      if (type === 'visibilitychange') listeners.delete(listener)
    },
    listeners,
    setHidden(value: boolean) {
      doc.hidden = value
      for (const listener of [...listeners]) listener({})
    },
  }
  return doc
}

/** A probe whose answers are handed out one at a time, in order, so a test can
 *  hold one open and answer it after the world has moved on. */
function fakeProbe() {
  const waiting: Array<(outcome: DaemonProbeOutcome) => void> = []
  let sent = 0
  return {
    get sent() { return sent },
    get open() { return waiting.length },
    probe: () => {
      sent += 1
      return new Promise<DaemonProbeOutcome>(resolve => { waiting.push(resolve) })
    },
    answer(outcome: DaemonProbeOutcome) {
      const resolve = waiting.shift()
      assert.ok(resolve, 'no probe is waiting for an answer')
      resolve(outcome)
    },
  }
}

/** Let every settled promise run its continuation. */
const settle = () => new Promise<void>(resolve => setImmediate(resolve))

function harness(options: { hidden?: boolean; enabled?: boolean } = {}) {
  const timers = fakeTimers()
  const doc = fakeDocument(options.hidden ?? false)
  const probe = fakeProbe()
  const changes: DaemonLivenessState[] = []
  let clock = t0
  const dispose = watchDaemonLiveness({
    onChange: state => changes.push(state),
    probe: probe.probe,
    now: () => clock,
    doc,
    timers: timers.host,
    enabled: options.enabled,
  })
  return {
    timers, doc, probe, changes, dispose,
    advance(ms: number) { clock += ms },
    last: () => changes[changes.length - 1],
  }
}

test('two missed probes through the scheduler raise the banner with the first send time', async () => {
  const h = harness()
  // The first probe leaves at once, at mount.
  assert.equal(h.probe.sent, 1)
  h.advance(2500)
  h.probe.answer('missed')
  await settle()
  assert.equal(h.last().misses, 1)
  assert.equal(h.last().stalled, false)
  // The next one is scheduled from when the last settled, not from when it was sent.
  assert.equal(h.timers.pending.size, 1)
  assert.equal([...h.timers.pending.values()][0].ms, DAEMON_PROBE_INTERVAL_MS)
  h.advance(DAEMON_PROBE_INTERVAL_MS)
  h.timers.fire()
  assert.equal(h.probe.sent, 2)
  h.advance(2500)
  h.probe.answer('missed')
  await settle()
  assert.equal(h.last().stalled, true)
  assert.equal(stalledSince(h.last()), t0)
  // And one answer takes it down.
  h.timers.fire()
  h.probe.answer('ok')
  await settle()
  assert.equal(h.last().stalled, false)
  h.dispose()
})

test('a slow probe cannot have the next one start on top of it', async () => {
  const h = harness()
  assert.equal(h.probe.sent, 1)
  // Nothing is scheduled while a probe is out: the interval is measured from
  // the settle, so a hung request costs its deadline and not the miss budget.
  assert.equal(h.timers.pending.size, 0)
  h.timers.fire()
  assert.equal(h.probe.sent, 1)
  h.probe.answer('ok')
  await settle()
  assert.equal(h.timers.pending.size, 1)
  h.dispose()
})

test('a hidden tab is not probed, and showing it probes at once', async () => {
  const h = harness()
  h.probe.answer('ok')
  await settle()
  assert.equal(h.timers.pending.size, 1)
  h.doc.setHidden(true)
  // The pending probe is cancelled, and the state says nothing is being measured.
  assert.equal(h.timers.pending.size, 0)
  assert.equal(h.last().paused, true)
  h.timers.fire()
  assert.equal(h.probe.sent, 1)
  // Back: one probe immediately rather than an interval later, so a phone
  // unlocked during a stall learns about it in one round trip plus one interval.
  h.doc.setHidden(false)
  assert.equal(h.probe.sent, 2)
  assert.equal(h.last().paused, false)
  h.dispose()
})

test('hiding during a stall clears it, and the stall is re-found rather than assumed', async () => {
  const h = harness()
  h.probe.answer('missed'); await settle()
  h.timers.fire(); h.probe.answer('missed'); await settle()
  assert.equal(h.last().stalled, true)
  h.doc.setHidden(true)
  assert.equal(h.last().stalled, false)
  assert.equal(stalledSince(h.last()), null)
  h.advance(60_000)
  h.doc.setHidden(false)
  // One miss after the resume is not yet a stall: the silence over the hidden
  // minute was never measured, so the count starts again.
  h.probe.answer('missed'); await settle()
  assert.equal(h.last().stalled, false)
  h.timers.fire(); h.probe.answer('missed'); await settle()
  assert.equal(h.last().stalled, true)
  assert.equal(stalledSince(h.last()), t0 + 60_000)
  h.dispose()
})

test('a probe abandoned by a hide/show cycle neither counts nor stops the loop', async () => {
  const h = harness()
  // Probe 1 is out when the tab hides and shows again. The resume finds it
  // still in flight and cannot send its own.
  h.doc.setHidden(true)
  h.doc.setHidden(false)
  assert.equal(h.probe.sent, 1)
  assert.equal(h.probe.open, 1)
  // Its answer belongs to the dropped measurement: it must not count as a miss...
  h.probe.answer('missed')
  await settle()
  assert.equal(h.last().misses, 0)
  // ...and the loop must not die with it: the resume's probe is sent now.
  assert.equal(h.probe.sent, 2)
  h.probe.answer('missed')
  await settle()
  assert.equal(h.last().misses, 1)
  h.dispose()
})

test('mounting hidden sends nothing until the tab is shown', () => {
  const h = harness({ hidden: true })
  assert.equal(h.probe.sent, 0)
  assert.equal(h.last().paused, true)
  h.doc.setHidden(false)
  assert.equal(h.probe.sent, 1)
  h.dispose()
})

test('a disabled watcher measures nothing, and a disposed one stops', async () => {
  const off = harness({ enabled: false })
  assert.equal(off.probe.sent, 0)
  assert.equal(off.last().paused, true)
  // Visibility cannot turn a disabled watcher on: the caller disabled it for a reason.
  off.doc.setHidden(false)
  assert.equal(off.probe.sent, 0)
  off.dispose()

  const on = harness()
  on.probe.answer('ok')
  await settle()
  assert.equal(on.timers.pending.size, 1)
  on.dispose()
  assert.equal(on.timers.pending.size, 0)
  assert.equal(on.doc.listeners.size, 0)
  // A probe that was out at dispose reports nothing and reschedules nothing.
  const late = harness()
  const before = late.changes.length
  late.dispose()
  late.probe.answer('missed')
  await settle()
  assert.equal(late.changes.length, before)
  assert.equal(late.timers.pending.size, 0)
})

test('the probe asks for a fresh answer, with a deadline, and counts every failure as a miss', async () => {
  const seen: Array<{ input: string; init?: RequestInit }> = []
  const ok = await probeDaemon(async (input, init) => { seen.push({ input, init }); return { ok: true } })
  assert.equal(ok, 'ok')
  assert.equal(seen[0].input, '/api/health')
  // No cache may answer for the daemon, and the request carries an abort signal.
  assert.equal(seen[0].init?.cache, 'no-store')
  assert.ok(seen[0].init?.signal instanceof AbortSignal)
  // A 503 (the daemon is up but not ready), a network error, and a timeout are
  // one fact from here: it is not answering.
  assert.equal(await probeDaemon(async () => ({ ok: false })), 'missed')
  assert.equal(await probeDaemon(async () => { throw new TypeError('Failed to fetch') }), 'missed')
  const hung = await probeDaemon(
    (_input, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    }),
    20,
  )
  assert.equal(hung, 'missed')
})
