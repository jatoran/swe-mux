import { isComposingKey } from './inputComposition.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { byProjectName } from './projectOptions'
import {
  allRailBackends, defaultPadTriggerMode, defaultRailConfig, isBuiltinRailId,
  padRingCount, padSlotKey, padWedgeCount, padWedgeName, parsePadSlotKey,
  railItemDisplayLabel, railItemDisplayMode,
  railItemVisible, railPadSlotMode, railPayload,
  railProjectScopeKind, railRowKey,
  RAIL_DEVICES, RAIL_PAD_MAX_WEDGES, RAIL_PAD_TRIGGER_MODES, RAIL_SURFACES,
  type RailBackend, type RailBlob, type RailConfig, type RailDevice, type RailItem,
  type RailItemDisplay, type RailItemType, type RailPadSlotKey,
  type RailPadTriggerMode, type RailRow, type RailSurface,
} from './commandRail'
import {
  addRailRow, copyRailSurface, moveRailEntry, moveRailRow, railPlacementCounts,
  removeRailEntry, removeRailRow, setRailPadShape, setRailRowLabel, updateRailCatalogItem,
  updateRailItemPresentation, updateRailPadSlot,
  type RailDropTarget, type RailRef,
} from './railLayout'
import { railItemHasIcon, RailItemIcon } from './railIcons'
import { beginRailDrag, railRefKey, type RailDragHost, type RailDragPreview, type RailDragSource } from './railDrag'
import {
  addProjectRailRow, addScopedRailItem, applyScopedRail, detachProjectRail,
  hideScopedRailEntry, isProjectRailPlacement, removeScopedRailItem, toggleScopedPlacement,
  unhideScopedRailEntry,
  type RailAddTarget, type ResolvedRail,
} from './railScope'
import { applyForkReattach, planForkReattach, type ForkReattachPlan } from './railReattach'
import { clearProjectRail, currentProfile, currentRailBlob, loadResolvedRail, saveRailBlob } from './deviceSettings'
import { harnessDisplayName, promptDeliveryHarnesses } from './harnessRegistry'
import { fetchPromptTemplates, findPromptTemplate, promptItemSummary, railItemLabel } from './promptRail'
import type { PromptTemplate } from './promptTemplates'
import type { Project } from './types'

// The Configure Actions surface.
//
// Progressive disclosure, top to bottom: one device's rail first (the thing most
// visits came to rearrange), custom-action creation collapsed below it, and the
// complete catalog collapsed at the bottom. One device at a time -
// defaulting to the device this browser *is* — with a Desktop/Mobile switch,
// because two columns of chips doubled the visual load for the rare cross-device
// drag that the catalog's placement checkboxes already cover.
//
// The catalog is the index of everything that exists. A collapsed row is a
// name, what it injects, and a plain-words summary of where it is placed;
// expanding it exposes the actual controls - one placement checkbox per device
// and a named checkbox per harness - instead of the abbreviated badge code they
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

const SURFACE_LABEL: Record<RailSurface, string> = { strip: 'Rail' }
const SURFACE_HINT: Record<RailSurface, string> = {
  get strip() { return t("RailEditor.7027964b6e") },
}
const DEVICE_LABEL: Record<RailDevice, string> = { desktop: 'Desktop', mobile: 'Mobile' }
const OTHER_DEVICE: Record<RailDevice, RailDevice> = { desktop: 'mobile', mobile: 'desktop' }
const INTRO_KEY = 'mux.actions.intro.v1'

const rowKey = (device: RailDevice, surface: RailSurface, rowId: string): string => `${device}|${surface}|${rowId}`

function introSeen(): boolean {
  try { return localStorage.getItem(INTRO_KEY) === '1' } catch { return true }
}

