/**
 * The portable per-Project file, `.swe-mux/config.toml`, as an editor sees it.
 *
 * One file, several independent owners: the Projects registry draws three sections
 * over it (defaults and repository options, the automation opt-ins, the agent
 * authority table), and outside the panel a grant gate, the land queue's verify
 * command, the configurator agent and the file browser's "ignore this" all write it
 * too. Each owns a disjoint set of keys and none of them can see the others' edits.
 *
 * So an editor here sends the fields it changed and the values it believed those
 * fields held, never the document. A whole-document write has to be guarded by a
 * whole-file revision, and a whole-file revision reports every one of those writers
 * as a collision with every other - which is the "project config changed externally"
 * the operator used to get from their own second click, with no way forward but
 * closing the panel and opening it again. Field-scoped writes make the common case
 * (disjoint keys) succeed by construction and leave a conflict meaning what it says.
 *
 * The hook that holds one shared copy of this per panel is `projectConfigState.ts`;
 * everything here is pure so the rules can be tested without mounting anything.
 */

// Explicit extensions: the node test runner imports this module directly and
// resolves no extensionless relative TypeScript specifier.
import { normalizeIgnorePatterns, sameDraftValue } from './settingsDraft.ts'
import type { ProjectBackend, PromptLibraryScope } from './types.ts'

/** The `[worktree]` table. Two owners, and only one of them is this panel. */
export type WorktreeValues = {
  setup_command?: string
  /**
   * Owned by the land queue (`PUT /api/land/verify-command`), never edited here, and
   * declared anyway so this panel *preserves* it: the setup-command field used to
   * replace the whole table, so clearing a setup command silently deleted the
   * approved verification command a landing runs.
   */
  verify_command?: string
}

/** The fields the Projects registry's own form draws. */
export type PortableValues = {
  default_shell_profile?: string
  /** Backend name to launch profile id. A selection only; the profile itself is
   *  defined on the device, because argv for an agent CLI is an authority field. */
  default_agent_profiles?: Record<string, string>
  preferred_backend?: ProjectBackend
  prompt_library_scope?: PromptLibraryScope
  notification_sounds_enabled?: boolean
  ignore_patterns?: string[]
  worktree?: WorktreeValues
}

/**
 * Everything the file holds, which is more than any one section edits: the
 * automation opt-ins, the four authority grants, the approval posture and the
 * verify command are all in here too. Typed as a superset rather than narrowed to
 * `PortableValues` so a section reading one of those keys does not have to cast,
 * and so nothing is tempted to write the map back whole.
 */
export type ProjectConfigValues = PortableValues & { [key: string]: unknown }

export type ProjectConfig = {
  project: { id: string; label: string; root: string }
  path: string
  status: string
  revision: string
  error?: string
  values: ProjectConfigValues
}

/** Fields to write. `null` (or `undefined`, sent as `null`) removes one. */
export type ProjectConfigChanges = Record<string, unknown>

/**
 * The keys the registry's Defaults and Repository options form owns.
 *
 * Named rather than derived from whatever the draft happens to hold, because
 * "reset to inherited" needs a closed set: it once wrote an empty document, which
 * cleared the automation opt-ins, the whole authority table, the approval rules and
 * the verify command - none of which that form draws, and none of which a button
 * labelled "reset repo options" should be able to reach.
 */
export const PANEL_CONFIG_FIELDS = [
  'preferred_backend',
  'default_shell_profile',
  'default_agent_profiles',
  'prompt_library_scope',
  'notification_sounds_enabled',
  'ignore_patterns',
  'worktree',
] as const

/** Set or clear one field of the `[worktree]` table, leaving its other field alone. */
export function nextWorktreeTable(
  table: WorktreeValues | undefined,
  field: keyof WorktreeValues,
  value: string,
): WorktreeValues | undefined {
  const next: WorktreeValues = { ...table }
  if (value) next[field] = value
  else delete next[field]
  return Object.keys(next).length ? next : undefined
}

