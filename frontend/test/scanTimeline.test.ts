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

test('scan timeline exposes cost, run tokens, source expansion, and the changeable model', () => {
  const timeline = source('ScanTimelineTab.tsx')
  const settings = source('Settings.tsx')
  const app = source('App.tsx')
  assert.ok(timeline.includes('spend_today.cost_usd'))
  assert.ok(timeline.includes('run_token_budget'))
  assert.ok(timeline.includes('?rehydrate=1'))
  // The scan-timeline model is a changeable default, not a fixed one.
  assert.ok(settings.includes('Changeable default'))
  assert.ok(!app.includes('ScanSpendStatus'))
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
