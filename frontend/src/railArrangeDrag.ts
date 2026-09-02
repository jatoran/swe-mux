// The pointer half of in-place rail arrangement. Rules: `railArrange.ts`.
//
// It is a sibling of the Actions editor's driver (`railDrag.ts`) rather than a reuse of it,
// and the two differences are the reason. The editor draws every configured chip, so its hit
// test's index is the stored index; a live rail draws a filtered projection, so every index
// here goes through `storedInsertIndex` before it may reach a config. And the editor's rows
// are inert list items, while these chips are the production buttons - a pad fires from the
// first pixel of a drag and an arrow repeats on a hold - so arrangement is a **mode** and the
// chips are made inert by CSS (`pointer-events:none` under `.rail-arranging`) rather than by
// a gesture that has to out-argue them. That is also why the pointer lands on the row
// container and the chip under it is found by rectangle: there is nothing to hit-test against.
//
// The contract is DOM-attribute based, like the editor's, so no host keeps a registry of live
// element refs:
//
//   [data-rail-arrange-surface]           a container that owns rows; scopes the row search
//   [data-rail-row="device|surface|id"]   a row, whose direct children are its chips
//   [data-rail-slot="<stored index>"]     one chip, at its position in the *stored* row
//   [data-reorder-id="<occurrence key>"]  the same chip's identity within its row
//   [data-rail-arrange-zone="remove"]     the bin and the new-row target, drawn while dragging
//   [data-rail-catalog-item="<item id>"]  a tray entry, dragged in from outside every row
//
// Scoping the row search to the surface under the pointer is load-bearing: the arrange panel
// and the rail draw the *same* rows at the same time, so a forgiveness margin that searched
// the whole document could answer a drop over the panel with a row hidden behind it.

import { MOBILE_HOLD_DRAG, POINTER_MOVE_DRAG, edgeAutoScrollDelta, pointerDragMoveDecision } from './dragReorder.ts'
import { claimPointerDrag } from './pointerDragClaim.ts'
import { dropIndexForPoint, type RailChipRect } from './railLayout.ts'
import {
  caretPosition, chipBandY, slotsWithoutDragged, storedInsertIndex,
  type RailArrangeSource, type RailArrangeTarget,
} from './railArrange.ts'

/** How far a lifted chip must travel before releasing means "drop it here" rather than
 *  "never mind". Inside this radius the release is the slot the chip already occupied. */
const DRAG_COMMIT_TRAVEL = 4

/** How far outside a row's own box the pointer may sit and still be dropping into it.
 *  Rows are separated by a border and on a phone a fingertip covers the strip it is aiming
 *  at, so an exact hit test turns a drop that visibly landed on a row into "off every row",
 *  which commits nothing and reads as the drag having failed. */
const DROP_ROW_MARGIN = 18

export interface RailArrangePreview {
  active: boolean
  /** Where the insertion caret is drawn: a row and a position among its rendered chips. */
  caret: { rowId: string; at: number } | null
  /** The special target under the pointer, so it can light up. */
  zone: 'remove' | 'new-row' | null
}

export const NO_RAIL_ARRANGE_PREVIEW: RailArrangePreview = { active: false, caret: null, zone: null }

export interface RailArrangeDragHost {
  /** Stored length of a row, which is what an append past the last rendered chip resolves to. */
  rowLength: (rowId: string) => number
  setPreview: (preview: RailArrangePreview) => void
  /** Called once, on a release that landed somewhere. */
  commit: (source: RailArrangeSource, target: RailArrangeTarget) => void
  /** Called once per drag that activated; `committed` says whether a drop landed. */
  onEnd?: (committed: boolean) => void
}

/** A resolved drop, plus the rendered position its caret is drawn at. */
type Resolved = { target: RailArrangeTarget; caret: { rowId: string; at: number } | null }

function nearestRowIn(surface: HTMLElement, x: number, y: number): HTMLElement | null {
  let best: HTMLElement | null = null
  let bestDistance = Infinity
  for (const row of surface.querySelectorAll<HTMLElement>('[data-rail-row]')) {
    const box = row.getBoundingClientRect()
    if (!box.width && !box.height) continue
    const distance = Math.hypot(Math.max(box.left - x, 0, x - box.right), Math.max(box.top - y, 0, y - box.bottom))
    if (distance <= DROP_ROW_MARGIN && distance < bestDistance) { best = row; bestDistance = distance }
  }
  return best
}

const chipsOf = (row: HTMLElement): HTMLElement[] =>
  Array.from(row.querySelectorAll<HTMLElement>(':scope > [data-rail-slot]'))

/** The chip whose box contains a point. Chips are `pointer-events:none` while arranging, so
 *  `elementFromPoint` never names one and the rectangles are the only handle on them. */
export function railChipAtPoint(row: HTMLElement, x: number, y: number): HTMLElement | null {
  for (const chip of chipsOf(row)) {
    const box = chip.getBoundingClientRect()
    if (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) return chip
  }
  return null
}

