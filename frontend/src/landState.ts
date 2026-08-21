/**
 * The two daemon reads the landing surfaces share.
 *
 * Landing is drawn twice on one surface - the act, inside the Map row of the worktree it
 * acts on, and everything Project-wide, in the strip at the head of that map - so the
 * queue read had to stop belonging to whichever component happened to own it. One hook,
 * mounted once by `GitTab` and handed down, is what keeps the row and the strip from
 * disagreeing about which request is running, and keeps them from polling the daemon
 * twice for the same answer.
 *
 * `active` exists because the Git tab has three readings and only Map is one of them. A
 * hook that polled regardless would keep a five-second timer alive behind Log and
 * Provenance for a payload nothing on screen reads.
 */

import { useCallback, useEffect, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import {
  parseLandQueue,
  parseLandVerifyCommand,
  type LandQueue,
  type LandVerifyCommand,
} from './gitLand'
import { PROJECT_AUTOMATIONS_CHANGED } from './projectAutomations'

/** The daemon's own sweep cadence. Anything faster reports the same row again. */
const LAND_POLL_MS = 5000

/** A daemon refusal's own words, which name the cause; anything else as it arrived. */
export function landErrorText(cause: unknown): string {
  const detail = (cause as ApiError)?.detail
  if (typeof detail?.error === 'string') return detail.error
  return cause instanceof Error ? cause.message : String(cause)
}

export type LandQueueState = {
  queue: LandQueue | null
  error: string
  refresh: () => Promise<void>
}

export function useLandQueue(projectId: string | undefined, active: boolean): LandQueueState {
  const [queue, setQueue] = useState<LandQueue | null>(null)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    if (!projectId) { setQueue(null); return }
    try {
      const raw = await api<unknown>('GET', `/api/land?project_id=${encodeURIComponent(projectId)}`)
      setQueue(parseLandQueue(raw)); setError('')
    } catch (cause) {
      setError(landErrorText(cause))
    }
  }, [projectId])

  useEffect(() => {
    setQueue(null); setError('')
    if (!projectId || !active) return
    void refresh()
    const changed = () => void refresh()
    window.addEventListener('mux:land-changed', changed)
    window.addEventListener('mux:events-connected', changed)
    // A verifying gate reports its own step and elapsed time, and neither of those
    // emits an event: a step boundary inside a subprocess is not a daemon transition.
    // The poll is what makes that reading move rather than freeze at the value the
    // last state change happened to carry.
    const timer = window.setInterval(changed, LAND_POLL_MS)
    return () => {
      window.removeEventListener('mux:land-changed', changed)
      window.removeEventListener('mux:events-connected', changed)
      window.clearInterval(timer)
    }
  }, [refresh, active])

  return { queue, error, refresh }
}

export type VerifyCommandState = {
  gate: LandVerifyCommand | null
  refresh: () => Promise<void>
  setGate: (value: LandVerifyCommand) => void
}

/**
 * The gate as it resolves for one checkout.
 *
 * Still per-worktree in the daemon - the script convention is fingerprinted from the
 * worktree's own copy, so a branch that edits `.worktree-verify` must present for
 * approval again. The *surface* asks it of the Project root only, once: a per-row copy
 * of the answer meant the same paragraph about approved bytes under every worktree,
 * which is a Project-wide fact drawn N times. A branch whose own script differs is
 * reported by its land refusing, which names the branch, rather than by eight blocks
 * that mostly agree.
 */
export function useVerifyCommand(
  projectId: string | undefined,
  worktreeRoot: string,
  active: boolean,
): VerifyCommandState {
  const [gate, setGate] = useState<LandVerifyCommand | null>(null)

  const refresh = useCallback(async () => {
    if (!projectId || !worktreeRoot) { setGate(null); return }
    try {
      const raw = await api<unknown>(
        'GET',
        `/api/land/verify-command?project_id=${encodeURIComponent(projectId)}`
        + `&worktree_root=${encodeURIComponent(worktreeRoot)}`,
      )
      setGate(parseLandVerifyCommand(raw))
    } catch { setGate(null) }
  }, [projectId, worktreeRoot])

  useEffect(() => {
    setGate(null)
    if (!active) return
    void refresh()
    // The bytes move when the Project's config is written, which is exactly what the
    // editor does. `App` re-broadcasts the daemon's `project_configuration_changed` on
    // this channel, so another client's edit reaches this reading too.
    const changed = () => void refresh()
    window.addEventListener(PROJECT_AUTOMATIONS_CHANGED, changed)
    // `mux:land-changed` also carries `land_verify_approved`, so an approval made from
    // another client (or from the other landing surface in this one) clears the gate
    // here rather than leaving a stale "not approved" behind.
    window.addEventListener('mux:land-changed', changed)
    return () => {
      window.removeEventListener(PROJECT_AUTOMATIONS_CHANGED, changed)
      window.removeEventListener('mux:land-changed', changed)
    }
  }, [refresh, active])

  return { gate, refresh, setGate }
}
