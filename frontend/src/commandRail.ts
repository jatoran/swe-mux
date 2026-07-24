// Data model for the configurable terminal command rail.
//
// The rail is the horizontal strip of buttons under an agent/shell terminal. It
// was originally hardcoded JSX; this module turns the region *after* the leading
// voice chips into an ordered, filterable list so it can be reordered, toggled,
// and extended (skills, slash commands, custom keys) from settings — globally,
// per device-class, per backend, and (later) per project.
//
// Rendering of each item still lives in TerminalPane, which owns the terminal
// handles and clipboard handlers; this module owns only the pure data model,
// the built-in defaults, and the resolve/injection helpers so they stay unit
// testable under the node type-stripping runner (no runtime imports here).

export type RailPlatform = 'desktop' | 'mobile'
export type RailBackend = 'claude' | 'codex' | 'shell'

// 'key'   → inject a raw byte sequence (arrow keys, Esc, Ctrl-C, newline…)
// 'action'→ invoke a named built-in handler (copy/paste/relaunch/toggle…)
// 'text'  → inject literal text, optionally submitting with Enter
// 'slash' → inject a provider slash command (/name), optionally submitting
// 'skill' → inject a skill invocation, backend-aware (/name on Claude, $name on Codex)
export type RailItemType = 'key' | 'action' | 'text' | 'slash' | 'skill'

export interface RailItem {
  id: string
  type: RailItemType
  label: string
  title?: string
  /** 'key' items: the raw sequence written to the pty. */
  bytes?: string
  /** 'text' | 'slash' | 'skill' items: the payload (command/skill name or literal text). */
  text?: string
  /** Append Enter after a text/slash/skill payload to submit it. */
  submit?: boolean
  /** 'action' items: which built-in handler to run. */
  action?: string
  /** Extra CSS class (e.g. 'term-key' styling, 'kbd-toggle'). */
  className?: string
  /** Restrict to these backends; undefined = all. */
  backends?: RailBackend[]
  /** Restrict to these platforms; undefined = both. */
  platforms?: RailPlatform[]
  /** User toggle; false hides the item everywhere. */
  enabled?: boolean
}

export const ALL_BACKENDS: readonly RailBackend[] = ['claude', 'codex', 'shell']
export const ALL_PLATFORMS: readonly RailPlatform[] = ['desktop', 'mobile']

// Built-in rail items in default order (the region after the leading voice chips).
// This reproduces the previously hardcoded rail exactly: the Task/Project-Action
// Relaunch button and the agent-only Copy reply / Copy resume are mutually
// exclusive at render time (see TerminalPane), Paste is always present, then the
// keyboard toggle (kept CSS-gated to coarse pointers via its class) and keys.
export const BUILTIN_RAIL: RailItem[] = [
  { id: 'relaunch', type: 'action', action: 'relaunch', label: 'Relaunch' },
  { id: 'copyReply', type: 'action', action: 'copyReply', label: 'Copy reply' },
  { id: 'copyResume', type: 'action', action: 'copyResume', label: 'Copy resume' },
  { id: 'branch', type: 'action', action: 'branch', label: 'Branch', backends: ['claude', 'codex'] },
  { id: 'paste', type: 'action', action: 'paste', label: 'Paste' },
  { id: 'kbdToggle', type: 'action', action: 'toggleKeyboard', label: '⌨', className: 'term-key kbd-toggle' },
  { id: 'esc', type: 'key', bytes: '\x1b', label: 'Esc', className: 'term-key', title: 'Escape' },
  { id: 'enter', type: 'key', bytes: '\r', label: '⏎', className: 'term-key', title: 'Enter' },
  { id: 'tab', type: 'key', bytes: '\t', label: 'Tab', className: 'term-key', title: 'Tab' },
  { id: 'ctrlC', type: 'key', bytes: '\x03', label: '^C', className: 'term-key', title: 'Interrupt (Ctrl-C)' },
  { id: 'up', type: 'key', bytes: '\x1b[A', label: '↑', className: 'term-key', title: 'Up / previous command' },
  { id: 'down', type: 'key', bytes: '\x1b[B', label: '↓', className: 'term-key', title: 'Down / next command' },
  { id: 'left', type: 'key', bytes: '\x1b[D', label: '←', className: 'term-key', title: 'Left' },
  { id: 'right', type: 'key', bytes: '\x1b[C', label: '→', className: 'term-key', title: 'Right' },
  // Navigation + editing extras. Seeded disabled so the default rail is
  // unchanged until a user enables them from settings.
  { id: 'home', type: 'key', bytes: '\x1b[H', label: 'Home', className: 'term-key', title: 'Home / start of line', enabled: false },
  { id: 'end', type: 'key', bytes: '\x1b[F', label: 'End', className: 'term-key', title: 'End / end of line', enabled: false },
  { id: 'ctrlHome', type: 'key', bytes: '\x1b[1;5H', label: '^Home', className: 'term-key', title: 'Ctrl+Home / top', enabled: false },
  { id: 'ctrlEnd', type: 'key', bytes: '\x1b[1;5F', label: '^End', className: 'term-key', title: 'Ctrl+End / bottom', enabled: false },
  // Ctrl+J (raw LF) inserts a newline in agent composers without submitting.
  { id: 'newline', type: 'key', bytes: '\n', label: '↵ nl', className: 'term-key', title: 'Insert newline (Ctrl+J) without submitting', enabled: false },
  // Ctrl+U clears the composed input (restorable with Ctrl+Y in Claude).
  { id: 'clearInput', type: 'key', bytes: '\x15', label: 'clear', className: 'term-key', title: 'Clear the current input (Ctrl+U)', enabled: false },
  // Opens Claude's interactive /rewind picker (there is no one-shot,
  // conversation-only variant, so this just launches the picker).
  { id: 'rewind', type: 'slash', text: 'rewind', label: 'Rewind…', submit: true, backends: ['claude'], title: 'Open Claude /rewind (interactive checkpoint picker)', enabled: false },
]

