import assert from 'node:assert/strict'
import test from 'node:test'
import {
  describeLastRun, describeUsageActivity, formatBytes, pollUsageRefresh, runUsageRefresh,
  type UsageCollector,
} from '../src/usageRefresh.ts'

type Status = { refreshing: boolean; step: number }

const noSleep = async () => {}

test('a background refresh is followed until the daemon says it ended', async () => {
  const reads: Status[] = [
    { refreshing: true, step: 1 },
    { refreshing: true, step: 2 },
    { refreshing: false, step: 3 },
  ]
  const seen: number[] = []
  const final = await runUsageRefresh(
    async () => ({ refreshing: true, step: 0 }),
    async () => reads.shift()!,
    status => seen.push(status.step),
    { sleep: noSleep },
  )
  assert.equal(final.step, 3)
  assert.deepEqual(seen, [0, 1, 2, 3])
  assert.equal(reads.length, 0)
})

test('a status that is not refreshing is reported once and never polled', async () => {
  let reads = 0
  const final = await pollUsageRefresh(
    { refreshing: false, step: 7 },
    async () => { reads += 1; return { refreshing: false, step: 8 } },
    () => {},
    { sleep: noSleep },
  )
  assert.equal(final.step, 7)
  assert.equal(reads, 0)
})

test('an unmounted view stops polling instead of running forever', async () => {
  let reads = 0
  let cancelled = false
  const final = await pollUsageRefresh(
    { refreshing: true, step: 0 },
    async () => { reads += 1; cancelled = reads >= 2; return { refreshing: true, step: reads } },
    () => {},
    { sleep: noSleep, cancelled: () => cancelled },
  )
  assert.equal(reads, 2)
  assert.equal(final.step, 2)
})

test('bytes read are stated in the unit a person reads them in', () => {
  assert.equal(formatBytes(null), null)
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(3 * 2 ** 20), '3 MB')
  assert.equal(formatBytes(4.4 * 2 ** 30), '4.4 GB')
})

test('a running refresh says what it scans, how much it has read, and for how long', () => {
  const collector: UsageCollector = {
    id: 'ccusage', status: 'refreshing', phase: 'running',
    run: { kind: 'incremental', since: '2026-10-30', started_at: 1000, read_bytes: 3 * 2 ** 30 },
  }
  assert.equal(
    describeUsageActivity(collector, 1075),
    'Scanning days since 2026-10-30 in the background · 3.0 GB read · 1m 15s.',
  )
  assert.equal(describeUsageActivity({ id: 'ccusage', status: 'ready' }, 0), null)
})

test('a deferred refresh says it is waiting and why', () => {
  const collector: UsageCollector = {
    id: 'ccusage', status: 'stale', phase: 'deferred', deferred_reason: 'CPU 97%',
  }
  assert.equal(
    describeUsageActivity(collector, 0),
    'Scheduled refresh waiting for a quieter moment (CPU 97%).',
  )
})

test('the last run is summarised with its scope, cost, containment and version', () => {
  assert.equal(describeLastRun(null), null)
  assert.equal(
    describeLastRun({
      kind: 'rebuild', duration_ms: 125_000, ccusage_version: '20.0.46',
      resources: { mechanism: 'job', cpu_percent_cap: 25, read_bytes: 46 * 2 ** 30 },
    }),
    'Last refresh: full history · 2m 5s · 46.0 GB read · background priority, ≤25% CPU · ccusage 20.0.46',
  )
  assert.match(describeLastRun({ kind: 'incremental', since: '2026-10-30', error: 'x' }) ?? '', /failed$/)
})
