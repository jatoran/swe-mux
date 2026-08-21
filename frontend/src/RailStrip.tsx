import { cloneElement, type ComponentChildren, type VNode } from 'preact'
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
  /** Fixed furniture pinned to the row's trailing edge (status readout, the gear). */
  trailing?: ComponentChildren
  /** Accessible name for this row's overflow popover. */
  label: string
}

/** Pixels reserved for trailing furniture that must never be pushed off the row. */
function reservedWidth(host: HTMLElement | null, gap: number): number {
  if (!host) return 0
  const fixed = Array.from(host.querySelectorAll<HTMLElement>('[data-rail-fixed]'))
  if (!fixed.length) return 0
  return fixed.reduce((total, element) => total + element.getBoundingClientRect().width + gap, 0)
}

export function RailStrip({ chips, trailing, label }: RailStripProps) {
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
    // The `+N` chip is fixed-width by CSS, so its own width is a constant the split can
    // reserve before it exists. Reading it when it happens to be mounted keeps the number
    // honest across density and scale without a second copy of it here.
    const overflowWidth = moreRef.current?.getBoundingClientRect().width
      || Number.parseFloat(style.getPropertyValue('--rail-more-width')) || 44
    const available = strip.clientWidth - padding - reservedWidth(trailingRef.current, gap)
    const next = railFitCount({ widths, gap, available, overflowWidth })
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
    <OverflowRail className="terminal-action-scroll" itemLabel="commands" wrapperClassName="terminal-action-scroller" touchDrag touchDragGain={1.75} preserveSoftKeyboard>
      {chips.slice(0, pinnedCount)}
      {overflow.length > 0 && <button
        ref={moreRef}
        type="button"
        class={`rail-more${open ? ' rail-more-open' : ''}`}
        aria-expanded={open}
        aria-label={`${overflow.length} more action${overflow.length === 1 ? '' : 's'}`}
        title={`${overflow.length} more action${overflow.length === 1 ? '' : 's'} — opens a panel that stays open`}
        onClick={() => setOpen(value => !value)}
      >+{overflow.length}</button>}
      {trailing && <div class="rail-row-trailing" ref={trailingRef}>{trailing}</div>}
    </OverflowRail>
    {open && overflow.length > 0 && <RailOverflowPopover
      label={label}
      anchor={moreRef.current}
      onClose={() => setOpen(false)}
    >{overflow}</RailOverflowPopover>}
  </div>
}
