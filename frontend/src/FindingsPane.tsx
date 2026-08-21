import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { SettingLink } from './SettingLink'
import { GrantButton, GrantGate } from './GrantGate'
import type { GrantId } from './grants'
import { INSTALL_CONFIG_CHANGED, installSwitch } from './installSwitches'
import { PROJECT_AUTOMATIONS_CHANGED, fetchProjectAutomations } from './projectAutomations'
import type { Project, Session } from './types'

// The human read of run notes: loop, declared-vs-verified, doc-debt, and provenance
// annotations from the deterministic consumers (Phase 3.7), plus the notes model observers
// write. Read-only by construction — no dismiss, no mutation — so the pane stays out of
// the actuation gate. It talks to the extended `GET /api/annotations`, whose
// `tag_counts` are what let "quiet" read apart from "buried under provenance".
//
// This is the *only* home for that table now. The Automation dashboard used to draw its own
// "Run notes" view over the same endpoint, so one record had two homes with two different
// filters and neither said the other existed. That view is gone and links here instead,
// which is why this pane grew a provenance filter: as one half of a pair it could afford to
// show deterministic findings only, and as the whole thing it cannot.
//
// The two words for one table are down to one, too. "Findings" is the user-facing name
// everywhere; `annotations` survives as the API term and nothing else.

type Finding = {
  id: string
  tag: string
  content: string
  provenance: string
  agent_run_id?: string | null
  project_id?: string | null
  resolved_model?: string | null
  created_at: number
  /** Set by the daemon when a stored finding's own evidence no longer supports it
   *  (`server._mark_unsupported`). The row is never rewritten — a finding is a
   *  record of what a detector concluded — so the retraction happens at the read. */
  unsupported?: boolean
  unsupported_reason?: string
}
type FindingsResponse = { items: Finding[]; tag_counts: Record<string, number> }
type Scope = 'session' | 'project'
/** Who wrote a finding. Not a scope and not a tag, so it filters on its own axis. */
type Source = 'all' | 'deterministic' | 'observer'
const SOURCE_LABELS: Record<Source, string> = {
  all: 'All sources',
  deterministic: 'Deterministic',
  observer: 'Observer',
}

// Provenance edges are the one high-volume tag, so the default (no-tag) view hides
// them; their count still shows on the chip, and their chip reveals them.
const PROVENANCE_TAG = 'provenance'
/** The opt-ins that write what this pane reads (`automation_registry.py`). */
const DETECTOR_AUTOMATIONS = ['loop_detection', 'declared_vs_verified', 'doc_debt', 'provenance_graph']
/** The same four as grants, in the order they are offered. `test/grants.test.ts` holds
 *  the two lists to each other, so adding a detector cannot leave the gate behind. */
