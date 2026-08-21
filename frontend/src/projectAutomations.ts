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

// Explicit extension: `test/all.ts` runs under node's type stripping, which does not
// resolve an extensionless relative TypeScript specifier, and this module is reached
// from a test.
import { api } from './api.ts'

export const PROJECT_AUTOMATIONS_CHANGED = 'mux:project-automations-changed'

export type ProjectAutomationState = {
  revision: string
  requested: Record<string, boolean>
  /** The effective set: requested, with every dependency it needs also satisfied. */
  enabled: string[]
  blocked: Record<string, string[]>
  /**
   * Opted in, dependencies satisfied, and held back anyway because the install has no
   * proven model provider. Its own field rather than a `blocked` entry: `blocked` values
   * are automation ids a grant can switch on, and no automation's enabling fixes an
   * unverified endpoint - merging them would render a gate offering to turn on nothing.
   */
  unverified?: string[]
  /** The install-wide provider verdict this resolution was computed under. */
  llm?: LlmReadiness | null
  automations: {
    id: string; kind: string; label: string; requires: string[]; implemented: boolean
    /** Whether switching it on can cost money. Read from the registry, never asserted
     *  by a surface: "free" is the fact a one-click grant most needs to get right. */
    spends: boolean
    /** Whether it is inert without a proven model provider. Separate from `spends`,
     *  because a model on the operator's own machine is a dependency with no bill. */
    needs_llm?: boolean
  }[]
  scan_timeline_auto_enable: boolean
}

/**
 * Why model-backed features can or cannot run, install-wide.
 *
 * `reason` is a whole sentence from the daemon and is rendered verbatim. A surface that
 * paraphrased it would be guessing at which of four states it is in - no key, no endpoint,
 * never verified, or verified-then-edited - and those need different next actions.
 */
export type LlmReadiness = {
  ready: boolean
  provider: string
  code: 'ready' | 'no_key' | 'no_endpoint' | 'no_model' | 'unverified' | 'endpoint_changed'
    | 'unknown'
  reason: string
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
