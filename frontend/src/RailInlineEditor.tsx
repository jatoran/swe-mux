import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import {
  isBuiltinRailId, railItemVisible, railPayload,
  type RailConfig, type RailItem,
} from './commandRail'
import { insertRailItem, removeRailEntry, moveRailEntry, type RailDropTarget, type RailRef } from './railLayout'
import { beginRailDrag, railRefKey, type RailDragHost, type RailDragPreview } from './railDrag'
import { applyScopedRail, type ResolvedRail } from './railScope'
import { currentProfile, currentRailBlob, loadResolvedRail, saveRailBlob } from './deviceSettings'
import { harnessDisplayName } from './harnessRegistry'

// In-place rail customization: the gear on a terminal's Action rail flips the
// rail area into this editor, so the arrangement is edited where it is used —
// on the real device, for the real backend — instead of in an abstraction two
// surfaces away. Most rail edits are one reorder or one removal; those should
// never require the full Configure Actions modal, which stays one click away
// behind "All options…" for rows, the Drawer, the other device, and creation.
//
// It edits the same effective config the rail renders (`railScope.ts` routes
// each change to the scope that owns it — shared rows to the global layout,
// project rows to the project). Items another backend would hide stay visible
// here but dimmed, because hiding them would make "why is this button not on my
// shell rail" undiagnosable from the one surface meant to answer it.

interface Props {
  projectId?: string
  backend: string
  /** Open the full Configure Actions modal (and close this editor). */
  onOpenFull?: () => void
  onClose: () => void
}