/** The row container under a point, and the surface that owns it. */
function rowUnderPoint(x: number, y: number): HTMLElement | null {
  const element = document.elementFromPoint(x, y)
  if (!(element instanceof Element)) return null
  const surface = element.closest<HTMLElement>('[data-rail-arrange-surface]')
  if (!surface) return null
  return element.closest<HTMLElement>('[data-rail-row]') || nearestRowIn(surface, x, y)
}

function zoneUnderPoint(x: number, y: number): 'remove' | 'new-row' | null {
  const element = document.elementFromPoint(x, y)
  if (!(element instanceof Element)) return null
  const zone = element.closest<HTMLElement>('[data-rail-arrange-zone]')?.dataset.railArrangeZone
  return zone === 'remove' || zone === 'new-row' ? zone : null
}

function resolveDrop(
  host: RailArrangeDragHost,
  source: RailArrangeSource,
  draggedKey: string | null,
  x: number,
  y: number,
): Resolved | null {
  const zone = zoneUnderPoint(x, y)
  if (zone) return { target: { kind: zone }, caret: null }
  const row = rowUnderPoint(x, y)
  if (!row) return null
  const rowId = (row.dataset.railRow || '').split('|')[2]
  if (!rowId) return null

  const chips = chipsOf(row)
  const rects: RailChipRect[] = chips.map(chip => {
    const box = chip.getBoundingClientRect()
    return { key: chip.dataset.reorderId || '', left: box.left, right: box.right, top: box.top, bottom: box.bottom }
  })
  const renderedIndex = dropIndexForPoint(rects, draggedKey, x, chipBandY(rects, y))

  // Where the chip came from decides two things at once: whether the target row's stored
  // positions have to be renumbered, and how many slots the row will have when the drop
  // lands. Both are the same question - is this chip about to leave this row - so they are
  // answered from one place.
  const removed = source.kind === 'chip' && source.ref.rowId === rowId ? source.ref.index : null
  const slots = chips
    .filter(chip => chip.dataset.reorderId !== draggedKey)
    .map(chip => Number(chip.dataset.railSlot))
    .filter(slot => Number.isInteger(slot))
  const length = host.rowLength(rowId) - (removed === null ? 0 : 1)
  const index = storedInsertIndex(slotsWithoutDragged(slots, removed), renderedIndex, Math.max(0, length))
  const draggedAt = draggedKey ? chips.findIndex(chip => chip.dataset.reorderId === draggedKey) : -1
  return {
    target: { kind: 'row', rowId, index },
    caret: { rowId, at: caretPosition(renderedIndex, draggedAt) },
  }
}

/** Nudge whichever container under the pointer can still scroll toward it. */
function autoScrollAt(x: number, y: number): boolean {
  let scrolled = false
  const row = rowUnderPoint(x, y)
  if (row && row.scrollWidth > row.clientWidth + 1) {
    const box = row.getBoundingClientRect()
    const delta = edgeAutoScrollDelta(x, box.left, box.right)
    if (delta !== 0) {
      const before = row.scrollLeft
      row.scrollLeft += delta
      scrolled ||= row.scrollLeft !== before
    }
  }
  const element = document.elementFromPoint(x, y)
  const surface = element instanceof Element ? element.closest<HTMLElement>('[data-rail-arrange-surface]') : null
  if (surface && surface.scrollHeight > surface.clientHeight + 1) {
    const box = surface.getBoundingClientRect()
    const delta = edgeAutoScrollDelta(y, box.top, box.bottom)
    if (delta !== 0) {
      const before = surface.scrollTop
      surface.scrollTop += delta
      scrolled ||= surface.scrollTop !== before
    }
  }
  return scrolled
}

/**
 * Begin a potential arrange drag from a pointerdown.
 *
 * Activation follows the workspace contract: a 5px movement threshold for a mouse, and for
 * touch the same hold-to-lift every other mobile reorder uses. Hold rather than movement on
 * touch is what leaves the rail's own horizontal pan intact - a finger that travels first is
 * scrolling the strip to reach a chip, and `OverflowRail` keeps that gesture because it
 * stands down for the pointer-drag claim the moment this one lifts.
 *
 * `draggedKey` is the chip's occurrence key, or null for a catalog entry, which is what the
 * hit test excludes so a chip hovering over its own slot does not oscillate.
 *
 * Returns a cancel function, or null when the event is not a primary-button primary pointer.
 */
