import { Fragment } from 'preact'
import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { ModelName } from './ModelName'
import {
  agentCompletenessLabel,
  agentOwnerLabel,
  agentScopeLabel,
  agentStateLabel,
  filterAgentEnvironmentSections,
  groupAgentEnvironmentItems,
  type AgentEnvironmentInventory,
  type AgentEnvironmentItem,
} from './agentEnvironment'
import type { Session } from './types'
import { harnessDisplayName, isAgentBackend } from './harnessRegistry'
import { sessionDisplayName } from './sessionNames'

function itemTitle(item: AgentEnvironmentItem): string {
  const details = item.meta.map(meta => `${meta.label}: ${meta.value}`)
  return [
    item.group ? `${item.group}` : '',
    item.description,
    ...details,
    item.source_label ? `Source: ${item.source_label}` : '',
  ]
    .filter(Boolean)
    .join('\n')
}

/**
 * The Agent tab's two inventory segments share one fetch.
 *
 * Config and Tools are two readings of a single `/agent-environment` payload, and only one
 * of them is mounted at a time — so without this, every toggle between them would be a
 * fresh round trip for a document the other segment had just read, and each would draw its
 * own Rescan over the other's stale copy. The daemon caches this response too
 * (`agent_environment.py`); this is the client half of the same idea.
 *
 * A module-scoped store rather than a context provider because there is exactly one drawer
 * and the two consumers are siblings in the same pane. `key` folds in everything the fetch
 * depends on, so a session change, a cwd change, or a new agent run is a different entry
 * rather than a stale one.
 */
type EnvironmentEntry = {
  inventory: AgentEnvironmentInventory | null
  error: string
  loading: boolean
  /** Guards against an in-flight response for a key nobody is looking at any more. */
  generation: number
}

const ENVIRONMENT_CACHE = new Map<string, EnvironmentEntry>()
const ENVIRONMENT_LISTENERS = new Set<() => void>()
let environmentGeneration = 0

const environmentKey = (session: Session): string =>
  [session.id, session.runtime_cwd || '', session.run_cwd || '', session.agent_run_seq ?? ''].join('\0')

function publishEnvironment(key: string, entry: EnvironmentEntry) {
  ENVIRONMENT_CACHE.set(key, entry)
  // Bounded: the drawer only ever reads the focused session, and a fleet of long-lived
  // sessions would otherwise accumulate one payload each for the life of the page.
  if (ENVIRONMENT_CACHE.size > 8) {
    const oldest = ENVIRONMENT_CACHE.keys().next().value
    if (oldest !== undefined && oldest !== key) ENVIRONMENT_CACHE.delete(oldest)
  }
  for (const listener of ENVIRONMENT_LISTENERS) listener()
}

async function fetchEnvironment(key: string, sessionId: string, refresh: boolean) {
  const mine = ++environmentGeneration
  const previous = ENVIRONMENT_CACHE.get(key)
  publishEnvironment(key, { inventory: previous?.inventory ?? null, error: '', loading: true, generation: mine })
  try {
    const result = await api<AgentEnvironmentInventory>(
      'GET',
      `/api/sessions/${encodeURIComponent(sessionId)}/agent-environment${refresh ? '?refresh=1' : ''}`,
      undefined,
      { timeoutMs: 10_000 },
    )
    if (ENVIRONMENT_CACHE.get(key)?.generation !== mine) return
    publishEnvironment(key, { inventory: result, error: '', loading: false, generation: mine })
  } catch (cause) {
    if (ENVIRONMENT_CACHE.get(key)?.generation !== mine) return
    publishEnvironment(key, {
      inventory: null,
      error: cause instanceof Error ? cause.message : String(cause),
      loading: false,
      generation: mine,
    })
  }
}