/** True for built-in item ids whose behaviour (type/bytes/action/label) is
 *  owned by the app; users may reorder them and edit only enabled/filters. */
export function isBuiltinRailId(id: string): boolean {
  return BUILTIN_RAIL.some(item => item.id === id)
}

/** Merge a saved customization over the built-in catalog. Built-in items keep
 *  their authoritative behaviour fields (so defaults can evolve) while adopting
 *  the user's enabled/platforms/backends and order; custom items are kept
 *  verbatim; any built-in the save predates is appended at its default. */
export function mergeRail(saved: RailItem[] | undefined | null): RailItem[] {
  if (!saved || !saved.length) return BUILTIN_RAIL.map(item => ({ ...item }))
  const builtinById = new Map(BUILTIN_RAIL.map(item => [item.id, item]))
  const seen = new Set<string>()
  const out: RailItem[] = []
  for (const entry of saved) {
    if (!entry || typeof entry.id !== 'string' || seen.has(entry.id)) continue
    seen.add(entry.id)
    const builtin = builtinById.get(entry.id)
    if (builtin) {
      out.push({ ...builtin, enabled: entry.enabled, platforms: entry.platforms, backends: entry.backends })
    } else if (entry.type === 'key' || entry.type === 'text' || entry.type === 'slash' || entry.type === 'skill') {
      // Custom items may only be safe injection types, never 'action'.
      out.push({ ...entry })
    }
  }
  for (const builtin of BUILTIN_RAIL) {
    if (!seen.has(builtin.id)) out.push({ ...builtin })
  }
  return out
}

// The stored command-rail configuration: a shared global list plus optional
// per-project overrides that fully replace the global list for that project
// (seeded from the resolved global when a project is first customized).
export interface RailBlob {
  items?: RailItem[]
  projects?: Record<string, RailItem[]>
}

/** Resolve the effective rail item list for a project (or the global list when
 *  no project id is given or the project has no override). */
export function railItemsFromBlob(blob: RailBlob | undefined, projectId?: string): RailItem[] {
  const perProject = projectId ? blob?.projects?.[projectId] : undefined
  const source = Array.isArray(perProject) ? perProject : Array.isArray(blob?.items) ? blob!.items : undefined
  return mergeRail(source)
}

/** Return a new blob with `items` written to the global list or a project override. */
export function writeRailBlob(blob: RailBlob | undefined, items: RailItem[], projectId?: string): RailBlob {
  const base: RailBlob = { ...(blob || {}) }
  if (projectId) base.projects = { ...(base.projects || {}), [projectId]: items }
  else base.items = items
  return base
}

/** Return a new blob with a project's override removed (reverting it to global). */
export function clearProjectRailBlob(blob: RailBlob | undefined, projectId: string): RailBlob {
  const projects = { ...(blob?.projects || {}) }
  delete projects[projectId]
  return { ...(blob || {}), projects }
}

export function railHasProjectOverride(blob: RailBlob | undefined, projectId: string): boolean {
  return Array.isArray(blob?.projects?.[projectId])
}

export interface RailContext {
  platform: RailPlatform
  backend: RailBackend
}

export function railItemVisible(item: RailItem, ctx: RailContext): boolean {
  if (item.enabled === false) return false
  if (item.platforms && !item.platforms.includes(ctx.platform)) return false
  if (item.backends && !item.backends.includes(ctx.backend)) return false
  return true
}

/** The ordered items to render for a given platform/backend. */
export function resolveRail(items: RailItem[], ctx: RailContext): RailItem[] {
  return items.filter(item => railItemVisible(item, ctx))
}

/** Backend-aware injected payload for text/slash/skill items. Claude invokes
 *  skills as `/name`; Codex invokes them as `$name`. Slash commands are literal
 *  `/name` on both; a `text` item is passed through verbatim. */
export function railPayload(item: RailItem, backend: RailBackend): string {
  if (item.type === 'text') return item.text || ''
  const bare = (item.text || '').trim().replace(/^[/$]/, '')
  if (!bare) return ''
  if (item.type === 'slash') return `/${bare}`
  if (item.type === 'skill') return backend === 'codex' ? `$${bare}` : `/${bare}`
  return ''
}
