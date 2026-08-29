/**
 * Turning a switch on from the surface that cannot work without it.
 *
 * `settingTargets.ts` answers "where does this switch live"; this module answers "can I
 * grant it from here, and what happens if I do". The two are keyed the same on purpose:
 * a grant *is* a setting target, so a gate renders the button and the owner's deep link
 * from one id and the pair cannot drift apart.
 *
 * The pattern is the Land queue's verification gate, generalised. That gate has always
 * stated the block, shown the exact bytes, and approved them in place; everything else
 * that went inert behind a switch only ever offered a link out to an overlay, a staged
 * Save, and a walk back. Two overlay round trips to see one drawer pane is the cost this
 * removes.
 *
 * Three rules make a write from inside a drawer pane safe, and the daemon enforces all
 * three independently (`src/swe_mux/grants.py`):
 *
 *  - **Additive only.** A gate can turn something on and can never turn anything off.
 *    Withdrawal stays with the owning editor, which is how "one owner per switch"
 *    survives having many places that can grant.
 *  - **Allowlisted.** Only the keys below, checked against the daemon's own closed set
 *    by `test/grants.test.ts`, so a renamed switch fails a test rather than a click.
 *  - **Disclosed before the press.** Scope, where the change is written, the dependency
 *    closure it drags in, and whether it can cost money - all on the button, not after.
 */

// Explicit extensions: `test/all.ts` runs under node's type stripping, which does not
// resolve an extensionless relative TypeScript specifier, and this module is reached
// from `test/grants.test.ts`.
import { api } from './api.ts'
import { SETTING_TARGETS, type SettingTargetId } from './settingTargets.ts'
import {
  fetchProjectAutomations,
  forgetProjectAutomations,
  type ProjectAutomationState,
} from './projectAutomations.ts'

export type GrantScope = 'install' | 'project' | 'device'

export type GrantDescriptor =
  /** An install-wide `Config` boolean. Applies to every Project and every session. */
  | { scope: 'install' }
  /** One of the Project's control-plane opt-ins, with its dependency closure. */
  | { scope: 'project'; kind: 'automation' }
  /** A typed field in the Project's `.swe-mux/config.toml`, set to exactly `value`. */
  | { scope: 'project'; kind: 'value'; value: unknown }
  /** This device's own preference. Never leaves the browser's settings profile. */
  | { scope: 'device' }

/**
 * Every switch a gate may turn on, keyed by the setting target that owns it.
 *
 * The key of the underlying switch is *derived* from that target rather than repeated
 * here (`grantKey`), so there is exactly one place a config key is written down.
 */
export const GRANTS = {
  // Install-wide.
  'clipboard.history': { scope: 'install' },
  'queue.autoDelivery': { scope: 'install' },
  'queue.agentMessaging': { scope: 'install' },
  'approvals.autoAnswer': { scope: 'install' },
  'usage.ccusage': { scope: 'install' },
  'voice.tts': { scope: 'install' },
  'voice.stt': { scope: 'install' },
  'schedules.install': { scope: 'install' },
  'automation.engine': { scope: 'install' },
  'automation.scanTimeline': { scope: 'install' },
  'automation.landQueue': { scope: 'install' },
  // Per-Project opt-ins.
  'project.scanTimeline': { scope: 'project', kind: 'automation' },
  'project.codeGraph': { scope: 'project', kind: 'automation' },
  'project.scheduledRuns': { scope: 'project', kind: 'automation' },
  'project.attentionRanking': { scope: 'project', kind: 'automation' },
  'project.spawnReview': { scope: 'project', kind: 'automation' },
  'project.provenanceGraph': { scope: 'project', kind: 'automation' },
  'project.loopDetection': { scope: 'project', kind: 'automation' },
  'project.declaredVsVerified': { scope: 'project', kind: 'automation' },
  'project.docDebt': { scope: 'project', kind: 'automation' },
  'project.landQueue': { scope: 'project', kind: 'automation' },
  'project.sessionControl': { scope: 'project', kind: 'automation' },
  // Per-Project authority fields. Each may only be *raised*; lowering one back to its
  // inert default belongs to the Project's own editor, beside every other way of taking
  // a permission away.
  'project.scanTimelineAutoArm': { scope: 'project', kind: 'value', value: true },
  'project.landGrant': { scope: 'project', kind: 'value', value: 'granted' },
  'project.landVerifyGrant': { scope: 'project', kind: 'value', value: 'granted' },
  'project.sessionControlGrant': { scope: 'project', kind: 'value', value: 'granted' },
  'project.spawnGrant': { scope: 'project', kind: 'value', value: 'granted' },
  'project.interjectGrant': { scope: 'project', kind: 'value', value: 'granted' },
  // This device.
  'alerts.master': { scope: 'device' },
} as const satisfies Partial<Record<SettingTargetId, GrantDescriptor>>

export type GrantId = keyof typeof GRANTS

export const isGrantable = (id: SettingTargetId): id is GrantId => id in GRANTS

/**
 * The underlying switch's key, read off the setting target rather than restated.
 *
 * A project automation target carries `automation:<id>`; everything else carries the
 * config key itself. One derivation, so a renamed control moves both ends at once.
 */
export function grantKey(id: GrantId): string {
  const setting = SETTING_TARGETS[id].setting
  if (!setting) throw new Error(`grant ${id} points at a target with no control`)
  return setting.startsWith('automation:') ? setting.slice('automation:'.length) : setting
}