export function RailEditor({ initialScope = '', contextProjectId }: { initialScope?: string; contextProjectId?: string } = {}) {
  const backends = allRailBackends()
  const rootRef = useRef<HTMLElement>(null)
  // '' = the shared global config; a project id shows that project's effective
  // layout (shared rows + project additions, or its fork).
  const [scope, setScope] = useState(initialScope)
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
  /** A computed fork→delta plan awaiting the operator's decision. Held rather than
   *  applied on the click, because what it *cannot* carry is the part worth reading. */
  const [reattach, setReattach] = useState<ForkReattachPlan | null>(null)

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
  // Prompt templates the rail can point at, scoped like the layout being edited: a
  // Project scope lists global plus that Project's own, and the Global scope lists
  // global alone. The confinement is structural rather than a warning, because a
  // Project template placed on the shared layout is a button that inserts nothing
  // in every other Project (`prompt-library.md`) — which is also why this editor
  // opens on a detached Project only when the operator came from one.
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
    // Every target is legal. A project-owned action dropped into a *shared* row
    // used to be refused, because the row is written to the global scope where
    // its id does not exist; a delta now records that placement as a splice
    // instead, leaving the row's definition global (`railScope.ts`).
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
  const projectName = scope ? (projects.find(project => project.id === scope)?.name || t("RailEditor.11453073e0")) : ''
  const contextProjectName = contextProjectId
    ? projects.find(project => project.id === contextProjectId)?.name || t("RailEditor.046b2d1d25")
    : ''
  const contextProjectKind = contextProjectId ? railProjectScopeKind(currentRailBlob(), contextProjectId) : 'global'
  /** Projects that hold something of their own. Recomputed with the resolution so
   *  it follows an edit that creates or empties an override. */
  const scopedProjects = useMemo(() => {
    const blob = currentRailBlob()
    return byProjectName(projects, project => project.name)
      .map(project => ({ name: project.name, kind: railProjectScopeKind(blob, project.id) }))
      .filter(entry => entry.kind !== 'global')
  }, [projects, resolved, uiLocale.value, catalogRevision.value])
  const catalogById = useMemo(() => new Map(shown.items.map(item => [item.id, item])), [shown, uiLocale.value, catalogRevision.value])

  const dismissIntro = () => {
    setShowIntro(false)
    try { localStorage.setItem(INTRO_KEY, '1') } catch { /* device preference is best effort */ }
  }

  /** The name a prompt button carries when it has none of its own: the template's
   *  live title, falling back to the stored label while the library is unread. */
  const promptTitleOf = (item: RailItem): string =>
    (item.promptKey ? findPromptTemplate(prompts, item.promptKey)?.title : '') || item.label
  const chipLabel = (item: RailItem): string => railItemLabel(item, prompts)
  const renderItemPreview = (item: RailItem, fallback = chipLabel(item)) => {
    const hasIcon = railItemHasIcon(item.id)
    const mode = railItemDisplayMode(item, hasIcon)
    const label = railItemDisplayLabel(item, fallback)
    return <span class={`rail-item-preview mode-${mode}`}>
      {(mode === 'icon' || mode === 'icon-label') && <RailItemIcon id={item.id} />}
      {(mode === 'label' || mode === 'icon-label') && <span>{label}</span>}
    </span>
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

  /** Plain-words "where is it": `Desktop rail · Mobile rail`. */
  const placementSummary = (itemId: string): string => {
    const counts = railPlacementCounts(shown, itemId)
    const parts: string[] = []
    for (const name of RAIL_DEVICES) {
      const placed = RAIL_SURFACES.filter(surface => counts[name][surface] > 0)
      if (placed.length) parts.push(`${DEVICE_LABEL[name]} ${placed.map(surface => SURFACE_LABEL[surface].toLowerCase()).join(' + ')}`)
    }
    return parts.length ? parts.join(' · ') : t("RailEditor.24290801db")
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
    const move = (to: RailDropTarget) => {
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
    const label = item ? chipLabel(item) : itemId
    const filtered = !!previewBackend && !!item && !railItemVisible(item, previewBackend)
    // In a shared row, whose chip is this? A project's own placement writes to the
    // delta; the row's own entries write to global, which is every project.
    const sharedRow = !resolved.projectRowIds.has(rowId)
    const mine = sharedRow && kind === 'delta' && isProjectRailPlacement(resolved, device, surface, rowId, itemId)
    const canHide = sharedRow && kind !== 'fork' && !!scope && !mine
    const removeTitle = mine ? t("RailEditor.995f9f988c", { value1: projectName })
      : sharedRow && !!scope ? t("RailEditor.d109f9a4c5")
        : t("RailEditor.4b97b824db")
    return <div
      key={key}
      class={`rail-chip${drag.key === key && drag.active ? ' dragging' : ''}${item ? '' : ' missing'}${filtered ? ' filtered' : ''}${mine ? ' project-owned' : ''}`}
      data-reorder-id={key}
      tabIndex={0}
      role="button"
      title={t("RailEditor.f3c9b51a58", { label: label, value2: item ? ` — ${itemMeta(item)}` : '', value3: mine ? t("RailEditor.ed58f6e7e2", { value1: projectName }) : '', value4: filtered ? t("RailEditor.0f194aef29", { value1: previewBackend }) : '' })}
      onPointerDown={event => startDrag(event, { kind: 'chip', ref }, label)}
      onKeyDown={event => { if (isComposingKey(event)) return; return onChipKey(event, ref) }}
    >
      <span class="rail-chip-label">{item ? renderItemPreview(item, label) : label}</span>
      {/* Hiding is the subtractive half of a splice, and it is a *separate* control
          from × on purpose: × on a shared row still means what the origin tag says
          it means — gone for everybody. */}
      {canHide && <button
        type="button"
        class="rail-chip-hide"
        tabIndex={-1}
        aria-label={t("RailEditor.7073465482", { label: label, projectName: projectName })}
        title={t("RailEditor.fbfdcc97b6", { projectName: projectName })}
        onPointerDown={event => event.stopPropagation()}
        onClick={() => commitBlob(hideScopedRailEntry(currentRailBlob(), scope, itemId, device, surface, rowId))}
      >⊘</button>}
      <button
        type="button"
        class="rail-chip-remove"
        tabIndex={-1}
        aria-label={t("RailEditor.90ad42dd1d", { label: label })}
        title={removeTitle}
        onPointerDown={event => event.stopPropagation()}
        onClick={() => commitConfig(removeRailEntry(resolved.config, ref))}
      >×</button>
    </div>
  }

  /** A button this project hides, drawn where the shared row still holds it. It is
   *  the only route back: a hidden entry is in no row, so nothing else could offer
   *  to restore it. */
  const renderGhost = (surface: RailSurface, rowId: string, entry: { item: string; index: number }) => {
    const item = catalogById.get(entry.item)
    const label = item ? chipLabel(item) : entry.item
    return <div
      key={`ghost:${rowId}:${entry.index}:${entry.item}`}
      class="rail-chip ghost"
      title={t("RailEditor.adbd50364f", { label: label, projectName: projectName })}
    >
      <span class="rail-chip-label">{item ? renderItemPreview(item, label) : label}</span>
      <button
        type="button"
        class="rail-chip-hide"
        aria-label={t("RailEditor.78aa2c5ef6", { label: label, projectName: projectName })}
        title={t("RailEditor.1dbe546818")}
        onClick={() => commitBlob(unhideScopedRailEntry(currentRailBlob(), scope, entry.item, device, surface, rowId))}
      >+</button>
    </div>
  }

  const renderRow = (surface: RailSurface, row: RailRow, indexInGroup: number, group: RailRow[]) => {
    const projectRow = resolved.projectRowIds.has(row.id)
    const hidden = resolved.hiddenEntries.get(railRowKey(device, surface, row.id)) || []
    return <article class={`rail-row-editor${projectRow ? ' project-owned' : ''}`} key={row.id}>
      <div class="rail-row-head">
        {scope && kind !== 'fork' && <span class={`rail-origin${projectRow ? ' project' : ''}`} title={projectRow ? t("RailEditor.994c7a6b7f", { projectName: projectName }) : t("RailEditor.dbe3c6072a")}>{projectRow ? t("RailEditor.11453073e0") : t("RailEditor.a4d2686801")}</span>}
        <input
          value={row.label || ''}
          placeholder={t("RailEditor.b78831f446", { value1: indexInGroup + 1 })}
          aria-label={t("RailEditor.f187ad1598", { value1: indexInGroup + 1 })}
          onChange={event => commitConfig(setRailRowLabel(resolved.config, device, surface, row.id, event.currentTarget.value))}
        />
        <button type="button" disabled={indexInGroup === 0} title={t("RailEditor.b3f30f2ec1")} onClick={() => commitConfig(moveRailRow(resolved.config, device, surface, row.id, -1))}>↑</button>
        <button type="button" disabled={indexInGroup === group.length - 1} title={t("RailEditor.8746126b2d")} onClick={() => commitConfig(moveRailRow(resolved.config, device, surface, row.id, 1))}>↓</button>
        <button type="button" class="rail-del" title={group.length === 1 && !projectRow ? t("RailEditor.68480dc41e") : t("RailEditor.c263ee662c")} onClick={() => commitConfig(removeRailRow(resolved.config, device, surface, row.id))}>×</button>
      </div>
      <div class="rail-chips" data-rail-row={rowKey(device, surface, row.id)} role="group" aria-label={t("RailEditor.53daa4604e", { value1: DEVICE_LABEL[device], value2: SURFACE_LABEL[surface], value3: indexInGroup + 1 })}>
        {row.items.map((itemId, index) => renderChip(surface, row.id, itemId, index))}
        {hidden.map(entry => renderGhost(surface, row.id, entry))}
        {!row.items.length && !hidden.length && <span class="rail-chips-empty">{t("RailEditor.373aca10c0")}</span>}
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
          title={t("RailEditor.83726c6cbb", { value1: SURFACE_LABEL[surface].toLowerCase(), value2: DEVICE_LABEL[other] })}
          onClick={() => { commitConfig(copyRailSurface(resolved.config, other, device, surface)); setNote(t("RailEditor.17c441f3e5", { value1: DEVICE_LABEL[other], value2: SURFACE_LABEL[surface].toLowerCase(), value3: DEVICE_LABEL[device] })) }}
        >{rich("RailEditor.2d27bc8b4b", { value1: DEVICE_LABEL[other] })}</button>}
        <button type="button" title={scope && kind !== 'fork' ? t("RailEditor.e5154bd035") : t("RailEditor.a4363a3c95")} onClick={() => commitConfig(addRailRow(resolved.config, device, surface))}>{t("RailEditor.bb37c37cdd")}</button>
        {scope && kind !== 'fork' && <button
          type="button"
          title={t("RailEditor.da27af7af0", { projectName: projectName })}
          onClick={() => commitBlob(addProjectRailRow(currentRailBlob(), scope, device, surface))}
        >{t("RailEditor.28f09d9dee")}</button>}
      </header>
      {sharedRows.map((row, index) => renderRow(surface, row, index, sharedRows))}
      {projectRows.map((row, index) => renderRow(surface, row, index, projectRows))}
    </section>
  }

  const query = catalogQuery.trim().toLowerCase()
  const catalogItems = query
    ? shown.items.filter(item => `${chipLabel(item)} ${item.id} ${itemMeta(item)}`.toLowerCase().includes(query))
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
        title={t("RailEditor.3f50ae96a4", { value1: chipLabel(item) || item.id, meta: meta })}
        onPointerDown={event => startDrag(event, { kind: 'catalog', itemId: item.id }, chipLabel(item) || item.id)}
        onClick={() => { if (!justDragged()) setExpandedItem(expanded ? null : item.id) }}
        onKeyDown={event => {
      if (isComposingKey(event)) return
 if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setExpandedItem(expanded ? null : item.id) } }}
      >
        <div class="rail-id">
          <span class="rail-name">{renderItemPreview(item, chipLabel(item) || item.id)}{projectItem && <i class="rail-origin project" title={t("RailEditor.4b564ae7de", { projectName: projectName })}>{t("RailEditor.11453073e0")}</i>}</span>
          <small class="rail-meta" title={meta}>{meta}</small>
        </div>
        <small class={`rail-placement-summary${placed ? '' : ' off'}`}>{placementSummary(item.id)}</small>
        <span class="rail-expand" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
      </div>
      {expanded && <div class="rail-catalog-detail">
        <fieldset class="rail-detail-group rail-detail-appearance">
          <legend>{t("RailEditor.d2052a72d6")}</legend>
          {railItemHasIcon(item.id) && <label>{rich("RailEditor.f20b1c7f2e", { value1: <Dropdown
            value={item.display || 'auto'}
            onChange={value => commitConfig(updateRailItemPresentation(resolved.config, item.id, { display: value as RailItemDisplay, displayLabel: item.displayLabel }))}
            options={[
              { value: 'auto', get label() { return t("RailEditor.aef8a41e91") } },
              { value: 'icon', get label() { return t("RailEditor.7a0470083b") } },
              { value: 'label', get label() { return t("RailEditor.af0b1b3355") } },
              { value: 'icon-label', get label() { return t("RailEditor.2fb6f3914a") } },
            ]}
          /> })}</label>}
          <label>{rich("RailEditor.c11d8f0f80", { value1: <input
            value={item.displayLabel || ''}
            placeholder={chipLabel(item)}
            onInput={event => commitConfig(updateRailItemPresentation(resolved.config, item.id, { display: item.display, displayLabel: event.currentTarget.value }))}
          /> })}</label>
          <div class="rail-appearance-preview"><span>{t("RailEditor.324b134f57")}</span>{renderItemPreview(item)}</div>
        </fieldset>
        <fieldset class="rail-detail-group">
          <legend>{t("RailEditor.731a510c93")}</legend>
          {RAIL_DEVICES.map(name => RAIL_SURFACES.map(surface => {
            const count = counts[name][surface]
            return <label class="check" key={`${name}-${surface}`}>
              <input
                type="checkbox"
                checked={count > 0}
                onChange={() => commitBlob(toggleScopedPlacement(currentRailBlob(), scope || undefined, resolvedRef.current, item.id, name, surface))}
              />
              <span>{DEVICE_LABEL[name]} {SURFACE_LABEL[surface].toLowerCase()}{count > 1 ? t("RailEditor.0f0302cfeb", { count: count }) : ''}</span>
            </label>
          }))}
        </fieldset>
        {item.type === 'pad' && <RailPadSlotEditor
          item={item}
          items={resolved.config.items}
          promptTitleOf={promptTitleOf}
          onShape={shape => commitConfig(setRailPadShape(resolved.config, item.id, shape))}
          onSlot={(slot, binding) => commitConfig(updateRailPadSlot(resolved.config, item.id, slot, binding))}
        />}
        <fieldset class="rail-detail-group">
          <legend>{t("RailEditor.caaf7d9250")}</legend>
          {backends.map(backend => <label class="check" key={backend}>
            <input
              type="checkbox"
              checked={itemBackends.includes(backend)}
              onChange={() => toggleBackend(item, backend)}
            />
            <span>{backend === 'shell' ? t("RailEditor.a733285486") : harnessDisplayName(backend)}</span>
          </label>)}
        </fieldset>
        {custom && <fieldset class="rail-detail-group rail-detail-edit">
          <legend>{t("RailEditor.464c4ffd01")}</legend>
          {/* A prompt button may have no name of its own: clearing the field hands
              the name back to the template, and it then follows every rename.
              Every other type needs a label, so an empty one is refused there. */}
          <label>{rich("RailEditor.caa4a06313", { value1: <input
            value={item.type === 'prompt' && item.autoLabel ? '' : item.label}
            placeholder={item.type === 'prompt' ? promptTitleOf(item) : undefined}
            title={item.type === 'prompt' ? t("RailEditor.c71d7f5bcf") : undefined}
            onChange={event => {
              const typed = event.currentTarget.value.trim()
              if (item.type !== 'prompt') { editCustomItem(item.id, { label: typed || item.label }); return }
              editCustomItem(item.id, typed
                ? { label: typed, autoLabel: false }
                : { label: promptTitleOf(item), autoLabel: true })
            }}
          /> })}</label>
          {item.type !== 'prompt' && <label>{item.type === 'text' ? t("RailEditor.bc471a63ce") : t("RailEditor.dcd1d5223f")}<input value={item.text || ''} onChange={event => editCustomItem(item.id, { text: event.currentTarget.value })} /></label>}
          {item.type !== 'prompt' && <label class="check"><input type="checkbox" checked={!!item.submit} onChange={event => editCustomItem(item.id, { submit: event.currentTarget.checked })} /><span>{t("RailEditor.1fe364c2ed")}</span></label>}
          <button
            type="button"
            class="rail-del"
            title={t("RailEditor.ba45db776f")}
            onClick={() => { setExpandedItem(null); commitBlob(removeScopedRailItem(currentRailBlob(), scope || undefined, item.id)) }}
          >{t("RailEditor.671fa3c0d3")}</button>
        </fieldset>}
      </div>}
    </article>
  }

  return <section class="commandrail-settings" ref={rootRef}>
    {showIntro && <div class="rail-intro-callout" role="note">
      <strong>{t("RailEditor.bdbd7706de")}</strong>
      <ul>
        <li>{rich("RailEditor.b02e1db1e4", { value1: <b>{t("RailEditor.8b270e3335")}</b> })}</li>
        <li>{t("RailEditor.4e9b0ec1a2")}</li>
        <li>{rich("RailEditor.dddb041ef5", { value1: <b>{t("RailEditor.83140cf130")}</b> })}</li>
      </ul>
      <button type="button" onClick={dismissIntro}>{t("RailEditor.5ad3dbd124")}</button>
    </div>}
    <div class="rail-toolbar">
      <label class="rail-scope">{rich("RailEditor.4c9d9f0f29", { value1: <Dropdown value={scope} filter filterPlaceholder={t("RailEditor.2e0060c45e")} onChange={value => { setScope(value); setExpandedItem(null); setNote(''); setReattach(null) }} options={[
        { value: '', get label() { return t("RailEditor.46c53fb7ee") } },
        // Ordered by the Project's own name, not by the suffix the scope kind appends, so
        // "swe-mux (detached)" still files under S.
        ...byProjectName(projects, project => project.name).map(project => {
          const projectKind = railProjectScopeKind(currentRailBlob(), project.id)
          return {
            value: project.id,
            get label() { return t("RailEditor.61a353b6d2", { name: project.name, value2: projectKind === 'fork' ? ' (detached)' : projectKind === 'delta' ? ' (has additions)' : '' }) },
          }
        }),
      ]}/> })}</label>
      <div class="rail-device-switch" role="group" aria-label={t("RailEditor.1ef89bfc74")}>
        {RAIL_DEVICES.map(name => <button
          type="button"
          class={device === name ? 'on' : ''}
          aria-pressed={device === name}
          onClick={() => setDevice(name)}
        >{DEVICE_LABEL[name]}</button>)}
      </div>
      <label class="rail-preview-as">{rich("RailEditor.17463d44c4", { value1: <Dropdown value={previewBackend} onChange={setPreviewBackend} title={t("RailEditor.3983e667a2")} options={[
        { value: '', get label() { return t("RailEditor.8711ba8489") } },
        ...backends.map(backend => ({ value: backend, label: backend === 'shell' ? t("RailEditor.a733285486") : harnessDisplayName(backend) })),
      ]}/> })}</label>
      <span class="rail-toolbar-gap" />
      {!scope && contextProjectId && contextProjectKind !== 'fork' && <button
        type="button"
        title={t("RailEditor.f4fd7562b3", { contextProjectName: contextProjectName })}
        onClick={() => {
          commitBlob(detachProjectRail(currentRailBlob(), contextProjectId))
          setScope(contextProjectId)
          setExpandedItem(null)
          setNote(t("RailEditor.3059a4bcde", { value1: contextProjectName }))
        }}
      >{rich("RailEditor.bef44b8c20", { contextProjectName: contextProjectName })}</button>}
      {scope && kind === 'fork' && <button
        type="button"
        title={t("RailEditor.57c2b787cd")}
        onClick={() => { setReattach(planForkReattach(currentRailBlob(), scope)); setNote('') }}
      >{t("RailEditor.4654f97086")}</button>}
      {scope && kind !== 'fork' && <button
        type="button"
        title={t("RailEditor.69b8d93d3b")}
        onClick={() => { commitBlob(detachProjectRail(currentRailBlob(), scope)); setNote(t("RailEditor.3059a4bcde", { value1: projectName })) }}
      >{t("RailEditor.bad2c0bcf2")}</button>}
      <button
        type="button"
        onClick={resetScope}
        disabled={!!scope && kind === 'global'}
        title={!scope ? t("RailEditor.5b56ef53bb") : kind === 'fork' ? t("RailEditor.18ab22a2dd") : kind === 'delta' ? t("RailEditor.74192b6196") : t("RailEditor.89889d20f4")}
      >{!scope ? t("RailEditor.602f8e6370") : kind === 'fork' ? t("RailEditor.b3fcc81eb4") : t("RailEditor.1d03d53afc")}</button>
    </div>
    {/* The default view is Global, where a project's own rows, actions, splices and
        hides are simply not drawn — so a reader can look straight at "all actions"
        and conclude the fleet has none of them. This names where the rest is. */}
    {!scope && !!scopedProjects.length && <p class="rail-scope-note">{rich("RailEditor.fdb1abbe84", { length: scopedProjects.length, value2: (scopedProjects.length === 1) ? 'yes' : 'no', value3: scopedProjects.map(entry => `${entry.name} (${entry.kind === 'fork' ? 'detached' : 'additions'})`).join(', ') })}</p>}
    {scope && <p class="rail-scope-note">{kind === 'fork' ? t("RailEditor.7dbc775f28", { projectName: projectName }) : t("RailEditor.1f5816f777", { value1: kind === 'delta' ? ` plus ${projectName}’s additions` : '' })}</p>}

    {reattach && <section class="rail-reattach" role="group" aria-label={t("RailEditor.db1cdcc4a7")}>
      <h5>{rich("RailEditor.f47335e120", { projectName: projectName })}</h5>
      <p>
        {reattach.exact ? t("RailEditor.d7cca9b78d") : t("RailEditor.3690e01d53")}
      </p>
      <p class="rail-add-note">{rich("RailEditor.832877bf2a", { items: reattach.counts.items, value2: (reattach.counts.items === 1) ? 'yes' : 'no', rows: reattach.counts.rows, value4: (reattach.counts.rows === 1) ? 'yes' : 'no', splices: reattach.counts.splices, value6: (reattach.counts.splices === 1) ? 'yes' : 'no', hides: reattach.counts.hides })}</p>
      {!!reattach.issues.length && <ul class="rail-reattach-issues">
        {reattach.issues.map((issue, index) => <li key={`${issue.kind}-${index}`}><b>{issue.subject}</b> — {issue.detail}</li>)}
      </ul>}
      <div class="rail-reattach-actions">
        <button
          type="button"
          class="primary"
          onClick={() => {
            commitBlob(applyForkReattach(currentRailBlob(), scope, reattach))
            setReattach(null)
            setNote(t("RailEditor.a77cfa2b57", { value1: projectName }))
          }}
        >{t("RailEditor.34b27e9861")}</button>
        <button type="button" onClick={() => setReattach(null)}>{t("RailEditor.8b1f066b9d")}</button>
      </div>
    </section>}

    <div class="rail-device">
      {renderSurface('strip')}
    </div>

    <RailAddForm
      items={resolved.config.items}
      prompts={prompts}
      projectName={scope && kind !== 'fork' ? projectName : ''}
      scopeName={projectName}
      onAdd={(item, target) => {
        commitBlob(addScopedRailItem(currentRailBlob(), scope || undefined, item, target))
        setNote(t("RailEditor.e397a6db13", { value1: item.label }))
      }}
    />

    <details class="rail-catalog">
      <summary>{rich("RailEditor.be8a195982", { length: shown.items.length, value2: <small>{t("RailEditor.38b9b97ead")}</small> })}</summary>
      <div class="rail-catalog-tools">
        <input
          type="search"
          value={catalogQuery}
          placeholder={t("RailEditor.a3cde03c1c")}
          aria-label={t("RailEditor.37dd87f0cd")}
          onInput={event => setCatalogQuery(event.currentTarget.value)}
        />
      </div>
      <div class="rail-catalog-list">
        {catalogItems.map(renderCatalogRow)}
        {!catalogItems.length && <p class="rail-add-note">{rich("RailEditor.79e942f47f", { catalogQuery: catalogQuery })}</p>}
      </div>
    </details>

    {note && <p class="rail-scope-note" aria-live="polite">{note}</p>}
  </section>
}