export function RailInlineEditor({ projectId, backend, onOpenFull, onClose }: Props) {
  const device = currentProfile()
  const rootRef = useRef<HTMLDivElement>(null)
  const [resolved, setResolved] = useState<ResolvedRail>(() => loadResolvedRail(projectId))
  const resolvedRef = useRef(resolved)
  resolvedRef.current = resolved
  const [drag, setDrag] = useState<RailDragPreview>({ key: null, config: null, active: false })
  const dragCancelRef = useRef<(() => void) | null>(null)
  const [pickerRow, setPickerRow] = useState<string | null>(null)
  const [pickerQuery, setPickerQuery] = useState('')

  const refresh = () => {
    const next = loadResolvedRail(projectId)
    resolvedRef.current = next
    setResolved(next)
  }
  useEffect(() => {
    refresh()
    window.addEventListener('mux:settings-changed', refresh)
    return () => window.removeEventListener('mux:settings-changed', refresh)
  }, [projectId])
  useEffect(() => () => dragCancelRef.current?.(), [])

  const commitConfig = (next: RailConfig) => {
    if (next === resolvedRef.current.config) return
    void saveRailBlob(applyScopedRail(currentRailBlob(), projectId, resolvedRef.current, next))
    refresh()
  }

  const dragHost: RailDragHost = {
    root: () => rootRef.current,
    config: () => resolvedRef.current.config,
    setPreview: setDrag,
    commit: commitConfig,
    canDrop: (target, itemId) => !resolvedRef.current.projectItemIds.has(itemId)
      || resolvedRef.current.projectRowIds.has(target.rowId),
  }

  const shown = drag.config || resolved.config
  const byId = useMemo(() => new Map(shown.items.map(item => [item.id, item])), [shown])
  const rows = shown.layouts[device].strip
  const scopeNote = resolved.kind === 'fork'
    ? 'this project only'
    : 'the shared layout (all projects); rows marked "project" stay here'

  const itemMeta = (item: RailItem): string => {
    const payload = item.type === 'skill' || item.type === 'slash' ? railPayload(item, backend) : ''
    return `${isBuiltinRailId(item.id) ? item.type : `custom ${item.type}`}${payload ? ` · ${payload}` : item.type === 'text' ? ` · "${(item.text || '').slice(0, 24)}"` : ''}`
  }

  const onChipKey = (event: JSX.TargetedKeyboardEvent<HTMLElement>, ref: RailRef) => {
    const config = resolvedRef.current.config
    const stripRows = config.layouts[device].strip
    const rowIndex = stripRows.findIndex(row => row.id === ref.rowId)
    if (rowIndex < 0) return
    const itemId = stripRows[rowIndex].items[ref.index]
    const allowed = (rowId: string) => !resolvedRef.current.projectItemIds.has(itemId)
      || resolvedRef.current.projectRowIds.has(rowId)
    const move = (to: RailDropTarget) => {
      if (!allowed(to.rowId)) return
      event.preventDefault()
      commitConfig(moveRailEntry(config, ref, to))
      const next = `[data-reorder-id="${railRefKey(to)}"]`
      requestAnimationFrame(() => rootRef.current?.querySelector<HTMLElement>(next)?.focus())
    }
    if (event.key === 'ArrowLeft' && ref.index > 0) return move({ ...ref, index: ref.index - 1 })
    if (event.key === 'ArrowRight' && ref.index < stripRows[rowIndex].items.length - 1) return move({ ...ref, index: ref.index + 1 })
    if (event.key === 'ArrowUp' && rowIndex > 0) {
      const above = stripRows[rowIndex - 1]
      return move({ ...ref, rowId: above.id, index: Math.min(ref.index, above.items.length) })
    }
    if (event.key === 'ArrowDown' && rowIndex < stripRows.length - 1) {
      const below = stripRows[rowIndex + 1]
      return move({ ...ref, rowId: below.id, index: Math.min(ref.index, below.items.length) })
    }
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault()
      commitConfig(removeRailEntry(config, ref))
    }
  }

  const addToRow = (rowId: string, itemId: string) => {
    const config = resolvedRef.current.config
    const row = config.layouts[device].strip.find(entry => entry.id === rowId)
    if (!row) return
    commitConfig(insertRailItem(config, itemId, { device, surface: 'strip', rowId, index: row.items.length }))
    setPickerRow(null)
    setPickerQuery('')
  }

  /** What the picker offers for a row: the whole catalog (duplicates are legal),
   *  minus project-owned actions when the row is shared — a shared row is global
   *  state that cannot reference them. */
  const pickerItems = (rowId: string): RailItem[] => {
    const shared = !resolved.projectRowIds.has(rowId)
    const needle = pickerQuery.trim().toLowerCase()
    return resolved.config.items.filter(item =>
      (!shared || !resolved.projectItemIds.has(item.id)) &&
      (!needle || `${item.label} ${item.id} ${itemMeta(item)}`.toLowerCase().includes(needle)))
  }

  return <div class="rail-inline-editor" ref={rootRef}>
    <header class="rail-inline-head">
      <strong>Customize rail</strong>
      <span class="rail-scope-note">Changes apply to {scopeNote}.</span>
      {onOpenFull && <button type="button" title="Open the full editor: rows, the Drawer, the other device, custom actions" onClick={onOpenFull}>All options…</button>}
      <button type="button" class="primary" onClick={onClose}>Done</button>
    </header>
    {rows.map(row => {
      const projectRow = resolved.projectRowIds.has(row.id)
      return <div class={`rail-inline-row${projectRow ? ' project-owned' : ''}`} key={row.id}>
        {projectRow && <span class="rail-origin project" title="Only this project has this row">project</span>}
        <div class="rail-chips" data-rail-row={`${device}|strip|${row.id}`} role="group" aria-label={row.label || 'Rail row'}>
          {row.items.map((itemId, index) => {
            const ref: RailRef = { device, surface: 'strip', rowId: row.id, index }
            const key = railRefKey(ref)
            const item = byId.get(itemId)
            const label = item?.label || itemId
            const hidden = !!item && !railItemVisible(item, backend)
            return <div
              key={key}
              class={`rail-chip${drag.key === key && drag.active ? ' dragging' : ''}${item ? '' : ' missing'}${hidden ? ' filtered' : ''}`}
              data-reorder-id={key}
              tabIndex={0}
              role="button"
              title={`${label}${item ? ` — ${itemMeta(item)}` : ''}${hidden ? `\nNot shown in ${harnessDisplayName(backend)} sessions (still on other rails).` : ''}\nDrag to move. Arrows move it, Delete removes it.`}
              onPointerDown={event => { dragCancelRef.current = beginRailDrag(event, dragHost, { kind: 'chip', ref }, label) }}
              onKeyDown={event => onChipKey(event, ref)}
            >
              <span class="rail-chip-label">{label}</span>
              <button
                type="button"
                class="rail-chip-remove"
                tabIndex={-1}
                aria-label={`Remove ${label} from the rail`}
                title="Remove from the rail"
                onPointerDown={event => event.stopPropagation()}
                onClick={() => commitConfig(removeRailEntry(resolvedRef.current.config, ref))}
              >×</button>
            </div>
          })}
          {!row.items.length && <span class="rail-chips-empty">drag actions here</span>}
        </div>
        <button
          type="button"
          class="rail-inline-add"
          aria-expanded={pickerRow === row.id}
          title="Add an action to this row"
          onClick={() => { setPickerRow(current => current === row.id ? null : row.id); setPickerQuery('') }}
        >+</button>
      </div>
    })}
    {pickerRow && <div class="rail-inline-picker">
      <div class="rail-inline-picker-head">
        <input
          type="search"
          value={pickerQuery}
          placeholder="Find an action…"
          aria-label="Find an action to add"
          onInput={event => setPickerQuery(event.currentTarget.value)}
        />
        {onOpenFull && <button type="button" title="Create a new skill, slash command, text macro, or prompt button" onClick={onOpenFull}>New action…</button>}
        <button type="button" aria-label="Close the action picker" onClick={() => setPickerRow(null)}>×</button>
      </div>
      <div class="rail-inline-picker-list">
        {pickerItems(pickerRow).map(item => <button
          type="button"
          key={item.id}
          class={railItemVisible(item, backend) ? '' : 'filtered'}
          title={`${item.label} — ${itemMeta(item)}${railItemVisible(item, backend) ? '' : `\nNot shown in ${harnessDisplayName(backend)} sessions.`}`}
          onClick={() => addToRow(pickerRow, item.id)}
        >
          <span>{item.label}</span>
          <small>{itemMeta(item)}</small>
        </button>)}
        {!pickerItems(pickerRow).length && <p class="rail-add-note">No action matches “{pickerQuery}”.</p>}
      </div>
    </div>}
    <p class="rail-inline-hint">
      Dimmed buttons are hidden for {harnessDisplayName(backend)} sessions.
      Drawer items live under Actions → Quick actions; arrange them in All options.
    </p>
  </div>
}
