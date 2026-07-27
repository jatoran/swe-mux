import { Fragment } from 'preact'
import type { ComponentChildren } from 'preact'
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'

// A collapsible group of items inside any `.context-menu`. Host a set of them with
// one `useState<string|null>(null)` and pass it as `openId`/`onOpenChange` so only
// one group is ever expanded.
//
// Two presentations, chosen by pointer capability rather than by caller:
//
// - Hover-capable + wide: a side flyout. An inline accordion would reflow the rows
//   beneath the header while the cursor is over them, which makes "hover another
//   row to collapse this one" jump around under the pointer.
// - Touch or narrow: an inline accordion. The menu is already
//   `min(300px, 100dvw - 8px)` wide, so a flyout has nowhere to go, and there is no
//   hover to open it with.
//
// Both presentations render their items as buttons inside a `.context-menu`
// subtree in document order right after the header, so the menu-wide arrow-key
// walk in App.tsx (`.context-menu button:not(:disabled)`, document order) steps
// through them in the position a reader expects. The flyout is `position:fixed`
// with measured coordinates because `.context-menu` sets `overflow-y:auto`, which
// would clip an absolutely positioned child.

const OPEN_DELAY_MS = 120
// Generous enough that the diagonal from the header to the flyout's far rows does
// not collapse the group mid-traverse.
const CLOSE_DELAY_MS = 250
const FLYOUT_QUERY = '(pointer:fine) and (min-width:761px)'

export function useFlyoutCapable() {
  const [capable, setCapable] = useState(() => window.matchMedia(FLYOUT_QUERY).matches)
  useEffect(() => {
    const query = window.matchMedia(FLYOUT_QUERY)
    const update = () => setCapable(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return capable
}

type Props = {
  id: string
  label: string
  openId: string | null
  onOpenChange: (id: string | null) => void
  children: ComponentChildren
  disabled?: boolean
  hint?: string
}

export function MenuGroup({ id, label, openId, onOpenChange, children, disabled, hint }: Props) {
  const flyout = useFlyoutCapable()
  const open = openId === id && !disabled
  const header = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const timer = useRef(0)
  const [box, setBox] = useState<{ left: number; top: number } | null>(null)

  const clear = () => { if (timer.current) { window.clearTimeout(timer.current); timer.current = 0 } }
  useEffect(() => clear, [])
  useEffect(() => { if (!open) setBox(null) }, [open])

  // Measured after the panel is in the DOM so the flip and clamp use its real size.
  useLayoutEffect(() => {
    if (!open || !flyout) return
    const anchor = header.current?.getBoundingClientRect()
    const size = panel.current?.getBoundingClientRect()
    if (!anchor || !size) return
    // Overlap the parent menu by a few pixels: a dead gap between header and panel
    // would cancel the hover the moment the pointer crossed it.
    const right = anchor.right - 4
    const left = right + size.width > window.innerWidth - 4
      ? Math.max(4, anchor.left - size.width + 4)
      : right
    const top = Math.max(4, Math.min(anchor.top - 5, window.innerHeight - size.height - 4))
    setBox(current => current && current.left === left && current.top === top ? current : { left, top })
  }, [open, flyout, children])

  const scheduleOpen = () => { if (disabled || !flyout) return; clear(); timer.current = window.setTimeout(() => onOpenChange(id), OPEN_DELAY_MS) }
  const scheduleClose = () => { if (!flyout) return; clear(); timer.current = window.setTimeout(() => onOpenChange(null), CLOSE_DELAY_MS) }

  return <Fragment>
    <button
      ref={header}
      class={`menu-group-header ${open ? 'open' : ''}`}
      role="menuitem"
      disabled={disabled}
      title={hint}
      aria-haspopup="menu"
      aria-expanded={open}
      onPointerEnter={event => { if (event.pointerType !== 'touch') scheduleOpen() }}
      onPointerLeave={event => { if (event.pointerType !== 'touch') scheduleClose() }}
      onClick={() => { clear(); onOpenChange(open ? null : id) }}
    >{label}<span class="menu-group-caret" aria-hidden="true">{flyout ? '›' : open ? '▾' : '▸'}</span></button>
    {open && (flyout
      ? <div
          ref={panel}
          class="context-menu menu-flyout"
          role="menu"
          aria-label={label}
          onPointerEnter={clear}
          onPointerLeave={event => { if (event.pointerType !== 'touch') scheduleClose() }}
          style={{ left: `${box?.left ?? -9999}px`, top: `${box?.top ?? -9999}px`, visibility: box ? 'visible' : 'hidden' }}
        >{children}</div>
      : <div class="menu-group-items" role="group" aria-label={label}>{children}</div>)}
  </Fragment>
}
