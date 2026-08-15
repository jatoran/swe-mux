import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildSpendRows, callHealth, exactMoney, formatCount, formatDuration, formatMoney, formatPercent,
  type SpendBreakdown,
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
