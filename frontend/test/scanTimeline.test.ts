import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const source = (name:string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

test('scan timeline is explicitly gated per current run and resets at conversation boundaries', () => {
  const timeline = source('ScanTimelineTab.tsx')
  assert.ok(timeline.includes("state.global_enabled&&state.project_enabled"))
  assert.ok(timeline.includes('/scan-timeline`,{enabled}'))
  assert.ok(timeline.includes('It resets on /clear, /new, or session end'))
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
  // The scan-timeline model is a changeable default, not a fixed one, and "which
  // model is this budget being spent on" still has to be answerable from the tab
  // holding the caps. The *control* moved to Accounts - switching endpoint changes
  // all seven models at once, and checking them across four tabs is how a broken
  // one goes unnoticed - so what stays here is the resolved value and a link.
  assert.ok(settings.includes('data-setting="scan_timeline_model"'))
  assert.match(settings, /model-routing-elsewhere[\s\S]{0,400}?scan_timeline_model/)
  assert.match(settings, /routedModel\('scan_timeline_model'\)/)
  assert.match(settings, /goToSetting\('accounts','scan_timeline_model'\)/)
  assert.ok(settings.indexOf('data-setting="scan_timeline_model"')
    > settings.indexOf("activeTab==='automation'"))
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
  assert.ok(settings.includes('scan_timeline_daily_budget'))
  assert.ok(settings.includes('scan_timeline_run_budget'))
  assert.ok(settings.includes('scan_timeline_hourly_call_cap'))
  assert.ok(settings.includes('scan_timeline_max_output_tokens'))
  assert.ok(!projects.includes('scan_timeline_daily_budget'))
  assert.ok(projects.includes('Settings → Automation'))
})

test('Project-wide scan settings live in Project settings, not the drawer tab', () => {
  const timeline = source('ScanTimelineTab.tsx')
  const projects = source('ProjectsManager.tsx')
  const editor = source('ProjectContextEditor.tsx')
  // The drawer tab is session-scoped. Hosting the Project permission and the
  // Project context editor there meant every session in a Project showed the
  // same two controls, competing with the tab's actual job.
  //
  // A *gate* is not that, and the distinction is what the rewritten assertion below
  // pins: a gate is drawn only while the timeline cannot work and disappears the
  // moment it can, so a working Project never sees a Project-wide control in a
  // session-scoped pane. What the old rule really forbade was a standing control,
  // and stating it as "no Project write from here at all" also forbade the one
  // arrangement that fixes the two-overlay walk it created.
  assert.ok(!timeline.includes('/project-context'))
  assert.ok(!timeline.includes('Copy setup prompt'))
  assert.ok(timeline.includes('Scan full session'))
  assert.ok(timeline.includes('/scan-timeline/backfill'))
  // ...and a route to where they went, from every timeline tab.
  assert.ok(timeline.includes('onOpenProjectSettings'))
  assert.ok(timeline.includes('Project settings'))
  assert.ok(projects.includes('ProjectContextEditor'))
  assert.ok(editor.includes('/project-context'))
  assert.ok(editor.includes('Copy setup prompt'))
})

test('the timeline grants both switches at once, and only while it is off', () => {
  const timeline = source('ScanTimelineTab.tsx')
  // Both rungs in one act. Offering only the outer one meant turning it on, walking
  // back, and meeting a second gate with a second link to a second overlay.
  assert.ok(timeline.includes("'automation.scanTimeline' as const"))
  assert.ok(timeline.includes("'project.scanTimeline' as const"))
  assert.ok(timeline.includes('<GrantGate'))
  // Inside the `!allowed` branch and nowhere else, which is what keeps the Project
  // permission from becoming a standing control in a session-scoped tab.
  const [beforeGate, afterGate] = timeline.split('if(!allowed)')
  assert.ok(!beforeGate.includes('<GrantGate'), 'the gate must not render above the off check')
  assert.ok(afterGate.includes('<GrantGate'))
  // The unarmed notice named a Project switch and offered nothing to press, which is
  // the defect the setting-link rule exists to remove.
  assert.ok(timeline.includes('id="project.scanTimelineAutoArm"'))
})

test('a Project can arm every new conversation instead of being re-armed by hand', () => {
  const projects = source('ProjectsManager.tsx')
  const timeline = source('ScanTimelineTab.tsx')
  assert.ok(projects.includes('scan_timeline_auto_enable'))
  assert.ok(projects.includes('Arm every new conversation'))
  // The drawer says which of the two states an unarmed run is in, so "off" and
  // "about to arm itself" do not read the same.
  assert.ok(timeline.includes('state.auto_enable'))
  assert.ok(timeline.includes('starts on the next turn'))
  // ...and does not promise that to a conversation the human switched off.
  assert.ok(timeline.includes('state.run_decided'))
  assert.ok(timeline.includes('stays off'))
})

test('the timeline tab keeps its budget to one row and shows when a scan is out', () => {
  const timeline = source('ScanTimelineTab.tsx')
  const styles = source('style.css')
  // Six budget lines is the honest set and was also most of a narrow drawer.
  assert.ok(timeline.includes('<details class="scan-budget">'))
  assert.ok(styles.includes('.scan-budget>summary{'))
  // "Working on it" and "nothing is happening" looked identical while waiting.
  assert.ok(timeline.includes('state.scanning'))
  assert.ok(timeline.includes('Scanning…'))
  assert.ok(styles.includes('scan-pulse'))
  // The newest record is at the bottom, so that is where the list opens.
  assert.ok(timeline.includes('list.scrollTop=list.scrollHeight'))
})

test('timeline records collapse verbose evidence targets by default', () => {
  const timeline = source('ScanTimelineTab.tsx')
  const styles = source('style.css')
  assert.ok(timeline.includes('<details class="scan-record-targets">'))
  assert.ok(timeline.includes('Evidence targets'))
  assert.ok(!timeline.includes("record.target.join(' · ')"))
  assert.ok(styles.includes('.scan-record-targets ul{max-height:240px;overflow:auto'))
})

test('a timeline record is compact until it is opened, and opens one at a time', () => {
  const timeline = source('ScanTimelineTab.tsx')
  // Every record rendering its full detail made the tab a wall of prose rather than a
  // timeline. Detail is mounted only for a record the reader opened.
  assert.ok(timeline.includes('const open=!!openRecords[record.id]'))
  assert.ok(timeline.includes('{open&&<div class="scan-record-detail"'))
  assert.ok(timeline.includes('toggleRecord(record.id)'))
  assert.ok(timeline.includes('aria-expanded={open}'))
  // Expansion is per-device throwaway state, never server state and never a device
  // store keyed by unbounded per-run record ids.
  assert.ok(!timeline.includes('localStorage'))
})

test('a collapsed record still carries its own identity and its warnings', () => {
  const timeline = source('ScanTimelineTab.tsx')
  const styles = source('style.css')
  // Time, phase, lifecycle state and one line of summary are what makes a row
  // identifiable; expansion is for detail, not for working out which record this is.
  assert.ok(timeline.includes('{clock(record.t1)}'))
  assert.ok(timeline.includes('class="scan-record-gist"'))
  assert.ok(timeline.includes('gistOf'))
  assert.ok(timeline.includes('No semantic change recorded.'))
  // A window where the run stalled, or a record that is itself partial, says so
  // without being opened.
  assert.ok(timeline.includes('flagsOf'))
  for (const flag of ['blocked', 'dead end', 'behind', 'repaired']) assert.ok(timeline.includes(`label:'${flag}'`))
  // Conversation boundaries are not records and never collapse.
  assert.ok(timeline.includes("event.kind==='boundary'"))
  assert.ok(timeline.includes('class="scan-boundary"'))
  // The row is the control, so it has to be a tap target on a phone.
  assert.ok(styles.includes('.scan-record-head{min-height:48px'))
})

test('the enablement and liveness block never hides behind a record collapse', () => {
  const timeline = source('ScanTimelineTab.tsx')
  // ScanTimelineService.liveness() owns why a quiet timeline is quiet. Budget-stopped
  // and genuinely idle look identical from an empty tail, so both readings stay in the
  // panel chrome, outside the per-record disclosure.
  const listStart = timeline.indexOf('<div class="scan-timeline-list"')
  for (const marker of ['Not scanning:', 'state.skip_reason', 'scan-gate-binding', 'scan-backfill-status',
    'It resets on /clear, /new, or session end']) {
    assert.ok(timeline.indexOf(marker) < listStart, `${marker} must render outside the record list`)
  }
})
