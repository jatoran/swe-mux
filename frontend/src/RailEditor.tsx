import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { api } from './api'
import {
  allRailBackends, defaultRailConfig, isBuiltinRailId, railItemVisible, railPayload,
  railProjectScopeKind,
  RAIL_DEVICES, RAIL_SURFACES,
  type RailBackend, type RailBlob, type RailConfig, type RailDevice, type RailItem,
  type RailItemType, type RailRow, type RailSurface,
} from './commandRail'
import {
  addRailRow, copyRailSurface, moveRailEntry, moveRailRow, railPlacementCounts,
  removeRailEntry, removeRailRow, setRailRowLabel, updateRailCatalogItem,
  type RailDropTarget, type RailRef,
} from './railLayout'
import { beginRailDrag, railRefKey, type RailDragHost, type RailDragPreview, type RailDragSource } from './railDrag'
import {
  addProjectRailRow, addScopedRailItem, applyScopedRail, detachProjectRail,
  removeScopedRailItem, toggleScopedPlacement,
  type RailAddTarget, type ResolvedRail,
} from './railScope'
import { clearProjectRail, currentProfile, currentRailBlob, loadResolvedRail, saveRailBlob } from './deviceSettings'
import { harnessDisplayName, promptDeliveryHarnesses } from './harnessRegistry'
import { fetchPromptTemplates, promptItemSummary } from './promptRail'
import type { PromptTemplate } from './promptTemplates'
import type { Project } from './types'

// The Configure Actions surface.
//
// Progressive disclosure, top to bottom: one device's two layouts first (the
// thing most visits came to rearrange), custom-action creation collapsed below
// them, and the complete catalog collapsed at the bottom. One device at a time —
// defaulting to the device this browser *is* — with a Desktop/Mobile switch,
// because two columns of chips doubled the visual load for the rare cross-device
// drag that the catalog's placement checkboxes already cover.
//
// The catalog is the index of everything that exists. A collapsed row is a
// name, what it injects, and a plain-words summary of where it is placed;
// expanding it exposes the actual controls — four placement checkboxes and a
// named checkbox per harness — instead of the abbreviated badge code they
// replaced.
//
// Scopes: the Global layout is shared by every project. A project selected in
// the scope picker shows its *effective* layout — shared rows plus any rows and
// actions the project has added — and each edit lands where the touched thing
// lives: shared rows write the global scope (all projects, said in place),
// project rows and project actions stay project state (`railScope.ts`). Detach
// is the deliberate escape into a full fork that stops tracking global edits.
//
// Everything commits immediately, like the other device-settings domains.

const SURFACE_LABEL: Record<RailSurface, string> = { strip: 'Rail', panel: 'Drawer' }
const SURFACE_HINT: Record<RailSurface, string> = {
  strip: 'the button strip under the terminal',
  panel: 'Quick actions in the Actions tab',
}
const DEVICE_LABEL: Record<RailDevice, string> = { desktop: 'Desktop', mobile: 'Mobile' }
const OTHER_DEVICE: Record<RailDevice, RailDevice> = { desktop: 'mobile', mobile: 'desktop' }
const INTRO_KEY = 'mux.actions.intro.v1'

const rowKey = (device: RailDevice, surface: RailSurface, rowId: string): string => `${device}|${surface}|${rowId}`

function introSeen(): boolean {
  try { return localStorage.getItem(INTRO_KEY) === '1' } catch { return true }
}

