import assert from 'node:assert/strict'
import test from 'node:test'
import { agentPot, quotaWindowLabel, tightestQuota } from '../src/usagePots'
import type { UsageRow, UsageSource } from '../src/usageAnalytics'

const row = (date: string, cost: number, tokens: number): UsageRow => ({
  date,
  input_tokens: tokens / 2,
  output_tokens: tokens / 2,
  cache_creation_tokens: 0,
  cache_read_tokens: 0,
  total_tokens: tokens,
  cost_usd: cost,
})

const source = (id: string, daily: UsageRow[]): UsageSource => ({
  source_id: id,
  source_label: id,
  collector_id: 'ccusage',
  daily,
  monthly: [],
  sessions: [],
  models: [],
  totals: row('', 0, 0),
})

test('the agent pot sums one window across every source', () => {
  const pot = agentPot([
    source('claude', [row('2026-08-15', 10, 1000), row('2026-08-14', 5, 500)]),
    source('codex', [row('2026-08-15', 2, 200), row('2026-08-13', 1, 100)]),
  ], 30)

  assert.equal(pot.cost_usd, 18)
  assert.equal(pot.total_tokens, 1800)
  assert.equal(pot.sources, 2)
})

test('the window counts days, not rows, so two sources on one day are one day', () => {
  // A naive `slice(0, days)` over concatenated rows would take 2026-08-15 twice and call
  // that two days, silently halving the window on a host with two harnesses.
  const pot = agentPot([
    source('claude', [row('2026-08-15', 10, 1000), row('2026-08-14', 10, 1000)]),
    source('codex', [row('2026-08-15', 1, 100), row('2026-08-14', 1, 100)]),
  ], 1)

  assert.equal(pot.cost_usd, 11)
  assert.equal(pot.latest_date, '2026-08-15')
})

test('the recent figure is the newest cached day, named by its date', () => {
  // Never "today". The cache is refreshed manually or on a slow cadence, so its newest row
  // is routinely yesterday's or older, and a stale figure captioned "today" sends someone
  // hunting a spike that was already paid for.
  const pot = agentPot([source('claude', [row('2026-08-11', 7, 700), row('2026-08-15', 3, 300)])], 30)

  assert.equal(pot.latest_date, '2026-08-15')
  assert.equal(pot.latest?.cost_usd, 3)
})

test('an empty cache reports nothing rather than zero-as-a-reading', () => {
  const pot = agentPot([], 30)

  assert.equal(pot.latest_date, null)
  assert.equal(pot.latest, null)
  assert.equal(pot.cost_usd, 0)
})

test('the tightest quota window wins across providers and window kinds', () => {
  const worst = tightestQuota({
    claude: {
      session: { used_percent: 91.4, resets_at: 100 },
      weekly: { used_percent: 38.2 },
      fable: null,
    },
    codex: {
      session: { used_percent: 12 },
      weekly: { used_percent: 44.5 },
      fable: null,
    },
  })

  // An average across these four would report comfortable headroom on an account that is
  // about to be cut off, which is the one wrong answer that matters here.
  assert.equal(worst?.provider, 'claude')
  assert.equal(worst?.window, 'session')
  assert.equal(worst?.used_percent, 91.4)
  assert.equal(Math.round(worst!.headroom_percent), 9)
  assert.equal(worst?.resets_at, 100)
  assert.equal(quotaWindowLabel(worst!.window), '5h')
})

test('unreadable quota is null, never full headroom', () => {
  // An account whose poll is erroring has unknown headroom. Rendering unknown as 100% free
  // is the failure direction that gets someone cut off mid-run.
  assert.equal(tightestQuota({}), null)
  assert.equal(tightestQuota({ claude: { session: null, weekly: null, fable: null } }), null)
})

test('a Fable weekly window can be the tightest one and is named as such', () => {
  const worst = tightestQuota({
    claude: { session: { used_percent: 20 }, weekly: { used_percent: 30 }, fable: { used_percent: 97 } },
  })

  assert.equal(worst?.window, 'fable')
  assert.equal(quotaWindowLabel(worst!.window), 'Fable weekly')
})
