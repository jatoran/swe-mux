import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { api } from './api'
import {
  allRailBackends, defaultRailConfig, isBuiltinRailId, railPayload,
  RAIL_DEVICES, RAIL_SURFACES,
  type RailBackend, type RailConfig, type RailDevice, type RailItem, type RailItemType, type RailSurface,
} from './commandRail'
import {
  addRailCatalogItem, addRailRow, copyRailSurface, deleteRailCatalogItem, dropIndexForPoint,
  insertRailItem, moveRailEntry, moveRailRow, railPlacementCounts, railRowAt, removeRailEntry,
  removeRailRow, setRailRowLabel, toggleRailPlacement,
  type RailDropTarget, type RailRef,
} from './railLayout'
import { clearProjectRail, loadRailConfig, projectRailIsCustom, saveRailConfig } from './deviceSettings'
import { MOBILE_PROJECT_HOLD_DRAG, POINTER_MOVE_DRAG, edgeAutoScrollDelta, pointerDragMoveDecision } from './dragReorder'
import { claimPointerDrag } from './pointerDragClaim'
import { promptDeliveryHarnesses } from './harnessRegistry'
import { fetchPromptTemplates, promptItemSummary } from './promptRail'
import type { PromptTemplate } from './PromptLibrary'
import type { Project } from './types'

// The Action configuration surface.
//
// Two things are being edited here and they are deliberately not the same thing:
//
//  * the **catalog** at the bottom - what actions exist, what they inject, which
//    backends they mean anything for;
//  * the **layouts** above it — one per device class, each with rows for the strip
//    under the terminal and for the drawer's Quick actions section.
//
// Desktop and mobile have genuinely separate arrangements, so there is no shared
// row and no "applies to both" switch. What keeps two layouts manageable instead:
// adding an action places it on *both* devices (a button you must remember to add
// twice is a button that never reaches the phone), the catalog's placement badges
// say at a glance where each action actually is, and a per-surface copy seeds one
// device from the other as a one-shot.
//
// Everything commits immediately, like the other device-settings domains, rather
// than through the config draft's Save button.

const SURFACE_LABEL: Record<RailSurface, string> = { strip: 'Rail', panel: 'Drawer' }
const SURFACE_HINT: Record<RailSurface, string> = {
  strip: 'the scrolling strip under the terminal',
  panel: 'Quick actions in the Actions drawer',
}
const DEVICE_LABEL: Record<RailDevice, string> = { desktop: 'Desktop', mobile: 'Mobile' }
const OTHER_DEVICE: Record<RailDevice, RailDevice> = { desktop: 'mobile', mobile: 'desktop' }

/** Wide enough to show both device layouts side by side. Below it the editor
 *  keeps one column and a device switch, because two columns of chips on a phone
 *  are two columns of nothing. */
const TWO_COLUMN_QUERY = '(min-width: 1040px)'

const refKey = (ref: RailRef): string => `${ref.device}|${ref.surface}|${ref.rowId}|${ref.index}`
const rowKey = (device: RailDevice, surface: RailSurface, rowId: string): string => `${device}|${surface}|${rowId}`

type DragSource = { kind: 'chip'; ref: RailRef } | { kind: 'catalog'; itemId: string }

function scrollableAncestor(start: HTMLElement | null): HTMLElement | null {
  for (let node = start; node; node = node.parentElement) {
    const overflow = getComputedStyle(node).overflowY
    if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight + 1) return node
  }
  return null
}

/** Read the drop target under a point straight from the DOM. Rows advertise
 *  themselves with `data-rail-row` and chips with `data-reorder-id`, so this needs
 *  no registry of live element refs. */
