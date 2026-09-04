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
  /**
   * Requested ids the install-wide ceiling turns off - the id itself or something
   * in its dependency closure disallowed globally. Its own field for the same
   * one-actionable-answer reason as `unverified`: the fix is global policy, not a
   * grant and not a Project toggle, and the Project's own choice is retained.
   */
  globally_disabled?: string[]
  /** The install-wide provider verdict this resolution was computed under. */
  llm?: LlmReadiness | null
  automations: AutomationRegistryEntry[]
  scan_timeline_auto_enable: boolean
}

export type AutomationRegistryEntry = {
  id: string; kind: string; label: string; requires: string[]; implemented: boolean
  /** The policy-matrix block this row is drawn in - what it is about, not what it
   *  depends on (`automation_registry.FAMILIES`). Rows keep registry order inside
   *  a family, so the session titler sits directly above the re-titler. */
  family?: string
  /** What this switch does, in a sentence or two, shown when its row is expanded.
   *  Read from the registry rather than restated here: a second copy of what a
   *  switch means is the one that drifts when the behaviour changes. */
  description?: string
  /** Whether switching it on can cost money. Read from the registry, never asserted
   *  by a surface: "free" is the fact a one-click grant most needs to get right. */
  spends: boolean
  /** Whether it is inert without a proven model provider. Separate from `spends`,
   *  because a model on the operator's own machine is a dependency with no bill. */
  needs_llm?: boolean
  /** Whether a Project that never wrote this id down has it on. Consumers that
   *  read only the explicit `requested` map must fall back to this, or they
   *  render a switch the daemon honours as on as if it were off. */
  default_on?: boolean
  /** What a Project that never wrote this id down actually inherits on *this*
   *  install: the operator's `automation_project_defaults` merged over the
   *  registry's `default_on`, dependency closure completed. This is the fallback
   *  a consumer wants; `default_on` alone answers only what the registry ships
   *  with, so reading it would show an inherited automation as off. Absent on
   *  payloads served without a config to resolve against, which is a different
   *  fact from `false` and must not be rendered as one. */
  install_default?: boolean
  /** The dedicated install switch that is this row's global ceiling, where one
   *  exists (`scan_timeline_enabled` and friends) - the Global toggle writes
   *  that key and never an `automation_global_allow` entry. */
  install_switch?: string | null
  /** The resolved install-wide ceiling: this id and its whole dependency
   *  closure are allowed anywhere at all. Absent on payloads served without a
   *  config to resolve against. */
  globally_allowed?: boolean
}

/** What an id a Project never mentioned resolves to, install defaults included.
 *
 *  One function because five surfaces ask it and each of them getting it subtly
 *  wrong is how "inherit" stops being visible: `install_default` is the daemon's
 *  answer and `default_on` is only the registry's, so a consumer that reads the
 *  second alone renders every inherited automation as off. */
export const inheritedDefault = (entry?: {install_default?: boolean; default_on?: boolean}): boolean =>
  entry?.install_default ?? entry?.default_on === true

/** Whether a Project has this automation on, its own answer first. */
export const automationRequested = (
  requested: Record<string, boolean> | undefined,
  entry?: {id?: string; install_default?: boolean; default_on?: boolean},
): boolean => {
  const own = entry?.id ? requested?.[entry.id] : undefined
  return own ?? inheritedDefault(entry)
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
  /**
   * Whether this endpoint reports what a completion cost. A local OpenAI-compatible
   * server usually does not, and absent cost is unknown rather than zero, so a
   * dollar-only budget cannot bind against it - which is what `BudgetControl` says
   * when this is false. Optional because an older daemon does not send it, and
   * "not stated" must not render as an accusation against a working endpoint.
   */
  reports_cost?: boolean
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
