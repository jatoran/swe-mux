import { cloneElement, type VNode } from 'preact'
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'

import { OverflowRail } from './RailScroller'
import { RailOverflowPopover } from './RailOverflowPopover'
import { railFitCount, railPopoverClosingCommand } from './railOverflow'

// One Action rail row: as many chips as fit, then a `+N` chip holding the rest.
//
// The rail used to answer overflow by scrolling, which is the wrong answer for this surface.
// A horizontal scroller hides an unknown number of controls behind a gesture, gives no count,
// and puts the thing you want at an offset you have to hunt for; the rail is a *toolbar*, and
// a toolbar's overflow menu is the convention because it says how much is hidden and shows
// all of it at once.
//
// Measurement, and why there are two copies of every chip in the DOM
// ------------------------------------------------------------------
// Splitting needs the width of chips that are not on the row. Measuring them by rendering
// everything for a frame and then collapsing costs a visible flash on every re-measure — and
// re-measures are frequent here, because a rail chip's label changes under it (End session
// arms to "Confirm ✓", an auto-labelled prompt resolves its title, the status readout
// appears). So the row keeps a hidden, `visibility:hidden` copy of the full chip list whose
// only job is to be measured. `visibility:hidden` is what makes the copy inert: it is not
// painted, not focusable, and (with `pointer-events:none`) not clickable, while still having
// a real layout box to read.
//
// The copy sits beside the row rather than inside it, because an absolutely-positioned child
// still contributes to a scroller's `scrollWidth` — a measure row *inside* the strip would
// report permanent overflow to the very component that draws the overflow chevrons.
//
// `OverflowRail` is still the row's container even though a split row never overflows. That
// is deliberate: it owns the touch-drag pan's click suppression, `holdSoftKeyboard`, wheel
// translation and focus reveal, and it degrades to nothing when the content fits. Keeping it
// means the `+N` chip is suppressed by a swipe exactly like any other chip, and a fit that is
// off by a pixel still scrolls rather than clipping.

interface RailStripProps {
  /** The row's chips, in configured order. Split between the row and its popover. */
  chips: VNode[]
  /** The status readout, on whichever row carries it. Shrinks and ellipsises rather than
   *  taking room from the chips, so its text changing never moves one. */
  status?: string
  /** Opens the in-place rail editor. Offered only inside the row's overflow popover:
   *  a standing gear chip on the strip spent rail width on chrome, and "this rail is
   *  too full" is most often thought while reading the overflow anyway. The full
   *  editor also stays reachable from Actions -> Configure. */
  onConfigure?: () => void
  /** Accessible name for this row's overflow popover. */
  label: string
}