/** What `key` is worth once the draft's editing conventions are applied. */
function draftValue(key: string, value: unknown): unknown {
  if (key !== 'ignore_patterns' || !Array.isArray(value)) return value
  // The textarea keeps the operator's blank lines while they type; the file never
  // stores them, so an all-blank draft is "no patterns" rather than a change.
  const patterns = normalizeIgnorePatterns(value as string[])
  return patterns.length ? patterns : undefined
}

/**
 * The fields to write: every draft entry that differs from what the file holds.
 *
 * Only the difference, so a section that changed one dropdown does not restate the
 * six fields it happens to have read - restating them is how a stale copy silently
 * reverts somebody else's edit, and the only thing standing in the way of that used
 * to be a revision check firing on every legitimate write.
 */
export function projectConfigDelta(
  draft: ProjectConfigChanges,
  saved: ProjectConfigValues | undefined,
): ProjectConfigChanges {
  const changes: ProjectConfigChanges = {}
  for (const [key, value] of Object.entries(draft)) {
    const next = draftValue(key, value)
    if (sameDraftValue(next, saved?.[key])) continue
    changes[key] = next === undefined ? null : next
  }
  return changes
}

/** What the caller believed each changed field held, for the daemon to compare against. */
export function projectConfigBase(
  changes: ProjectConfigChanges,
  saved: ProjectConfigValues | undefined,
): ProjectConfigChanges {
  const base: ProjectConfigChanges = {}
  for (const key of Object.keys(changes)) {
    const value = saved?.[key]
    base[key] = value === undefined ? null : value
  }
  return base
}

export type ProjectConfigConflict = { fields: string[]; current?: ProjectConfig }

/**
 * The daemon's field-scoped conflict, or `null` for any other failure.
 *
 * `current` is the file as it stands, sent with the refusal so the panel can resync
 * from the answer it already has rather than firing a second read.
 */
export function revisionConflict(cause: unknown): ProjectConfigConflict | null {
  const detail = (cause as { detail?: Record<string, unknown> } | null)?.detail
  if (!detail || detail.code !== 'revision_conflict') return null
  const fields = Array.isArray(detail.conflicts)
    ? detail.conflicts.filter((field): field is string => typeof field === 'string')
    : []
  const current = detail.current as ProjectConfig | undefined
  return { fields, current: current && typeof current === 'object' ? current : undefined }
}

const CONFLICT_LABELS: Record<string, string> = {
  automations: 'the automation opt-ins',
  scan_timeline_auto_enable: 'the scan timeline arming rule',
  session_control_grant: 'the session-control authority',
  spawn_grant: 'the spawn authority',
  land_grant: 'the landing authority',
  interject_grant: 'the interject authority',
  approval_allow: "the Project's approval rules",
  approval_ceiling: 'the approval ceiling',
  worktree: 'the worktree commands',
  ignore_patterns: 'the ignore patterns',
  preferred_backend: 'the default backend',
  default_shell_profile: 'the shell profile',
  default_agent_profiles: 'the launch profiles',
  prompt_library_scope: 'the prompt library scope',
  notification_sounds_enabled: 'the notification sound setting',
}

/**
 * What to tell the operator, given the panel has already resynced.
 *
 * Deliberately not "reload before saving", which is the daemon's phrasing for a
 * caller that cannot: this panel just adopted the current file, so the next click
 * works and the sentence has to say which change it would now be overwriting.
 */
export function conflictNotice(fields: string[]): string {
  const named = fields.map(field => CONFLICT_LABELS[field] || field)
  const subject = named.length
    ? named.length === 1
      ? named[0]
      : `${named.slice(0, -1).join(', ')} and ${named[named.length - 1]}`
    : 'this Project’s configuration'
  return `Something else changed ${subject} while this panel was open. `
    + 'The panel now shows the current value — make the change again to overwrite it.'
}
