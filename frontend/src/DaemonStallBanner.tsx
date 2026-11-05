import { rich } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import {
  daemonStallHeading, daemonStallPromise, daemonStallText, stallSeconds, useDaemonLiveness,
} from './daemonLiveness'

/** A non-blocking status strip. Recovery belongs to the desktop process, since
 * an HTTP recovery button cannot reach a dead or GIL-blocked daemon. */

/**
 * @param suppressed True while a deliberate outage is in flight - a redeploy's
 *   daemon-down stage or a session-preserving restart. Those already have a
 *   surface, and a second one saying "not responding" over the top would read
 *   as a second failure.
 */
export function DaemonStallBanner({ suppressed = false }: { suppressed?: boolean }) {
  const { stalled, stalledSince } = useDaemonLiveness(!suppressed)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!stalled) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [stalled])
  if (!stalled || stalledSince === null) return null
  return (
    <div
      class="daemon-stall-banner"
      role="status"
      aria-live="polite"
      data-testid="daemon-stall-banner"
      title={daemonStallText(stalledSince, now)}
    >
      <strong>{daemonStallHeading()}</strong>
      <em class="daemon-stall-clock" aria-live="off">{rich("DaemonStallBanner.6732456ec2", { value1: stallSeconds(stalledSince, now) })}</em>
      <span>{daemonStallPromise()}</span>
    </div>
  )
}
