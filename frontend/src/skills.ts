// Discovered agent skills: the shapes the daemon returns and the pure helpers the
// Actions tab renders them with.
//
// These are the CLI's *own* skills — the `SKILL.md` directories Claude and Codex
// read at startup — not swe-mux prompt templates or rail items. They are
// discovered rather than configured, so nothing here writes: the drawer lists
// what is on disk and injects an invocation, and the CLI owns the rest.
//
// Two facts have to survive all the way to the button, because they are the
// difference between a useful list and a misleading one:
//   • `added_after_start` — the file is newer than the running CLI generation, so
//     the CLI has not loaded it and typing the invocation will not work yet.
//   • `implicit: false` — Codex's `allow_implicit_invocation: false`. The skill is
//     real and invocable by name, the model just never reaches for it on its own.

export type SkillScope = 'project' | 'user' | 'plugin' | 'system'

export interface AgentSkill {
  name: string
  description: string
  path: string
  scope: SkillScope
  /** Human label for the root it came from ('user skills', 'plugin: dev-browser'). */
  origin: string
  kind: 'skill' | 'command'
  /** Ready to type: `/name` on Claude, `$name` on Codex. */
  invocation: string
  mtime: number
  implicit: boolean
  display_name: string | null
  short_description: string | null
  /** Set on the loser of a name collision, naming the root that wins. */
  shadowed_by: string | null
  added_after_start: boolean
}

export interface SkillRoot {
  path: string
  scope: SkillScope
  kind: 'skill' | 'command'
  origin: string
  exists: boolean
  count: number
}

export interface SkillInventory {
  backend: string
  cwd: string
  generated_at: number
  agent_loaded_at: number
  agent_run_started_at: number
  roots: SkillRoot[]
  skills: AgentSkill[]
  errors: Array<{ path: string; message: string }>
  truncated: boolean
  skipped_plugins: string[]
  /** Claude only: its built-ins live in the binary and cannot be enumerated. */
  builtin_skills_hidden: boolean
}

const SCOPE_ORDER: SkillScope[] = ['project', 'user', 'plugin', 'system']

const SCOPE_LABELS: Record<SkillScope, string> = {
  project: 'This project',
  user: 'Global',
  plugin: 'Plugins',
  system: 'Bundled',
}

export function scopeLabel(scope: SkillScope): string {
  return SCOPE_LABELS[scope] || scope
}

/** Substring match over name, description, and origin; blank query keeps order.
 *
 *  Deliberately not fuzzy like the command palette's matcher: a skill list is
 *  browsed by reading, and subsequence matching on ~40 long descriptions returns
 *  a hit for nearly any query, which is worse than returning nothing. */
export function filterSkills(skills: AgentSkill[], query: string): AgentSkill[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return skills
  return skills.filter(skill =>
    `${skill.name} ${skill.display_name || ''} ${skill.description} ${skill.origin}`
      .toLowerCase()
      .includes(needle),
  )
}

export interface SkillGroup {
  scope: SkillScope
  label: string
  skills: AgentSkill[]
}

/** Group by scope in precedence order, dropping empty groups. */
export function groupSkills(skills: AgentSkill[]): SkillGroup[] {
  return SCOPE_ORDER.map(scope => ({
    scope,
    label: scopeLabel(scope),
    skills: skills.filter(skill => skill.scope === scope),
  })).filter(group => group.skills.length > 0)
}

/** What the button says. A Codex sidecar display name wins over the raw name. */
export function skillLabel(skill: AgentSkill): string {
  return skill.display_name || skill.name
}

/** Tooltip: the caveats first, because they change what the click does. */
export function skillTitle(skill: AgentSkill): string {
  const parts: string[] = []
  if (skill.added_after_start) parts.push('Added after this agent loaded - restart the agent to load it.')
  if (!skill.implicit) parts.push('Explicit-only: the agent never invokes this on its own.')
  if (skill.shadowed_by) parts.push(`Shadowed by ${skill.shadowed_by}.`)
  parts.push(`${skill.invocation} · ${skill.origin}`)
  if (skill.description) parts.push(skill.description)
  return parts.join('\n')
}

/** The one-line note under the list, or '' when there is nothing to disclose.
 *
 *  Claude's built-ins are invisible to a filesystem scan, so a bare list would
 *  quietly imply `/review` and `/dataviz` do not exist. Say it once, in place. */
export function inventoryNote(inventory: SkillInventory | null): string {
  if (!inventory) return ''
  const notes: string[] = []
  if (inventory.builtin_skills_hidden) {
    notes.push("Claude's built-in skills are compiled into the CLI and are not listed here.")
  }
  if (inventory.skipped_plugins.length) {
    notes.push(`${inventory.skipped_plugins.length} disabled plugin(s) not scanned.`)
  }
  if (inventory.truncated) notes.push('List truncated.')
  if (inventory.errors.length) notes.push(`${inventory.errors.length} unreadable entr(ies).`)
  return notes.join(' ')
}
