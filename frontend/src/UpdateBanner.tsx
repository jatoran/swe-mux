import { useCallback, useEffect, useState } from 'preact/hooks'
import {
  dismissUpdate, fetchUpdateStatus, shouldShowUpdateBanner, updateBannerText,
  type UpdateStatus,
} from './updateCheck'

/**
 * App-level strip saying a newer swe-mux release exists.
 *
 * It sits in the shell's banner row beside `ContinuityBanner` and the UI-build
 * strip, which is what makes it non-blocking by construction: it takes a row of
 * chrome and never covers a terminal, never opens a dialog, and never takes
 * focus. `role="status"` with `aria-live="polite"` is the accessible form of the
 * same promise - a screen reader announces it at the next pause rather than
 * interrupting, so it cannot arrive on top of a turn in progress.
 *
 * Nothing here downloads or installs anything. The link goes to the release
 * notes and the operator decides; declining is per version and is recorded by
 * the daemon, so it holds across a reload, a restart, and the phone.
 *
 * The poll is deliberately slack. The daemon checks once a day and this only
 * reads the answer it already has, so a slow cadence costs nothing and a fast
 * one would buy nothing.
 */

/** How often the mounted banner re-reads the daemon's answer. */
export const POLL_INTERVAL_MS = 60 * 60 * 1000

export function UpdateBanner() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const load = useCallback(() => {
    // A failed read leaves the previous answer in place rather than clearing it:
    // one lost poll during a daemon restart must not blink the banner away and
    // back. An update notice is the last thing that should be noisy.
    void fetchUpdateStatus().then(setStatus).catch(() => {})
  }, [])
  useEffect(() => {
    load()
    const timer = window.setInterval(load, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [load])
  const decline = useCallback(() => {
    const version = status?.latest?.version
    if (!version) return
    // Hidden immediately and persisted in the background: the press is the
    // decision, and making the operator watch a round trip to dismiss a notice
    // would be worse than the notice.
    setStatus(current => current ? { ...current, banner: false } : current)
    void dismissUpdate(version).then(setStatus).catch(() => {})
  }, [status])
  if (!shouldShowUpdateBanner(status) || !status) return null
  const latest = status.latest
  return (
    <div class="release-update-banner" role="status" aria-live="polite">
      <strong>Update available</strong>
      <span>{updateBannerText(status)}</span>
      {latest?.changelog
        ? <a href={latest.changelog} target="_blank" rel="noreferrer">Release notes</a>
        : null}
      <button onClick={decline} aria-label={`Dismiss the ${latest?.version} update notice`}>
        Dismiss
      </button>
    </div>
  )
}