const TRIGGER_LABEL: Record<RailPadTriggerMode, string> = {
  get 'enter'() { return t("RailEditor.aabfda4ae2") },
  get 'enter-repeat'() { return t("RailEditor.fbe960a332") },
  get 'enter-repeat-far'() { return t("RailEditor.9b4b1a3a10") },
  get 'release'() { return t("RailEditor.cdc6b6573f") },
}

/**
 * The pad's wedges, drawn as the pad.
 *
 * A picture rather than a list of rows, because the thing being configured is spatial:
 * "which action is up" is a question about a position, and a list makes you translate every
 * answer back into one. So the wedges sit across the top in the order they are reached, a
 * far ring sits above its near one because it is further out, and the centre sits underneath
 * where a tap lands.
 *
 * Each position carries two controls and no more: what it runs, and when it fires. The mode
 * defaults from the action and the ring count (`defaultPadTriggerMode`), so an arrow arrives
 * already repeating, anything destructive already waiting for release, and every slot of a
 * ringed pad waiting too - the select is there to override a default, not to make every
 * binding a two-step.
 */
function RailPadSlotEditor({ item, items, promptTitleOf, onShape, onSlot }: {
  item: RailItem
  items: readonly RailItem[]
  promptTitleOf: (item: RailItem) => string
  onShape: (shape: { wedges?: number; rings?: number }) => void
  onSlot: (slot: RailPadSlotKey, binding: { item: string | null; mode?: RailPadTriggerMode }) => void
}) {
  const pad = item.pad
  const wedges = padWedgeCount(pad)
  const rings = padRingCount(pad)
  // A pad may not hold a pad. One level is a control; two is a menu with a hidden second
  // page, and the gesture has no way to say "and then" without the dwell the whole design
  // is built to avoid.
  const choices = items
    .filter(entry => entry.type !== 'pad' && entry.id !== item.id)
    .map(entry => ({
      value: entry.id,
      label: entry.type === 'prompt' ? promptTitleOf(entry) : railItemDisplayLabel(entry),
    }))
    .sort((a, b) => a.label.localeCompare(b.label))

  const slotCell = (key: RailPadSlotKey) => {
    const binding = pad?.slots[key]
    const target = binding ? items.find(entry => entry.id === binding.item) : undefined
    const mode = railPadSlotMode(binding, target, rings)
    const auto = defaultPadTriggerMode(target, rings)
    const at = parsePadSlotKey(key)
    return <div class="rail-pad-cell" key={key}>
      <span class="rail-pad-cell-name">
        {at ? padWedgeName(at.wedge, wedges, at.ring) : key}
      </span>
      <Dropdown
        value={binding?.item || ''}
        onChange={value => onSlot(key, { item: value || null, mode: binding?.mode })}
        options={[{ value: '', get label() { return t("RailEditor.168c1a669c") } }, ...choices]}
      />
      {binding && <Dropdown
        value={binding.mode || ''}
        onChange={value => onSlot(key, { item: binding.item, mode: (value || undefined) as RailPadTriggerMode | undefined })}
        options={[
          { value: '', get label() { return t("RailEditor.2e423a150a", { value1: TRIGGER_LABEL[auto].toLowerCase() }) } },
          // Repeat-on-push-out has no meaning on a ringed pad: the outer band is already a
          // different slot there. Offered only where it can actually happen, rather than
          // listed and then silently resolved to something else.
          ...RAIL_PAD_TRIGGER_MODES
            .filter(value => value !== 'enter-repeat-far' || rings === 1)
            .map(value => ({ value, label: TRIGGER_LABEL[value] })),
        ]}
      />}
      {binding && mode === 'release' && <small class="rail-pad-cell-note">{t("RailEditor.7867a44643")}</small>}
      {binding && mode === 'enter-repeat-far' && <small class="rail-pad-cell-note">{t("RailEditor.2684d2b08b")}</small>}
    </div>
  }

  // Drawn left to right, which is the reverse of the wedge index: wedge 0 is the rightmost,
  // because the fan's angles grow counter-clockwise from due east. The far ring is the top
  // row for the same literal reason - it is further from the finger.
  const rows: RailPadSlotKey[][] = []
  for (let ring = rings - 1; ring >= 0; ring -= 1) {
    const row: RailPadSlotKey[] = []
    for (let wedge = wedges - 1; wedge >= 0; wedge -= 1) row.push(padSlotKey(ring, wedge))
    rows.push(row)
  }

  return <fieldset class="rail-detail-group rail-pad-editor">
    <legend>{t("RailEditor.f458616540")}</legend>
    <label>{rich("RailEditor.eb6fd64d5d", { value1: <Dropdown
      value={String(wedges)}
      onChange={value => onShape({ wedges: Number(value) })}
      title={t("RailEditor.e0b375df0e")}
      options={Array.from({ length: RAIL_PAD_MAX_WEDGES }, (_, index) => ({
        value: String(index + 1),
        get label() { return t("RailEditor.eff53e36f5", { value1: index + 1 }) },
      }))}
    /> })}</label>
    <label>{rich("RailEditor.11f025f204", { value1: <Dropdown
      value={String(rings)}
      onChange={value => onShape({ rings: Number(value) })}
      title={t("RailEditor.bfe62713a3")}
      options={[
        { value: '1', get label() { return t("RailEditor.0d6f8f2cb1") } },
        { value: '2', get label() { return t("RailEditor.eaa48c5f1f") } },
      ]}
    /> })}</label>
    {rows.map((row, index) => <div class="rail-pad-grid" key={index} style={{ gridTemplateColumns: `repeat(${wedges}, minmax(0, 1fr))` }}>
      {row.map(slotCell)}
    </div>)}
    <p class="rail-add-note">{rich("RailEditor.557183692f", { value1: rings > 1 && t("RailEditor.2148ba0fc7"), value2: wedges >= 5 && t("RailEditor.33fe30f9e2") })}</p>
  </fieldset>
}