export function beginRailArrangeDrag(
  event: PointerEvent,
  host: RailArrangeDragHost,
  source: RailArrangeSource,
  draggedKey: string | null,
  label: string,
): (() => void) | null {
  if (event.button !== 0 || !event.isPrimary) return null
  const pointerId = event.pointerId
  const touch = event.pointerType === 'touch'
  const startX = event.clientX, startY = event.clientY
  const activation = touch ? MOBILE_HOLD_DRAG : POINTER_MOVE_DRAG

  let active = false, done = false
  let ghost: HTMLDivElement | null = null
  let holdTimer: number | null = null
  let scrollFrame: number | null = null
  let releaseClaim: (() => void) | null = null
  let latest = { x: startX, y: startY }
  let activateX = startX, activateY = startY, moved = false
  let landing: RailArrangeTarget | null = null

  const recompute = () => {
    const resolved = resolveDrop(host, source, draggedKey, latest.x, latest.y)
    landing = resolved?.target ?? null
    host.setPreview({
      active: true,
      caret: resolved?.caret ?? null,
      zone: resolved?.target.kind === 'remove' || resolved?.target.kind === 'new-row' ? resolved.target.kind : null,
    })
  }

  const stopAutoScroll = () => {
    if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame)
    scrollFrame = null
  }
  const autoScroll = () => {
    scrollFrame = null
    if (!active) return
    if (autoScrollAt(latest.x, latest.y)) recompute()
    scrollFrame = window.requestAnimationFrame(autoScroll)
  }

  // Touch only, and the reason a hold works on a phone at all. `preventDefault` on a
  // *pointer* move does not stop a touch from scrolling - only `touch-action` and a
  // cancelled `touchmove` do - so during the hold the browser is otherwise free to latch a
  // pan off the finger's micro-jitter, and a latched pan ignores every later
  // `preventDefault` and cancels the pointer. That is exactly the shape of "the drag only
  // works about a third of the time". Past the slop the hold is a scroll it never owned, so
  // it is released to the browser untouched.
  const blockTouchScroll = (moveEvent: TouchEvent) => {
    if (!moveEvent.cancelable) return
    if (active) { moveEvent.preventDefault(); return }
    if (activation.mode !== 'hold' || moveEvent.touches.length !== 1) return
    const point = moveEvent.touches[0]
    if (Math.hypot(point.clientX - startX, point.clientY - startY) <= activation.slop) moveEvent.preventDefault()
  }
  // Android fires a native long-press `contextmenu` about 500 ms into a stationary touch and
  // cancels the pointer with it, so a hold that lifts at 350 ms and then lingers dies a
  // moment later. Nothing on an arranging rail wants a context menu.
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
      try { if (document.body.hasPointerCapture(pointerId)) document.body.releasePointerCapture(pointerId) } catch { /* already gone */ }
      releaseClaim?.()
    }
    const target = commitDrop ? landing : null
    host.setPreview(NO_RAIL_ARRANGE_PREVIEW)
    if (target) host.commit(source, target)
    if (wasActive) host.onEnd?.(!!target)
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
    // States that this gesture is a drag, which is what makes the rail's own horizontal pan
    // and the mobile swipe recognizer stand down without either of them having to guess.
    releaseClaim = claimPointerDrag()
    document.body.classList.add('workspace-pointer-dragging')
    // Capture on the body, never on a chip: the panel and the rail redraw around the caret
    // every move, and a captured element that leaves the document drops the pointer mid-drag.
    // Best effort; the real routing is the window listeners keyed by pointerId.
    try { document.body.setPointerCapture(pointerId) } catch { /* window listeners still track it */ }
    // The lift is otherwise invisible on a phone until the finger moves, and guessing when a
    // chip became draggable is what makes a hold-drag feel unreliable even once it works.
    if (touch) navigator.vibrate?.(15)
    ghost = document.createElement('div')
    ghost.className = 'mux-pointer-drag-ghost'
    ghost.textContent = label
    ghost.style.transform = `translate3d(${latest.x + 14}px,${latest.y + 12}px,0)`
    document.body.appendChild(ghost)
    recompute()
    if (scrollFrame === null) scrollFrame = window.requestAnimationFrame(autoScroll)
  }

  function onMove(pointer: PointerEvent) {
    if (pointer.pointerId !== pointerId) return
    latest = { x: pointer.clientX, y: pointer.clientY }
    if (!active) {
      const decision = pointerDragMoveDecision(activation, Math.hypot(pointer.clientX - startX, pointer.clientY - startY))
      if (decision === 'wait') return
      // A hold-drag that moves before the delay is the rail's pan, not a drag.
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
    // A chip that lifted and was let go where it sat has nowhere to land: committing would
    // write back the layout already saved, at the cost of a settings broadcast.
    finish(active && moved)
  }
  function onCancel(pointer: PointerEvent) {
    if (pointer.pointerId !== pointerId) return
    finish(false)
  }
  function onBlur() { finish(false) }
  function onKey(keyEvent: KeyboardEvent) {
    if (keyEvent.key !== 'Escape') return
    // Escape abandons the drag without leaving arrange mode, so the two escapes are ordered
    // the way a hand expects: put the chip back first, close the mode on a second press.
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
