import assert from 'node:assert/strict'
import test from 'node:test'
import {
  EVENT_REFRESH_MAX_GAP_MS, EVENT_REFRESH_MIN_GAP_MS, EVENT_REFRESH_SETTLE_MS, FLEET_REFRESH_TIMEOUT_MS,
  createEventRefreshScheduler, createFleetRefreshController, describeFleetFailures, fetchFleetSlices,
} from '../src/fleetRefresh.ts'

type Call = { method: string; path: string; timeoutMs: number | undefined; signal: AbortSignal | undefined }

/** A stand-in for `api` that records what each slice was asked for. */
const recorder = (answer: (path: string) => Promise<unknown>) => {
  const calls: Call[] = []
  const request = <T,>(method: string, path: string, _body?: unknown, options: { timeoutMs?: number; signal?: AbortSignal } = {}) => {
    calls.push({ method, path, timeoutMs: options.timeoutMs, signal: options.signal })
    return answer(path) as Promise<T>
  }
  return { calls, request }
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0))

test('every fleet read carries a deadline, which is what stops a waking PWA hanging forever', async () => {
  const { calls, request } = recorder(async () => [])
  await fetchFleetSlices({}, request)
  assert.equal(calls.length, 5)
  assert.deepEqual(calls.map(call => call.path).sort(), [
    '/api/harnesses', '/api/previews', '/api/project-groups', '/api/projects', '/api/sessions',
  ])
  for (const call of calls) assert.equal(call.timeoutMs, FLEET_REFRESH_TIMEOUT_MS)
})

test('a caller may tighten the deadline but never drop it', async () => {
  const { calls, request } = recorder(async () => [])
  await fetchFleetSlices({ timeoutMs: 250 }, request)
  for (const call of calls) assert.equal(call.timeoutMs, 250)
})

test('one failed endpoint no longer discards the other four slices', async () => {
  const { request } = recorder(async path => {
    if (path === '/api/previews') throw new Error('previews exploded')
    return path === '/api/sessions' ? [{ id: 'a' }] : []
  })
  const { slices, failures } = await fetchFleetSlices({}, request)
  assert.deepEqual(slices.sessions, [{ id: 'a' }] as never)
  assert.deepEqual(slices.projects, [] as never)
  assert.deepEqual(slices.groups, [] as never)
  assert.equal(slices.previews, undefined)
  assert.deepEqual(failures.map(failure => failure.slice), ['previews'])
  assert.equal(describeFleetFailures(failures), 'previews exploded')
})

test('several failures report one line that says how many', async () => {
  const { request } = recorder(async path => {
    if (path === '/api/previews' || path === '/api/harnesses') throw new Error('daemon is away')
    return []
  })
  const { failures } = await fetchFleetSlices({}, request)
  assert.equal(describeFleetFailures(failures), 'daemon is away (2 of 5 fleet reads failed)')
  assert.equal(describeFleetFailures([]), '')
})

test('a fetch never rejects, so a refresh cycle cannot die on one bad endpoint', async () => {
  const { request } = recorder(async () => { throw new Error('everything is down') })
  const { slices, failures } = await fetchFleetSlices({}, request)
  assert.deepEqual(slices, {})
  assert.equal(failures.length, 5)
})

test('concurrent callers share one cycle', async () => {
  let runs = 0
  let release = () => {}
  const controller = createFleetRefreshController(async () => {
    runs += 1
    await new Promise<void>(resolve => { release = resolve })
  })
  const first = controller.refresh()
  assert.equal(runs, 1)
  assert.equal(controller.pending(), true)
  release()
  await first
  assert.equal(runs, 1)
  assert.equal(controller.pending(), false)
})

test('a caller that arrives mid-cycle is given the follow-up, not the snapshot that predates it', async () => {
  // The stale-await defect: a mutation POST that awaits refresh used to be handed the
  // in-flight promise, whose GETs left before the mutation did, so it resolved with a
  // fleet that had never seen the change.
  const cycles: (() => void)[] = []
  const started: number[] = []
  const controller = createFleetRefreshController(async () => {
    started.push(started.length)
    await new Promise<void>(resolve => cycles.push(resolve))
  })
  const first = controller.refresh()
  const mutationAwait = controller.refresh()
  let firstDone = false
  let mutationDone = false
  void first.then(() => { firstDone = true })
  void mutationAwait.then(() => { mutationDone = true })
  assert.equal(started.length, 1)
  cycles[0]()
  await tick()
  assert.equal(firstDone, true)
  // The first cycle finishing does not settle the mutation's wait: its own cycle is only
  // now starting.
  assert.equal(mutationDone, false)
  assert.equal(started.length, 2)
  cycles[1]()
  await tick()
  assert.equal(mutationDone, true)
})

