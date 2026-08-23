import { cloneElement, type VNode } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import { OverflowRail } from './RailScroller'
import { RailOverflowPopover } from './RailOverflowPopover'
import { railPopoverClosingCommand } from './railOverflow'
import { RailDrawerIcon } from './railIcons'

// One Action rail row. Every chip remains in the horizontal scroller, while a permanent
// drawer button opens the complete row as a wrap grid. The two routes serve different
// reading styles: quick pan in place, or one stable view of everything.

interface RailStripProps {
  /** The row's chips, in configured order. The popover receives a cloned complete list. */
  chips: VNode[]
  /** The status readout, on whichever row carries it. Shrinks and ellipsises rather than
   *  taking room from the chips, so its text changing never moves one. Empty is not the
   *  same as absent to the caller — it marks the row that *would* carry a readout — but it
   *  is the same to the row, so it renders nothing (see below). */
  status?: string
  /** Opens the full Configure Actions modal. */
  onConfigure: () => void
  /** Accessible name for this row's overflow popover. */
  label: string
}

export function RailStrip({ chips, status, onConfigure, label }: RailStripProps) {
  const trailingRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)

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

  return <div class="rail-row">
    <OverflowRail className="terminal-action-scroll" wrapperClassName="terminal-action-scroller" touchDrag touchDragGain={1.75} preserveSoftKeyboard>
      {chips}
    </OverflowRail>
    <div class="rail-row-trailing" ref={trailingRef}>
      {/* Truthiness, not `!== undefined`. The caller marks the status row by passing a
          string, and that string is empty most of the time — an empty readout still
          carried its own `padding-right` plus the cluster's `gap`, taking ~23px of scroll
          width from the row's chips for nothing. Unmounting it costs no announcement that
          `display:none` would not have cost either: both leave the accessibility tree. */}
      {status ? <span aria-live="polite">{status}</span> : null}
      <button
        type="button"
        class={`rail-more${open ? ' rail-more-open' : ''}`}
        aria-expanded={open}
        aria-label={`Open all ${chips.length} action${chips.length === 1 ? '' : 's'} in this row`}
        title={`Open all ${chips.length} action${chips.length === 1 ? '' : 's'} in this row`}
        onClick={() => setOpen(value => !value)}
      ><RailDrawerIcon /><span class="rail-more-count">{chips.length}</span></button>
    </div>
    {open && <RailOverflowPopover
      label={label}
      anchor={trailingRef.current}
      onClose={() => setOpen(false)}
      onConfigure={onConfigure}
    >{chips.map(chip => cloneElement(chip))}</RailOverflowPopover>}
  </div>
}