export function RailEditor() {
  const backends = allRailBackends()
  const rootRef = useRef<HTMLElement>(null)
  // '' = the shared global config; a project id shows that project's effective
  // layout (shared rows + project additions, or its fork).
  const [scope, setScope] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [resolved, setResolved] = useState<ResolvedRail>(() => loadResolvedRail())
  const [prompts, setPrompts] = useState<PromptTemplate[]>([])
  const [device, setDevice] = useState<RailDevice>(() => currentProfile())
  // '' = no filter; a backend name dims what that session type would not show.
  const [previewBackend, setPreviewBackend] = useState('')
  const [expandedItem, setExpandedItem] = useState<string | null>(null)
  const [catalogQuery, setCatalogQuery] = useState('')
  const [note, setNote] = useState('')
  const [showIntro, setShowIntro] = useState(() => !introSeen())

  const resolvedRef = useRef(resolved)
  resolvedRef.current = resolved
  const scopeRef = useRef(scope)
  scopeRef.current = scope

  const refresh = () => {
    const next = loadResolvedRail(scopeRef.current || undefined)
    resolvedRef.current = next
    setResolved(next)
  }
  useEffect(() => {
    refresh()
    window.addEventListener('mux:settings-changed', refresh)
    return () => window.removeEventListener('mux:settings-changed', refresh)
  }, [scope])
  useEffect(() => { void api<Project[]>('GET', '/api/projects').then(setProjects).catch(() => {}) }, [])
  // Prompt templates the rail can point at. Scoped like the config being edited, so
  // a project's rail can carry that project's own templates as well as the global ones.
  useEffect(() => {
    void fetchPromptTemplates(scope || undefined).then(setPrompts).catch(() => setPrompts([]))
  }, [scope])

  /** Persist a blob from the scope-aware ops and re-read the resolution. The
   *  settings cache is updated synchronously by the save, so the re-read never
   *  races the write. */
  const commitBlob = (blob: RailBlob) => {
    void saveRailBlob(blob)
    refresh()
  }
  /** Persist an edited effective config, split by ownership. */
  const commitConfig = (next: RailConfig) => {
    if (next === resolvedRef.current.config) return
    commitBlob(applyScopedRail(currentRailBlob(), scopeRef.current || undefined, resolvedRef.current, next))
  }

  // Live drag state; `config` is the layout a drop would commit.
  const [drag, setDrag] = useState<RailDragPreview>({ key: null, config: null, active: false })
  const dragCancelRef = useRef<(() => void) | null>(null)
  const dragEndedAt = useRef(0)
  useEffect(() => () => dragCancelRef.current?.(), [])
  const dragHost: RailDragHost = {
    root: () => rootRef.current,
    config: () => resolvedRef.current.config,
    setPreview: setDrag,
    commit: commitConfig,
    // A project-owned action may only occupy project rows: a shared row is
    // written to the global scope, where the project item's id does not exist.
    canDrop: (target, itemId) => !resolvedRef.current.projectItemIds.has(itemId)
      || resolvedRef.current.projectRowIds.has(target.rowId),
    onEnd: () => { dragEndedAt.current = performance.now() },
  }
  const startDrag = (event: JSX.TargetedPointerEvent<HTMLElement>, source: RailDragSource, label: string) => {
    setNote('')
    dragCancelRef.current = beginRailDrag(event, dragHost, source, label)
  }
  /** True right after a drag, so the click that ends it does not also expand a
   *  row. Guarded on a drag having happened at all: the ref starts at 0, and
   *  `performance.now()` is still under the window shortly after page load. */
  const justDragged = () => dragEndedAt.current > 0 && performance.now() - dragEndedAt.current < 250

  const shown = drag.config || resolved.config
  const kind = scope ? resolved.kind : 'global'
  const projectName = scope ? (projects.find(project => project.id === scope)?.name || 'this project') : ''
  const catalogById = useMemo(() => new Map(shown.items.map(item => [item.id, item])), [shown])

  const dismissIntro = () => {
    setShowIntro(false)
    try { localStorage.setItem(INTRO_KEY, '1') } catch { /* device preference is best effort */ }
  }

  const itemMeta = (item: RailItem): string => {
    const builtin = isBuiltinRailId(item.id)
    const agentPayloads = promptDeliveryHarnesses().map(harness => railPayload(item, harness.name))
    const preview = item.type === 'skill' ? [...new Set(agentPayloads)].join(' · ')
      : item.type === 'slash' ? agentPayloads[0] || ''
        : item.type === 'text' ? `"${(item.text || '').slice(0, 24)}"`
          : item.type === 'prompt' ? promptItemSummary(item, prompts) : ''
    return `${builtin ? item.type : `custom ${item.type}`}${preview ? ` · ${preview}` : ''}${item.submit && item.type !== 'prompt' ? ' · sends' : ''}`
  }

  /** Plain-words "where is it": `Desktop rail + drawer · Mobile drawer`. */
  const placementSummary = (itemId: string): string => {
    const counts = railPlacementCounts(shown, itemId)
    const parts: string[] = []
    for (const name of RAIL_DEVICES) {
      const placed = RAIL_SURFACES.filter(surface => counts[name][surface] > 0)
      if (placed.length) parts.push(`${DEVICE_LABEL[name]} ${placed.map(surface => SURFACE_LABEL[surface].toLowerCase()).join(' + ')}`)
    }
    return parts.length ? parts.join(' · ') : 'not placed'
  }

  const toggleBackend = (item: RailItem, backend: RailBackend) => {
    const set = new Set(item.backends ?? backends)
    if (set.has(backend)) { if (set.size <= 1) return; set.delete(backend) } else set.add(backend)
    const next = backends.filter(name => set.has(name))
    commitConfig({
      ...resolved.config,
      items: resolved.config.items.map(entry => entry.id === item.id
        ? { ...entry, backends: next.length === backends.length ? undefined : next }
        : entry),
    })
  }

  const editCustomItem = (itemId: string, patch: Partial<RailItem>) => {
    commitConfig(updateRailCatalogItem(resolved.config, itemId, patch))
  }

  /** Keyboard placement, and the only reorder path for anyone not using a pointer.
   *  Arrows move the focused chip along its row and between rows; Delete unplaces it. */
  const onChipKey = (event: JSX.TargetedKeyboardEvent<HTMLElement>, ref: RailRef) => {
    const config = resolved.config
    const rows = config.layouts[ref.device][ref.surface]
    const rowIndex = rows.findIndex(row => row.id === ref.rowId)
    if (rowIndex < 0) return
    const itemId = rows[rowIndex].items[ref.index]
    const allowed = (rowId: string) => !resolved.projectItemIds.has(itemId) || resolved.projectRowIds.has(rowId)
    const move = (to: RailDropTarget) => {
      if (!allowed(to.rowId)) return
      event.preventDefault()
      commitConfig(moveRailEntry(config, ref, to))
      // Follow the chip so a run of arrow presses keeps moving the same one.
      const next = `[data-reorder-id="${railRefKey(to)}"]`
      requestAnimationFrame(() => rootRef.current?.querySelector<HTMLElement>(next)?.focus())
    }
    if (event.key === 'ArrowLeft' && ref.index > 0) return move({ ...ref, index: ref.index - 1 })
    if (event.key === 'ArrowRight' && ref.index < rows[rowIndex].items.length - 1) return move({ ...ref, index: ref.index + 1 })
    if (event.key === 'ArrowUp' && rowIndex > 0) {
      const above = rows[rowIndex - 1]
      return move({ ...ref, rowId: above.id, index: Math.min(ref.index, above.items.length) })
    }
    if (event.key === 'ArrowDown' && rowIndex < rows.length - 1) {
      const below = rows[rowIndex + 1]
      return move({ ...ref, rowId: below.id, index: Math.min(ref.index, below.items.length) })
    }
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault()
      commitConfig(removeRailEntry(config, ref))
    }
  }

  const resetScope = () => {
    setNote('')
    // A project reverts by dropping its override; the global scope has nothing to
    // fall back to, so it is rewritten with the shipped catalog and layouts.
    if (scope) { void clearProjectRail(scope); refresh() }
    else commitConfig(defaultRailConfig())
  }

  const renderChip = (surface: RailSurface, rowId: string, itemId: string, index: number) => {
    const ref: RailRef = { device, surface, rowId, index }
    const key = railRefKey(ref)
    const item = catalogById.get(itemId)
    const label = item?.label || itemId
    const filtered = !!previewBackend && !!item && !railItemVisible(item, previewBackend)
    return <div
      key={key}
      class={`rail-chip${drag.key === key && drag.active ? ' dragging' : ''}${item ? '' : ' missing'}${filtered ? ' filtered' : ''}`}
      data-reorder-id={key}
      tabIndex={0}
      role="button"
      title={`${label}${item ? ` — ${itemMeta(item)}` : ''}${filtered ? `\nHidden in ${previewBackend} sessions.` : ''}\nDrag to move. Arrows move it, Delete removes it.`}
      onPointerDown={event => startDrag(event, { kind: 'chip', ref }, label)}
      onKeyDown={event => onChipKey(event, ref)}
    >
      <span class="rail-chip-label">{label}</span>
      <button
        type="button"
        class="rail-chip-remove"
        tabIndex={-1}
        aria-label={`Remove ${label} from this row`}
        title="Remove from this row"
        onPointerDown={event => event.stopPropagation()}
        onClick={() => commitConfig(removeRailEntry(resolved.config, ref))}
      >×</button>
    </div>
  }

  const renderRow = (surface: RailSurface, row: RailRow, indexInGroup: number, group: RailRow[]) => {
    const projectRow = resolved.projectRowIds.has(row.id)
    return <article class={`rail-row-editor${projectRow ? ' project-owned' : ''}`} key={row.id}>
      <div class="rail-row-head">
        {scope && kind !== 'fork' && <span class={`rail-origin${projectRow ? ' project' : ''}`} title={projectRow ? `Only ${projectName} has this row` : 'Shared with every project — edits here change all of them'}>{projectRow ? 'this project' : 'shared'}</span>}
        <input
          value={row.label || ''}
          placeholder={`Row ${indexInGroup + 1}${surface === 'panel' ? ' (name shows as a heading)' : ''}`}
          aria-label={`Name for row ${indexInGroup + 1}`}
          onChange={event => commitConfig(setRailRowLabel(resolved.config, device, surface, row.id, event.currentTarget.value))}
        />
        <button type="button" disabled={indexInGroup === 0} title="Move this row up" onClick={() => commitConfig(moveRailRow(resolved.config, device, surface, row.id, -1))}>↑</button>
        <button type="button" disabled={indexInGroup === group.length - 1} title="Move this row down" onClick={() => commitConfig(moveRailRow(resolved.config, device, surface, row.id, 1))}>↓</button>
        <button type="button" class="rail-del" title={group.length === 1 && !projectRow ? 'Empty this row' : 'Delete this row and everything in it'} onClick={() => commitConfig(removeRailRow(resolved.config, device, surface, row.id))}>×</button>
      </div>
      <div class="rail-chips" data-rail-row={rowKey(device, surface, row.id)} role="group" aria-label={`${DEVICE_LABEL[device]} ${SURFACE_LABEL[surface]} row ${indexInGroup + 1}`}>
        {row.items.map((itemId, index) => renderChip(surface, row.id, itemId, index))}
        {!row.items.length && <span class="rail-chips-empty">drag actions here</span>}
      </div>
    </article>
  }

  const renderSurface = (surface: RailSurface) => {
    const rows = shown.layouts[device][surface]
    const sharedRows = rows.filter(row => !resolved.projectRowIds.has(row.id))
    const projectRows = rows.filter(row => resolved.projectRowIds.has(row.id))
    const other = OTHER_DEVICE[device]
    return <section class="rail-surface" key={`${device}-${surface}`}>
      <header class="rail-surface-head">
        <h5>{SURFACE_LABEL[surface]}<small>{SURFACE_HINT[surface]}</small></h5>
        {kind !== 'delta' && <button
          type="button"
          title={`Replace this device’s ${SURFACE_LABEL[surface].toLowerCase()} with a copy of ${DEVICE_LABEL[other]}’s. A one-shot copy — the two stay independent afterwards.`}
          onClick={() => { commitConfig(copyRailSurface(resolved.config, other, device, surface)); setNote(`Copied ${DEVICE_LABEL[other]} ${SURFACE_LABEL[surface].toLowerCase()} to ${DEVICE_LABEL[device]}.`) }}
        >Copy from {DEVICE_LABEL[other]}</button>}
        <button type="button" title={scope && kind !== 'fork' ? 'Add another shared row (all projects)' : 'Add another row to this surface'} onClick={() => commitConfig(addRailRow(resolved.config, device, surface))}>+ Row</button>
        {scope && kind !== 'fork' && <button
          type="button"
          title={`Add a row only ${projectName} has. Project-only actions can live there.`}
          onClick={() => commitBlob(addProjectRailRow(currentRailBlob(), scope, device, surface))}
        >+ Project row</button>}
      </header>
      {sharedRows.map((row, index) => renderRow(surface, row, index, sharedRows))}
      {projectRows.map((row, index) => renderRow(surface, row, index, projectRows))}
    </section>
  }

  const query = catalogQuery.trim().toLowerCase()
  const catalogItems = query
    ? shown.items.filter(item => `${item.label} ${item.id} ${itemMeta(item)}`.toLowerCase().includes(query))
    : shown.items

  const renderCatalogRow = (item: RailItem) => {
    const counts = railPlacementCounts(shown, item.id)
    const placed = RAIL_DEVICES.some(name => RAIL_SURFACES.some(surface => counts[name][surface] > 0))
    const itemBackends = item.backends ?? [...backends]
    const meta = itemMeta(item)
    const custom = !isBuiltinRailId(item.id)
    const projectItem = resolved.projectItemIds.has(item.id)
    const expanded = expandedItem === item.id
    return <article class={`rail-catalog-row${placed ? '' : ' off'}${expanded ? ' expanded' : ''}`} key={item.id}>
      <div
        class="rail-catalog-head"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        title={`${item.label || item.id} — ${meta}\nClick for placement and session options. Drag into a row above to place it exactly.`}
        onPointerDown={event => startDrag(event, { kind: 'catalog', itemId: item.id }, item.label || item.id)}
        onClick={() => { if (!justDragged()) setExpandedItem(expanded ? null : item.id) }}
        onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setExpandedItem(expanded ? null : item.id) } }}
      >
        <div class="rail-id">
          <span class="rail-name">{item.label || item.id}{projectItem && <i class="rail-origin project" title={`Only ${projectName} has this action`}>this project</i>}</span>
          <small class="rail-meta" title={meta}>{meta}</small>
        </div>
        <small class={`rail-placement-summary${placed ? '' : ' off'}`}>{placementSummary(item.id)}</small>
        <span class="rail-expand" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
      </div>
      {expanded && <div class="rail-catalog-detail">
        <fieldset class="rail-detail-group">
          <legend>Where it appears</legend>
          {RAIL_DEVICES.map(name => RAIL_SURFACES.map(surface => {
            const count = counts[name][surface]
            return <label class="check" key={`${name}-${surface}`}>
              <input
                type="checkbox"
                checked={count > 0}
                onChange={() => commitBlob(toggleScopedPlacement(currentRailBlob(), scope || undefined, resolvedRef.current, item.id, name, surface))}
              />
              <span>{DEVICE_LABEL[name]} {SURFACE_LABEL[surface].toLowerCase()}{count > 1 ? ` (${count} copies)` : ''}</span>
            </label>
          }))}
        </fieldset>
        <fieldset class="rail-detail-group">
          <legend>Shown in these sessions</legend>
          {backends.map(backend => <label class="check" key={backend}>
            <input
              type="checkbox"
              checked={itemBackends.includes(backend)}
              onChange={() => toggleBackend(item, backend)}
            />
            <span>{backend === 'shell' ? 'Shell' : harnessDisplayName(backend)}</span>
          </label>)}
        </fieldset>
        {custom && <fieldset class="rail-detail-group rail-detail-edit">
          <legend>Edit</legend>
          <label>Button label<input value={item.label} onChange={event => editCustomItem(item.id, { label: event.currentTarget.value.trim() || item.label })} /></label>
          {item.type !== 'prompt' && <label>{item.type === 'text' ? 'Text to insert' : 'Name'}<input value={item.text || ''} onChange={event => editCustomItem(item.id, { text: event.currentTarget.value })} /></label>}
          {item.type !== 'prompt' && <label class="check"><input type="checkbox" checked={!!item.submit} onChange={event => editCustomItem(item.id, { submit: event.currentTarget.checked })} /><span>Press Enter after inserting</span></label>}
          <button
            type="button"
            class="rail-del"
            title="Delete this action and every button pointing at it"
            onClick={() => { setExpandedItem(null); commitBlob(removeScopedRailItem(currentRailBlob(), scope || undefined, item.id)) }}
          >Delete action</button>
        </fieldset>}
      </div>}
    </article>
  }

  return <section class="commandrail-settings" ref={rootRef}>
    <h3>Actions</h3>
    {showIntro && <div class="rail-intro-callout" role="note">
      <strong>How this works</strong>
      <ul>
        <li><b>Rail</b> is the button strip under every terminal. <b>Drawer</b> is the Quick actions section of the Actions tab — the overflow with room for labels.</li>
        <li>Desktop and mobile each keep their own arrangement. Pick a device, then drag buttons between rows, or remove them with ×.</li>
        <li>Everything that can be placed lives under <b>All actions</b> below — open one to choose where it appears and which sessions show it.</li>
      </ul>
      <button type="button" onClick={dismissIntro}>Got it</button>
    </div>}
    <div class="rail-toolbar">
      <label class="rail-scope">Editing<select value={scope} onChange={event => { setScope(event.currentTarget.value); setExpandedItem(null); setNote('') }}>
        <option value="">Global (all projects)</option>
        {projects.map(project => {
          const projectKind = railProjectScopeKind(currentRailBlob(), project.id)
          return <option value={project.id}>{project.name}{projectKind === 'fork' ? ' (detached)' : projectKind === 'delta' ? ' (has additions)' : ''}</option>
        })}
      </select></label>
      <div class="rail-device-switch" role="group" aria-label="Device layout to edit">
        {RAIL_DEVICES.map(name => <button
          type="button"
          class={device === name ? 'on' : ''}
          aria-pressed={device === name}
          onClick={() => setDevice(name)}
        >{DEVICE_LABEL[name]}</button>)}
      </div>
      <label class="rail-preview-as">Preview as<select value={previewBackend} onChange={event => setPreviewBackend(event.currentTarget.value)} title="Dim the actions a session of this type would not show">
        <option value="">all sessions</option>
        {backends.map(backend => <option value={backend}>{backend === 'shell' ? 'Shell' : harnessDisplayName(backend)}</option>)}
      </select></label>
      <span class="rail-toolbar-gap" />
      {scope && kind !== 'fork' && <button
        type="button"
        title="Freeze this project’s current layout as its own copy. It stops following global edits; “Use global layout” undoes it (dropping project changes)."
        onClick={() => { commitBlob(detachProjectRail(currentRailBlob(), scope)); setNote(`${projectName} now has a detached copy of the layout.`) }}
      >Detach from global</button>}
      <button
        type="button"
        onClick={resetScope}
        disabled={!!scope && kind === 'global'}
        title={!scope ? 'Restore the built-in layout and drop custom actions'
          : kind === 'fork' ? 'Drop this project’s detached copy and follow the global layout again'
            : kind === 'delta' ? 'Remove this project’s added rows and actions; the shared layout stays'
              : 'This project has nothing project-specific yet'}
      >{!scope ? 'Restore defaults' : kind === 'fork' ? 'Use global layout' : 'Remove project additions'}</button>
    </div>
    {scope && <p class="rail-scope-note">{kind === 'fork'
      ? `${projectName} has a detached copy. Edits here change only this project.`
      : `Showing the shared layout${kind === 'delta' ? ` plus ${projectName}’s additions` : ''}. Edits to shared rows change every project; rows and actions marked “this project” stay here.`}</p>}

    <div class="rail-device">
      {RAIL_SURFACES.map(surface => renderSurface(surface))}
    </div>

    <RailAddForm
      items={resolved.config.items}
      prompts={prompts}
      projectName={scope && kind !== 'fork' ? projectName : ''}
      onAdd={(item, target) => {
        commitBlob(addScopedRailItem(currentRailBlob(), scope || undefined, item, target))
        setNote(`Added “${item.label}” to the ${item.defaultSurface === 'strip' ? 'Rail' : 'Drawer'} on both devices.`)
      }}
    />

    <details class="rail-catalog">
      <summary>All actions ({shown.items.length})<small>everything that can be placed, and where it is</small></summary>
      <div class="rail-catalog-tools">
        <input
          type="search"
          value={catalogQuery}
          placeholder="Filter actions…"
          aria-label="Filter actions"
          onInput={event => setCatalogQuery(event.currentTarget.value)}
        />
      </div>
      <div class="rail-catalog-list">
        {catalogItems.map(renderCatalogRow)}
        {!catalogItems.length && <p class="rail-add-note">No action matches “{catalogQuery}”.</p>}
      </div>
    </details>

    {note && <p class="rail-scope-note" aria-live="polite">{note}</p>}
  </section>
}

