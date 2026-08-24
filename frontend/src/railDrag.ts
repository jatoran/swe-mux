// Pointer-drag machinery shared by the Action editors — the Configure Actions
// modal (`RailEditor.tsx`).
//
// The contract is DOM-attribute based so neither host keeps a registry of live
// element refs: rows advertise themselves with `data-rail-row="device|surface|rowId"`
// and chips with `data-reorder-id` (the per-occurrence key), and the hit test
// reads whatever is under the pointer.
//
// The live preview is the config a drop would commit, recomputed from the
// committed config on every move rather than from the previous preview, so a
// long drag cannot accumulate drift. Pointer capture is taken on `document.body`,
// never on the chip or on the host root: the preview reparents the chip between
// rows, and a captured element that leaves the document loses the pointer mid-drag.
//
// Touch follows the same hold-to-lift contract as the workspace reorder
// (`beginPointerDrag` in `App.tsx`), including the two defences a hold on a
// scrollable phone surface cannot work without — a `touchmove` canceller and a
// `contextmenu` suppressor. See `blockTouchScroll` below for why.

import type { JSX } from 'preact'
import type { RailConfig } from './commandRail.ts'
import {
  dropIndexForPoint, insertRailItem, railRowAt, removeRailEntry,
  type RailDropTarget, type RailRef,
} from './railLayout.ts'
import { MOBILE_HOLD_DRAG, POINTER_MOVE_DRAG, edgeAutoScrollDelta, pointerDragMoveDecision } from './dragReorder.ts'
import { claimPointerDrag } from './pointerDragClaim.ts'

/** How far a lifted chip must travel before releasing means "drop it here" rather than
 *  "never mind". A release inside this radius is the same slot the chip already occupied,
 *  so committing it would only write an identical layout back to the daemon. */
const DRAG_COMMIT_TRAVEL = 4

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
   *  "off every row" rather than as an error.
   *
   *  Nothing sets it today: the scoped editors used it to keep a project-owned
   *  action out of shared rows, and a delta can now say "this project's, in that
   *  shared row" outright (`commandRail.ts`, splices). The hook stays because the
   *  refusal it implements — preview as off-every-row rather than as an error —
   *  is the drag's own vocabulary, not the scope rule that happened to need it. */
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

/** How far outside a row's own box a pointer may sit and still be dropping into it.
 *
 *  Rows are separated by a gap and by the next row's header, and on a phone a fingertip
 *  covers the strip it is aiming at — so an exact hit test turns a drop that visibly landed
 *  on a row into "off every row", which commits nothing and reads as the drag having failed.
 *  Tight enough that the catalog list below can never claim the last row. */
const DROP_ROW_MARGIN = 20

/** The row whose box the point is closest to, within the forgiveness margin. */
function nearestRow(root: HTMLElement, x: number, y: number): HTMLElement | null {
  let best: HTMLElement | null = null
  let bestDistance = Infinity
  for (const row of root.querySelectorAll<HTMLElement>('[data-rail-row]')) {
    const box = row.getBoundingClientRect()
    if (!box.width && !box.height) continue
    const distance = Math.hypot(Math.max(box.left - x, 0, x - box.right), Math.max(box.top - y, 0, y - box.bottom))
    if (distance <= DROP_ROW_MARGIN && distance < bestDistance) { best = row; bestDistance = distance }
  }
  return best
}