function useAgentEnvironment(session: Session | null) {
  const isAgent = isAgentBackend(session?.backend)
  const key = session && isAgent ? environmentKey(session) : ''
  const [, bump] = useState(0)
  useEffect(() => {
    const listener = () => bump(value => value + 1)
    ENVIRONMENT_LISTENERS.add(listener)
    return () => { ENVIRONMENT_LISTENERS.delete(listener) }
  }, [])
  useEffect(() => {
    if (!key || !session) return
    if (ENVIRONMENT_CACHE.has(key)) return
    void fetchEnvironment(key, session.id, false)
  }, [key])
  const entry = key ? ENVIRONMENT_CACHE.get(key) : undefined
  return {
    isAgent,
    inventory: entry?.inventory ?? null,
    error: entry?.error ?? '',
    loading: entry?.loading ?? !!key,
    rescan: () => { if (key && session) void fetchEnvironment(key, session.id, true) },
  }
}

/** The header both segments carry: which session, which harness, and one Rescan. */
function EnvironmentHeader({ session, inventory, loading, onRescan }: {
  session: Session
  inventory: AgentEnvironmentInventory | null
  loading: boolean
  onRescan: () => void
}) {
  return <header class="agent-environment-header">
    <div>
      <strong>{sessionDisplayName(session) || session.id}</strong>
      <span>{inventory ? `${harnessDisplayName(inventory.backend)}${inventory.runtime.version ? ` · ${inventory.runtime.version}` : ''}` : harnessDisplayName(session.backend)}</span>
    </div>
    <button
      disabled={loading}
      title="Rescan passive configuration sources. This never connects MCP servers or executes plugin and hook code."
      onClick={onRescan}
    >{loading ? 'Scanning…' : 'Rescan'}</button>
  </header>
}

/**
 * The empty states both segments share.
 *
 * Returns `null` when there is something to draw. Both cases are reachable even though
 * `drawerSegments.ts` gates these segments on an agent session: the gate picks which
 * segment to *show*, and a session can go away or change backend under a mounted one.
 */
function environmentGate(session: Session | null, isAgent: boolean) {
  if (!session) {
    return <>
      <p class="drawer-status">no terminal focused</p>
      <p class="drawer-empty">Focus an agent session to inspect its environment.</p>
    </>
  }
  if (!isAgent) {
    return <>
      <p class="drawer-status">{sessionDisplayName(session) || session.id} · shell</p>
      <p class="drawer-empty">The focused terminal is a shell, not an agent harness.</p>
    </>
  }
  return null
}

/**
 * Agent → Config: what this session is running as, and what decided that.
 *
 * The runtime block plus the two resolved-configuration sections (policies, feature flags)
 * and the sources and diagnostics behind them. The split from Tools is by question, not by
 * size: this segment answers "how is it set up", Tools answers "what can it do", and the
 * one column they used to share made a reader scroll past a hooks inventory to check a
 * model name.
 */
