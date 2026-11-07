/**
 * Keep a menu opened by a touch hold from being pressed by the finger that opened it.
 *
 * A hold opens its menu where the finger is, so that finger can be resting on one of the menu's
 * rows the moment it renders, and lifting it delivers a click to that row: long-press a Project
 * low on the screen and land in Rename. Measured on Chromium's touch input with a 420 ms and a
 * 1500 ms hold, so holding longer does not avoid it.
 *
 * A window measured from when the menu opened cannot answer this, because nobody decides how
 * long a finger stays down. The guard is keyed to the gesture instead. It swallows clicks while
 * that pointer is down and for a moment after it lifts, and any new press ends it at once, so a
 * deliberate tap on the menu always works however quickly it follows.
 */

/** How long after the lift a click can still be the gesture's own. Generous because it costs
 *  nothing: every deliberate click starts with a `pointerdown`, which ends the guard. */
const RELEASE_SETTLE_MS = 700

const CAPTURE = { capture: true }

type GuardTarget = Pick<EventTarget, 'addEventListener' | 'removeEventListener'>

let endActiveGuard: (() => void) | null = null

/** Call while the pointer is still down, before the menu opens. Returns the guard's end. */
export function guardHeldPointer(pointerId: number, target: GuardTarget = window): () => void {
  endActiveGuard?.()
  let settle: ReturnType<typeof setTimeout> | null = null

  const onClick = (event: Event) => {
    // A keyboard or `element.click()` reports no click count, and has no finger behind it.
    if ((event as MouseEvent).detail === 0) return
    event.preventDefault()
    event.stopPropagation()
    if (settle !== null) end()
  }
  const onLift = (event: Event) => {
    if ((event as PointerEvent).pointerId !== pointerId || settle !== null) return
    settle = setTimeout(end, RELEASE_SETTLE_MS)
  }
  function end() {
    if (settle !== null) clearTimeout(settle)
    settle = null
    target.removeEventListener('click', onClick, CAPTURE)
    target.removeEventListener('pointerdown', end, CAPTURE)
    target.removeEventListener('pointerup', onLift, CAPTURE)
    target.removeEventListener('pointercancel', onLift, CAPTURE)
    if (endActiveGuard === end) endActiveGuard = null
  }

  target.addEventListener('click', onClick, CAPTURE)
  target.addEventListener('pointerdown', end, CAPTURE)
  target.addEventListener('pointerup', onLift, CAPTURE)
  target.addEventListener('pointercancel', onLift, CAPTURE)
  endActiveGuard = end
  return end
}
