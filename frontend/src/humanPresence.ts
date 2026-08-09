/**
 * Is a human currently able to see this window?
 *
 * Used to gate read acknowledgements. "Visible in the layout" is not the same
 * question: a pane can be mounted and on screen in a window that is minimized,
 * behind another app, or on a phone whose screen is off. Marking those read is
 * how a night of finished turns used to vanish before anyone looked at them.
 *
 * Focus, not just visibility, is required. A window parked visible on a second
 * monitor while nobody is at the desk is exactly the case that has to keep its
 * unread marks, and an over-strict answer only leaves a row lit slightly longer
 * than needed - the recoverable direction, and the one a manual mark-read can
 * always override.
 */

export type PresenceListener = (present: boolean) => void

export function isHumanPresent(): boolean {
  if (typeof document === 'undefined') return false
  return document.visibilityState === 'visible' && document.hasFocus()
}

/**
 * Subscribe to presence changes. Calls back only on transitions, and returns an
 * unsubscribe. `pageshow` is included for the mobile bfcache restore, which can
 * deliver neither `focus` nor `visibilitychange`.
 */
export function watchHumanPresence(listener: PresenceListener): () => void {
  let present = isHumanPresent()
  const report = () => {
    const next = isHumanPresent()
    if (next === present) return
    present = next
    listener(next)
  }
  window.addEventListener('focus', report)
  window.addEventListener('blur', report)
  window.addEventListener('pageshow', report)
  document.addEventListener('visibilitychange', report)
  return () => {
    window.removeEventListener('focus', report)
    window.removeEventListener('blur', report)
    window.removeEventListener('pageshow', report)
    document.removeEventListener('visibilitychange', report)
  }
}