/** Where a grant's change is written, in the words the operator needs before pressing. */
export function grantWrittenTo(id: GrantId): string {
  const grant: GrantDescriptor = GRANTS[id]
  if (grant.scope === 'install') return 'this machine’s swe-mux configuration'
  if (grant.scope === 'device') return 'this device’s own settings'
  // Worth spelling out every time: a Project opt-in is committed repository content, so
  // it reaches every clone, every worktree, and everyone else who works here.
  return 'this Project’s .swe-mux/config.toml, which travels with the checkout'
}

/** How wide the change reaches, for the scope chip on the button. */
export const GRANT_SCOPE_LABEL: Record<GrantScope, string> = {
  install: 'all projects',
  project: 'this Project',
  device: 'this device',
}

export type AutomationEntry = ProjectAutomationState['automations'][number]

/**
 * Every automation a grant would switch on, itself plus its transitive dependencies.
 *
 * Rendered on the button rather than discovered afterwards. Enabling the change map
 * quietly enables deterministic fact capture and the raw transcript store, and an
 * operator who reads "Code-structure graph" and gets three things has been misled even
 * though nothing went wrong.
 */
export function automationClosure(ids: string[], registry: AutomationEntry[]): string[] {
  const byId = new Map(registry.map(item => [item.id, item]))
  const seen = new Set<string>()
  const walk = (id: string) => {
    if (seen.has(id)) return
    seen.add(id)
    for (const dependency of byId.get(id)?.requires || []) walk(dependency)
  }
  for (const id of ids) walk(id)
  return [...seen].sort()
}

/** Whether granting these automations can cost money, closure included. */
export function automationsSpend(ids: string[], registry: AutomationEntry[]): boolean {
  const byId = new Map(registry.map(item => [item.id, item]))
  return automationClosure(ids, registry).some(id => byId.get(id)?.spends === true)
}

/**
 * Install switches that exist to run a model. Turning one on does not spend by itself -
 * a Project still has to permit the work - but a gate that stayed quiet about it would
 * be hiding the one fact worth reading twice. Mirrors `SPENDING_INSTALL_KEYS`.
 */
const SPENDING_INSTALL_KEYS = new Set(['automation_enabled', 'scan_timeline_enabled'])

export type GrantRequest = {
  /** Grant ids to apply together, as one act. */
  ids: GrantId[]
  /** Required when any id is Project-scoped. */
  projectId?: string
  /** Device-scoped grants are a local write the host owns; called before the daemon call. */
  applyDevice?: (id: GrantId) => void | Promise<void>
}

export type GrantResult = {
  applied: { install: string[]; automations: string[]; values: string[] }
  spends: boolean
  project?: ProjectAutomationState
}

export const GRANTS_CHANGED = 'mux:grants-changed'

/**
 * Apply a set of grants as one act.
 *
 * At most one daemon call, whatever the mix of scopes: the endpoint applies the Project
 * write and the install write together and reports what landed. Sequencing them from the
 * browser would mean two revisions, two failure modes, and a half-granted state whenever
 * the second call lost - and a gate exists precisely to spare someone that.
 */
export async function applyGrants(request: GrantRequest): Promise<GrantResult> {
  const install: Record<string, true> = {}
  const automations: string[] = []
  const values: Record<string, unknown> = {}
  let needsProject = false
  for (const id of request.ids) {
    const grant: GrantDescriptor = GRANTS[id]
    if (grant.scope === 'device') {
      await request.applyDevice?.(id)
      continue
    }
    if (grant.scope === 'install') { install[grantKey(id)] = true; continue }
    needsProject = true
    if (grant.kind === 'automation') automations.push(grantKey(id))
    else values[grantKey(id)] = grant.value
  }
  if (needsProject && !request.projectId) {
    throw new Error('Select a Project first — that switch belongs to one Project.')
  }
  if (!Object.keys(install).length && !automations.length && !Object.keys(values).length) {
    return { applied: { install: [], automations: [], values: [] }, spends: false }
  }
  const result = await api<GrantResult>('POST', '/api/grants', {
    install,
    automations,
    values,
    project_id: request.projectId,
  })
  // The daemon broadcasts too, but a gate must clear the moment its button resolves
  // rather than a websocket round trip later: the pane the operator is looking at is the
  // one thing that has to be right immediately.
  if (request.projectId) forgetProjectAutomations(request.projectId)
  window.dispatchEvent(new CustomEvent(GRANTS_CHANGED, {
    detail: { projectId: request.projectId, applied: result.applied },
  }))
  return result
}

/**
 * Whether a set of grants can cost money, resolved against the live registry.
 *
 * Needs the Project's registry payload for the automation half, so it is async and
 * cached by `projectAutomations.ts` like every other read of that answer.
 */
export async function grantsSpend(ids: GrantId[], projectId?: string): Promise<boolean> {
  if (ids.some(id => GRANTS[id].scope === 'install' && SPENDING_INSTALL_KEYS.has(grantKey(id)))) {
    return true
  }
  const automations = ids
    .filter((id): id is GrantId => {
      const grant: GrantDescriptor = GRANTS[id]
      return grant.scope === 'project' && grant.kind === 'automation'
    })
    .map(grantKey)
  if (!automations.length || !projectId) return false
  const state = await fetchProjectAutomations(projectId)
  return automationsSpend(automations, state.automations)
}
