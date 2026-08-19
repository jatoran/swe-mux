import { useEffect, useState } from 'preact/hooks'
import { elapsedLabel, phaseDetail, phaseLabel, type RedeployState } from './redeployProgress.ts'

/** The persistent redeploy indicator: a spinner pinned to the top-left corner.
 *
 *  It exists because a redeploy used to be invisible to every client except the
 *  tab that started it - a phone, a second window, or the desktop when the
 *  redeploy was triggered from the phone simply watched their requests start
 *  failing. It stays up for the whole redeploy, through the daemon-down window
 *  and until the page reloads into the successor.
 *
 *  Non-blocking on purpose. During the build the app is fully usable and this is
 *  only a notice; the blocking overlay is a separate surface that appears only
 *  once the daemon actually goes away.
 */
export function RedeployChip({ state }: { state: RedeployState }) {
  const [expanded, setExpanded] = useState(false)
  // Re-renders once a second purely for the elapsed timer. With the daemon gone
  // there is nothing to poll for real progress, and a clock that visibly moves is
  // what separates "still working" from "wedged".
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (state.phase === 'idle') return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [state.phase])
  // A redeploy that ends collapses the panel, so the next one does not open
  // already expanded onto the previous one's log.
  useEffect(() => { if (state.phase === 'idle') setExpanded(false) }, [state.phase])
  if (state.phase === 'idle') return null
  const label = phaseLabel(state.phase)
  const elapsed = elapsedLabel(state.startedAt, now)
  return <div class={`redeploy-chip ${state.phase}`} role="status" aria-live="polite">
    <button
      type="button"
      class="redeploy-chip-summary"
      aria-expanded={expanded}
      title={`${label} - ${phaseDetail(state.phase)}`}
      onClick={() => setExpanded(value => !value)}
    >
      <span class="redeploy-spinner" aria-hidden="true" />
      <strong>{label}</strong>
      <small>{elapsed}</small>
    </button>
    {expanded && <div class="redeploy-chip-body">
      <p>{phaseDetail(state.phase)}</p>
      {/* Real build output while the daemon is still up to serve it. Once it is
          gone the last lines simply stop advancing, which is honest: no process
          the browser can reach knows any more than this. */}
      {state.logTail.length > 0 && <pre>{state.logTail.slice(-8).join('\n')}</pre>}
      {state.phase === 'down' && <p class="redeploy-chip-note">
        Your sessions are held by the PTY supervisor and are not affected.
      </p>}
    </div>}
  </div>
}