type AddDraft = { type: RailItemType; name: string; label: string; submit: boolean; surface: RailSurface; target: RailAddTarget }

/** Adding an action places it into *both* device layouts, because a button you
 *  must remember to add twice is a button that never reaches the phone. In a
 *  project scope it can be added for that project alone (the default there) or
 *  for every project. */
function RailAddForm({ items, prompts, projectName, onAdd }: {
  items: readonly RailItem[]
  prompts: PromptTemplate[]
  /** Non-empty enables the project-scope choice (named for the note). */
  projectName: string
  onAdd: (item: RailItem, target: RailAddTarget) => void
}) {
  const backends = allRailBackends()
  const [draft, setDraft] = useState<AddDraft>({ type: 'skill', name: '', label: '', submit: true, surface: 'panel', target: projectName ? 'project' : 'global' })
  // Entering or leaving a project scope re-defaults the target without touching
  // the rest of a half-typed draft.
  useEffect(() => { setDraft(current => ({ ...current, target: projectName ? 'project' : 'global' })) }, [projectName])

  const uniqueId = (base: string): string => {
    let candidate = base, suffix = 1
    while (items.some(item => item.id === candidate)) { suffix += 1; candidate = `${base}:${suffix}` }
    return candidate
  }

  const add = () => {
    const name = draft.name.trim()
    if (!name) return
    const target: RailAddTarget = projectName ? draft.target : 'global'
    // A prompt item stores the library key, never the body: the template stays the
    // source of truth, so editing it updates every button pointing at it.
    if (draft.type === 'prompt') {
      const template = prompts.find(entry => entry.key === name)
      if (!template) return
      // Templates declare their compatible backends; carry that through instead of
      // making the user re-pick it, but only when it is actually a restriction.
      const restricted = template.backends.length === backends.length ? undefined : [...template.backends]
      onAdd({
        id: uniqueId(`custom:prompt:${template.id}`),
        type: 'prompt',
        label: draft.label.trim() || template.title,
        promptKey: template.key,
        defaultSurface: draft.surface,
        backends: restricted,
      }, target)
      setDraft({ ...draft, name: '', label: '' })
      return
    }
    const base = name.replace(/^[/$]/, '').trim() || name
    const label = draft.label.trim() || (draft.type === 'text' ? base.slice(0, 12) : base)
    const id = uniqueId(`custom:${draft.type}:${base}`)
    onAdd(draft.type === 'text'
      ? { id, type: 'text', label, text: name, submit: draft.submit, defaultSurface: draft.surface }
      : { id, type: draft.type, label, text: base, submit: draft.submit, defaultSurface: draft.surface }, target)
    setDraft({ ...draft, name: '', label: '' })
  }

  return <details class="rail-add">
    <summary>Add custom action<small>a skill, slash command, text macro, or prompt-template button</small></summary>
    <div class="rail-add-form">
      <label>Type<select value={draft.type} onChange={event => setDraft({ ...draft, type: event.currentTarget.value as RailItemType })}>
        <option value="skill">Skill</option>
        <option value="slash">Slash command</option>
        <option value="text">Text macro</option>
        <option value="prompt">Prompt template</option>
      </select></label>
      {draft.type === 'prompt'
        ? <label>Template<select value={draft.name} onChange={event => setDraft({ ...draft, name: event.currentTarget.value })}>
          <option value="">Choose a template…</option>
          {prompts.map(template => <option value={template.key}>{template.favorite ? '★ ' : ''}{template.title}{template.scope === 'project' ? ' (project)' : ''}{template.variables.length ? ` · ${template.variables.length} field${template.variables.length === 1 ? '' : 's'}` : ''}</option>)}
        </select></label>
        : <label>{draft.type === 'text' ? 'Text to insert' : 'Name'}<input value={draft.name} placeholder={draft.type === 'skill' ? 'commit' : draft.type === 'slash' ? 'new' : 'literal text'} onInput={event => setDraft({ ...draft, name: event.currentTarget.value })} /></label>}
      <label>Button label<input value={draft.label} placeholder="(auto)" onInput={event => setDraft({ ...draft, label: event.currentTarget.value })} /></label>
      <label>Place in<select value={draft.surface} onChange={event => setDraft({ ...draft, surface: event.currentTarget.value as RailSurface })}>
        <option value="panel">Drawer, on both devices</option>
        <option value="strip">Rail, on both devices</option>
      </select></label>
      {projectName && <label>For<select value={draft.target} onChange={event => setDraft({ ...draft, target: event.currentTarget.value as RailAddTarget })}>
        <option value="project">{projectName} only</option>
        <option value="global">All projects</option>
      </select></label>}
      {draft.type !== 'prompt' && <label class="check"><span>Submit with Enter</span><input type="checkbox" checked={draft.submit} onChange={event => setDraft({ ...draft, submit: event.currentTarget.checked })} /></label>}
      <button class="primary" type="button" disabled={!draft.name.trim()} onClick={add}>Add action</button>
    </div>
    {draft.type === 'prompt' && <p class="rail-add-note">{prompts.length
      ? 'A prompt button points at the template, so editing the template updates the button. It inserts without sending - templates with {{fields}} open Prompt templates in Actions to be filled in first.'
      : 'No prompt templates yet. Create one from Actions → Prompt templates → Manage, then it appears here.'}</p>}
    <p class="rail-add-note">
      Tip: skills and prompt templates can also be pinned straight from their lists in the Actions tab.
      Skills inject <code>/name</code> in Claude and <code>$name</code> in Codex; slash commands
      inject <code>/name</code> in both. Built-in actions can be placed and filtered, but not edited.
    </p>
  </details>
}