function targetUnderPoint(x: number, y: number, draggedKey: string | null): RailDropTarget | null {
  const element = document.elementFromPoint(x, y)
  const rowElement = element instanceof Element ? element.closest<HTMLElement>('[data-rail-row]') : null
  if (!rowElement) return null
  const [device, surface, rowId] = (rowElement.dataset.railRow || '').split('|')
  if (!device || !surface || !rowId) return null
  const rects = Array.from(rowElement.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).map(node => {
    const box = node.getBoundingClientRect()
    return { key: node.dataset.reorderId || '', left: box.left, right: box.right, top: box.top, bottom: box.bottom }
  })
  return {
    device: device as RailDevice,
    surface: surface as RailSurface,
    rowId,
    index: dropIndexForPoint(rects, draggedKey, x, y),
  }
}

export function RailEditor() {
  const backends = allRailBackends()
  const rootRef = useRef<HTMLElement>(null)
  // '' = the shared global config; a project id edits that project's override
  // (seeded from the global one on first change).
  const [scope, setScope] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [config, setConfig] = useState<RailConfig>(() => loadRailConfig())
  const [prompts, setPrompts] = useState<PromptTemplate[]>([])
  const [twoColumn, setTwoColumn] = useState(() =>
    typeof window === 'undefined' || !window.matchMedia ? true : window.matchMedia(TWO_COLUMN_QUERY).matches)
  // Which layout the single-column (narrow) view shows. Ignored when both fit.
  const [soloDevice, setSoloDevice] = useState<RailDevice>('desktop')
  const [note, setNote] = useState('')

  useEffect(() => {
    const reload = () => setConfig(loadRailConfig(scope || undefined))
    reload()
    window.addEventListener('mux:settings-changed', reload)
    return () => window.removeEventListener('mux:settings-changed', reload)
  }, [scope])
  useEffect(() => { void api<Project[]>('GET', '/api/projects').then(setProjects).catch(() => {}) }, [])
  // Prompt templates the rail can point at. Scoped like the config being edited, so
  // a project's rail can carry that project's own templates as well as the global ones.
  useEffect(() => {
    void fetchPromptTemplates(scope || undefined).then(setPrompts).catch(() => setPrompts([]))
  }, [scope])
  useEffect(() => {
    const query = window.matchMedia?.(TWO_COLUMN_QUERY)
    if (!query) return
    const sync = () => setTwoColumn(query.matches)
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])

  const configRef = useRef(config)
  configRef.current = config
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const commit = (next: RailConfig) => {
    if (next === configRef.current) return
    configRef.current = next
    setConfig(next)
    void saveRailConfig(next, scopeRef.current || undefined)
  }

  // Live drag state. `preview` is the config the drop would commit, so what is on
  // screen mid-drag is literally the result rather than an approximation; `key`
  // is where the dragged chip currently renders, which the hit test must exclude.
  const [drag, setDrag] = useState<{ key: string | null; preview: RailConfig | null; active: boolean }>(
    { key: null, preview: null, active: false })
  const dragCancelRef = useRef<(() => void) | null>(null)
  useEffect(() => () => dragCancelRef.current?.(), [])

  const shown = drag.preview || config
  const scopeCustom = !!scope && projectRailIsCustom(scope)
  const catalogById = useMemo(() => new Map(shown.items.map(item => [item.id, item])), [shown])
  const devices: RailDevice[] = twoColumn ? [...RAIL_DEVICES] : [soloDevice]

  const beginDrag = (event: JSX.TargetedPointerEvent<HTMLElement>, source: DragSource, label: string) => {
    if (event.button !== 0 || !event.isPrimary) return
    const root = rootRef.current
    if (!root) return
    const node = event.currentTarget
    const pointerId = event.pointerId
    const startX = event.clientX, startY = event.clientY
    const activation = event.pointerType === 'touch' ? MOBILE_PROJECT_HOLD_DRAG : POINTER_MOVE_DRAG
    const scroller = scrollableAncestor(node)

    let active = false, done = false
    let ghost: HTMLDivElement | null = null
    let holdTimer: number | null = null
    let scrollFrame: number | null = null
    let releaseClaim: (() => void) | null = null
    let latest = { x: startX, y: startY }
    // Where the dragged chip renders right now, and the config a drop would save.
    let previewRef: RailRef | null = source.kind === 'chip' ? source.ref : null
    let previewConfig: RailConfig | null = null
    setNote('')

    const recompute = () => {
      const base = configRef.current
      const target = targetUnderPoint(latest.x, latest.y, previewRef ? refKey(previewRef) : null)
      if (!target) {
        // Off every row: fall back to the untouched config, so letting go over
        // nothing leaves a chip where it was and never places a catalog entry.
        previewRef = source.kind === 'chip' ? source.ref : null
        previewConfig = null
        setDrag({ key: previewRef ? refKey(previewRef) : null, preview: null, active: true })
        return
      }
      const itemId = source.kind === 'chip' ? (railRowAt(base, source.ref.device, source.ref.surface, source.ref.rowId)?.items[source.ref.index] ?? null) : source.itemId
      if (itemId === null) return
      // Always recomputed from the committed config rather than from the last
      // preview, so a long drag cannot accumulate drift.
      const without = source.kind === 'chip' ? removeRailEntry(base, source.ref) : base
      const row = railRowAt(without, target.device, target.surface, target.rowId)
      if (!row) return
      const index = Math.max(0, Math.min(target.index, row.items.length))
      previewRef = { device: target.device, surface: target.surface, rowId: target.rowId, index }
      previewConfig = insertRailItem(without, itemId, { ...target, index })
      setDrag({ key: refKey(previewRef), preview: previewConfig, active: true })
    }

    const stopAutoScroll = () => {
      if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame)
      scrollFrame = null
    }
    const autoScroll = () => {
      scrollFrame = null
      if (!active || !scroller) return
      const box = scroller.getBoundingClientRect()
      const delta = edgeAutoScrollDelta(latest.y, box.top, box.bottom)
      if (delta !== 0) {
        const before = scroller.scrollTop
        scroller.scrollTop += delta
        if (scroller.scrollTop !== before) recompute()
      }
      scrollFrame = window.requestAnimationFrame(autoScroll)
    }

    const finish = (commitDrop: boolean) => {
      if (done) return
      done = true
      dragCancelRef.current = null
      if (holdTimer !== null) window.clearTimeout(holdTimer)
      stopAutoScroll()
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      window.removeEventListener('keydown', onKey, true)
      ghost?.remove()
      if (active) {
        document.body.classList.remove('workspace-pointer-dragging')
        node.classList.remove('dragging')
        try { if (root.hasPointerCapture(pointerId)) root.releasePointerCapture(pointerId) } catch { /* already gone */ }
        releaseClaim?.()
      }
      const landed = commitDrop ? previewConfig : null
      setDrag({ key: null, preview: null, active: false })
      if (landed) commit(landed)
    }

    const activate = () => {
      if (active || done) return
      active = true
      if (holdTimer !== null) window.clearTimeout(holdTimer)
      holdTimer = null
      releaseClaim = claimPointerDrag()
      document.body.classList.add('workspace-pointer-dragging')
      node.classList.add('dragging')
      // Capture on the editor root, not on the chip: the preview reparents the
      // chip between rows, and a captured element that leaves the document loses
      // the pointer mid-drag. The root never moves.
      try { root.setPointerCapture(pointerId) } catch { /* capture is best effort */ }
      ghost = document.createElement('div')
      ghost.className = 'mux-pointer-drag-ghost'
      ghost.textContent = label
      ghost.style.transform = `translate3d(${latest.x + 14}px,${latest.y + 12}px,0)`
      document.body.appendChild(ghost)
      recompute()
      if (scroller && scrollFrame === null) scrollFrame = window.requestAnimationFrame(autoScroll)
    }

    function onMove(pointer: PointerEvent) {
      if (pointer.pointerId !== pointerId) return
      latest = { x: pointer.clientX, y: pointer.clientY }
      if (!active) {
        const decision = pointerDragMoveDecision(activation, Math.hypot(pointer.clientX - startX, pointer.clientY - startY))
        if (decision === 'wait') return
        // A hold-drag that moves before the delay is a scroll, not a drag.
        if (decision === 'cancel') { finish(false); return }
        activate()
      }
      pointer.preventDefault()
      if (ghost) ghost.style.transform = `translate3d(${pointer.clientX + 14}px,${pointer.clientY + 12}px,0)`
      recompute()
    }
    function onUp(pointer: PointerEvent) {
      if (pointer.pointerId !== pointerId) return
      finish(active)
    }
    function onCancel(pointer: PointerEvent) {
      if (pointer.pointerId !== pointerId) return
      finish(false)
    }
    function onKey(keyEvent: KeyboardEvent) {
      if (keyEvent.key !== 'Escape') return
      keyEvent.preventDefault()
      keyEvent.stopPropagation()
      finish(false)
    }

    window.addEventListener('pointermove', onMove, { passive: false })
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    window.addEventListener('keydown', onKey, true)
    dragCancelRef.current = () => finish(false)
    if (activation.mode === 'hold') holdTimer = window.setTimeout(activate, activation.delayMs)
  }

  /** Keyboard placement, and the only reorder path for anyone not using a pointer.
   *  Arrows move the focused chip along its row and between rows; Delete unplaces it. */
  const onChipKey = (event: JSX.TargetedKeyboardEvent<HTMLElement>, ref: RailRef) => {
    const rows = config.layouts[ref.device][ref.surface]
    const rowIndex = rows.findIndex(row => row.id === ref.rowId)
    if (rowIndex < 0) return
    const move = (to: RailDropTarget) => {
      event.preventDefault()
      commit(moveRailEntry(config, ref, to))
      // Follow the chip so a run of arrow presses keeps moving the same one.
      const next = `[data-reorder-id="${refKey(to)}"]`
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
      commit(removeRailEntry(config, ref))
    }
  }

  const resetScope = () => {
    setNote('')
    // A project reverts by dropping its override; the global scope has nothing to
    // fall back to, so it is rewritten with the shipped catalog and layouts.
    if (scope) { void clearProjectRail(scope); setConfig(loadRailConfig(scope)) }
    else commit(defaultRailConfig())
  }

  const itemMeta = (item: RailItem): string => {
    const builtin = isBuiltinRailId(item.id)
    const agentPayloads = promptDeliveryHarnesses().map(harness => railPayload(item, harness.name))
    const preview = item.type === 'skill' ? [...new Set(agentPayloads)].join(' · ')
      : item.type === 'slash' ? agentPayloads[0] || ''
        : item.type === 'text' ? `"${(item.text || '').slice(0, 24)}"`
          : item.type === 'prompt' ? promptItemSummary(item, prompts) : ''
    return `${builtin ? item.type : `custom ${item.type}`}${preview ? ` · ${preview}` : ''}${item.submit && item.type !== 'prompt' ? ' · ⏎' : ''}`
  }

  const toggleBackend = (id: string, backend: RailBackend) => {
    const item = config.items.find(entry => entry.id === id)
    if (!item) return
    const set = new Set(item.backends ?? backends)
    if (set.has(backend)) { if (set.size <= 1) return; set.delete(backend) } else set.add(backend)
    const next = backends.filter(name => set.has(name))
    commit({
      ...config,
      items: config.items.map(entry => entry.id === id
        ? { ...entry, backends: next.length === backends.length ? undefined : next }
        : entry),
    })
  }

  const renderChip = (device: RailDevice, surface: RailSurface, rowId: string, itemId: string, index: number) => {
    const ref: RailRef = { device, surface, rowId, index }
    const key = refKey(ref)
    const item = catalogById.get(itemId)
    const label = item?.label || itemId
    return <div
      key={key}
      class={`rail-chip${drag.key === key && drag.active ? ' dragging' : ''}${item ? '' : ' missing'}`}
      data-reorder-id={key}
      tabIndex={0}
      role="button"
      title={`${label}${item ? ` — ${itemMeta(item)}` : ''}\nDrag to move. Arrows move it, Delete removes it.`}
      onPointerDown={event => beginDrag(event, { kind: 'chip', ref }, label)}
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
        onClick={() => commit(removeRailEntry(config, ref))}
      >×</button>
    </div>
  }

  const renderSurface = (device: RailDevice, surface: RailSurface) => {
    const rows = shown.layouts[device][surface]
    const other = OTHER_DEVICE[device]
    return <section class="rail-surface" key={`${device}-${surface}`}>
      <header class="rail-surface-head">
        <h5>{SURFACE_LABEL[surface]}<small>{SURFACE_HINT[surface]}</small></h5>
        <button
          type="button"
          title={`Replace this device’s ${SURFACE_LABEL[surface].toLowerCase()} with a copy of ${DEVICE_LABEL[other]}’s. A one-shot copy — the two stay independent afterwards.`}
          onClick={() => { commit(copyRailSurface(config, other, device, surface)); setNote(`Copied ${DEVICE_LABEL[other]} ${SURFACE_LABEL[surface].toLowerCase()} to ${DEVICE_LABEL[device]}.`) }}
        >Copy from {DEVICE_LABEL[other]}</button>
        <button type="button" title="Add another row to this surface" onClick={() => commit(addRailRow(config, device, surface))}>+ Row</button>
      </header>
      {rows.map((row, rowIndex) => <article class="rail-row-editor" key={row.id}>
        <div class="rail-row-head">
          <input
            value={row.label || ''}
            placeholder={`Row ${rowIndex + 1}${surface === 'panel' ? ' (name shows as a heading)' : ''}`}
            aria-label={`Name for row ${rowIndex + 1}`}
            onChange={event => commit(setRailRowLabel(config, device, surface, row.id, event.currentTarget.value))}
          />
          <button type="button" disabled={rowIndex === 0} title="Move this row up" onClick={() => commit(moveRailRow(config, device, surface, row.id, -1))}>↑</button>
          <button type="button" disabled={rowIndex === rows.length - 1} title="Move this row down" onClick={() => commit(moveRailRow(config, device, surface, row.id, 1))}>↓</button>
          <button type="button" class="rail-del" title={rows.length === 1 ? 'Empty this row' : 'Delete this row and everything in it'} onClick={() => commit(removeRailRow(config, device, surface, row.id))}>×</button>
        </div>
        <div class="rail-chips" data-rail-row={rowKey(device, surface, row.id)} role="group" aria-label={`${DEVICE_LABEL[device]} ${SURFACE_LABEL[surface]} row ${rowIndex + 1}`}>
          {row.items.map((itemId, index) => renderChip(device, surface, row.id, itemId, index))}
          {!row.items.length && <span class="rail-chips-empty">drag actions here</span>}
        </div>
      </article>)}
    </section>
  }

  return <section class="commandrail-settings" ref={rootRef}>
    <h3>Layouts and catalog</h3>
    <p class="rail-intro">
      Desktop and mobile each get their own arrangement: their own rows, their own order.
      <b> Rail</b> is the strip under the terminal, <b>Drawer</b> is Quick actions in the Actions tab.
      Drag an action between rows, or use the badges in <b>Action catalog</b> below to place it. An action
      in no row simply does not appear on that device.
    </p>
    <div class="rail-toolbar">
      <label class="rail-scope">Editing<select value={scope} onChange={event => setScope(event.currentTarget.value)}>
        <option value="">Global (all projects)</option>
        {projects.map(project => <option value={project.id}>{project.name}{projectRailIsCustom(project.id) ? ' ✎' : ''}</option>)}
      </select></label>
      <button
        type="button"
        onClick={resetScope}
        disabled={!!scope && !scopeCustom}
        title={scope ? (scopeCustom ? 'Drop this project’s copy and inherit the global layout' : 'This project already uses the global layout') : 'Restore the built-in layout and drop custom actions'}
      >{scope ? 'Use global layout' : 'Restore defaults'}</button>
      {!twoColumn && <div class="rail-device-switch" role="group" aria-label="Layout to edit">
        {RAIL_DEVICES.map(name => <button
          type="button"
          class={soloDevice === name ? 'on' : ''}
          aria-pressed={soloDevice === name}
          onClick={() => setSoloDevice(name)}
        >{DEVICE_LABEL[name]}</button>)}
      </div>}
    </div>
    {scope && <p class="rail-scope-note">{scopeCustom
      ? 'This project has its own rail. Editing changes only this project.'
      : 'This project inherits the global rail. Any change here creates a project-specific copy.'}</p>}

    <RailAddForm config={config} prompts={prompts} backends={backends} onAdd={commit} />

    <div class={`rail-devices${twoColumn ? ' two-up' : ''}`}>
      {devices.map(name => <div class="rail-device" key={name}>
        <h4 class="rail-device-name">{DEVICE_LABEL[name]}</h4>
        {RAIL_SURFACES.map(surface => renderSurface(name, surface))}
      </div>)}
    </div>

    <section class="rail-catalog">
      <h4>Action catalog</h4>
      <p class="rail-add-note">
        Every available rail or Drawer action, and where it is placed. The four badges are desktop rail, desktop Drawer,
        mobile rail, and mobile Drawer - click one to place the action there or take it off. Drag a row into a
        layout to put it somewhere exact. <b>cld</b>/<b>cdx</b>/<b>sh</b> limit an action to Claude,
        Codex, or Shell sessions.
      </p>
      <div class="rail-catalog-list">
        {shown.items.map(item => {
          const counts = railPlacementCounts(shown, item.id)
          const placed = RAIL_DEVICES.some(name => RAIL_SURFACES.some(surface => counts[name][surface] > 0))
          const itemBackends = item.backends ?? [...backends]
          const meta = itemMeta(item)
          return <article
            class={`rail-catalog-row${placed ? '' : ' off'}`}
            key={item.id}
            onPointerDown={event => beginDrag(event, { kind: 'catalog', itemId: item.id }, item.label || item.id)}
          >
            <div class="rail-id">
              <span class="rail-name">{item.label || item.id}</span>
              <small class="rail-meta" title={meta}>{meta}</small>
            </div>
            <div class="rail-where" role="group" aria-label={`Where ${item.label || item.id} appears`}>
              {RAIL_DEVICES.map(name => RAIL_SURFACES.map(surface => {
                const count = counts[name][surface]
                return <button
                  type="button"
                  class={count ? 'on' : ''}
                  aria-pressed={count > 0}
                  title={`${DEVICE_LABEL[name]} ${SURFACE_LABEL[surface].toLowerCase()}${count > 1 ? ` — ${count} copies` : ''} (${SURFACE_HINT[surface]})`}
                  onPointerDown={event => event.stopPropagation()}
                  onClick={() => commit(toggleRailPlacement(config, item.id, name, surface))}
                >{name === 'desktop' ? 'D' : 'M'}{surface === 'strip' ? 'r' : 'p'}{count > 1 ? <sup>{count}</sup> : null}</button>
              }))}
            </div>
            <div class="rail-tags" role="group" aria-label={`Which sessions ${item.label || item.id} applies to`}>
              {backends.map(backend => <button
                type="button"
                class={itemBackends.includes(backend) ? 'on' : ''}
                aria-pressed={itemBackends.includes(backend)}
                title={`Available in ${backend} sessions`}
                onPointerDown={event => event.stopPropagation()}
                onClick={() => toggleBackend(item.id, backend)}
              >{backend === 'shell' ? 'sh' : backend.slice(0, 3)}</button>)}
            </div>
            <div class="rail-order">
              {!isBuiltinRailId(item.id) && <button
                type="button"
                class="rail-del"
                title="Delete this action and every button pointing at it"
                onPointerDown={event => event.stopPropagation()}
                onClick={() => commit(deleteRailCatalogItem(config, item.id))}
              >×</button>}
            </div>
          </article>
        })}
      </div>
    </section>

    {note && <p class="rail-scope-note" aria-live="polite">{note}</p>}
  </section>
}

