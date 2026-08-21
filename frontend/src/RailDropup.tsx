import type { ComponentChildren } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import { holdSoftKeyboard } from './mobileKeyboard'
import { RAIL_DROPUP_MAX_WIDTH_PX } from './railOverflow'
import { railOverlayStyle, watchRailOverlayPlacement } from './railOverlayPlacement'

// A short list, opened upward from a button on the terminal's Action rail.
//
// The rail sits at the bottom of a pane, so anything it opens has to open *up*.
// That geometry is shared with the rail's overflow popover and lives in
// `railOverlayPlacement.ts`; only the width cap differs, because this lays out a
// list and that one a wrap grid. It used to borrow `anchoredPopoverStyle` from the
// account switcher, which is the same *shape* and a different problem: that one
// knows nothing about the soft keyboard resizing the visual viewport out from
// under it, nor about a phone's width budget, and every rail overlay needs both.
//
// It exists because the rail's high-frequency pickers (clipboard history, the
// session's skills) had exactly one route: open the utility drawer, find the
// section, read it, act. That is the right surface for browsing and a poor one
// for "paste the thing I copied a minute ago", which is a two-tap job. So the
// drop-up is deliberately *not* a second copy of the drawer section: it shows the
// most recent handful, does the one obvious thing on tap, and its first row is a
// sticky link into the full section for everything else. Nothing is reachable
// only from here.
//
// Five rows is a height cap, not a slice. The list holds everything its source
// gives it and scrolls; capping by count instead would make the sticky link the
// only way to reach a sixth entry, which is worse than a scroll. The number lives
// in CSS (`--rail-dropup-rows` over a fixed `--rail-dropup-row`) because a height
// cap is the only place it can be enforced, and a copy here would be a second
// answer nothing reads.
//
// Two mobile details the rail's other controls also carry:
//   * `holdSoftKeyboard` on pointer-down, so opening a picker does not lower a
//     keyboard the operator is mid-sentence in.
//   * repositioning on scroll *with capture*, because the rail itself is a
//     horizontal scroller — a trigger that pans out from under a fixed popover
//     leaves it pointing at nothing — and on `visualViewport` resize, which is
//     the only event the keyboard's open and close fire.

function sameStyle(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = Object.keys(b)
  return keys.length === Object.keys(a).length && keys.every(key => a[key] === b[key])
}

/** One entry in the sticky bar: a way out of the shortcut and into a full surface. */
export type RailDropupAction = { label: string; title: string; run: () => void }

type Props = {
  /** Accessible name for the dialog. */
  label: string
  /** The rail button this hangs from. */
  anchor: HTMLElement | null
  onClose: () => void
  /** The sticky first row: the way out to the full surface this summarises. More
   *  than one shares that row side by side, which is what keeps a second exit
   *  (say, "new template") from costing a whole row of the five-row list. */
  sticky: RailDropupAction | readonly RailDropupAction[]
  children: ComponentChildren
}

export function RailDropup({ label, anchor, onClose, sticky, children }: Props) {
  const actions: readonly RailDropupAction[] = Array.isArray(sticky) ? sticky : [sticky as RailDropupAction]
  const root = useRef<HTMLDivElement>(null)
  const [style, setStyle] = useState<Record<string, string>>({})

  // `onClose` through a ref, deliberately. Callers pass an inline arrow, so it is a
  // new function on every render; listing it in the effect's dependencies made the
  // effect re-run on each one, and the effect's own `setStyle` (a fresh object every
  // time) caused that render — a loop whose visible symptom was a panel that never
  // got its measured position at all and fell back to its static one.
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    if (!anchor) return
    const position = () => {
      const next = railOverlayStyle(anchor.getBoundingClientRect(), root.current, RAIL_DROPUP_MAX_WIDTH_PX)
      // Only on a real change: a scroll listener that set fresh state on every event
      // would re-render the whole list for each frame of a rail pan.
      setStyle(current => sameStyle(current, next) ? current : next)
    }
    position()
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node
      if (!root.current?.contains(target) && !anchor.contains(target)) close.current()
    }
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); close.current() } }
    const unwatch = watchRailOverlayPlacement(position)
    window.addEventListener('pointerdown', dismiss)
    window.addEventListener('keydown', key)
    return () => {
      unwatch()
      window.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('keydown', key)
    }
  }, [anchor])

  // Arrow keys walk the rows in document order, which puts the sticky link first
  // — the same order a reader sees, and the same walk the context menus use.
  const walk = (event: KeyboardEvent) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    const buttons = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [])
    if (!buttons.length) return
    event.preventDefault()
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement)
    const step = event.key === 'ArrowDown' ? 1 : -1
    const next = at < 0 ? (step > 0 ? 0 : buttons.length - 1) : (at + step + buttons.length) % buttons.length
    buttons[next].focus()
  }

  return <div
    ref={root}
    class="rail-dropup ui-portal"
    style={style}
    role="dialog"
    aria-label={label}
    onMouseDown={holdSoftKeyboard}
    onKeyDown={walk}
  >
    {actions.length === 1
      ? <button type="button" class="rail-dropup-open" title={actions[0].title} onClick={() => { onClose(); actions[0].run() }}>
        {actions[0].label}
      </button>
      : <div class="rail-dropup-sticky">
        {actions.map(action => <button
          key={action.label}
          type="button"
          class="rail-dropup-open"
          title={action.title}
          onClick={() => { onClose(); action.run() }}
        >{action.label}</button>)}
      </div>}
    <div class="rail-dropup-list">{children}</div>
  </div>
}
