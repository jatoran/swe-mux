import { isComposingKey } from './inputComposition.ts'
import { dateTimeFormatter } from './localization/index.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { mobileLayout } from './deviceMode'
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import {
  AGENT_CONTEXT_DESKTOP_MENU_QUERY,
  AGENT_CONTEXT_DISCLOSURE_DEFAULTS,
  agentContextSourceMenuEnabled,
  backupAction,
  canonicalLinkLabel,
  comparisonLabel,
  memoryFileCount,
  statusLabel,
  type AgentContextBackup,
  type AgentContextDirection,
  type AgentContextInventory,
  type AgentContextLinkPreview,
  type AgentContextRead,
  type AgentContextSource,
  type AgentContextSyncPreview,
} from './agentContext'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import { useModalFocus } from './modalFocus'
import type { Project, Session } from './types'

const REQUEST_TIMEOUT_MS = 12_000

/**
 * Which source each Project was last reading, device-local.
 *
 * The tab used to open on whichever file happened to sort first - in practice the focused
 * harness's own CLAUDE.md or AGENTS.md - which made every visit look like the tab had
 * decided something for you, and made the viewer's contents ambiguous at a glance: a body
 * with no obvious owner directly under a list of files reads as part of the list.
 * Nothing is selected until you pick something, and what you picked is what you get back.
 *
 * Per Project because the choice is about a repository's files, and device-local (like the
 * drawer's own arrangement) because it is a reading position rather than a setting.
 */
const SOURCE_SELECTION_KEY = 'mux.agentContext.source.v1'

function readSourceSelections(): Record<string, string> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(SOURCE_SELECTION_KEY) || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
    )
  } catch {
    return {}
  }
}

function rememberSourceSelection(projectId: string, sourceId: string) {
  if (!projectId) return
  const next = readSourceSelections()
  if (sourceId) next[projectId] = sourceId
  else delete next[projectId]
  try { localStorage.setItem(SOURCE_SELECTION_KEY, JSON.stringify(next)) }
  catch { /* a reading position is best effort */ }
}

function formatBytes(value: number): string {
  if (value < 1024) return t("AgentContextTab.4d41026686", { value: value })
  return t("AgentContextTab.e419c42726", { value1: Math.max(1, Math.round(value / 1024)) })
}

function formatTime(value: number | null): string {
  if (!value) return ''
  return dateTimeFormatter({ dateStyle: 'medium', timeStyle: 'short' })
    .format(new Date(value * 1000))
}

function errorMessage(cause: unknown): string {
  const error = cause as ApiError
  if (error?.detail?.code === 'revision_conflict') {
    return t("AgentContextTab.6a5a3ed321")
  }
  return cause instanceof Error ? cause.message : String(cause)
}

type SourceMenu = { item: AgentContextSource; x: number; y: number }

/**
 * The last inventory read per Project, so a remount is not a cold read.
 *
 * The Agent tab is not `keepMounted`: switching to another drawer tab and back unmounts
 * and remounts this one, and every remount was a fresh scan of every instruction file
 * with an empty pane while it ran. The daemon caches this response too
 * (`agent_context.py`); this is the client half of the same idea, and the same one
 * `AgentEnvironmentTab`'s `ENVIRONMENT_CACHE` already applies to the sibling segment.
 *
 * Module-scoped rather than lifted into a provider because there is exactly one drawer,
 * and bounded because a fleet of Projects would otherwise retain one payload each for
 * the life of the page. Every write to the underlying files reaches this through
 * `agent_context_changed` and the filtered `project_files_changed`, which refresh and
 * overwrite the entry; the rescan button bypasses both ends.
 */
const INVENTORY_CACHE = new Map<string, AgentContextInventory>()
const INVENTORY_CACHE_LIMIT = 8

function rememberInventory(projectId: string, value: AgentContextInventory) {
  if (!projectId) return
  INVENTORY_CACHE.set(projectId, value)
  if (INVENTORY_CACHE.size > INVENTORY_CACHE_LIMIT) {
    const oldest = INVENTORY_CACHE.keys().next().value
    if (oldest !== undefined && oldest !== projectId) INVENTORY_CACHE.delete(oldest)
  }
}

