import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import {
  AGENT_CONTEXT_SYNC_OPTIONS,
  backupAction,
  comparisonLabel,
  statusLabel,
  type AgentContextBackup,
  type AgentContextDirection,
  type AgentContextInventory,
  type AgentContextRead,
  type AgentContextSource,
  type AgentContextSyncPreview,
} from './agentContext'
import type { Project, Session } from './types'

const REQUEST_TIMEOUT_MS = 12_000

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  return `${Math.max(1, Math.round(value / 1024))} KiB`
}

function formatTime(value: number | null): string {
  if (!value) return ''
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    .format(new Date(value * 1000))
}

function errorMessage(cause: unknown): string {
  const error = cause as ApiError
  if (error?.detail?.code === 'revision_conflict') {
    return 'The instruction files changed after this preview. Rescan and review the new diff.'
  }
  return cause instanceof Error ? cause.message : String(cause)
}

function SourceRow({ item, selected, focusedBackend, runStartedAt, onOpen }: {
  item: AgentContextSource
  selected: boolean
  focusedBackend?: Session['backend']
  runStartedAt?: number
  onOpen: (id: string) => void
}) {
  const available = item.status === 'available'
  const focused = focusedBackend === item.provider
  const newerThanRun = !!runStartedAt && !!item.modified_at && item.modified_at > runStartedAt
  return <button
    class={`agent-context-source ${selected ? 'selected' : ''}`}
    disabled={!available}
    title={item.detail || (available ? `View ${item.label}` : statusLabel(item.status))}
    onClick={() => onOpen(item.id)}
  >
    <span class="agent-context-source-main">
      <strong>{item.label}</strong>
      <i class={`context-state ${item.status}`}>{statusLabel(item.status)}</i>
    </span>
    {(focused && item.kind === 'instructions' || newerThanRun || item.changed_since_start) && <span class="agent-context-source-flags">
      {focused && item.kind === 'instructions' && <i class="context-state focused">focused agent</i>}
      {newerThanRun && <i class="context-state changed">newer than run</i>}
      {item.changed_since_start && <i class="context-state changed-daemon">changed this daemon run</i>}
    </span>}
    {available
      ? <small>{formatBytes(item.size)}{item.modified_at ? ` · ${formatTime(item.modified_at)}` : ''}</small>
      : <small>{item.detail || 'No readable file is available.'}</small>}
  </button>
}

