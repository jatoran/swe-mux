import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const source = (name:string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

test('scan timeline is explicitly gated per current run and resets at conversation boundaries', () => {
  const timeline = source('ScanTimelineTab.tsx')
  assert.ok(timeline.includes("state.global_enabled&&state.project_enabled"))
  assert.ok(timeline.includes('/scan-timeline`,{enabled}'))
  assert.ok(timeline.includes('It resets on /clear, /new, or session end.'))
  assert.ok(timeline.includes("event.kind==='boundary'"))
})

test('scan timeline shows every cap and names whichever one is closest to binding', () => {
  const timeline = source('ScanTimelineTab.tsx')
  const settings = source('Settings.tsx')
  const app = source('App.tsx')
  // The reported failure was a timeline that stopped for three hours while the
  // drawer showed only budgets that had headroom. The cap doing the stopping
  // has to be on screen, and the scanner's own reason has to be readable.
  assert.ok(timeline.includes('bindingGate'))
  assert.ok(timeline.includes('scan-gate-binding'))
  assert.ok(timeline.includes('state.skip_reason'))
  assert.ok(timeline.includes('Not scanning:'))
  assert.ok(timeline.includes('?rehydrate=1'))
  // The scan-timeline model is a changeable default, not a fixed one.
  assert.ok(settings.includes('Changeable default'))
  assert.ok(!app.includes('ScanSpendStatus'))
})

test('a full-session scan reports its chunk arithmetic and can be stopped', () => {
  const timeline = source('ScanTimelineTab.tsx')
  assert.ok(timeline.includes('Stop full scan'))
  assert.ok(timeline.includes("api('DELETE',`/api/sessions/${sid}/scan-timeline/backfill`)"))
  assert.ok(timeline.includes('completed_with_gaps'))
  assert.ok(timeline.includes('failed_chunks'))
  // Terminal states used to drop the chunk counts, so the size of the hole a
  // stopped job left behind was invisible.
  assert.ok(timeline.includes('${state.backfill.processed_chunks}/${state.backfill.total_chunks}'))
})

test('a record admits when it was written behind the transcript or repaired', () => {
  const timeline = source('ScanTimelineTab.tsx')
  assert.ok(timeline.includes('record.coverage?.remaining'))
  assert.ok(timeline.includes('Model output repaired'))
})

test('scan spending limits are global settings, never per-project', () => {
  const settings = source('Settings.tsx')
  const projects = source('ProjectsManager.tsx')
  // The dollar ceiling lived in each Project's committed .swe-mux/config.toml,
  // so the cap most likely to stop scanning sat in a file nobody opens and had
  // to be raised once per checkout.
  assert.ok(settings.includes('scan_timeline_daily_budget_usd'))
  assert.ok(settings.includes('scan_timeline_daily_token_budget'))
  assert.ok(settings.includes('scan_timeline_hourly_call_cap'))
  assert.ok(settings.includes('scan_timeline_max_output_tokens'))
  assert.ok(!projects.includes('scan_timeline_daily_budget_usd'))
  assert.ok(projects.includes('Settings → Automation'))
})

test('timeline drawer owns project context, project permission, and full-session scans', () => {
  const timeline = source('ScanTimelineTab.tsx')
  assert.ok(timeline.includes('Project context'))
  assert.ok(timeline.includes('Copy setup prompt'))
  assert.ok(timeline.includes('/project-context'))
  assert.ok(timeline.includes('/scan-timeline/project'))
  assert.ok(timeline.includes('Scan full session'))
  assert.ok(timeline.includes('/scan-timeline/backfill'))
})

test('timeline records collapse verbose evidence targets by default', () => {
  const timeline = source('ScanTimelineTab.tsx')
  const styles = source('style.css')
  assert.ok(timeline.includes('<details class="scan-record-targets">'))
  assert.ok(timeline.includes('Evidence targets'))
  assert.ok(!timeline.includes("record.target.join(' · ')"))
  assert.ok(styles.includes('.scan-record-targets ul{max-height:240px;overflow:auto'))
})
