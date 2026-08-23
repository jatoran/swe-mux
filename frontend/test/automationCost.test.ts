import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildSpendRows, cacheEconomics, cacheEconomicsDetail, cacheHit, cacheHitDetail, callHealth,
  exactMoney, formatCount, formatDuration, formatMoney, formatPercent, type SpendBreakdown,
} from '../src/automationCost.ts'

const rule = (overrides: Partial<SpendBreakdown['rules'][number]> & { rule_id: string }) => ({
  calls: 0, tokens: 0, cost_usd: 0, today_calls: 0, today_tokens: 0, today_cost_usd: 0,
  ...overrides,
})

test('money keeps the precision its magnitude actually carries', () => {
  // The two magnitudes that share these tables: an agent bill and one observer call.
  assert.equal(formatMoney(8600.754787), '$8,600.75')
  assert.equal(formatMoney(0.63), '$0.63')
  assert.equal(formatMoney(0.0006258), '$0.0006')
  assert.equal(formatMoney(0), '$0')
})

test('a cost too small to print does not render as free', () => {
  assert.equal(formatMoney(0.0000012), '<$0.0001')
  assert.notEqual(formatMoney(0.00000012), '$0.0000')
})

test('the exact figure survives the rounding as a title', () => {
  assert.equal(exactMoney(0.0006258), '$0.0006258')
  assert.equal(exactMoney(8600.754787), '$8600.754787')
  assert.equal(exactMoney(2), '$2')
})

test('counts stay exact while they are readable and go compact when they are not', () => {
  assert.equal(formatCount(2269), '2,269')
  assert.equal(formatCount(99_999), '99,999')
  assert.equal(formatCount(9_664_898_958), '9.7B')
  assert.equal(formatCount(35_087_852_487), '35.1B')
})

test('durations read as time rather than as a seconds count', () => {
  assert.equal(formatDuration(8404), '2h 20m')
  assert.equal(formatDuration(5691), '1h 35m')
  assert.equal(formatDuration(95), '1m 35s')
  assert.equal(formatDuration(42), '42s')
  assert.equal(formatDuration(null), '—')
  assert.equal(formatDuration(0), '—')
})

test('percentages round to whole points', () => {
  assert.equal(formatPercent(0.4337991119862109), '43%')
  assert.equal(formatPercent(null), '0%')
})

test('spend rows rank by window cost and carry each share of it', () => {
  const rows = buildSpendRows({
    days: 7, today: '2026-08-15', start_day: '2026-08-09',
    totals: { calls: 0, tokens: 0, cost_usd: 0, today_calls: 0, today_tokens: 0, today_cost_usd: 0 },
    rules: [
      rule({ rule_id: 'titler', label: 'Session titler', kind: 'observer', calls: 400, cost_usd: 0.25 }),
      rule({ rule_id: 'builtin:scan-timeline', label: 'Scan timeline', kind: 'feature', calls: 12, cost_usd: 0.75 }),
    ],
  })

  assert.deepEqual(rows.map(row => row.rule_id), ['builtin:scan-timeline', 'titler'])
  assert.equal(rows[0].share, 0.75)
  assert.equal(rows[1].share, 0.25)
})

/** A ranked list of zeroes hides the row making hundreds of calls, which is the row to look at. */
test('with no measurable cost the share falls back to call count', () => {
  const rows = buildSpendRows({
    days: 7, today: '2026-08-15', start_day: '2026-08-09',
    totals: { calls: 0, tokens: 0, cost_usd: 0, today_calls: 0, today_tokens: 0, today_cost_usd: 0 },
    rules: [
      rule({ rule_id: 'a', calls: 30 }),
      rule({ rule_id: 'b', calls: 10 }),
    ],
  })

  assert.equal(rows[0].share, 0)
  assert.equal(rows[0].callShare, 0.75)
  assert.equal(rows[1].callShare, 0.25)
})