type AddDraft = { type: RailItemType; name: string; label: string; submit: boolean; target: RailAddTarget }

/** Adding an action places it into *both* device layouts, because a button you
 *  must remember to add twice is a button that never reaches the phone. In a
 *  project scope it can be added for that project alone (the default there) or
 *  for every project. */
function RailAddForm({ items, prompts, projectName, scopeName, onAdd }: {
  items: readonly RailItem[]
  prompts: PromptTemplate[]
  /** Non-empty enables the project-scope choice (named for the note). */
  projectName: string
  /** The Project whose layout is open, forks included; '' for the global layout.
   *  Only used to tell a template from somewhere else apart from one from here. */
  scopeName: string
  onAdd: (item: RailItem, target: RailAddTarget) => void
}) {
  const backends = allRailBackends()
  const [draft, setDraft] = useState<AddDraft>({ type: 'skill', name: '', label: '', submit: true, target: projectName ? 'project' : 'global' })
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
      // An empty label field means "call it what the template is called", which is
      // a live answer rather than a copy taken now (`railItemLabel`).
      const typed = draft.label.trim()
      onAdd({
        id: uniqueId(`custom:prompt:${template.id}`),
        type: 'prompt',
        label: typed || template.title,
        ...(typed ? {} : { autoLabel: true }),
        promptKey: template.key,
        backends: restricted,
      }, target)
      setDraft({ ...draft, name: '', label: '' })
      return
    }
    const base = name.replace(/^[/$]/, '').trim() || name
    const label = draft.label.trim() || (draft.type === 'text' ? base.slice(0, 12) : base)
    const id = uniqueId(`custom:${draft.type}:${base}`)
    // A pad arrives empty and cardinal. Its directions are bound afterwards, in the
    // picture of the pad under "All actions", because binding four things is a different
    // job from naming one and cramming both into this form would make every other type
    // pay for it.
    if (draft.type === 'pad') {
      onAdd({ id, type: 'pad', label, className: 'term-key', pad: { wedges: 3, rings: 1, slots: {} } }, target)
      setDraft({ ...draft, name: '', label: '' })
      return
    }
    onAdd(draft.type === 'text'
      ? { id, type: 'text', label, text: name, submit: draft.submit }
      : { id, type: draft.type, label, text: base, submit: draft.submit }, target)
    setDraft({ ...draft, name: '', label: '' })
  }

  return <details class="rail-add">
    <summary>{rich("RailEditor.6fafe24ad6", { value1: <small>{t("RailEditor.598246f83f")}</small> })}</summary>
    <div class="rail-add-form">
      <label>{rich("RailEditor.1fcbc7e095", { value1: <Dropdown value={draft.type} onChange={value => setDraft({ ...draft, type: value as RailItemType })} options={[
        { value: 'skill', get label() { return t("RailEditor.6df1bb18a5") } },
        { value: 'slash', get label() { return t("RailEditor.85dec3aee0") } },
        { value: 'text', get label() { return t("RailEditor.744fe34f1e") } },
        { value: 'prompt', get label() { return t("RailEditor.abae9cbc1b") } },
        { value: 'pad', get label() { return t("RailEditor.c98326fde5") } },
      ]}/> })}</label>
      {draft.type === 'pad' ? <label>{rich("RailEditor.caa4a06313", { value1: <input value={draft.name} placeholder={t("RailEditor.9ee0ca3e24")} onInput={event => setDraft({ ...draft, name: event.currentTarget.value })} /> })}</label> : draft.type === 'prompt' ? <label>{rich("RailEditor.79c8b33c8e", { value1: <Dropdown value={draft.name} onChange={value => setDraft({ ...draft, name: value })} options={[
          { value: '', get label() { return t("RailEditor.8689580b81") } },
          // Every row says which library it is in, because a Project template
          // only resolves inside that Project and the title alone cannot say so.
          ...prompts.map(template => ({
            value: template.key,
            get label() { return t("RailEditor.c5725bfc26", { value1: (template.favorite) ? 'yes' : 'no', title: template.title, value3: template.scope === 'project' ? (template.project_name || 'project') : 'global', value4: template.variables.length ? ` · ${template.variables.length} field${template.variables.length === 1 ? '' : 's'}` : '' }) },
          })),
        ]}/> })}</label> : <label>{draft.type === 'text' ? t("RailEditor.bc471a63ce") : t("RailEditor.dcd1d5223f")}<input value={draft.name} placeholder={draft.type === 'skill' ? t("RailEditor.9505cacb7c") : draft.type === 'slash' ? t("RailEditor.11507a0e2f") : t("RailEditor.2fff2f99e1")} onInput={event => setDraft({ ...draft, name: event.currentTarget.value })} /></label>}
      {draft.type !== 'pad' && <label>{rich("RailEditor.caa4a06313", { value1: <input value={draft.label} placeholder={t("RailEditor.45961044b3")} onInput={event => setDraft({ ...draft, label: event.currentTarget.value })} /> })}</label>}
      {projectName && <label>{rich("RailEditor.44945c9c85", { value1: <Dropdown value={draft.target} onChange={value => setDraft({ ...draft, target: value as RailAddTarget })} options={[
        { value: 'project', get label() { return t("RailEditor.159b2bb5dd", { projectName: projectName }) } },
        { value: 'global', get label() { return t("RailEditor.4b87271b6b") } },
      ]}/> })}</label>}
      {draft.type !== 'prompt' && draft.type !== 'pad' && <label class="check"><span>{t("RailEditor.713ac73bd5")}</span><input type="checkbox" checked={draft.submit} onChange={event => setDraft({ ...draft, submit: event.currentTarget.checked })} /></label>}
      <button class="primary" type="button" disabled={!draft.name.trim()} onClick={add}>{t("RailEditor.6390ba9cbb")}</button>
    </div>
    {draft.type === 'pad' && <p class="rail-add-note">{t("RailEditor.1009ea1b3b")}</p>}
    {draft.type === 'prompt' && <p class="rail-add-note">{prompts.length ? t("RailEditor.defdcca55f") : t("RailEditor.f298b9d2cc")}</p>}
    {/* Why a Project's templates are absent here, said in place. Structural rather
        than a warning, because the alternative is a button that inserts nothing in
        every other Project (`prompt-library.md`). */}
    {draft.type === 'prompt' && !scopeName && <p class="rail-add-note">{t("RailEditor.d1b04b1b6d")}</p>}
    <p class="rail-add-note">{rich("RailEditor.b68a4fadcd", { value1: <code>{t("RailEditor.76bb045a39")}</code>, value2: <code>{t("RailEditor.38a30440f2")}</code>, value3: <code>{t("RailEditor.76bb045a39")}</code> })}</p>
  </details>
}