export function AgentConfigTab({ session }: { session: Session | null }) {
  const { inventory, error, loading, isAgent, rescan } = useAgentEnvironment(session)
  const gate = environmentGate(session, isAgent)
  if (gate || !session) return gate
  const configSections = (inventory?.sections || []).filter(section => CONFIG_SECTION_IDS.has(section.id))
  const changedSources = inventory?.sources.filter(source => source.changed_after_start) || []

  return <div class="agent-environment">
    <EnvironmentHeader session={session} inventory={inventory} loading={loading} onRescan={rescan} />
    {!inventory && !error && <p class="drawer-empty">Reading the agent environment…</p>}
    {error && <p class="drawer-empty">Agent environment could not be read: {error}</p>}
    {inventory && <>
      <section class="agent-runtime" aria-label="Agent runtime">
        <div><span>Executable</span><b>{inventory.runtime.executable}</b></div>
        <div><span>Model</span><b><ModelName model={inventory.runtime.model} fallback="CLI default"/></b></div>
        <div><span>Working directory</span><b title={inventory.cwd}>{inventory.cwd}</b></div>
        {inventory.runtime.options.map(option => <div key={`${option.label}:${option.value}`}><span>{option.label}</span><b>{option.value}</b></div>)}
        {!!inventory.runtime.modes.length && <div><span>Modes</span><b>{inventory.runtime.modes.join(', ')}</b></div>}
      </section>

      <div class="agent-environment-summary">
        <span>{inventory.sources.length} sources</span>
        {!!changedSources.length && <span class="warn">{changedSources.length} changed since load</span>}
      </div>

      <div class="agent-environment-sections">
        {configSections.map(section => <EnvironmentSection key={section.id} section={section} query="" open />)}

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

/** Agent → Tools: everything this session can actually invoke or that runs on its behalf. */
export function AgentToolsTab({ session }: { session: Session | null }) {
  const { inventory, error, loading, isAgent, rescan } = useAgentEnvironment(session)
  const [query, setQuery] = useState('')
  useEffect(() => setQuery(''), [session?.id])
  const gate = environmentGate(session, isAgent)
  const capabilitySections = useMemo(
    () => filterAgentEnvironmentSections(
      (inventory?.sections || []).filter(section => !CONFIG_SECTION_IDS.has(section.id)),
      query,
    ),
    [inventory, query],
  )
  if (gate || !session) return gate
  const itemCount = (inventory?.sections || [])
    .filter(section => !CONFIG_SECTION_IDS.has(section.id))
    .reduce((total, section) => total + section.items.length, 0)

  return <div class="agent-environment">
    <EnvironmentHeader session={session} inventory={inventory} loading={loading} onRescan={rescan} />
    {!inventory && !error && <p class="drawer-empty">Reading the agent environment…</p>}
    {error && <p class="drawer-empty">Agent environment could not be read: {error}</p>}
    {inventory && <>
      <div class="agent-environment-summary"><span>{itemCount} items</span></div>

      {itemCount > 12 && <input
        class="agent-environment-filter"
        type="search"
        placeholder="Filter tools, MCP, plugins…"
        aria-label="Filter agent tools"
        value={query}
        onInput={event => setQuery((event.target as HTMLInputElement).value)}
      />}

      <div class="agent-environment-sections">
        {query && !capabilitySections.length && <p class="drawer-empty">No capability matches “{query}”.</p>}
        {capabilitySections.map(section => <EnvironmentSection key={section.id} section={section} query={query} open={!!query} />)}
      </div>
    </>}
  </div>
}

/** Sections that answer "how is this configured" rather than "what can it do". */
const CONFIG_SECTION_IDS = new Set(['policies', 'features'])

function EnvironmentSection({ section, query, open }: {
  section: AgentEnvironmentInventory['sections'][number]
  query: string
  open: boolean
}) {
  return <details open={open}>
    <summary>
      <span>{section.label}</span>
      <small>{section.items.length}{query ? ` / ${section.total}` : ''}</small>
      <em title={section.note}>{agentCompletenessLabel(section.completeness)}</em>
    </summary>
    <div class="agent-environment-items">
      {!section.items.length && <p class="drawer-empty">Nothing configured or discoverable.</p>}
      {/* Grouped runs, not a flat list: a Hooks section named by event repeated
          the event on every row and answered nothing about what each one runs. */}
      {groupAgentEnvironmentItems(section.items).map(run => <Fragment key={run.key || section.id}>
        {run.key && <h4 class="agent-environment-group"><span>{run.key}</span><small>{run.items.length}</small></h4>}
        {run.items.map(item => <article key={item.id} title={itemTitle(item)}>
          <header>
            <strong>{item.name}</strong>
            {item.owner && <span class="agent-owner">{agentOwnerLabel(item.owner)}</span>}
            <span class={`agent-state state-${item.state}`}>{agentStateLabel(item.state)}</span>
          </header>
          {item.description && <p>{item.description}</p>}
          <div class="agent-item-facts">
            <span>{agentScopeLabel(item.scope)}</span>
            <span>{item.origin}</span>
            {item.changed_after_start && <span class="warn">changed since load</span>}
          </div>
          {!!item.meta.length && <dl>{item.meta.map(meta => <div key={`${meta.label}:${meta.value}`}><dt>{meta.label}</dt><dd title={meta.value}>{meta.value}</dd></div>)}</dl>}
        </article>)}
      </Fragment>)}
      {section.truncated && <p class="agent-environment-note">Showing {section.items.length} of {section.total} entries.</p>}
      <p class="agent-environment-note">{section.note}</p>
    </div>
  </details>
}