test('an unlabelled rule id still names itself and reads as retired', () => {
  const rows = buildSpendRows({
    days: 7, today: '2026-08-15', start_day: '2026-08-09',
    totals: { calls: 0, tokens: 0, cost_usd: 0, today_calls: 0, today_tokens: 0, today_cost_usd: 0 },
    rules: [rule({ rule_id: 'builtin.removed-observer', calls: 3, cost_usd: 0.01 })],
  })

  assert.equal(rows[0].label, 'builtin.removed-observer')
  assert.equal(rows[0].kind, 'retired')
})

test('an absent breakdown is an empty list, not a crash', () => {
  assert.deepEqual(buildSpendRows(null), [])
  assert.deepEqual(buildSpendRows(undefined), [])
})

/** The dashboard summed these lifetime counts under the heading "calls today". */
test('call health separates the failures from the total', () => {
  assert.deepEqual(callHealth({ cancelled: 39, completed: 446, failed: 196 }), {
    total: 681, failed: 235, failureRate: 235 / 681,
  })
  assert.deepEqual(callHealth(undefined), { total: 0, failed: 0, failureRate: 0 })
})

/**
 * Null and 0% are different answers: null is "nothing was billed in this window", which is
 * what an unused rule and a pre-cache-accounting daemon both look like, and printing 0% for
 * either accuses a working cache of being broken.
 */
test('a cache hit rate needs billed prompt tokens to exist at all', () => {
  assert.equal(cacheHit(undefined, undefined), null)
  assert.equal(cacheHit(0, 0), null)
  assert.deepEqual(cacheHit(4000, 0), { rate: 0, cached: 0, prompt: 4000 })
})

test('the rate is measured against prompt tokens, never the token total', () => {
  const hit = cacheHit(4000, 3000)
  assert.deepEqual(hit, { rate: 0.75, cached: 3000, prompt: 4000 })
  assert.equal(formatPercent(hit?.rate), '75%')
})

test('a provider over-reporting its cache cannot print more than a full hit', () => {
  assert.equal(cacheHit(100, 250)?.rate, 1)
  assert.equal(cacheHit(100, -5)?.rate, 0)
})

test('the tooltip carries the two exact figures the percentage came from', () => {
  assert.match(cacheHitDetail(cacheHit(4000, 3000)), /3,000 of 4,000 prompt tokens/)
  assert.equal(cacheHitDetail(null), 'no billed prompt tokens in this window')
})

test('spend rows carry the cached figures through untouched', () => {
  const rows = buildSpendRows({
    days: 7, today: '2026-08-20', start_day: '2026-08-14',
    totals: { calls: 0, tokens: 0, cost_usd: 0, today_calls: 0, today_tokens: 0, today_cost_usd: 0 },
    rules: [rule({
      rule_id: 'builtin:assistant', calls: 2, cost_usd: 0.02,
      input_tokens: 4000, cached_tokens: 2000,
      today_input_tokens: 4000, today_cached_tokens: 2000,
    })],
  })

  assert.equal(cacheHit(rows[0].input_tokens, rows[0].cached_tokens)?.rate, 0.5)
  assert.equal(cacheHit(rows[0].today_input_tokens, rows[0].today_cached_tokens)?.rate, 0.5)
})

test('cache economics separate a cold prefix from no caching at all', () => {
  // Both report a 0% hit rate. Only one of them is also paying 1.25x input for
  // a write nothing reads back, and that is the one worth acting on.
  const written = cacheEconomics(-0.004, 8100, 0)
  assert.ok(written)
  assert.equal(written.netLoss, true)
  assert.equal(written.written, 8100)
  assert.match(cacheEconomicsDetail(written), /write premium not read back/)

  const read = cacheEconomics(0.0162, 0, 8100)
  assert.ok(read)
  assert.equal(read.netLoss, false)
  assert.match(cacheEconomicsDetail(read), /saved/)
})

test('a provider that reports no cache figures is unmeasured, not zero', () => {
  // Inventing a $0.00 saving would put a confident number where there is no
  // measurement, which is the same mistake as pricing an unpriced call at zero.
  assert.equal(cacheEconomics(undefined, undefined, undefined), null)
  assert.equal(cacheEconomics(0, 0, 0), null)
  assert.equal(cacheEconomicsDetail(null), 'this provider reports no cache figures')
})
