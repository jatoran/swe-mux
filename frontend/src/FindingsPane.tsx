import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { Project, Session } from './types'

// The human read of the deterministic consumers' findings (Phase 3.7): loop,
// declared-vs-verified, doc-debt, and provenance annotations, scoped and filtered.
// Read-only by construction — no dismiss, no mutation — so the pane stays out of
// the actuation gate. It talks to the extended `GET /api/annotations`, whose
// `tag_counts` are what let "quiet" read apart from "buried under provenance".

type Finding = {
  id: string
  tag: string
  content: string
  provenance: string
  agent_run_id?: string | null
  project_id?: string | null
  resolved_model?: string | null
  created_at: number
}
type FindingsResponse = { items: Finding[]; tag_counts: Record<string, number> }
type Scope = 'session' | 'project'

// Provenance edges are the one high-volume tag, so the default (no-tag) view hides
// them; their count still shows on the chip, and their chip reveals them.
const PROVENANCE_TAG = 'provenance'
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
  const [selectedTag, setSelectedTag] = useState<string | null>(null)
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
  // Findings land on a turn boundary, the same event the timeline listens for.
  useEffect(() => {
    const refresh = () => void load()
    window.addEventListener('mux:turn-ended', refresh)
    return () => window.removeEventListener('mux:turn-ended', refresh)
  }, [scope, selectedTag, sid, projectId])

  // Resetting the tag on a scope change keeps a session-only chip from sticking
  // into a Project view where that tag may not exist.
  const selectScope = (next: Scope) => { setSelectedTag(null); setScope(next) }

  const counts = data?.tag_counts || {}
  const tags = Object.keys(counts).sort()
  const provenanceCount = counts[PROVENANCE_TAG] || 0
  const rows = (data?.items || []).filter(item => selectedTag ? true : item.tag !== PROVENANCE_TAG)

  // The "off vs quiet" rule: silence must read as scope, never as absence. Session
  // scope resolves to the session's run ids, so a Project-anchored finding with no
  // run (doc-debt, provenance) is absent there by construction.
  const exclusion = scope === 'session'
    ? 'Session scope shows only this session’s run findings. Doc-debt and cross-session provenance are anchored to the Project, not a run, so they are hidden here — switch to Project to see them.'
    : 'Project scope includes findings from every session in this Project, not only this one.'

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
    {error && <p class="usage-error">{error}</p>}
    <div class="findings-list">
      {scope === 'session' && !sid && <p class="drawer-empty">Focus a session to view its findings, or switch to Project scope.</p>}
      {rows.length === 0 && !loading && !(scope === 'session' && !sid) &&
        <p class="drawer-empty">{selectedTag ? `No ${tagLabel(selectedTag)} findings in scope.` : 'No findings in scope.'}</p>}
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
