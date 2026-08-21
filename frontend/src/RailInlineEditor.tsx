import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import {
  isBuiltinRailId, railItemVisible, railPayload,
  type RailConfig, type RailItem,
} from './commandRail'
import { insertRailItem, removeRailEntry, moveRailEntry, type RailDropTarget, type RailRef } from './railLayout'
import { beginRailDrag, railRefKey, type RailDragHost, type RailDragPreview } from './railDrag'
import {
  applyScopedRail, hideScopedRailEntry, isProjectRailPlacement, unhideScopedRailEntry,
  type ResolvedRail,
} from './railScope'
import { currentProfile, currentRailBlob, loadResolvedRail, saveRailBlob } from './deviceSettings'
import { harnessDisplayName } from './harnessRegistry'
import { railItemLabel } from './promptRail'
import { usePromptTitles } from './promptTitles'

// In-place rail customization: the gear on a terminal's Action rail flips the
// rail area into this editor, so the arrangement is edited where it is used —
// on the real device, for the real backend — instead of in an abstraction two
// surfaces away. Most rail edits are one reorder or one removal; those should
// never require the full Configure Actions modal, which stays one click away
// behind "All options…" for rows, the Drawer, the other device, and creation.
//
// It edits the same effective config the rail renders (`railScope.ts` routes
// each change to the scope that owns it — shared rows to the global layout,
// project rows and project placements to the project). Items another backend
// would hide stay visible here but dimmed, because hiding them would make "why is
// this button not on my shell rail" undiagnosable from the one surface meant to
// answer it.
//
// In a project scope, three things a shared row's chip can be are drawn apart,
// because they are three different acts: an entry the project placed there itself
// (marked, and removable here alone), an entry of the shared row (whose × removes
// it for everybody, and whose ⊘ hides it here only), and a ghost — something this
// project hides, drawn where the shared row still holds it, because a hidden
// button is in no row and nothing else could offer to bring it back.

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
    // No refused targets: a project action dropped into a shared row is recorded
    // as a splice, so the row's definition stays global (`railScope.ts`).
  }

  const shown = drag.config || resolved.config
  const byId = useMemo(() => new Map(shown.items.map(item => [item.id, item])), [shown])
  const rows = shown.layouts[device].strip
  // Live titles for prompt buttons pinned without a name of their own; free unless
  // this rail actually carries one (`promptTitles.ts`).
  const promptTitles = usePromptTitles(projectId, shown.items.some(item => item.type === 'prompt' && item.autoLabel))
  const scopeNote = resolved.kind === 'fork'
    ? 'this project only'
    : 'the shared layout (all projects); rows marked "project" stay here'

  const itemMeta = (item: RailItem): string => {
    const payload = item.type === 'skill' || item.type === 'slash' ? railPayload(item, backend) : ''
    return `${isBuiltinRailId(item.id) ? item.type : `custom ${item.type}`}${payload ? ` · ${payload}` : item.type === 'text' ? ` · "${(item.text || '').slice(0, 24)}"` : ''}`
  }
  const chipLabel = (item: RailItem): string => railItemLabel(item, promptTitles)

  const onChipKey = (event: JSX.TargetedKeyboardEvent<HTMLElement>, ref: RailRef) => {
    const config = resolvedRef.current.config
    const stripRows = config.layouts[device].strip
    const rowIndex = stripRows.findIndex(row => row.id === ref.rowId)
    if (rowIndex < 0) return
    const move = (to: RailDropTarget) => {
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

  /** What the picker offers for a row: the whole catalog, duplicates included.
   *  Project-owned actions used to be withheld from shared rows; a delta now
   *  records that placement as a splice, so nothing is withheld any more. */
  const pickerItems = (): RailItem[] => {
    const needle = pickerQuery.trim().toLowerCase()
    return resolved.config.items.filter(item =>
      !needle || `${chipLabel(item)} ${item.id} ${itemMeta(item)}`.toLowerCase().includes(needle))
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
      const hiddenHere = resolved.hiddenEntries.get(`${device}|strip|${row.id}`) || []
      return <div class={`rail-inline-row${projectRow ? ' project-owned' : ''}`} key={row.id}>
        {projectRow && <span class="rail-origin project" title="Only this project has this row">project</span>}
        <div class="rail-chips" data-rail-row={`${device}|strip|${row.id}`} role="group" aria-label={row.label || 'Rail row'}>
          {row.items.map((itemId, index) => {
            const ref: RailRef = { device, surface: 'strip', rowId: row.id, index }
            const key = railRefKey(ref)
            const item = byId.get(itemId)
            const label = item ? chipLabel(item) : itemId
            const filtered = !!item && !railItemVisible(item, backend)
            // Whose chip is this? In a shared row, the project's own placement edits
            // the delta while the row's own entries edit the shared layout.
            const mine = !projectRow && resolved.kind === 'delta'
              && isProjectRailPlacement(resolved, device, 'strip', row.id, itemId)
            const canHide = !projectRow && !!projectId && resolved.kind !== 'fork' && !mine
            return <div
              key={key}
              class={`rail-chip${drag.key === key && drag.active ? ' dragging' : ''}${item ? '' : ' missing'}${filtered ? ' filtered' : ''}${mine ? ' project-owned' : ''}`}
              data-reorder-id={key}
              tabIndex={0}
              role="button"
              title={`${label}${item ? ` — ${itemMeta(item)}` : ''}${mine ? '\nOnly this project has it here.' : ''}${filtered ? `\nNot shown in ${harnessDisplayName(backend)} sessions (still on other rails).` : ''}\nDrag to move. Arrows move it, Delete removes it.`}
              onPointerDown={event => { dragCancelRef.current = beginRailDrag(event, dragHost, { kind: 'chip', ref }, label) }}
              onKeyDown={event => onChipKey(event, ref)}
            >
              <span class="rail-chip-label">{label}</span>
              {canHide && <button
                type="button"
                class="rail-chip-hide"
                tabIndex={-1}
                aria-label={`Hide ${label} in this project only`}
                title="Hide it in this project only. The shared rail keeps it everywhere else."
                onPointerDown={event => event.stopPropagation()}
                onClick={() => { void saveRailBlob(hideScopedRailEntry(currentRailBlob(), projectId!, itemId, device, 'strip', row.id)); refresh() }}
              >⊘</button>}
              <button
                type="button"
                class="rail-chip-remove"
                tabIndex={-1}
                aria-label={`Remove ${label} from the rail`}
                title={mine ? 'Remove from this project’s copy of the rail'
                  : !projectRow && !!projectId ? 'Remove from the shared rail — every project loses it'
                    : 'Remove from the rail'}
                onPointerDown={event => event.stopPropagation()}
                onClick={() => commitConfig(removeRailEntry(resolvedRef.current.config, ref))}
              >×</button>
            </div>
          })}
          {/* The only way back from a hide: a hidden button is in no row, so nothing
              else could offer to restore it. */}
          {hiddenHere.map(entry => {
            const item = byId.get(entry.item)
            const label = item ? chipLabel(item) : entry.item
            return <div key={`ghost:${row.id}:${entry.index}:${entry.item}`} class="rail-chip ghost" title={`${label} — hidden in this project. The shared rail still has it everywhere else.`}>
              <span class="rail-chip-label">{label}</span>
              <button
                type="button"
                class="rail-chip-hide"
                aria-label={`Show ${label} here again`}
                title="Show it here again"
                onClick={() => { void saveRailBlob(unhideScopedRailEntry(currentRailBlob(), projectId!, entry.item, device, 'strip', row.id)); refresh() }}
              >+</button>
            </div>
          })}
          {!row.items.length && !hiddenHere.length && <span class="rail-chips-empty">drag actions here</span>}
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
        {pickerItems().map(item => <button
          type="button"
          key={item.id}
          class={railItemVisible(item, backend) ? '' : 'filtered'}
          title={`${chipLabel(item)} — ${itemMeta(item)}${railItemVisible(item, backend) ? '' : `\nNot shown in ${harnessDisplayName(backend)} sessions.`}`}
          onClick={() => addToRow(pickerRow, item.id)}
        >
          <span>{chipLabel(item)}</span>
          <small>{itemMeta(item)}</small>
        </button>)}
        {!pickerItems().length && <p class="rail-add-note">No action matches “{pickerQuery}”.</p>}
      </div>
    </div>}
    <p class="rail-inline-hint">
      Dimmed buttons are hidden for {harnessDisplayName(backend)} sessions.
      Drawer items live under Actions → Quick actions; arrange them in All options.
    </p>
  </div>
}
