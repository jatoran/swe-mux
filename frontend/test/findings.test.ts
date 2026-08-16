import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const source = (name: string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

test('the Insight tab is a segmented control over Timeline and Findings, gating only the timeline segment', () => {
  const insight = source('InsightTab.tsx')
  // Both surfaces are reachable, and the Findings pane is Project-aware so it does
  // not need a harness transcript - only the Timeline segment does.
  assert.ok(insight.includes('<ScanTimelineTab'))
  assert.ok(insight.includes('<FindingsPane'))
  assert.ok(insight.includes('hasHarnessTranscript(session?.backend)'))
  assert.ok(insight.includes('disabled={!timelineAvailable}'))
  // A shell session with no timeline falls back to Findings rather than a dead tab.
  assert.ok(insight.includes("view === 'timeline' && !timelineAvailable ? 'findings' : view"))
})

test('the Findings pane scope-toggles session and Project, defaulting to session', () => {
  const pane = source('FindingsPane.tsx')
  assert.ok(pane.includes("useState<Scope>('session')"), 'scope must default to session')
  assert.ok(pane.includes('This session'))
  assert.ok(pane.includes('This Project'))
  assert.ok(pane.includes("params.set(scope === 'session' ? 'session_id' : 'project_id', target)"))
  assert.ok(pane.includes("api<FindingsResponse>('GET', `/api/annotations?"))
})

test('the Findings pane always states what the current scope excludes', () => {
  const pane = source('FindingsPane.tsx')
  // The "off vs quiet" rule: session scope hides the Project-anchored findings, and
  // the pane says so rather than letting silence read as absence.
  assert.ok(pane.includes('const exclusion = scope'))
  assert.ok(pane.includes('are hidden here — switch to Project to see them'))
  assert.ok(pane.includes('includes findings from every session in this Project'))
  assert.ok(pane.includes('class="findings-exclusion"'))
})

test('Findings chips come from tag_counts with provenance hidden by default', () => {
  const pane = source('FindingsPane.tsx')
  assert.ok(pane.includes('data?.tag_counts'))
  assert.ok(pane.includes("const PROVENANCE_TAG = 'provenance'"))
  // The default (no-tag) view filters provenance out; the chip still reveals its count.
  assert.ok(pane.includes('item => selectedTag ? true : item.tag !== PROVENANCE_TAG'))
  assert.ok(pane.includes('are hidden here by volume'))
  assert.ok(pane.includes('class="findings-chip-count"'))
})

test('the Findings pane is read-only and links to the Automation dashboard', () => {
  const pane = source('FindingsPane.tsx')
  // Read-only keeps the pane out of the actuation gate: no mutating verbs, ever.
  assert.ok(!/'POST'|'PUT'|'DELETE'|'PATCH'/.test(pane), 'the Findings pane must issue no mutating requests')
  assert.ok(pane.includes('onOpenAutomationDashboard'))
  assert.ok(pane.includes('Open Automation dashboard'))
  // Provenance is labelled deterministic vs model, and the run id shows when present.
  assert.ok(pane.includes("provenance === 'deterministic_consumer'"))
  assert.ok(pane.includes('item.agent_run_id'))
})

test('a persisted Timeline tab migrates forward to the Insight tab', () => {
  const layout = source('drawerLayout.ts')
  const app = source('App.tsx')
  const icons = source('railIcons.tsx')
  // Both the canonical migratedTabId helper and the legacy v1-seed path in App map
  // the retired `timeline` id to `insight`, so no saved workspace loses the tab.
  assert.ok(layout.includes("if (value === 'timeline') return 'insight'"))
  assert.ok(app.includes("legacyRaw==='timeline'?'insight'"))
  // The icon map is keyed by DrawerTabId, so the new id must carry an icon.
  assert.ok(icons.includes('insight: InsightIcon'))
})
