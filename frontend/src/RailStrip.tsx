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
  /** Opens the full Configure Actions modal. */
  onConfigure: () => void
  /** Accessible name for this row's overflow popover. */
  label: string
}

/**
 * A row carries no message of its own, deliberately.
 *
 * It used to take a `status` string and render it in the trailing cluster, which is where
 * the selection readout lived. The cluster does not shrink, and the readout was capped in
 * *viewport* units, so in a split pane narrower than 34vw it consumed the whole row and the
 * chips were squeezed out of sight - the rail vanished behind a sentence about the
 * selection. Every terminal message now goes to `.terminal-clip-toast`, over the terminal
 * and flush on the rail's top edge, so no message can take a chip's place again.
 */
export function RailStrip({ chips, onConfigure, label }: RailStripProps) {
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
