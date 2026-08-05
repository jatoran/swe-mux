import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import {
  agentCompletenessLabel,
  agentScopeLabel,
  agentStateLabel,
  filterAgentEnvironmentSections,
  type AgentEnvironmentInventory,
  type AgentEnvironmentItem,
} from './agentEnvironment'
import type { Session } from './types'

function itemTitle(item: AgentEnvironmentItem): string {
  const details = item.meta.map(meta => `${meta.label}: ${meta.value}`)
  return [item.description, ...details, item.source_label ? `Source: ${item.source_label}` : '']
    .filter(Boolean)
    .join('\n')
}

export function AgentEnvironmentTab({ session }: { session: Session | null }) {
  const [inventory, setInventory] = useState<AgentEnvironmentInventory | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const generation = useRef(0)
  const isAgent = session?.backend === 'claude' || session?.backend === 'codex'

  const load = async (sessionId: string, refresh = false) => {
    const mine = ++generation.current
    setLoading(true)
    try {
      const result = await api<AgentEnvironmentInventory>(
        'GET',
        `/api/sessions/${encodeURIComponent(sessionId)}/agent-environment${refresh ? '?refresh=1' : ''}`,
        undefined,
        { timeoutMs: 10_000 },
      )
      if (mine !== generation.current) return
      setInventory(result)
      setError('')
    } catch (cause) {
      if (mine !== generation.current) return
      setInventory(null)
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (mine === generation.current) setLoading(false)
    }
  }

  useEffect(() => {
    generation.current++
    setInventory(null)
    setError('')
    setQuery('')
    setLoading(false)
    if (session && isAgent) void load(session.id)
  }, [session?.id, isAgent, session?.runtime_cwd, session?.run_cwd, session?.agent_run_seq])

  const sections = useMemo(
    () => filterAgentEnvironmentSections(inventory?.sections || [], query),
    [inventory, query],
  )
  const itemCount = inventory?.sections.reduce((total, section) => total + section.items.length, 0) || 0
  const changedSources = inventory?.sources.filter(source => source.changed_after_start) || []

  if (!session) {
    return <>
      <p class="drawer-status">no terminal focused</p>
      <p class="drawer-empty">Focus an agent session to inspect its environment.</p>
    </>
  }
  if (!isAgent) {
    return <>
      <p class="drawer-status">{session.name || session.id} · shell</p>
      <p class="drawer-empty">The focused terminal is a shell, not a Claude or Codex agent.</p>
    </>
  }

  return <div class="agent-environment">
    <header class="agent-environment-header">
      <div>
        <strong>{session.name || session.id}</strong>
        <span>{inventory ? `${inventory.backend}${inventory.runtime.version ? ` · ${inventory.runtime.version}` : ''}` : session.backend}</span>
      </div>
      <button
        disabled={loading}
        title="Rescan passive configuration sources. This never connects MCP servers or executes plugin and hook code."
        onClick={() => void load(session.id, true)}
      >{loading ? 'Scanning…' : 'Rescan'}</button>
    </header>

    {!inventory && !error && <p class="drawer-empty">Reading the agent environment…</p>}
    {error && <p class="drawer-empty">Agent environment could not be read: {error}</p>}
    {inventory && <>
      <section class="agent-runtime" aria-label="Agent runtime">
        <div><span>Executable</span><b>{inventory.runtime.executable}</b></div>
        <div><span>Model</span><b>{inventory.runtime.model || 'CLI default'}</b></div>
        <div><span>Working directory</span><b title={inventory.cwd}>{inventory.cwd}</b></div>
        {inventory.runtime.options.map(option => <div key={`${option.label}:${option.value}`}><span>{option.label}</span><b>{option.value}</b></div>)}
        {!!inventory.runtime.modes.length && <div><span>Modes</span><b>{inventory.runtime.modes.join(', ')}</b></div>}
      </section>

      <div class="agent-environment-summary">
        <span>{itemCount} items</span>
        <span>{inventory.sources.length} sources</span>
        {!!changedSources.length && <span class="warn">{changedSources.length} changed since load</span>}
      </div>

      {itemCount > 12 && <input
        class="agent-environment-filter"
        type="search"
        placeholder="Filter tools, MCP, plugins…"
        aria-label="Filter agent environment"
        value={query}
        onInput={event => setQuery((event.target as HTMLInputElement).value)}
      />}

      <div class="agent-environment-sections">
        {query && !sections.length && <p class="drawer-empty">No capability matches “{query}”.</p>}
        {sections.map(section => <details key={section.id} open={!!query || section.id === 'policies'}>
          <summary>
            <span>{section.label}</span>
            <small>{section.items.length}{query ? ` / ${section.total}` : ''}</small>
            <em title={section.note}>{agentCompletenessLabel(section.completeness)}</em>
          </summary>
          <div class="agent-environment-items">
            {!section.items.length && <p class="drawer-empty">Nothing configured or discoverable.</p>}
            {section.items.map(item => <article key={item.id} title={itemTitle(item)}>
              <header>
                <strong>{item.name}</strong>
                <span class={`agent-state state-${item.state}`}>{agentStateLabel(item.state)}</span>
              </header>
              {item.description && <p>{item.description}</p>}
              <div class="agent-item-facts">
                <span>{agentScopeLabel(item.scope)}</span>
                <span>{item.origin}</span>
                {item.changed_after_start && <span class="warn">changed since load</span>}
              </div>
              {!!item.meta.length && <dl>{item.meta.map(meta => <div key={`${meta.label}:${meta.value}`}><dt>{meta.label}</dt><dd>{meta.value}</dd></div>)}</dl>}
            </article>)}
            {section.truncated && <p class="agent-environment-note">Showing {section.items.length} of {section.total} entries.</p>}
            <p class="agent-environment-note">{section.note}</p>
          </div>
        </details>)}

        <details class="agent-environment-sources" open={changedSources.length > 0}>
          <summary><span>Configuration sources</span><small>{inventory.sources.length}</small></summary>
          <div>
            {inventory.sources.map(source => <p key={source.id}>
              <strong>{source.label}</strong>
              <span>{agentScopeLabel(source.scope)} · {agentStateLabel(source.status)}</span>
              {source.changed_after_start && <b>changed since load</b>}
            </p>)}
          </div>
        </details>

        {!!inventory.diagnostics.length && <details class="agent-environment-diagnostics" open>
          <summary><span>Diagnostics</span><small>{inventory.diagnostics.length}</small></summary>
          <ul>{inventory.diagnostics.map((diagnostic, index) => <li key={`${diagnostic.source_id}:${index}`}>{diagnostic.message}</li>)}</ul>
        </details>}
      </div>
    </>}
  </div>
}