test('many mid-cycle callers coalesce onto one follow-up cycle', async () => {
  const cycles: (() => void)[] = []
  const controller = createFleetRefreshController(async () => {
    await new Promise<void>(resolve => cycles.push(resolve))
  })
  const first = controller.refresh()
  const followers = [controller.refresh(), controller.refresh(), controller.refresh()]
  cycles[0]()
  await first
  assert.equal(cycles.length, 2)
  cycles[1]()
  await Promise.all(followers)
  assert.equal(cycles.length, 2)
  // And the queue is empty again: nothing keeps running on its own.
  await tick()
  assert.equal(cycles.length, 2)
  assert.equal(controller.pending(), false)
})

test('a hung cycle is abandoned instead of pinning every future refresh', async () => {
  // The freeze this controller exists to prevent: the old dedupe stored the hung promise
  // and handed it to the interval, the visibility handler and the socket reconnect alike.
  const signals: AbortSignal[] = []
  let runs = 0
  const controller = createFleetRefreshController(signal => {
    runs += 1
    signals.push(signal)
    // Never settles, exactly like a fetch with no deadline against a sleeping daemon.
    return new Promise<void>(() => {})
  }, { stallMs: 10 })
  const stalled = controller.refresh()
  assert.equal(runs, 1)
  await stalled
  assert.equal(signals[0].aborted, true)
  assert.equal(controller.pending(), false)
  // The next refresh runs rather than joining the corpse.
  void controller.refresh()
  assert.equal(runs, 2)
})

test('the stall is reported once per abandoned cycle', async () => {
  const stalls: number[] = []
  const controller = createFleetRefreshController(() => new Promise<void>(() => {}), {
    stallMs: 10,
    onStall: ms => stalls.push(ms),
  })
  await controller.refresh()
  await new Promise(resolve => setTimeout(resolve, 30))
  assert.deepEqual(stalls, [10])
})

test('a cycle that finishes in time is never reported as stalled', async () => {
  const stalls: number[] = []
  const controller = createFleetRefreshController(async () => {}, { stallMs: 50, onStall: ms => stalls.push(ms) })
  await controller.refresh()
  await new Promise(resolve => setTimeout(resolve, 80))
  assert.deepEqual(stalls, [])
})

test('an abandoned cycle that finally returns cannot clobber the one that replaced it', async () => {
  let releaseFirst = () => {}
  const runs: AbortSignal[] = []
  const controller = createFleetRefreshController(signal => {
    runs.push(signal)
    return runs.length === 1
      ? new Promise<void>(resolve => { releaseFirst = resolve })
      : Promise.resolve()
  }, { stallMs: 10 })
  await controller.refresh()
  const second = controller.refresh()
  releaseFirst()
  await second
  // The late first cycle must not have ended the second one's turn as in-flight, nor
  // started a phantom third.
  assert.equal(runs.length, 2)
  assert.equal(controller.pending(), false)
  assert.equal(runs[0].aborted, true)
  assert.equal(runs[1].aborted, false)
})

test('a run that throws still concludes its cycle and still runs the follow-up', async () => {
  let runs = 0
  const controller = createFleetRefreshController(async () => {
    runs += 1
    throw new Error('apply blew up')
  })
  await controller.refresh()
  assert.equal(runs, 1)
  assert.equal(controller.pending(), false)
  await controller.refresh()
  assert.equal(runs, 2)
})

test('a synchronous throw is a concluded cycle too, not an unhandled rejection', async () => {
  let runs = 0
  const controller = createFleetRefreshController(() => {
    runs += 1
    throw new Error('threw before awaiting')
  })
  await controller.refresh()
  assert.equal(runs, 1)
  assert.equal(controller.pending(), false)
})