export function RailStrip({ chips, status, onConfigure, label }: RailStripProps) {
  const rowRef = useRef<HTMLDivElement>(null)
  const measureRef = useRef<HTMLDivElement>(null)
  const trailingRef = useRef<HTMLDivElement>(null)
  const moreRef = useRef<HTMLButtonElement>(null)
  const [fit, setFit] = useState(chips.length)
  const [open, setOpen] = useState(false)

  const syncFit = () => {
    const measure = measureRef.current
    const row = rowRef.current
    if (!measure || !row) return
    const strip = row.querySelector<HTMLElement>('.terminal-action-scroll')
    if (!strip) return
    const style = getComputedStyle(strip)
    const gap = Number.parseFloat(style.columnGap || style.gap || '0') || 0
    const padding = (Number.parseFloat(style.paddingLeft) || 0) + (Number.parseFloat(style.paddingRight) || 0)
    const widths = Array.from(measure.children, child => child.getBoundingClientRect().width)
    // No width is reserved for the `+N` chip: it lives outside the scroller, in the
    // row's fixed trailing cluster, so the scroller's own clientWidth already excludes
    // it. The count is simply how many chips are beyond the unscrolled viewport.
    const available = strip.clientWidth - padding
    const next = railFitCount({ widths, gap, available, overflowWidth: 0 })
    setFit(current => current === next ? current : next)
  }

  // Layout effect, not effect: the split has to be settled in the same frame the chips are
  // first painted, or the row shows its full width for a frame and then snaps.
  useLayoutEffect(syncFit)

  useEffect(() => {
    const measure = measureRef.current
    const row = rowRef.current
    if (!measure || !row) return
    // The same observers `OverflowRail` wires for its own endpoint detection, on the copy:
    // the row's own width, plus every chip, plus label text changing under a chip.
    const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(syncFit)
    const observeSizes = () => {
      resizeObserver?.disconnect()
      resizeObserver?.observe(row)
      resizeObserver?.observe(measure)
      for (const child of Array.from(measure.children)) resizeObserver?.observe(child)
    }
    const mutationObserver = typeof MutationObserver === 'undefined' ? null : new MutationObserver(() => {
      observeSizes()
      syncFit()
    })
    observeSizes()
    mutationObserver?.observe(measure, { childList: true, characterData: true, subtree: true })
    window.addEventListener('resize', syncFit)
    // A monospace face still loading changes every chip's advance without resizing anything
    // the observers watch.
    void document.fonts?.ready.then(syncFit).catch(() => {})
    return () => {
      resizeObserver?.disconnect()
      mutationObserver?.disconnect()
      window.removeEventListener('resize', syncFit)
    }
  }, [])

  // A chip that navigates somewhere else folds the panel. Listened for on the bus rather
  // than wrapped around each handler, because the same departure arrives from a chip here,
  // from a drop-up's sticky "all of them…" row, and from a voice command.
  useEffect(() => {
    if (!open) return
    const onCommand = (event: Event) => {
      const command = (event as CustomEvent<string>).detail
      if (typeof command === 'string' && railPopoverClosingCommand(command)) setOpen(false)
    }
    window.addEventListener('mux:command', onCommand)
    return () => window.removeEventListener('mux:command', onCommand)
  }, [open])

  const pinnedCount = Math.min(fit, chips.length)
  const overflow = chips.slice(pinnedCount)
  // An empty overflow renders no `+N` at all, so a row that fits is byte-for-byte the row
  // that existed before this split.
  useEffect(() => { if (!overflow.length) setOpen(false) }, [overflow.length])

  return <div class="rail-row" ref={rowRef}>
    {/* Cloned rather than the same vnode objects: one vnode may only occupy one place in a
        tree, and relying on Preact's clone-on-reuse to notice would make a correctness
        property of this row an implementation detail of the renderer. */}
    <div class="terminal-action-scroll rail-row-measure" aria-hidden="true" ref={measureRef}>
      {chips.map(chip => cloneElement(chip))}
    </div>
    {/* Every chip renders in the scroller - the row is scrollable end to end, and the
        popover is the other way to the same chips, not the only one (operator decision
        2026-08-22: scroll OR overlay, reader's choice). `OverflowRail` owns the pan,
        chevrons, wheel translation, and click suppression exactly as it always did. */}
    <OverflowRail className="terminal-action-scroll" itemLabel="commands" wrapperClassName="terminal-action-scroller" touchDrag touchDragGain={1.75} preserveSoftKeyboard>
      {chips}
    </OverflowRail>
    {/* The trailing cluster sits OUTSIDE the scroller as the row's fixed furniture, so
        the `+N` chip and the readout stay pinned at the trailing edge while the chips
        pan underneath. The count is the chips beyond the unscrolled viewport - the same
        set the popover shows - so the two affordances always agree. */}
    {(overflow.length > 0 || status !== undefined) && <div class="rail-row-trailing" ref={trailingRef}>
      {status !== undefined && <span aria-live="polite">{status}</span>}
      {overflow.length > 0 && <button
        ref={moreRef}
        type="button"
        class={`rail-more${open ? ' rail-more-open' : ''}`}
        aria-expanded={open}
        aria-label={`${overflow.length} more action${overflow.length === 1 ? '' : 's'}`}
        title={`${overflow.length} more action${overflow.length === 1 ? '' : 's'} — scroll the row, or open a panel that stays open`}
        onClick={() => setOpen(value => !value)}
      >+{overflow.length}</button>}
    </div>}
    {open && overflow.length > 0 && <RailOverflowPopover
      label={label}
      // The whole trailing cluster, not the `+N` chip: the panel aligns to the rail's
      // trailing edge, which is the cluster's right edge. The cluster's top is the
      // chip's top, since it is a single centred row, so one element answers both axes.
      anchor={trailingRef.current}
      onClose={() => setOpen(false)}
      onConfigure={onConfigure}
    >{overflow}</RailOverflowPopover>}
  </div>
}