const DETECTOR_GRANTS: GrantId[] = [
  'project.loopDetection', 'project.declaredVsVerified', 'project.docDebt', 'project.provenanceGraph',
]
const stamp = (value: number) =>
  new Date(value * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const tagLabel = (tag: string) => tag.replace(/-/g, ' ')
const isDeterministic = (provenance: string) => provenance === 'deterministic_consumer'

export function FindingsPane({ session, project, onOpenAutomationDashboard }: {
  session: Session | null
  project?: Project
  onOpenAutomationDashboard: () => void
}) {
  const [scope, setScope] = useState<Scope>('session')
  const [source, setSource] = useState<Source>('all')
  const [selectedTag, setSelectedTag] = useState<string | null>(null)
  const [detectors, setDetectors] = useState<string[] | null>(null)
  const [engine, setEngine] = useState<boolean | null>(null)
  const [data, setData] = useState<FindingsResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const sid = session?.id || ''
  const projectId = project?.id || session?.project_id || ''
  const target = scope === 'session' ? sid : projectId

  const load = async () => {
    if (!target) { setData(null); return }
    setLoading(true)
    const params = new URLSearchParams()
    params.set(scope === 'session' ? 'session_id' : 'project_id', target)
    if (selectedTag) params.set('tag', selectedTag)
    try {
      setData(await api<FindingsResponse>('GET', `/api/annotations?${params.toString()}`))
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setData(null)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { void load() }, [scope, selectedTag, sid, projectId])

  // The detectors that write findings are per-Project opt-ins, all four of them off until a
  // human turns them on. Without this read the pane cannot tell a quiet Project from one
  // whose detectors were never permitted, and it showed both as "No findings in scope" —
  // the exact confusion its own "off vs quiet" rule exists to prevent.
  useEffect(() => {
    if (!projectId) { setDetectors(null); return }
    let stale = false
    const read = () => {
      fetchProjectAutomations(projectId)
        .then(state => { if (!stale) setDetectors(state.enabled) })
        .catch(() => { if (!stale) setDetectors(null) })
    }
    read()
    window.addEventListener(PROJECT_AUTOMATIONS_CHANGED, read)
    return () => { stale = true; window.removeEventListener(PROJECT_AUTOMATIONS_CHANGED, read) }
  }, [projectId])
  // The install half of the same question, for the observer notes the detectors do not
  // write. Same three-valued shape and same invalidation as the Project read above.
  useEffect(() => {
    let stale = false
    const read = () => {
      void installSwitch('automation_enabled').then(value => { if (!stale) setEngine(value) })
    }
    read()
    window.addEventListener(INSTALL_CONFIG_CHANGED, read)
    return () => { stale = true; window.removeEventListener(INSTALL_CONFIG_CHANGED, read) }
  }, [])
  // Findings land on a turn boundary, the same event the timeline listens for.
  useEffect(() => {
    const refresh = () => void load()
    window.addEventListener('mux:turn-ended', refresh)
    return () => window.removeEventListener('mux:turn-ended', refresh)
  }, [scope, selectedTag, sid, projectId])

  // Resetting the tag on a scope change keeps a session-only chip from sticking
  // into a Project view where that tag may not exist.
  const selectScope = (next: Scope) => { setSelectedTag(null); setScope(next) }
  // The source filter is not reset with it: "show me observer notes" is a standing
  // preference about what you are reading for, and it survives a scope change the way the
  // tag chip cannot (a tag may simply not exist in the other scope).

  const counts = data?.tag_counts || {}
  const tags = Object.keys(counts).sort()
  const provenanceCount = counts[PROVENANCE_TAG] || 0
  // Two independent filters, applied client-side because the endpoint has no source
  // parameter and the payload is already in hand: tag decides *what* a finding is about,
  // source decides *who concluded it*. Provenance edges stay hidden unless their own chip
  // is picked, at every source setting — they are high-volume by construction, not by
  // whether a model wrote them.
  const rows = (data?.items || [])
    .filter(item => selectedTag ? true : item.tag !== PROVENANCE_TAG)
    .filter(item => !item.unsupported)
    .filter(item => source === 'all'
      || (source === 'deterministic') === isDeterministic(item.provenance))
  // Withheld, not deleted, and never silently: the row still exists and the reason
  // is stated, because "no findings" and "findings that no longer stand" are
  // different answers and the chip counts still include the second.
  const withheld = (data?.items || []).filter(item => item.unsupported)
  const sourceCounts = (data?.items || []).reduce((totals, item) => {
    if (isDeterministic(item.provenance)) totals.deterministic += 1
    else totals.observer += 1
    return totals
  }, { deterministic: 0, observer: 0 })

  // The "off vs quiet" rule: silence must read as scope, never as absence. Session
  // scope resolves to the session's run ids, so a Project-anchored finding with no
  // run (doc-debt, provenance) is absent there by construction.
  const exclusion = scope === 'session'
    ? 'Session scope shows only this session’s run findings. Doc-debt and cross-session provenance are anchored to the Project, not a run, so they are hidden here — switch to Project to see them.'
    : 'Project scope includes findings from every session in this Project, not only this one.'

  // The four deterministic detectors this pane reads. A Project with none of them permitted
  // produces nothing here, ever — which is a different statement from "nothing found yet".
  const detectorsOff = detectors !== null && !DETECTOR_AUTOMATIONS.some(id => detectors.includes(id))
  // Three-valued on purpose: `null` is "not read", and rendering that as "off" would be
  // the same lie the gate exists to remove, one layer down.
  const engineOff = engine === false

  return <section class="findings-pane">
    <header class="findings-header">
      <div class="findings-scope" role="tablist" aria-label="Findings scope">
        <button role="tab" aria-selected={scope === 'session'} class={scope === 'session' ? 'active' : ''}
          disabled={!sid} onClick={() => selectScope('session')}>This session</button>
        <button role="tab" aria-selected={scope === 'project'} class={scope === 'project' ? 'active' : ''}
          disabled={!projectId} onClick={() => selectScope('project')}>This Project</button>
      </div>
    </header>
    <p class="findings-exclusion">{exclusion}</p>
    {/* All four at once, named on the button. The old link went to `project.automations`,
        which is an *area* rather than a switch: it revealed a heading above twenty
        checkboxes and left the reader to pick four of them out by name. All four are
        model-free and ship together, so offering them together is also the honest
        granularity — a Project running one of them and not the others is a preference,
        not a starting point. */}
    {detectorsOff && <GrantGate ids={DETECTOR_GRANTS} projectId={projectId}
      heading="This Project has not permitted any finding detectors."
      confirmLabel="Turn on all four detectors"
      onGranted={load}
      extra={<p class="findings-gate-alt">
        Prefer some and not others? <SettingLink variant="link" target="project.automations"
          projectId={projectId}>Choose them individually</SettingLink>.
      </p>}
    >
      <p>Loop and stall detection, declared-vs-verified, the doc-debt ledger, and the
      provenance graph read this Project’s transcripts and write what they conclude here.
      Nothing appears on this pane until at least one is on.</p>
    </GrantGate>}
    {/* Its own row rather than another chip: a source is not a tag, and mixing the two in
        one strip made "doc debt" and "deterministic" look like alternatives. Drawn only
        when both kinds are actually present, so a Project running deterministic detectors
        alone never sees a control with one real setting. */}
    {sourceCounts.deterministic > 0 && sourceCounts.observer > 0 &&
      <div class="findings-sources" role="group" aria-label="Filter by who wrote the finding">
        {(Object.keys(SOURCE_LABELS) as Source[]).map(id => <button key={id}
          class={source === id ? 'active' : ''} aria-pressed={source === id}
          title={id === 'deterministic' ? 'Model-free detectors only'
            : id === 'observer' ? 'Notes written by a model observer only'
              : 'Every run note, however it was concluded'}
          onClick={() => setSource(id)}>{SOURCE_LABELS[id]}{id !== 'all' && <span class="findings-chip-count">{sourceCounts[id]}</span>}</button>)}
      </div>}
    {tags.length > 0 && <div class="findings-chips" role="group" aria-label="Filter by finding type">
      <button class={selectedTag === null ? 'active' : ''} aria-pressed={selectedTag === null}
        onClick={() => setSelectedTag(null)}>All</button>
      {tags.map(tag => <button key={tag} class={selectedTag === tag ? 'active' : ''}
        aria-pressed={selectedTag === tag}
        onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}>
        {tagLabel(tag)} <span class="findings-chip-count">{counts[tag]}</span>
      </button>)}
    </div>}
    {selectedTag === null && provenanceCount > 0 && <p class="findings-hint">
      Provenance edges ({provenanceCount}) are hidden here by volume. Open the Provenance chip to read them.
    </p>}
    {withheld.length > 0 && <p class="findings-hint findings-withheld">
      {withheld.length} loop {withheld.length === 1 ? 'finding is' : 'findings are'} withheld: {withheld[0].unsupported_reason}
    </p>}
    {/* The second half of "off vs quiet" on this pane. The four detectors above are
        per-Project and model-free; the observer notes beside them come from the
        automation engine, which is an install switch that is off by default. With the
        detectors permitted and the engine off, half of this table can never appear and
        nothing said so — the Source filter simply never grew its second button. */}
    {engineOff && !detectorsOff && <p class="findings-hint findings-engine-off">
      Observer notes are switched off for this install, so everything here is from the
      model-free detectors.{' '}
      <GrantButton id="automation.engine">Turn on the automation engine</GrantButton>
    </p>}
    {error && <p class="usage-error">{error}</p>}
    <div class="findings-list">
      {scope === 'session' && !sid && <p class="drawer-empty">Focus a session to view its findings, or switch to Project scope.</p>}
      {rows.length === 0 && !loading && !(scope === 'session' && !sid) &&
        <p class="drawer-empty">{
          source !== 'all' ? `No ${SOURCE_LABELS[source].toLocaleLowerCase()} findings in scope.`
            : selectedTag ? `No ${tagLabel(selectedTag)} findings in scope.`
              : 'No findings in scope.'
        }</p>}
      {rows.map(item => <article class="finding-row" key={item.id}>
        <header>
          <span class={`finding-tag finding-tag-${item.tag}`}>{tagLabel(item.tag)}</span>
          <span class={`finding-provenance ${isDeterministic(item.provenance) ? 'deterministic' : 'model'}`}
            title={isDeterministic(item.provenance) ? 'Model-free deterministic detector' : 'Written by a model observer'}>
            {isDeterministic(item.provenance) ? 'deterministic' : (item.resolved_model || 'model')}
          </span>
          <time>{stamp(item.created_at)}</time>
        </header>
        <p>{item.content}</p>
        {item.agent_run_id && <small class="finding-run">run {item.agent_run_id.slice(0, 8)}</small>}
      </article>)}
    </div>
    <footer class="findings-footer">
      <button class="findings-dashboard-link" onClick={onOpenAutomationDashboard}>Open Automation dashboard</button>
    </footer>
  </section>
}
