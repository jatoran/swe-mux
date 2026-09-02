import { cloneElement, type VNode } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import { OverflowRail } from './RailScroller'
import { RailOverflowPopover } from './RailOverflowPopover'
import { railPopoverClosingCommand } from './railOverflow'
import { RailDrawerIcon } from './railIcons'
import { useRailLongPress } from './railLongPress'
import { arrangeChildren } from './railArrangeChips'
import type { RailDevice } from './commandRail'

// One Action rail row. Every chip remains in the horizontal scroller, while a permanent
// drawer button opens the complete row as a wrap grid. The two routes serve different
// reading styles: quick pan in place, or one stable view of everything.

interface RailStripProps {
  /** The row's chips, in configured order. The popover receives a cloned complete list. */
  chips: VNode[]
  /** Opens the full editor in Settings → Actions. */
  onConfigure: () => void
  /** Accessible name for this row's overflow popover. */
  label: string
  /** This row's stored identity, published so a drag can resolve a drop against it. */
  device: RailDevice
  rowId: string
  /** The rail is in arrange mode: chips are inert and the row is a drop target. */
  arranging: boolean
  /** Where the insertion caret is drawn among this row's chips, or null. */
  caretAt: number | null
  /** Enter arrange mode. Reached by holding this row's drawer control, right-clicking it,
   *  pressing the context-menu key on it, or from the popover's own control. */
  onArrange: () => void
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
 *
 * The drawer control is also the way into arranging the rail, and it is the right press to
 * spend on that: it is the one control on the row that runs nothing, so a hold on it cannot
 * race a pad opening its fan or an arrow starting to repeat. While arranging it stands down
 * entirely - a popover over a rail being rearranged would draw the same row twice with only
 * one of the copies under the pointer.
 */
export function RailStrip({ chips, onConfigure, label, device, rowId, arranging, caretAt, onArrange }: RailStripProps) {
  const trailingRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const press = useRailLongPress()

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

  // Arranging draws the rows in its own panel, so an open popover would be a third copy of
  // this row and the one the pointer is least likely to mean.
  useEffect(() => { if (arranging) setOpen(false) }, [arranging])

  const arrange = () => { setOpen(false); onArrange() }

  return <div class="rail-row">
    <OverflowRail
      className="terminal-action-scroll"
      wrapperClassName="terminal-action-scroller"
      touchDrag
      touchDragGain={1.75}
      preserveSoftKeyboard
      stripProps={{ 'data-rail-row': `${device}|strip|${rowId}` }}
    >
      {arrangeChildren(chips, caretAt)}
    </OverflowRail>
    <div class="rail-row-trailing" ref={trailingRef}>
      <button
        type="button"
        class={`rail-more${open ? ' rail-more-open' : ''}`}
        aria-expanded={open}
        aria-label={`Open all ${chips.length} action${chips.length === 1 ? '' : 's'} in this row`}
        title={arranging
          ? 'Arranging: drag chips between rows'
          : `Open all ${chips.length} action${chips.length === 1 ? '' : 's'} in this row (hold to arrange)`}
        disabled={arranging}
        onPointerDown={event => press.begin(event, arrange)}
        onContextMenu={event => press.contextMenu(event, arrange)}
        onKeyDown={event => { press.keyboardMenu(event, arrange) }}
        onClickCapture={press.suppressClick}
        onClick={() => setOpen(value => !value)}
      ><RailDrawerIcon /><span class="rail-more-count">{chips.length}</span></button>
    </div>
    {open && !arranging && <RailOverflowPopover
      label={label}
      anchor={trailingRef.current}
      onClose={() => setOpen(false)}
      onConfigure={onConfigure}
      onArrange={arrange}
    >{chips.map(chip => cloneElement(chip))}</RailOverflowPopover>}
  </div>
}
