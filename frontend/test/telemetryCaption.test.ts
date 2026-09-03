import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import { captionText, cohortLabel, coverageLabel, rangeLabel } from '../src/telemetryCaptionText.ts'

const root = join(import.meta.dirname, '..')
const source = (name: string) => readFileSync(join(root, 'src', name), 'utf8')

// The completion gate: every displayed total names its time range, its cohort, its
// denominator, and its coverage. The words are pure and tested here; the views are held
// to drawing them beside every total by source text, because the totals are what a
// reader acts on and a bare number is the bug.

test('a caption names the range, the cohort, the denominator, and the coverage', () => {
  const text = captionText({
    days: 7,
    origin: 'mux_owned',
    denominator: '4,821 calls',
    coverage: { rolled_days: 6, rolled_hours: 13, raw_spans: 2, raw_seconds: 5400 },
  })
  assert.equal(text, 'last 7 days · mux-owned runs · 4,821 calls · 6 rolled-up days · 13 rolled-up hours · 1.5h read from entities')
})

test('the active filters are named so a filtered total cannot read as the whole', () => {
  const text = captionText({
    days: 1,
    origin: 'all',
    denominator: '12 runs',
    coverage: { rolled_days: 0, rolled_hours: 23, raw_spans: 1, raw_seconds: 1800 },
    filters: { backend: 'codex', project_id: '', evidence_quality: 'native' },
  })
  assert.equal(text, 'last 24 hours · mux-owned and imported runs · backend = codex, evidence quality = native · 12 runs · 23 rolled-up hours · 0.5h read from entities')
})

test('coverage that is missing or empty is said, never implied', () => {
  assert.equal(coverageLabel(undefined), 'coverage unavailable')
  assert.equal(coverageLabel({ rolled_days: 0, rolled_hours: 0, raw_spans: 0, raw_seconds: 0 }), 'no data in range')
  assert.equal(coverageLabel({ rolled_days: 1, rolled_hours: 1, raw_spans: 0, raw_seconds: 0 }), '1 rolled-up day · 1 rolled-up hour')
})

test('every range and cohort the controls offer has a label', () => {
  assert.equal(rangeLabel(0), 'all retained time')
  assert.equal(rangeLabel(1), 'last 24 hours')
  assert.equal(rangeLabel(30), 'last 30 days')
  assert.equal(cohortLabel('mux_owned'), 'mux-owned runs')
  assert.equal(cohortLabel('imported'), 'imported runs')
  assert.equal(cohortLabel('all'), 'mux-owned and imported runs')
})

test('every Fleet activity view draws a caption with a denominator beside its totals', () => {
  const fleet = source('FleetActivityView.tsx')
  const workload = source('WorkloadTelemetry.tsx')
  // One caption per domain the view can show: tools (aggregate and the calls page),
  // skills, verification, context, inefficiencies, and the workload tab.
  const captions = fleet.match(/<TelemetryCaption /g) || []
  assert.ok(captions.length >= 6, `FleetActivityView draws ${captions.length} captions; expected one per total`)
  assert.ok((workload.match(/<TelemetryCaption /g) || []).length >= 1, 'the workload tab draws no caption')
  for (const text of [fleet, workload]) {
    for (const use of text.split('<TelemetryCaption ').slice(1)) {
      const head = use.slice(0, use.indexOf('/>'))
      assert.ok(head.includes('denominator='), 'a caption without a denominator is a bare number')
      assert.ok(head.includes('days='), 'a caption without a range')
      assert.ok(head.includes('origin='), 'a caption without a cohort')
    }
  }
})
