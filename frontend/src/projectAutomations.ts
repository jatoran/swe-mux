/**
 * Which control-plane automations one Project has opted into, for the surfaces that read
 * from them.
 *
 * The Projects registry owns the editing; this is the read every *consumer* needs, and it
 * exists because those consumers cannot otherwise tell "off" from "quiet". A findings pane
 * with no findings and a findings pane whose detectors were never permitted look identical,
 * and the identical-looking one is a lie: nothing will ever appear there, however long you
 * wait. Knowing the opt-in state is what lets a surface say so and offer the switch.
 *
 * Shared and cached because two drawer panes ask the same question about the same Project
 * within a second of each other, and the answer changes only when a human edits it — which
 * the daemon announces (`project_configuration_changed`, re-broadcast by `App` as
 * `mux:project-automations-changed`), so the cache is dropped on that event rather than
 * expiring on a timer that would be wrong in both directions.
 */

import { api } from './api'

export const PROJECT_AUTOMATIONS_CHANGED = 'mux:project-automations-changed'

export type ProjectAutomationState = {
  revision: string
  requested: Record<string, boolean>
  /** The effective set: requested, with every dependency it needs also satisfied. */
  enabled: string[]
  blocked: Record<string, string[]>
  automations: { id: string; kind: string; label: string; requires: string[]; implemented: boolean }[]
  scan_timeline_auto_enable: boolean
}

const cache = new Map<string, Promise<ProjectAutomationState>>()

export function forgetProjectAutomations(projectId?: string): void {
  if (projectId) cache.delete(projectId)
  else cache.clear()
}

export function fetchProjectAutomations(projectId: string): Promise<ProjectAutomationState> {
  const cached = cache.get(projectId)
  if (cached) return cached
  const pending = api<ProjectAutomationState>('GET', `/api/projects/${projectId}/automations`)
    // A failed read is not evidence of anything, so it is not remembered as one: the next
    // caller retries rather than inheriting an error as a state.
    .catch(error => { cache.delete(projectId); throw error })
  cache.set(projectId, pending)
  return pending
}

if (typeof window !== 'undefined') {
  window.addEventListener(PROJECT_AUTOMATIONS_CHANGED, event => {
    forgetProjectAutomations((event as CustomEvent<{ projectId?: string }>).detail?.projectId)
  })
}
