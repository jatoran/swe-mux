import type { ComponentChildren } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import { holdSoftKeyboard } from './mobileKeyboard'
import { railPopoverStyle } from './railOverflow'

// The rest of one rail row, as a wrap grid of the same real chips.
//
// It is deliberately *not* a `RailDropup`. A drop-up is a picker: it lists things, you take
// one, it closes. This is the rail itself, folded — the chips inside it are the production
// buttons with their production handlers — and a rail does not close when you press a key on
// it. So a selection here leaves the panel open, which is what makes the two-click End
// session confirm and the repeat-tap arrow keys work from inside it exactly as they work on
// the row. Dismissal is therefore always deliberate: outside, Escape, or the close control.
//
// Three consequences, each of which is a real behaviour rather than a detail:
//
//   * A drop-up chip (Actions, Clip, Skills, Prompts) opened from in here renders *over* this
//     panel and dismisses itself on an outside pointer. That pointer is usually inside this
//     panel, so this panel's own outside-dismissal has to ignore anything that lands in a
//     `.rail-dropup`, or picking a clipboard entry would close the rail behind it.
//   * Escape has the same collision. Both listeners sit on `window`, where `stopPropagation`
//     does not stop a sibling, and this one is registered first because it mounted first — so
//     the panel steps aside while a drop-up is open rather than trying to win the race.
//   * A selection that opens a drawer or the prompt library *does* collapse the panel
//     (`railPopoverClosingCommand`), because leaving it standing would cover the surface the
//     tap just asked for. The caller owns that, since it owns the command bus.
//
// Placement and translucency both live outside this component: `railPopoverStyle` for the
// geometry, `style.css` for the glass. The opacity there is not a taste value — see
// `test/railGlassContrast.test.ts`, which composites it over a white and a black terminal
// for every theme and holds label text at 4.5:1.

function sameStyle(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = Object.keys(b)
  return keys.length === Object.keys(a).length && keys.every(key => a[key] === b[key])
}

type Props = {
  /** Accessible name for the panel; one row's worth of overflow. */
  label: string
  /** The `+N` chip this hangs from. */
  anchor: HTMLElement | null
  onClose: () => void
  children: ComponentChildren
}

export function RailOverflowPopover({ label, anchor, onClose, children }: Props) {
  const root = useRef<HTMLDivElement>(null)
  const [style, setStyle] = useState<Record<string, string>>({})

  // Through a ref for the reason `RailDropup` does it: callers pass an inline arrow, so
  // listing it in the effect's dependencies re-runs the effect on every render, and the
  // effect's own `setStyle` causes that render.
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    if (!anchor) return
    const position = () => {
      const rect = anchor.getBoundingClientRect()
      const next = railPopoverStyle(rect, { width: window.innerWidth, height: window.innerHeight })
      setStyle(current => sameStyle(current, next) ? current : next)
    }
    position()
    const dropupOpen = () => !!document.querySelector('.rail-dropup')
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node
      if (root.current?.contains(target) || anchor.contains(target)) return
      // A drop-up opened from a chip in here is outside this panel's DOM and takes its own
      // pointers; those are not a dismissal of the rail it was opened from.
      if (target instanceof Element && target.closest('.rail-dropup')) return
      close.current()
    }
    const key = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || dropupOpen()) return
      event.stopPropagation()
      close.current()
    }
    window.addEventListener('resize', position)
    window.addEventListener('scroll', position, true)
    window.addEventListener('pointerdown', dismiss)
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('resize', position)
      window.removeEventListener('scroll', position, true)
      window.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('keydown', key)
    }
  }, [anchor])

  return <div
    ref={root}
    class="rail-overflow-popover"
    style={style}
    // A `group`, not a `dialog`. It is non-modal by design and its contents are the same
    // toolbar controls the row holds, so it belongs to the rail's `toolbar` rather than
    // interrupting it - and a dialog role would promise focus management this deliberately
    // does not do. The `+N` chip carries `aria-expanded`, which is what names the pairing.
    role="group"
    aria-label={label}
    onMouseDown={holdSoftKeyboard}
  >
    <header>
      <strong>More actions</strong>
      {/* The one thing a reader cannot discover by trying: this panel does not close when
          you use it. Said in words rather than implied, because every other popover on the
          rail behaves the opposite way. */}
      <span>stays open</span>
      <button type="button" class="rail-overflow-close" aria-label="Close more actions" title="Close" onClick={onClose}>×</button>
    </header>
    <div class="rail-overflow-grid">{children}</div>
  </div>
}
