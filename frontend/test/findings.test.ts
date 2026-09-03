import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const source = (name: string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

test('Activity draws Timeline, Findings, and the Change Map, gating only the timeline segment', () => {
  const segments = source('drawerSegments.ts')
  const host = source('UtilityDrawer.tsx')
  // All three are registered segments of one tab, which is what gives each of them a
  // palette entry and a voice phrase of its own (`App.tsx` generates them from
  // DRAWER_SEGMENTS). Timeline needs a harness transcript; the other two do not.
  for (const id of ["id: 'timeline'", "id: 'findings'", "id: 'changes'"]) {
    assert.ok(segments.includes(id), id)
  }
  assert.ok(segments.includes("const hasTranscript = (context: DrawerSegmentContext) => context.hasTranscript"))
  assert.match(segments, /id: 'timeline'[^\n]*available: hasTranscript/)
  assert.ok(host.includes("<ScanTimelineTab"))
  assert.ok(host.includes("<FindingsPane"))
  assert.ok(host.includes("<LazyChangeMap"))
  // A shell session with no timeline falls back to an available segment rather than a
  // dead tab. That rule used to be inline in InsightTab; it belongs to every segmented
  // tab now, so it lives in `resolveDrawerSegment`.
  assert.ok(segments.includes('if (requested && available.some(item => item.id === requested)) return requested'))
  assert.ok(segments.includes('return available[0].id'))
  // The graph keeps the pop-out that makes a 380px column tolerable.
  assert.ok(host.includes('onPopOut={session ? () => props.onChangeMapOpenAsTab(session.id) : undefined}'))
  // ...and it is the one segment kept mounted, because its layout worker's settled
  // positions are the expensive part.
  assert.match(segments, /id: 'changes'[^\n]*keepMounted: true/)
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

test('Findings is the only home for run notes, so it filters by who concluded them', () => {
  const pane = source('FindingsPane.tsx')
  const dashboard = source('AutomationDashboard.tsx')
  // The Automation dashboard used to draw a second, differently-filtered copy of this
  // table. Run notes stay Findings' alone (the dashboard's Activity tab mirrors the
  // attention inbox and the runtime trail, never annotations), so this pane has to
  // cover both kinds: observer-written notes as well as deterministic ones.
  assert.ok(!dashboard.includes("view==='notes'"), 'the run-notes view must not come back')
  assert.ok(!dashboard.includes('/api/annotations'), 'run notes must not grow a second table here')
  assert.ok(pane.includes("type Source = 'all' | 'deterministic' | 'observer'"))
  assert.ok(pane.includes("source === 'all'"))
  assert.ok(pane.includes("(source === 'deterministic') === isDeterministic(item.provenance)"))
  // Drawn only when both kinds are present: a control with one real setting is noise.
  assert.ok(pane.includes('sourceCounts.deterministic > 0 && sourceCounts.observer > 0'))
})

test('the Automation dashboard mirrors the attention inbox as the same component', () => {
  const dashboard = source('AutomationDashboard.tsx')
  // "Mirrored" has to mean the same component - the rule `AutomationSpendView`
  // already carries. A hand-rolled second inbox here would have its own read
  // state and its own actions, and the two surfaces would eventually disagree
  // about what is unread; the shared component makes that impossible.
  assert.ok(dashboard.includes('<AttentionInbox'), 'Activity mirrors the inbox via the shared component')
  assert.ok(!dashboard.includes('/api/attention/inbox'), 'the dashboard must not fetch the inbox itself')
  assert.ok(!dashboard.includes("view==='health'"), 'the health view was split three ways')
  // Three tabs, and the tab is the question: what may run (and where), what it
  // costs, what it did.
  assert.ok(dashboard.includes("export type AutomationView='policy'|'usage'|'activity'"))
  assert.ok(!dashboard.includes("view==='graph'"), 'the read-only graph view must not come back')
  assert.ok(dashboard.includes('<AutomationPolicyMatrix'))
  assert.ok(dashboard.includes('<AutomationPolicyView'))
  // The away report stays with the drawer inbox it summarizes.
  assert.ok(source('Notifications.tsx').includes("api('GET','/api/attention/absence')"))
  // The workload table moved to Resources, following the cost column that left before it.
  assert.ok(source('WorkloadTelemetry.tsx').includes("api<Workloads>('GET', `/api/telemetry/v2/workload?${telemetryQuery("))
})

test('the spend view is one component drawn in both places rather than two copies', () => {
  // "Fully mirrored" has to mean the same component: two views over one endpoint is
  // exactly the drift this consolidation removed everywhere else.
  assert.ok(source('AutomationDashboard.tsx').includes('<AutomationSpendView/>'))
  assert.ok(source('UsageModal.tsx').includes('<AutomationSpendView />'))
})

test('the agent figure in the spend view is labelled as a subset, not as the agent total', () => {
  // `provider_cost_dimensions` covers only runs mux observed; ccusage covers every
  // transcript the harness wrote. Drawn as two bare totals they were two competing answers
  // to one question, which is the exact failure the shared component above prevents
  // elsewhere. The denominator is therefore in the label, in the heading, and in the foot.
  const spend = source('AutomationSpendView.tsx')
  assert.ok(spend.includes('agents · observed runs'))
  assert.ok(spend.includes('Agent model spend · observed runs only'))
  assert.ok(spend.includes('all observed runs'))
  // ...and it never claims to be the whole pot.
  assert.ok(!spend.includes('<h3>Agent model spend</h3>'))
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

test('every retired tab id migrates forward to a tab and, where it exists, a segment', () => {
  const layout = source('drawerLayout.ts')
  const app = source('App.tsx')
  const icons = source('railIcons.tsx')
  // One table, in one place. The `segment` half is what makes a merge non-destructive:
  // without it, a reader who had Change Map selected would land on Activity's *first*
  // segment, which is a different surface than the one they chose.
  assert.ok(layout.includes("if (value === 'timeline') return { tab: 'activity', segment: 'timeline' }"))
  assert.ok(layout.includes("if (value === 'changemap') return { tab: 'activity', segment: 'changes' }"))
  assert.ok(layout.includes("if (value === 'context') return { tab: 'agent', segment: 'instructions' }"))
  assert.ok(layout.includes("if (value === 'insight') return { tab: 'activity' }"))
  assert.ok(layout.includes("if (value === 'clipboard') return { tab: 'actions' }"))
  // The legacy v1-seed path in App calls the same helper rather than re-spelling it,
  // which is what it used to do and what drifted every time a tab was folded in.
  assert.ok(app.includes('const legacy=migratedTabTarget(legacyDrawerTab.current)'))
  assert.ok(!app.includes("legacyRaw==='timeline'"), 'the inline copy of the table must stay gone')
  // The icon map is keyed by DrawerTabId, so the surviving id must carry an icon.
  assert.ok(icons.includes('activity: ActivityIcon'))
})