/** Read the drop target under a point straight from the DOM. */
function targetUnderPoint(root: HTMLElement, x: number, y: number, draggedKey: string | null): RailDropTarget | null {
  const element = document.elementFromPoint(x, y)
  const hit = element instanceof Element ? element.closest<HTMLElement>('[data-rail-row]') : null
  const rowElement = hit || nearestRow(root, x, y)
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
 * contract (`dragReorder.ts`): a 5px movement threshold for pointers, and for
 * touch the same hold-to-lift the sidebar and tab strips use — the chip lifts on
 * a stationary hold, and a finger that travels first scrolls instead.
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
  const touch = event.pointerType === 'touch'
  const startX = event.clientX, startY = event.clientY
  const activation = touch ? MOBILE_HOLD_DRAG : POINTER_MOVE_DRAG
  const scroller = scrollableAncestor(node)

  let active = false, done = false
  let ghost: HTMLDivElement | null = null
  let holdTimer: number | null = null
  let scrollFrame: number | null = null
  let releaseClaim: (() => void) | null = null
  let latest = { x: startX, y: startY }
  // Where the chip was lifted, and whether it has travelled since. A hold that lifts and
  // lets go without moving is not a drop (`DRAG_COMMIT_TRAVEL`).
  let activateX = startX, activateY = startY, moved = false
  // Where the dragged chip renders right now, and the config a drop would save.
  let previewRef: RailRef | null = source.kind === 'chip' ? source.ref : null
  let previewConfig: RailConfig | null = null

  const recompute = () => {
    const base = host.config()
    const target = targetUnderPoint(root, latest.x, latest.y, previewRef ? railRefKey(previewRef) : null)
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

  // Touch only, and the reason hold-to-drag works on a phone at all. `preventDefault` on a
  // *pointer* move does not stop a touch from scrolling — only `touch-action` and a cancelled
  // `touchmove` do. Both drag sources here (`.rail-chip`, `.rail-catalog-head`) sit inside the
  // editor's own vertical scroller and carry `touch-action:pan-y`, because a catalog row is
  // most of what a finger can land on and `touch-action:none` would cost the editor its
  // scroll. So during the hold the browser is free to latch a pan off the finger's
  // micro-jitter — and a latched pan ignores every later `preventDefault` and cancels the
  // pointer, which is exactly the shape of "the drag only works about a third of the time".
  // Cancelling within-slop touchmoves keeps it from ever latching; past the slop the hold is
  // a scroll it never owned, so it is released to the browser untouched.
  //
  // Registered at pointer-down (which precedes `touchstart`) so the sequence stays on the
  // main thread from the start and its moves stay cancelable.
  const blockTouchScroll = (event: TouchEvent) => {
    if (!event.cancelable) return
    if (active) { event.preventDefault(); return }
    if (activation.mode !== 'hold' || event.touches.length !== 1) return
    const point = event.touches[0]
    if (Math.hypot(point.clientX - startX, point.clientY - startY) <= activation.slop) event.preventDefault()
  }
  // Android fires a native long-press `contextmenu` about 500ms into a stationary touch and
  // cancels the pointer with it — so a hold that lifts at 350ms and then lingers before
  // moving dies a moment later. Nothing in this editor wants a context menu on a chip.
  const suppressContextMenu = (menu: Event) => menu.preventDefault()

  const finish = (commitDrop: boolean) => {
    if (done) return
    done = true
    if (holdTimer !== null) window.clearTimeout(holdTimer)
    stopAutoScroll()
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onCancel)
    window.removeEventListener('keydown', onKey, true)
    window.removeEventListener('blur', onBlur)
    window.removeEventListener('touchmove', blockTouchScroll)
    window.removeEventListener('contextmenu', suppressContextMenu, true)
    ghost?.remove()
    const wasActive = active
    if (active) {
      document.body.classList.remove('workspace-pointer-dragging')
      node.classList.remove('dragging')
      try { if (document.body.hasPointerCapture(pointerId)) document.body.releasePointerCapture(pointerId) } catch { /* already gone */ }
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
    activateX = latest.x
    activateY = latest.y
    // A movement-mode drag only activates *because* the pointer already travelled, so it is
    // droppable from its first frame; only a hold has to prove it went somewhere.
    if (activation.mode === 'movement') moved = true
    if (holdTimer !== null) window.clearTimeout(holdTimer)
    holdTimer = null
    releaseClaim = claimPointerDrag()
    document.body.classList.add('workspace-pointer-dragging')
    node.classList.add('dragging')
    // Capture on the body, never on the chip or the host root: the preview reparents the
    // chip between rows every move, and a captured element that leaves the document — even
    // for the instant of an insertBefore — drops the pointer mid-drag. Capture is best
    // effort; the real routing is the window listeners keyed by pointerId.
    try { document.body.setPointerCapture(pointerId) } catch { /* window listeners still track it */ }
    // The lift is otherwise invisible on a phone until the finger moves, so the operator is
    // left guessing when the chip became draggable — and guessing is what makes a hold-drag
    // feel unreliable even once it works.
    if (touch) navigator.vibrate?.(15)
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
    if (Math.hypot(pointer.clientX - activateX, pointer.clientY - activateY) > DRAG_COMMIT_TRAVEL) moved = true
    pointer.preventDefault()
    if (ghost) ghost.style.transform = `translate3d(${pointer.clientX + 14}px,${pointer.clientY + 12}px,0)`
    recompute()
  }
  function onUp(pointer: PointerEvent) {
    if (pointer.pointerId !== pointerId) return
    // A chip that lifted and was let go where it sat has nowhere to land: committing the
    // preview would write back a layout identical to the one already saved.
    finish(active && moved)
  }
  function onCancel(pointer: PointerEvent) {
    if (pointer.pointerId !== pointerId) return
    finish(false)
  }
  function onBlur() { finish(false) }
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
  window.addEventListener('blur', onBlur)
  if (touch) {
    window.addEventListener('touchmove', blockTouchScroll, { passive: false })
    window.addEventListener('contextmenu', suppressContextMenu, true)
  }
  if (activation.mode === 'hold') holdTimer = window.setTimeout(activate, activation.delayMs)
  return () => finish(false)
}
