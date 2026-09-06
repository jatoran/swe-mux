import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import { captionText, cohortLabel, rangeLabel } from '../src/telemetryCaptionText.ts'

const root = join(import.meta.dirname, '..')
const source = (name: string) => readFileSync(join(root, 'src', name), 'utf8')

// The completion gate: a total a reader acts on never appears as a bare number. The
// section states the window and the cohort every view under it was measured over, in
// words that live here and nowhere else, and each view states its own denominator.

test('a caption names the range and the cohort', () => {
  assert.equal(captionText({ days: 7, origin: 'mux_owned' }), 'last 7 days · mux-owned runs')
})

test('the active filters are named so a filtered total cannot read as the whole', () => {
  const text = captionText({ days: 1, origin: 'all', filters: ['codex', '', 'native'] })
  assert.equal(text, 'last 24 hours · mux-owned and imported runs · codex · native')
})

test('every range and cohort the controls offer has a label', () => {
  assert.equal(rangeLabel(0), 'all retained time')
  assert.equal(rangeLabel(1), 'last 24 hours')
  assert.equal(rangeLabel(30), 'last 30 days')
  assert.equal(cohortLabel('mux_owned'), 'mux-owned runs')
  assert.equal(cohortLabel('imported'), 'imported runs')
  assert.equal(cohortLabel('all'), 'mux-owned and imported runs')
})

test('the Activity section states its window and every view under it states a denominator', () => {
  const fleet = source('FleetActivityView.tsx')
  const workload = source('WorkloadTelemetry.tsx')
  // The window and the cohort are drawn once, for the section, because one set of controls
  // windows every view below it - and they are drawn from the shared words rather than
  // spelled again here, which is what a second view rewording "Mux-owned" would break.
  assert.ok(fleet.includes('captionText({days:filters.days,origin:filters.origin'), 'the section caption is not built from the shared words')
  assert.ok(!fleet.includes("'Mux-owned'"), 'a view spelling a cohort its own way is the drift the shared words prevent')
  assert.ok(!workload.includes('rangeLabel('), 'the range belongs to the section, not to a view inside it')
  // One caption per domain the section can show: tools (aggregate and the calls page),
  // checks, context, patterns, and the runs browser's two modes.
  const captions = (text: string) => (text.match(/class="analytics-caption"/g) || []).length
  assert.ok(captions(fleet) >= 5, `FleetActivityView draws ${captions(fleet)} captions; expected one per total`)
  assert.ok(captions(workload) >= 2, 'the runs browser draws no caption over its totals')
})
