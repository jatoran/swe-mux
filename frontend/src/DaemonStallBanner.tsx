import { useEffect, useState } from 'preact/hooks'
import {
  DAEMON_STALL_HEADING, DAEMON_STALL_PROMISE, daemonStallText, stallSeconds, useDaemonLiveness,
} from './daemonLiveness'

/**
 * App-level strip saying the daemon has stopped answering.
 *
 * It sits in the shell's banner row beside `UpdateBanner` and the UI-build
 * strip, which is what makes it non-blocking by construction: a row of chrome
 * that never covers a terminal, opens no dialog, and takes no focus. That last
 * point is the whole reason it is a banner and not an overlay - the thing it
 * reports is a daemon that will come back by itself, and the operator's
 * keystrokes are queued in the sockets meanwhile, so the worst thing this
 * could do is get between them and the terminal they were typing into.
 *
 * `role="status"` with `aria-live="polite"` announces the arrival once. The
 * clock beside the heading ticks every second and is marked `aria-live="off"`,
 * because a live region re-read on every change would announce a number
 * nobody asked for thirty times over a thirty-second stall.
 *
 * No buttons. There is nothing to press: a reload during a stall hangs on its
 * first request, and a daemon restart is a heavier act than the situation
 * warrants (`daemonLiveness.ts` has the reasoning).
 */

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
      <strong>{DAEMON_STALL_HEADING}</strong>
      <em class="daemon-stall-clock" aria-live="off">{stallSeconds(stalledSince, now)}s</em>
      <span>{DAEMON_STALL_PROMISE}</span>
    </div>
  )
}