function SourceRow({ item, selected, focusedBackend, runStartedAt, onOpen, onRevealMenu }: {
  item: AgentContextSource
  selected: boolean
  focusedBackend?: Session['backend']
  runStartedAt?: number
  onOpen: (id: string) => void
  onRevealMenu: (item: AgentContextSource, x: number, y: number) => void
}) {
  const available = item.status === 'available'
  const focused = focusedBackend === item.provider
  const newerThanRun = !!runStartedAt && !!item.modified_at && item.modified_at > runStartedAt
  return <button
    class={`agent-context-source ${selected ? 'selected' : ''}`}
    aria-disabled={!available}
    title={t("AgentContextTab.1a69eac848", { value1: item.detail || (available ? `View ${item.label}` : statusLabel(item.status)), value2: (item.revealable) ? 'yes' : 'no' })}
    onClick={() => available && onOpen(item.id)}
    onContextMenu={event => {
      if (mobileLayout() || !agentContextSourceMenuEnabled(item, window.matchMedia(AGENT_CONTEXT_DESKTOP_MENU_QUERY).matches)) return
      event.preventDefault()
      event.stopPropagation()
      onRevealMenu(item, event.clientX, event.clientY)
    }}
  >
    <span class="agent-context-source-main">
      <strong>{item.label}</strong>
      <i class={`context-state ${item.status}`}>{statusLabel(item.status)}</i>
    </span>
    {(focused && item.kind === 'instructions' || newerThanRun || item.changed_since_start) && <span class="agent-context-source-flags">
      {focused && item.kind === 'instructions' && <i class="context-state focused">{t("AgentContextTab.25f9f86c76")}</i>}
      {newerThanRun && <i class="context-state changed">{t("AgentContextTab.7bce069b16")}</i>}
      {item.changed_since_start && <i class="context-state changed-daemon">{t("AgentContextTab.7e3ae20ec4")}</i>}
    </span>}
    {available ? <small>{formatBytes(item.size)}{item.modified_at ? t("AgentContextTab.f6b53f9c8a", { value1: formatTime(item.modified_at) }) : ''}</small> : <small>{item.detail || t("AgentContextTab.cfae9f3c91")}</small>}
  </button>
}