test('reset abandons the cycle and releases everyone waiting on it', async () => {
  const signals: AbortSignal[] = []
  const controller = createFleetRefreshController(signal => {
    signals.push(signal)
    return new Promise<void>(() => {})
  })
  const running = controller.refresh()
  const queuedWaiter = controller.refresh()
  controller.reset()
  await Promise.all([running, queuedWaiter])
  assert.equal(signals[0].aborted, true)
  assert.equal(controller.pending(), false)
  // The queued follow-up was released, not silently started.
  assert.equal(signals.length, 1)
})

/** Let a settled refresh's continuation run: a few microtask turns, no real timer. */
const flush = async () => { for (let turn = 0; turn < 5; turn += 1) await Promise.resolve() }

/** A clock and timer queue the scheduler runs on, advanced by hand. */
const manualTime = () => {
  let clock = 0
  let nextId = 0
  const timers = new Map<number, { at: number; run: () => void }>()
  return {
    now: () => clock,
    setTimer: (run: () => void, delayMs: number) => {
      nextId += 1
      timers.set(nextId, { at: clock + delayMs, run })
      return nextId
    },
    clearTimer: (handle: unknown) => { timers.delete(handle as number) },
    pending: () => [...timers.values()].map(timer => timer.at - clock),
    /** Move the clock forward, firing every timer that comes due, in order. */
    advance: async (ms: number) => {
      const until = clock + ms
      for (;;) {
        const due = [...timers.entries()].filter(([, timer]) => timer.at <= until).sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        timers.delete(due[0])
        clock = due[1].at
        due[1].run()
        await flush()
      }
      clock = until
      await flush()
    },
  }
}

test('a burst of events costs one refresh, after a short settle', async () => {
  const time = manualTime()
  let runs = 0
  const scheduler = createEventRefreshScheduler(async () => { runs += 1 }, time)
  for (let index = 0; index < 50; index += 1) scheduler.request()
  assert.deepEqual(time.pending(), [EVENT_REFRESH_SETTLE_MS])
  await time.advance(EVENT_REFRESH_SETTLE_MS)
  assert.equal(runs, 1)
})

test('a continuous event stream refreshes at the minimum gap, not back to back', async () => {
  const time = manualTime()
  const starts: number[] = []
  const scheduler = createEventRefreshScheduler(async () => { starts.push(time.now()) }, time)
  // ~17 events a second for ten seconds: the measured subagent fleet.
  for (let at = 0; at < 10_000; at += 60) {
    scheduler.request()
    await time.advance(60)
  }
  await time.advance(EVENT_REFRESH_MAX_GAP_MS)
  const gaps = starts.slice(1).map((start, index) => start - starts[index])
  assert.ok(starts.length <= 11, `expected about one refresh a second, got ${starts.length}`)
  for (const gap of gaps) assert.ok(gap >= EVENT_REFRESH_MIN_GAP_MS, `two refreshes ${gap}ms apart`)
})

test('a slow daemon is asked less often, up to the ceiling', async () => {
  const time = manualTime()
  const starts: number[] = []
  let finish: () => void = () => {}
  const scheduler = createEventRefreshScheduler(() => {
    starts.push(time.now())
    return new Promise<void>(resolve => { finish = resolve })
  }, time)
  scheduler.request()
  await time.advance(EVENT_REFRESH_SETTLE_MS)
  // Events keep arriving while a 1.5s cycle runs; none starts a second one.
  for (let index = 0; index < 10; index += 1) {
    scheduler.request()
    await time.advance(150)
  }
  assert.equal(starts.length, 1)
  finish()
  await flush()
  await time.advance(EVENT_REFRESH_MAX_GAP_MS)
  assert.equal(starts.length, 2)
  // Twice the 1.5s the first cycle took, measured from when it started.
  assert.equal(starts[1] - starts[0], 3_000)
})

test('a quiet fleet refreshes nothing, and cancel drops what was scheduled', async () => {
  const time = manualTime()
  let runs = 0
  const scheduler = createEventRefreshScheduler(async () => { runs += 1 }, time)
  await time.advance(10_000)
  assert.equal(runs, 0)
  scheduler.request()
  scheduler.cancel()
  await time.advance(10_000)
  assert.equal(runs, 0)
  scheduler.request()
  assert.deepEqual(time.pending(), [])
})
