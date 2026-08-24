import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { subscribeRowClock } from '../src/sessionRowPrefs.ts'

const source = (name: string) => readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8')

type FakeGlobals = {
  intervals: number
  cleared: number
  fire: () => void
  visibility: (() => void)[]
  hide: (hidden: boolean) => void
}

/**
 * A browser just wide enough for the ticker: `window.setInterval`, `clearInterval`,
 * and a `document` that can go hidden.
 *
 * Restored by the caller's `finally`; every test file in this suite shares one
 * process, and a `window` left lying around changes what other modules believe they
 * are running in.
 */
function withFakeBrowser<T>(body: (fake: FakeGlobals) => T): T {
  const scope = globalThis as Record<string, unknown>
  const hadWindow = 'window' in scope, hadDocument = 'document' in scope
  const previousWindow = scope.window, previousDocument = scope.document
  let ticks: (() => void)[] = []
  const fake: FakeGlobals = {
    intervals: 0,
    cleared: 0,
    fire: () => { for (const tick of [...ticks]) tick() },
    visibility: [],
    hide: hidden => {
      ;(scope.document as { hidden: boolean }).hidden = hidden
      for (const listener of [...fake.visibility]) listener()
    },
  }
  scope.window = {
    setInterval: (handler: () => void) => { fake.intervals += 1; ticks.push(handler); return ticks.length },
    clearInterval: () => { fake.cleared += 1; ticks = [] },
  }
  scope.document = {
    hidden: false,
    addEventListener: (_type: string, listener: () => void) => { fake.visibility.push(listener) },
    removeEventListener: (_type: string, listener: () => void) => {
      fake.visibility = fake.visibility.filter(item => item !== listener)
    },
  }
  try {
    return body(fake)
  } finally {
    if (hadWindow) scope.window = previousWindow; else delete scope.window
    if (hadDocument) scope.document = previousDocument; else delete scope.document
  }
}

test('every subscriber shares one interval, and the last one out stops it', () => {
  withFakeBrowser(fake => {
    const seen: number[][] = [[], [], []]
    const leave = seen.map(bucket => subscribeRowClock(now => bucket.push(now)))
    // Thirty rows on screen is thirty subscribers and still one timer: the clock moved
    // into the rows precisely so the shell would stop re-rendering for it, and a timer
    // per row would have been the price.
    assert.equal(fake.intervals, 1)
    fake.fire()
    for (const bucket of seen) assert.ok(bucket.length >= 1, 'every subscriber is told the time')
    assert.equal(new Set(seen.map(bucket => bucket[bucket.length - 1])).size, 1, 'every subscriber sees the same quantized value')
    leave[0]()
    leave[1]()
    assert.equal(fake.cleared, 0, 'a subscriber leaving does not stop the clock for the rest')
    leave[2]()
    assert.equal(fake.cleared, 1)
    // And a later subscriber starts it again rather than sitting on a dead timer.
    const again = subscribeRowClock(() => {})
    assert.equal(fake.intervals, 2)
    again()
  })
})

test('a hidden tab stops the clock and a visible one resumes it', () => {
  withFakeBrowser(fake => {
    const ticks: number[] = []
    const leave = subscribeRowClock(now => ticks.push(now))
    assert.equal(fake.intervals, 1)
    fake.hide(true)
    assert.equal(fake.cleared, 1)
    fake.fire()
    assert.equal(ticks.length, 1, 'a background tab has no rows to age')
    fake.hide(false)
    assert.equal(fake.intervals, 2)
    fake.fire()
    assert.ok(ticks.length > 1)
    leave()
  })
})

test('the composition root does not subscribe to the row clock', () => {
  // The isolation itself: if App.tsx reads the tick again, every five seconds re-renders
  // the whole shell - every menu, drawer, tab strip and pane frame - to age a few
  // sidebar rows. `SessionRowLive` is where the tick belongs.
  const app = source('App.tsx')
  assert.ok(!/useRowClock/.test(app), 'App.tsx must not subscribe to the row clock')
  assert.ok(!/buildSessionRowTokens|identityRowTokens/.test(app), 'row tokens are built below the clock, not at the root')
  assert.match(app, /deriveRowFleetFacts\(/)
  assert.match(app, /<SessionRowLive /)
  const row = source('SessionRowLive.tsx')
  assert.match(row, /const now = useRowClock\(\)/)
})