export function AgentContextTab({ project, session }: { project?: Project; session?: Session | null }) {
  const [inventory, setInventory] = useState<AgentContextInventory | null>(null)
  const [selectedId, setSelectedIdState] = useState('')
  const [selected, setSelected] = useState<AgentContextRead | null>(null)
  const [sourceNonce, setSourceNonce] = useState(0)
  const [preview, setPreview] = useState<AgentContextSyncPreview | null>(null)
  const [linkPreview, setLinkPreview] = useState<AgentContextLinkPreview | null>(null)
  const [unlinkConfirm, setUnlinkConfirm] = useState<AgentContextSource | null>(null)
  const [restoreConfirm, setRestoreConfirm] = useState<AgentContextBackup | null>(null)
  const [syncOpen, setSyncOpen] = useState(false)
  const [sourceMenu, setSourceMenu] = useState<SourceMenu | null>(null)
  const [busy, setBusy] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const generation = useRef(0)
  const syncPanel = useRef<HTMLElement>(null)
  const sourceMenuPanel = useRef<HTMLDivElement>(null)
  const projectId = project?.id || ''
  const closeSync = useCallback(() => {
    setSyncOpen(false)
    setPreview(null)
    setRestoreConfirm(null)
    setLinkPreview(null)
    setUnlinkConfirm(null)
  }, [])
  useModalFocus(syncPanel, closeSync, syncOpen)

  useEffect(() => {
    if (!sourceMenu) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const frame = requestAnimationFrame(() => sourceMenuPanel.current?.querySelector<HTMLButtonElement>('button')?.focus())
    const dismiss = () => setSourceMenu(null)
    const key = (event: KeyboardEvent) => {
      if (isComposingKey(event)) return

      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopImmediatePropagation()
      dismiss()
    }
    window.addEventListener('mousedown', dismiss)
    window.addEventListener('blur', dismiss)
    window.addEventListener('keydown', key, true)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('mousedown', dismiss)
      window.removeEventListener('blur', dismiss)
      window.removeEventListener('keydown', key, true)
      previous?.focus()
    }
  }, [sourceMenu])

  const refresh = useCallback(async (rescan = false) => {
    if (!projectId) { setInventory(null); return }
    const mine = ++generation.current
    setLoading(true)
    try {
      const next = await api<AgentContextInventory>(
        'GET',
        `/api/projects/${projectId}/agent-context${rescan ? '?refresh=1' : ''}`,
        undefined,
        { timeoutMs: REQUEST_TIMEOUT_MS },
      )
      if (mine !== generation.current) return
      rememberInventory(projectId, next)
      setInventory(next)
      const readable = [
        ...next.instructions.items,
        ...next.global_instructions.items,
        ...next.providers.flatMap(provider => provider.items),
      ].filter(item => item.status === 'available')
      // Restore, never choose. A stored id that no longer names a readable file is
      // dropped; the empty state is a legitimate resting place, and falling back to
      // `readable[0]` is what made the tab look like it had opened a file on your behalf.
      setSelectedIdState(current => {
        const remembered = current || readSourceSelections()[projectId] || ''
        return readable.some(item => item.id === remembered) ? remembered : ''
      })
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
    // Seeded from the last reading of *this* Project, so a remount draws immediately and
    // the fetch below replaces it. `null` for a Project never read here, which is the
    // "scanning…" state and the only time this pane is legitimately empty.
    setInventory(INVENTORY_CACHE.get(projectId) || null)
    // Seeded from storage rather than cleared: `refresh` below validates it against what
    // is actually readable, so an id for a file that has since gone still resolves to the
    // empty state without a flash of the wrong body.
    setSelectedIdState(readSourceSelections()[projectId] || '')
    setSelected(null)
    setPreview(null)
    setRestoreConfirm(null)
    setSyncOpen(false)
    setSourceMenu(null)
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

  const setSelectedId = useCallback((value: string) => {
    setSelectedIdState(value)
    rememberSourceSelection(projectId, value)
  }, [projectId])

  const currentRevision = useMemo(() => {
    const values = new Map<string, string>()
    for (const item of inventory?.instructions.items || []) {
      if (item.revision) values.set(item.label, item.revision)
    }
    return values
  }, [inventory, uiLocale.value, catalogRevision.value])

  if (!project) {
    return <>
      <p class="drawer-status">{t("AgentContextTab.b4b1fa111c")}</p>
      <p class="drawer-empty">{t("AgentContextTab.0d34ffe109")}</p>
    </>
  }

  const openPreview = async (direction: AgentContextDirection) => {
    setBusy(`preview:${direction}`)
    setMessage('')
    setRestoreConfirm(null)
    setLinkPreview(null)
    setUnlinkConfirm(null)
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
      setMessage(t("AgentContextTab.05f4fedc21", { target: result.target, label: preview.source.label }))
      setSelectedId(inventory?.sync_options.find(option => option.direction === preview.direction)?.target_id || '')
      setPreview(null)
      await refresh()
    } catch (cause) {
      setError(errorMessage(cause))
      if ((cause as ApiError)?.detail?.code === 'revision_conflict') await refresh()
    } finally {
      setBusy('')
    }
  }

  const openLinkPreview = async (direction: AgentContextDirection) => {
    setBusy(`link-preview:${direction}`)
    setMessage('')
    setPreview(null)
    setRestoreConfirm(null)
    setUnlinkConfirm(null)
    try {
      setLinkPreview(await api<AgentContextLinkPreview>(
        'POST',
        `/api/projects/${project.id}/agent-context/link/preview`,
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

  const commitLink = async () => {
    if (!linkPreview) return
    setBusy('link')
    try {
      const result = await api<{ target: string; revision: string }>(
        'POST',
        `/api/projects/${project.id}/agent-context/link`,
        {
          direction: linkPreview.direction,
          source_revision: linkPreview.source.revision,
          target_revision: linkPreview.target.revision,
        },
        { timeoutMs: REQUEST_TIMEOUT_MS },
      )
      setMessage(t("AgentContextTab.8ac1498432", { target: result.target, label: linkPreview.source.label }))
      setSelectedId(inventory?.sync_options.find(option => option.direction === linkPreview.direction)?.target_id || '')
      setLinkPreview(null)
      await refresh()
    } catch (cause) {
      setError(errorMessage(cause))
      if ((cause as ApiError)?.detail?.code === 'revision_conflict') await refresh()
    } finally {
      setBusy('')
    }
  }

  const commitUnlink = async (item: AgentContextSource) => {
    if (!item.revision) return
    setBusy(`unlink:${item.id}`)
    try {
      const result = await api<{ target: string; revision: string }>(
        'POST',
        `/api/projects/${project.id}/agent-context/unlink`,
        { source_id: item.id, target_revision: item.revision },
        { timeoutMs: REQUEST_TIMEOUT_MS },
      )
      setMessage(t("AgentContextTab.1e90392c68", { target: result.target }))
      setSelectedId(item.id)
      setUnlinkConfirm(null)
      await refresh()
    } catch (cause) {
      setError(errorMessage(cause))
      await refresh()
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
      setMessage(t("AgentContextTab.28c02098d0", { target: backup.target }))
      setRestoreConfirm(null)
      await refresh()
    } catch (cause) {
      setError(errorMessage(cause))
      await refresh()
    } finally {
      setBusy('')
    }
  }

  const revealSource = async (item: AgentContextSource) => {
    setSourceMenu(null)
    try {
      await api(
        'POST',
        `/api/projects/${project.id}/agent-context/sources/${encodeURIComponent(item.id)}/reveal`,
        undefined,
        { timeoutMs: REQUEST_TIMEOUT_MS },
      )
      setError('')
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  const instructions = inventory?.instructions.items || []
  const managedLinks = instructions.filter(item => item.link_target)
  const linkableSources = new Map(
    instructions.map(item => [item.id, item.status === 'available' && !item.link_target]),
  )
  const linkableTargets = new Map(
    instructions.map(item => [item.id, ['available', 'missing'].includes(item.status) && !item.link_target]),
  )
  const globalInstructions = inventory?.global_instructions.items || []
  const providers = inventory?.providers || []
  const memories = memoryFileCount(providers)
  const sourceExists = new Map(instructions.map(item => [item.label, item.status === 'available' && !item.link_target]))
  const focusedAgent = session && session.project_id === project.id && session.backend !== 'shell'
    ? session
    : null
  const focusCwd = focusedAgent?.runtime_cwd_live && focusedAgent.runtime_cwd
    ? focusedAgent.runtime_cwd
    : focusedAgent?.run_cwd || focusedAgent?.cwd

  return <div class="agent-context">
    <header class="agent-context-header">
      <div>
        <small title={focusCwd || project.root}>
          {focusedAgent ? t("AgentContextTab.eb34ec31ed", { backend: focusedAgent.backend, value2: focusCwd || project.root }) : t("AgentContextTab.cd9142b1ca", { name: project.name })}
        </small>
      </div>
      {/* The one read that skips both caches, on both ends. "Rescan" has to mean it: a
          stat signature cannot see a same-size rewrite landing in the same nanosecond,
          and this is what a reader presses when they believe they are looking at one. */}
      <button disabled={loading || !!busy} onClick={() => void refresh(true)}>{loading ? t("AgentContextTab.15fd21ae62") : t("AgentContextTab.2944692d97")}</button>
    </header>

    {(error || message) && <p class={`agent-context-notice ${error ? 'error' : ''}`} aria-live="polite">
      {error || message}
    </p>}

    <details
      key={`project-instructions:${project.id}`}
      class="agent-context-disclosure agent-context-instructions"
      open={AGENT_CONTEXT_DISCLOSURE_DEFAULTS.projectInstructions}
    >
      <summary>
        <span>{t("AgentContextTab.6205b4d88e")}</span>
        {inventory && <i class={`context-comparison ${inventory.instructions.comparison}`}>
          {comparisonLabel(inventory.instructions.comparison)}
        </i>}
      </summary>
      <div class="agent-context-disclosure-body">
        <div class="agent-context-disclosure-intro">
          <button disabled={!inventory || !!busy} onClick={() => setSyncOpen(true)}>{t("AgentContextTab.50c3fcb98b")}</button>
        </div>
        <div class="agent-context-sources">
          {instructions.map(item => <SourceRow
            key={item.id}
            item={item}
            selected={selectedId === item.id}
            focusedBackend={focusedAgent?.backend}
            runStartedAt={focusedAgent?.agent_run_started_at}
            onOpen={setSelectedId}
            onRevealMenu={(item, x, y) => setSourceMenu({ item, x, y })}
          />)}
        </div>
      </div>
    </details>

    <details
      key={`global-instructions:${project.id}`}
      class="agent-context-disclosure agent-context-instructions"
      open={AGENT_CONTEXT_DISCLOSURE_DEFAULTS.globalInstructions}
    >
      <summary><span>{t("AgentContextTab.4b8b3efd0b")}</span></summary>
      <div class="agent-context-disclosure-body">
        <p>{t("AgentContextTab.af8e707470")}</p>
        <div class="agent-context-sources">
          {globalInstructions.map(item => <SourceRow
            key={item.id}
            item={item}
            selected={selectedId === item.id}
            focusedBackend={focusedAgent?.backend}
            runStartedAt={focusedAgent?.agent_run_started_at}
            onOpen={setSelectedId}
            onRevealMenu={(item, x, y) => setSourceMenu({ item, x, y })}
          />)}
        </div>
      </div>
    </details>

    <details
      key={`memories:${project.id}`}
      class="agent-context-disclosure agent-context-memories"
      open={AGENT_CONTEXT_DISCLOSURE_DEFAULTS.memories}
    >
      <summary>
        <span>{t("AgentContextTab.e7218b5e4a")}</span>
        <i>{rich("AgentContextTab.22fb26c456", { memories: memories, value2: (memories === 1) ? 'yes' : 'no' })}</i>
      </summary>
      <div class="agent-context-memory-list">
        {providers.map(provider => <section class="agent-context-memory-provider" key={provider.id}>
          <header>
            <h4>{provider.label}</h4>
            {focusedAgent?.backend === provider.id && <i class="context-state focused">{t("AgentContextTab.25f9f86c76")}</i>}
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
              onRevealMenu={(item, x, y) => setSourceMenu({ item, x, y })}
            />)}
          </div>}
          {provider.truncated && <p>{rich("AgentContextTab.6e583b6d74", { length: provider.items.length, item_count: provider.item_count })}</p>}
        </section>)}
      </div>
    </details>

    {/* Always drawn, even with nothing selected. The viewer used to appear only once a
        file was open, so the tab's bottom edge moved as you clicked around and, worse, an
        open file's body butted straight against the memory list above it with no rule and
        no label between them - two different things sharing one continuous column. It is
        a labelled region now, with its own heading rule, whether or not it holds anything. */}
    <section class={`agent-context-viewer ${selectedId ? '' : 'empty'}`}>
      <header>
        <span class="agent-context-viewer-kicker">{t("AgentContextTab.324b134f57")}</span>
        <strong>{selectedId ? (selected?.source.label || t("AgentContextTab.ba3bbbe10d")) : t("AgentContextTab.26bfbd5c83")}</strong>
        {selectedId && selected && <small>{rich("AgentContextTab.b2de9a7c2e", { provider: selected.source.provider, value2: formatBytes(selected.source.size) })}</small>}
        {selectedId && <button class="agent-context-viewer-clear" title={t("AgentContextTab.6f1ca61c86")} onClick={() => setSelectedId('')}>×</button>}
      </header>
      {selectedId && (selected ? <pre tabIndex={0}>{selected.text}</pre> : <p>{t("AgentContextTab.8e9e4d2e37")}</p>)}
    </section>

    {sourceMenu && <div
      class="context-menu agent-context-source-menu"
      ref={element => { sourceMenuPanel.current = element; fitMenuInViewport(element) }}
      role="menu"
      aria-label={t("AgentContextTab.67a942d90b", { label: sourceMenu.item.label })}
      style={{
        left: clampContextMenuLeft(sourceMenu.x, window.innerWidth),
        top: Math.max(4, Math.min(sourceMenu.y, window.innerHeight - 80)),
      }}
      onMouseDown={event => event.stopPropagation()}
    >
      <div class="context-title"><strong>{sourceMenu.item.label}</strong></div>
      <button role="menuitem" onClick={() => void revealSource(sourceMenu.item)}>{t("AgentContextTab.4bbc7fc723")}</button>
    </div>}

    {syncOpen && <div class="modal-layer agent-context-sync-layer" onMouseDown={event => event.target === event.currentTarget && closeSync()}>
      <section ref={syncPanel} class="modal agent-context-sync-modal" role="dialog" aria-modal="true" aria-label={t("AgentContextTab.444b403821")}>
        <div class="modal-heading">
          <div><span>{t("AgentContextTab.ad0657f6eb")}</span><h2 data-heading-message="AgentContextTab.75c9be9efe">{t("AgentContextTab.75c9be9efe")}</h2></div>
          <button type="button" aria-label={t("AgentContextTab.9311a168f8")} onClick={closeSync}>×</button>
        </div>
        <div class="agent-context-sync-body">
          {(error || message) && <p class={`agent-context-notice ${error ? 'error' : ''}`} aria-live="polite">
            {error || message}
          </p>}
          <section class="agent-context-sync-card">
            <header>
              <div><strong>{t("AgentContextTab.acfaa3d4d6")}</strong><small>{project.root}</small></div>
              {inventory && <i class={`context-comparison ${inventory.instructions.comparison}`}>
                {comparisonLabel(inventory.instructions.comparison)}
              </i>}
            </header>
            <p>{t("AgentContextTab.da259c99fb")}</p>
            <div class="agent-context-sync">
              {(inventory?.sync_options || []).map(option => <button
                key={option.direction}
                disabled={!sourceExists.get(option.source) || managedLinks.length > 0 || !!busy}
                onClick={() => void openPreview(option.direction)}
              >
                {busy === `preview:${option.direction}` ? t("AgentContextTab.586466fe59") : t("AgentContextTab.56fcac7a82", { source: option.source, target: option.target })}
              </button>)}
            </div>
          </section>

          {preview && <section class="agent-context-confirm" aria-label={t("AgentContextTab.81223b1be7")}>
            <header>
              <strong>{rich("AgentContextTab.fa5eeb30f8", { label: preview.target.label })}</strong>
              <small>{rich("AgentContextTab.034bd68a3e", { label: preview.source.label })}</small>
            </header>
            {preview.in_sync ? <p>{t("AgentContextTab.9e154f7071")}</p> : <pre>{preview.diff || t("AgentContextTab.b6693d24f3")}</pre>}
            <footer>
              <button onClick={() => setPreview(null)}>{t("AgentContextTab.2374d91794")}</button>
              <button class="danger" disabled={preview.in_sync || busy === 'sync'} onClick={() => void commitSync()}>
                {busy === 'sync' ? t("AgentContextTab.2c5fde28bc") : t("AgentContextTab.85575f2d2c", { label: preview.target.label })}
              </button>
            </footer>
          </section>}

          <section class="agent-context-sync-card">
            <header>
              <div><strong>{t("AgentContextTab.d27dd3eef7")}</strong><small>{t("AgentContextTab.fb10228e58")}</small></div>
              {managedLinks.length > 0 && <i class="context-comparison linked">{t("AgentContextTab.bfda026e6c")}</i>}
            </header>
            <p>{t("AgentContextTab.d62803b138")}</p>
            {managedLinks.length > 0 ? managedLinks.map(item => {
                const confirming = unlinkConfirm?.id === item.id
                return <div class="agent-context-backup" key={`link:${item.id}`}>
                  <span>
                    <strong>{rich("AgentContextTab.4e8078eee1", { label: item.label, link_target: item.link_target })}</strong>
                    <small>{t("AgentContextTab.cdb7337e96")}</small>
                  </span>
                  {confirming ? <span class="agent-context-backup-confirm">
                      <button onClick={() => setUnlinkConfirm(null)}>{t("AgentContextTab.2374d91794")}</button>
                      <button class="danger" disabled={!!busy} onClick={() => void commitUnlink(item)}>
                        {busy === `unlink:${item.id}` ? t("AgentContextTab.a62eac49df") : t("AgentContextTab.3a12eb44bb")}
                      </button>
                    </span> : <button disabled={!!busy} onClick={() => {
                      setUnlinkConfirm(item)
                      setLinkPreview(null)
                      setPreview(null)
                      setRestoreConfirm(null)
                    }}>{t("AgentContextTab.3ff361099f")}</button>}
                </div>
              }) : <div class="agent-context-sync">
                {(inventory?.sync_options || []).map(option => <button
                  key={`link:${option.direction}`}
                  disabled={!linkableSources.get(option.source_id) || !linkableTargets.get(option.target_id) || !!busy}
                  title={t("AgentContextTab.bb3aa9853a", { target: option.target, source: option.source })}
                  onClick={() => void openLinkPreview(option.direction)}
                >
                  {busy === `link-preview:${option.direction}` ? t("AgentContextTab.24f20c2546") : canonicalLinkLabel(option)}
                </button>)}
              </div>}
            <p>{t("AgentContextTab.b47a18ff85")}</p>
          </section>

          {linkPreview && <section class="agent-context-confirm" aria-label={t("AgentContextTab.badc38e910")}>
            <header>
              <strong>{rich("AgentContextTab.8ab0bd02eb", { label: linkPreview.target.label, value2: linkPreview.source.label })}</strong>
              <small>{rich("AgentContextTab.b2e350a48b", { label: linkPreview.source.label, value2: linkPreview.target.label })}</small>
            </header>
            {linkPreview.already_linked ? <p>{t("AgentContextTab.58dc994a04")}</p> : linkPreview.diff ? <pre>{linkPreview.diff}</pre> : <p>{t("AgentContextTab.472d649c1f")}</p>}
            <p>{t("AgentContextTab.6ef54c2469")}</p>
            <footer>
              <button onClick={() => setLinkPreview(null)}>{t("AgentContextTab.2374d91794")}</button>
              <button class="danger" disabled={linkPreview.already_linked || busy === 'link'} onClick={() => void commitLink()}>
                {busy === 'link' ? t("AgentContextTab.d0833b268a") : t("AgentContextTab.7b2b9cbab8", { label: linkPreview.target.label })}
              </button>
            </footer>
          </section>}

          <section class="agent-context-sync-card agent-context-backups">
            <header><div><strong>{t("AgentContextTab.29f90b645a")}</strong><small>{t("AgentContextTab.6a97da62ec")}</small></div></header>
            {(inventory?.backups.length || 0) === 0 ? <p>{t("AgentContextTab.efb291e282")}</p> : inventory!.backups.map(backup => {
                const confirming = restoreConfirm?.id === backup.id
                const canRestore = currentRevision.has(backup.target)
                return <div class="agent-context-backup" key={backup.id}>
                  <span>
                    <strong>{backupAction(backup)}</strong>
                    <small>{formatTime(backup.created_at)} · {backup.existed ? formatBytes(backup.size) : t("AgentContextTab.7da59b277b")}</small>
                  </span>
                  {confirming ? <span class="agent-context-backup-confirm">
                      <button onClick={() => setRestoreConfirm(null)}>{t("AgentContextTab.2374d91794")}</button>
                      <button class="danger" disabled={!canRestore || !!busy} onClick={() => void restore(backup)}>
                        {busy === `restore:${backup.id}` ? t("AgentContextTab.38de3dcd94") : t("AgentContextTab.dfc454e79e")}
                      </button>
                    </span> : <button disabled={!canRestore || !!busy} onClick={() => { setRestoreConfirm(backup); setPreview(null) }}>{t("AgentContextTab.7af7c43080")}</button>}
                </div>
              })}
          </section>
        </div>
        <div class="modal-footer"><button type="button" onClick={closeSync}>{t("AgentContextTab.7d9eb7acb1")}</button></div>
      </section>
    </div>}
  </div>
}