export function AgentContextTab({ project, session }: { project?: Project; session?: Session | null }) {
  const [inventory, setInventory] = useState<AgentContextInventory | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [selected, setSelected] = useState<AgentContextRead | null>(null)
  const [sourceNonce, setSourceNonce] = useState(0)
  const [preview, setPreview] = useState<AgentContextSyncPreview | null>(null)
  const [restoreConfirm, setRestoreConfirm] = useState<AgentContextBackup | null>(null)
  const [busy, setBusy] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const generation = useRef(0)
  const projectId = project?.id || ''

  const refresh = useCallback(async () => {
    if (!projectId) { setInventory(null); return }
    const mine = ++generation.current
    setLoading(true)
    try {
      const next = await api<AgentContextInventory>(
        'GET',
        `/api/projects/${projectId}/agent-context`,
        undefined,
        { timeoutMs: REQUEST_TIMEOUT_MS },
      )
      if (mine !== generation.current) return
      setInventory(next)
      const readable = [
        ...next.instructions.items,
        ...next.providers.flatMap(provider => provider.items),
      ].filter(item => item.status === 'available')
      setSelectedId(current => readable.some(item => item.id === current)
        ? current
        : readable[0]?.id || '')
      setSourceNonce(value => value + 1)
      setError('')
    } catch (cause) {
      if (mine === generation.current) setError(errorMessage(cause))
    } finally {
      if (mine === generation.current) setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    generation.current += 1
    setInventory(null)
    setSelectedId('')
    setSelected(null)
    setPreview(null)
    setRestoreConfirm(null)
    setMessage('')
    setError('')
    void refresh()
  }, [refresh])

  useEffect(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ projectId?: string }>).detail
      if (!detail?.projectId || detail.projectId === projectId) void refresh()
    }
    const filesChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ projectId?: string; paths?: string[] }>).detail
      if (detail?.projectId !== projectId) return
      if ((detail.paths || []).some(path => /(^|[/\\])(CLAUDE|AGENTS)\.md$/i.test(path))) {
        void refresh()
      }
    }
    window.addEventListener('mux:agent-context-changed', changed)
    window.addEventListener('mux:project-files-changed', filesChanged)
    window.addEventListener('mux:events-connected', changed)
    return () => {
      window.removeEventListener('mux:agent-context-changed', changed)
      window.removeEventListener('mux:project-files-changed', filesChanged)
      window.removeEventListener('mux:events-connected', changed)
    }
  }, [projectId, refresh])

  useEffect(() => {
    if (!projectId || !selectedId) { setSelected(null); return }
    let alive = true
    setSelected(null)
    void api<AgentContextRead>(
      'GET',
      `/api/projects/${projectId}/agent-context/sources/${encodeURIComponent(selectedId)}`,
      undefined,
      { timeoutMs: REQUEST_TIMEOUT_MS },
    ).then(value => { if (alive) { setSelected(value); setError('') } })
      .catch(cause => { if (alive) setError(errorMessage(cause)) })
    return () => { alive = false }
  }, [projectId, selectedId, sourceNonce])

  const currentRevision = useMemo(() => {
    const values = new Map<string, string>()
    for (const item of inventory?.instructions.items || []) {
      if (item.revision) values.set(item.label, item.revision)
    }
    return values
  }, [inventory])

  if (!project) {
    return <>
      <p class="drawer-status">no Project selected</p>
      <p class="drawer-empty">Agent context belongs to a Project. Select one to inspect its instruction and memory files.</p>
    </>
  }

  const openPreview = async (direction: AgentContextDirection) => {
    setBusy(`preview:${direction}`)
    setMessage('')
    setRestoreConfirm(null)
    try {
      setPreview(await api<AgentContextSyncPreview>(
        'POST',
        `/api/projects/${project.id}/agent-context/sync/preview`,
        { direction },
        { timeoutMs: REQUEST_TIMEOUT_MS },
      ))
      setError('')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy('')
    }
  }

  const commitSync = async () => {
    if (!preview) return
    setBusy('sync')
    try {
      const result = await api<{ target: string; revision: string }>(
        'POST',
        `/api/projects/${project.id}/agent-context/sync`,
        {
          direction: preview.direction,
          source_revision: preview.source.revision,
          target_revision: preview.target.revision,
        },
        { timeoutMs: REQUEST_TIMEOUT_MS },
      )
      setMessage(`${result.target} now matches ${preview.source.label}. A restore point was saved.`)
      setSelectedId(result.target === 'CLAUDE.md' ? 'instruction:claude' : 'instruction:codex')
      setPreview(null)
      await refresh()
    } catch (cause) {
      setError(errorMessage(cause))
      if ((cause as ApiError)?.detail?.code === 'revision_conflict') await refresh()
    } finally {
      setBusy('')
    }
  }

  const restore = async (backup: AgentContextBackup) => {
    const targetRevision = currentRevision.get(backup.target)
    if (!targetRevision) return
    setBusy(`restore:${backup.id}`)
    try {
      await api(
        'POST',
        `/api/projects/${project.id}/agent-context/restore`,
        { backup_id: backup.id, target_revision: targetRevision },
        { timeoutMs: REQUEST_TIMEOUT_MS },
      )
      setMessage(`${backup.target} was restored. The replaced state was also backed up.`)
      setRestoreConfirm(null)
      await refresh()
    } catch (cause) {
      setError(errorMessage(cause))
      await refresh()
    } finally {
      setBusy('')
    }
  }

  const instructions = inventory?.instructions.items || []
  const sourceExists = new Map(instructions.map(item => [item.label, item.status === 'available']))
  const focusedAgent = session && session.project_id === project.id && session.backend !== 'shell'
    ? session
    : null
  const focusCwd = focusedAgent?.runtime_cwd_live && focusedAgent.runtime_cwd
    ? focusedAgent.runtime_cwd
    : focusedAgent?.run_cwd || focusedAgent?.cwd

  return <div class="agent-context">
    <header class="agent-context-header">
      <div>
        <strong>Agent Context</strong>
        <small title={focusCwd || project.root}>
          {focusedAgent
            ? `${focusedAgent.backend} focused · ${focusCwd || project.root}`
            : `${project.name} · Project inventory`}
        </small>
      </div>
      <button disabled={loading || !!busy} onClick={() => void refresh()}>{loading ? 'scanning…' : 'rescan'}</button>
    </header>

    {(error || message) && <p class={`agent-context-notice ${error ? 'error' : ''}`} aria-live="polite">
      {error || message}
    </p>}

    <section class="agent-context-section">
      <header>
        <h4>Project instructions</h4>
        {inventory && <i class={`context-comparison ${inventory.instructions.comparison}`}>
          {comparisonLabel(inventory.instructions.comparison)}
        </i>}
      </header>
      <p>Root instruction files read by Claude and Codex. Viewing never edits them.</p>
      <div class="agent-context-sources">
        {instructions.map(item => <SourceRow
          key={item.id}
          item={item}
          selected={selectedId === item.id}
          focusedBackend={focusedAgent?.backend}
          runStartedAt={focusedAgent?.agent_run_started_at}
          onOpen={setSelectedId}
        />)}
      </div>
      <div class="agent-context-sync">
        <strong>Manual whole-file sync</strong>
        <small>Choose a direction, review the diff, then confirm the overwrite.</small>
        {AGENT_CONTEXT_SYNC_OPTIONS.map(option => <button
          key={option.direction}
          disabled={!sourceExists.get(option.source) || !!busy}
          onClick={() => void openPreview(option.direction)}
        >
          {busy === `preview:${option.direction}` ? 'preparing…' : `${option.source} → ${option.target}`}
        </button>)}
      </div>
    </section>

    {preview && <section class="agent-context-confirm" aria-label="Confirm instruction sync">
      <header>
        <strong>Overwrite {preview.target.label}?</strong>
        <small>{preview.source.label} will replace the entire file. A restore point is created first.</small>
      </header>
      {preview.in_sync
        ? <p>The normalized contents already match; there is nothing to copy.</p>
        : <pre>{preview.diff || '(The destination is empty.)'}</pre>}
      <footer>
        <button onClick={() => setPreview(null)}>cancel</button>
        <button class="danger" disabled={preview.in_sync || busy === 'sync'} onClick={() => void commitSync()}>
          {busy === 'sync' ? 'overwriting…' : `overwrite ${preview.target.label}`}
        </button>
      </footer>
    </section>}

    {(inventory?.providers || []).map(provider => <section class="agent-context-section" key={provider.id}>
      <header>
        <h4>{provider.label} learned memory</h4>
        {focusedAgent?.backend === provider.id && <i class="context-state focused">focused agent</i>}
        <i class={`context-state ${provider.status}`}>{statusLabel(provider.status)}</i>
      </header>
      <p>{provider.detail}</p>
      {provider.items.length > 0 && <div class="agent-context-sources">
        {provider.items.map(item => <SourceRow
          key={item.id}
          item={item}
          selected={selectedId === item.id}
          focusedBackend={focusedAgent?.backend}
          runStartedAt={focusedAgent?.agent_run_started_at}
          onOpen={setSelectedId}
        />)}
      </div>}
      {provider.truncated && <p>Only the first bounded set of memory files is shown.</p>}
    </section>)}

    {selectedId && <section class="agent-context-viewer">
      <header>
        <strong>{selected?.source.label || 'Loading…'}</strong>
        {selected && <small>{selected.source.provider} · read-only · {formatBytes(selected.source.size)}</small>}
      </header>
      {selected ? <pre tabIndex={0}>{selected.text}</pre> : <p>Reading source…</p>}
    </section>}

    {(inventory?.backups.length || 0) > 0 && <section class="agent-context-section agent-context-backups">
      <header><h4>Restore points</h4></header>
      <p>Created automatically before a manual sync or restore.</p>
      {inventory!.backups.map(backup => {
        const confirming = restoreConfirm?.id === backup.id
        const canRestore = currentRevision.has(backup.target)
        return <div class="agent-context-backup" key={backup.id}>
          <span>
            <strong>{backupAction(backup)}</strong>
            <small>{formatTime(backup.created_at)} · {backup.existed ? formatBytes(backup.size) : 'file did not exist'}</small>
          </span>
          {confirming
            ? <span class="agent-context-backup-confirm">
              <button onClick={() => setRestoreConfirm(null)}>cancel</button>
              <button class="danger" disabled={!canRestore || !!busy} onClick={() => void restore(backup)}>
                {busy === `restore:${backup.id}` ? 'restoring…' : 'restore now'}
              </button>
            </span>
            : <button disabled={!canRestore || !!busy} onClick={() => { setRestoreConfirm(backup); setPreview(null) }}>restore…</button>}
        </div>
      })}
    </section>}
  </div>
}
