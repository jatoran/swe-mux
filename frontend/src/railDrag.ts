// Pointer-drag machinery shared by the Action editors — the Configure Actions
// modal (`RailEditor.tsx`) and the in-place rail editor (`RailInlineEditor.tsx`).
//
// The contract is DOM-attribute based so neither host keeps a registry of live
// element refs: rows advertise themselves with `data-rail-row="device|surface|rowId"`
// and chips with `data-reorder-id` (the per-occurrence key), and the hit test
// reads whatever is under the pointer.
//
// The live preview is the config a drop would commit, recomputed from the
// committed config on every move rather than from the previous preview, so a
// long drag cannot accumulate drift. Pointer capture is taken on the host root,
// not on the chip: the preview reparents the chip between rows, and a captured
// element that leaves the document loses the pointer mid-drag.

import type { JSX } from 'preact'
import type { RailConfig } from './commandRail.ts'
import {
  dropIndexForPoint, insertRailItem, railRowAt, removeRailEntry,
  type RailDropTarget, type RailRef,
} from './railLayout.ts'
import { MOBILE_PROJECT_HOLD_DRAG, POINTER_MOVE_DRAG, edgeAutoScrollDelta, pointerDragMoveDecision } from './dragReorder.ts'
import { claimPointerDrag } from './pointerDragClaim.ts'

export type RailDragSource = { kind: 'chip'; ref: RailRef } | { kind: 'catalog'; itemId: string }

export interface RailDragPreview {
  /** Where the dragged chip currently renders (its occurrence key), or null. */
  key: string | null
  /** The config a drop right now would commit, or null when off every row. */
  config: RailConfig | null
  active: boolean
}

export interface RailDragHost {
  root: () => HTMLElement | null
  /** The committed config previews are recomputed from. */
  config: () => RailConfig
  setPreview: (state: RailDragPreview) => void
  commit: (next: RailConfig) => void
  /** Refuse a target for this item. A refused target previews (and drops) as
   *  "off every row" rather than as an error. Scoped editors use this to keep a
   *  project-owned action out of shared rows, where its id would not survive
   *  the global write. */
  canDrop?: (target: RailDropTarget, itemId: string) => boolean
  /** Called once per drag session that activated; `committed` says whether a drop landed. */
  onEnd?: (committed: boolean) => void
}

export const railRefKey = (ref: RailRef): string => `${ref.device}|${ref.surface}|${ref.rowId}|${ref.index}`

function scrollableAncestor(start: HTMLElement | null): HTMLElement | null {
  for (let node = start; node; node = node.parentElement) {
    const overflow = getComputedStyle(node).overflowY
    if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight + 1) return node
  }
  return null
}

/** Read the drop target under a point straight from the DOM. */
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
    device: device as RailDropTarget['device'],
    surface: surface as RailDropTarget['surface'],
    rowId,
    index: dropIndexForPoint(rects, draggedKey, x, y),
  }
}

/**
 * Begin a potential drag from a pointerdown. Activation follows the workspace
 * contract (`dragReorder.ts`): a 5px movement threshold for pointers, a hold
 * with slop for touch so a finger that moves first scrolls instead of dragging.
 *
 * Returns a cancel function (for unmount), or null when the event is not a
 * primary-button primary pointer.
 */
export function beginRailDrag(
  event: JSX.TargetedPointerEvent<HTMLElement>,
  host: RailDragHost,
  source: RailDragSource,
  label: string,
): (() => void) | null {
  if (event.button !== 0 || !event.isPrimary) return null
  const root = host.root()
  if (!root) return null
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

  const recompute = () => {
    const base = host.config()
    const target = targetUnderPoint(latest.x, latest.y, previewRef ? railRefKey(previewRef) : null)
    if (!target) {
      // Off every row: fall back to the untouched config, so letting go over
      // nothing leaves a chip where it was and never places a catalog entry.
      previewRef = source.kind === 'chip' ? source.ref : null
      previewConfig = null
      host.setPreview({ key: previewRef ? railRefKey(previewRef) : null, config: null, active: true })
      return
    }
    const itemId = source.kind === 'chip'
      ? (railRowAt(base, source.ref.device, source.ref.surface, source.ref.rowId)?.items[source.ref.index] ?? null)
      : source.itemId
    if (itemId === null) return
    if (host.canDrop && !host.canDrop(target, itemId)) {
      previewRef = source.kind === 'chip' ? source.ref : null
      previewConfig = null
      host.setPreview({ key: previewRef ? railRefKey(previewRef) : null, config: null, active: true })
      return
    }
    const without = source.kind === 'chip' ? removeRailEntry(base, source.ref) : base
    const row = railRowAt(without, target.device, target.surface, target.rowId)
    if (!row) return
    const index = Math.max(0, Math.min(target.index, row.items.length))
    previewRef = { device: target.device, surface: target.surface, rowId: target.rowId, index }
    previewConfig = insertRailItem(without, itemId, { ...target, index })
    host.setPreview({ key: railRefKey(previewRef), config: previewConfig, active: true })
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
    if (holdTimer !== null) window.clearTimeout(holdTimer)
    stopAutoScroll()
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onCancel)
    window.removeEventListener('keydown', onKey, true)
    ghost?.remove()
    const wasActive = active
    if (active) {
      document.body.classList.remove('workspace-pointer-dragging')
      node.classList.remove('dragging')
      try { if (root.hasPointerCapture(pointerId)) root.releasePointerCapture(pointerId) } catch { /* already gone */ }
      releaseClaim?.()
    }
    const landed = commitDrop ? previewConfig : null
    host.setPreview({ key: null, config: null, active: false })
    if (landed) host.commit(landed)
    if (wasActive) host.onEnd?.(!!landed)
  }

  const activate = () => {
    if (active || done) return
    active = true
    if (holdTimer !== null) window.clearTimeout(holdTimer)
    holdTimer = null
    releaseClaim = claimPointerDrag()
    document.body.classList.add('workspace-pointer-dragging')
    node.classList.add('dragging')
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
  if (activation.mode === 'hold') holdTimer = window.setTimeout(activate, activation.delayMs)
  return () => finish(false)
}