type AddDraft = { type: RailItemType; name: string; label: string; submit: boolean; surface: RailSurface }

/** Adding an action places it into *both* device layouts. Divergence is then one
 *  drag away, but it is a deliberate act rather than something you forget. */
function RailAddForm({ config, prompts, backends, onAdd }: {
  config: RailConfig
  prompts: PromptTemplate[]
  backends: readonly RailBackend[]
  onAdd: (next: RailConfig) => void
}) {
  const [draft, setDraft] = useState<AddDraft>({ type: 'skill', name: '', label: '', submit: true, surface: 'panel' })

  const uniqueId = (base: string): string => {
    let candidate = base, suffix = 1
    while (config.items.some(item => item.id === candidate)) { suffix += 1; candidate = `${base}:${suffix}` }
    return candidate
  }

  const add = () => {
    const name = draft.name.trim()
    if (!name) return
    // A prompt item stores the library key, never the body: the template stays the
    // source of truth, so editing it updates every button pointing at it.
    if (draft.type === 'prompt') {
      const template = prompts.find(entry => entry.key === name)
      if (!template) return
      // Templates declare their compatible backends; carry that through instead of
      // making the user re-pick it, but only when it is actually a restriction.
      const restricted = template.backends.length === backends.length ? undefined : [...template.backends]
      onAdd(addRailCatalogItem(config, {
        id: uniqueId(`custom:prompt:${template.id}`),
        type: 'prompt',
        label: draft.label.trim() || template.title,
        promptKey: template.key,
        defaultSurface: draft.surface,
        backends: restricted,
      }))
      setDraft({ ...draft, name: '', label: '' })
      return
    }
    const base = name.replace(/^[/$]/, '').trim() || name
    const label = draft.label.trim() || (draft.type === 'text' ? base.slice(0, 12) : base)
    const id = uniqueId(`custom:${draft.type}:${base}`)
    onAdd(addRailCatalogItem(config, draft.type === 'text'
      ? { id, type: 'text', label, text: name, submit: draft.submit, defaultSurface: draft.surface }
      : { id, type: draft.type, label, text: base, submit: draft.submit, defaultSurface: draft.surface }))
    setDraft({ ...draft, name: '', label: '' })
  }

  return <details class="rail-add" open>
    <summary>Add custom action</summary>
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
      {draft.type !== 'prompt' && <label class="check"><span>Submit with Enter</span><input type="checkbox" checked={draft.submit} onChange={event => setDraft({ ...draft, submit: event.currentTarget.checked })} /></label>}
      <button class="primary" type="button" disabled={!draft.name.trim()} onClick={add}>Add action</button>
    </div>
    {draft.type === 'prompt' && <p class="rail-add-note">{prompts.length
      ? 'A prompt button points at the template, so editing the template updates the button. It inserts without sending - templates with {{fields}} open Prompt templates in Actions to be filled in first.'
      : 'No prompt templates yet. Create one from Actions → Prompt templates → Manage, then it appears here.'}</p>}
    <p class="rail-add-note">
      New actions land at the end of the chosen surface on <b>both</b> devices; drag them where you want
      from there. Skills inject <code>/name</code> in Claude and <code>$name</code> in Codex; slash commands
      inject <code>/name</code> in both. Built-in actions can be placed and filtered, but not edited.
    </p>
  </details>
}
