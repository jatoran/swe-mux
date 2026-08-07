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
// testable under the node type-stripping runner (no browser dependencies here).

import { AGENT_NEWLINE } from './terminalKeys.ts'
import { allBackendNames, isAgentBackend } from './harnessRegistry.ts'

export type RailPlatform = 'desktop' | 'mobile'
export type RailBackend = string

// 'key'   → inject a raw byte sequence (arrow keys, Esc, Ctrl-C, newline…)
// 'action'→ invoke a named built-in handler (copy/paste/relaunch/toggle…)
// 'text'  → inject literal text, optionally submitting with Enter
// 'slash' → inject a provider slash command (/name), optionally submitting
// 'skill' → inject a skill invocation, backend-aware (/name on Claude, $name on Codex)
// 'prompt'→ insert a prompt-library template, resolved from the server by key at
//           click time so the button always injects the template's current text
export type RailItemType = 'key' | 'action' | 'text' | 'slash' | 'skill' | 'prompt'

// Where an item lives. The horizontal strip under a terminal is scarce — that
// scarcity is why several built-ins shipped switched off — so it holds the items
// you hammer, and the utility drawer's Commands tab holds the long tail you
// browse. Nothing is hidden by placement; `enabled: false` still hides outright.
export type RailPlacement = 'strip' | 'drawer'

// A stored placement may also be 'both': the two regions are independent
// surfaces, not a single either/or slot, so an item you hammer under the
// terminal can also carry a full label in the drawer.
export type RailPlacementSetting = RailPlacement | 'both'

export interface RailItem {
  id: string
  type: RailItemType
  label: string
  title?: string
  /** 'key' items: the raw sequence written to the pty. */
  bytes?: string
  /** 'text' | 'slash' | 'skill' items: the payload (command/skill name or literal text). */
  text?: string
  /** 'prompt' items: the library template's `scope:id` key. The body is deliberately
   *  *not* copied here — a rail button is a pointer at the template, so editing the
   *  template updates every button that references it. */
  promptKey?: string
  /** Append Enter after a text/slash/skill payload to submit it. */
  submit?: boolean
  /** 'action' items: which built-in handler to run. */
  action?: string
  /** Extra CSS class (e.g. 'term-key' styling, 'kbd-toggle'). */
  className?: string
  /** Restrict to these backends; undefined = all. */
  backends?: RailBackend[]
  /** Restrict a built-in to registered agent harnesses without freezing their names. */
  agentOnly?: boolean
  /** Restrict to these platforms; undefined = both. */
  platforms?: RailPlatform[]
  /** User toggle; false hides the item everywhere (strip and drawer alike). */
  enabled?: boolean
  /** 'strip' (default) renders under the terminal, 'drawer' in the Commands tab,
   *  'both' in each. */
  placement?: RailPlacementSetting
}

export const allRailBackends = (): readonly RailBackend[] => allBackendNames()
export const ALL_PLATFORMS: readonly RailPlatform[] = ['desktop', 'mobile']

/** Encode line breaks as the composer-newline key understood by both agent TUIs.
 *  Raw LF/CR is submission input in these composers, so multiline editing helpers
 *  must travel through the key path rather than the terminal paste path. */
function agentComposerSequence(text: string): string {
  return text.replace(/\r\n?/g, '\n').replace(/\n/g, AGENT_NEWLINE)
}

// Built-in rail items in default order (the region after the leading voice chips).
// The Task/Project-Action Relaunch button and the agent-only Copy reply / Copy resume
// are mutually exclusive at render time (see TerminalPane). Editing helpers follow
// Up/Down as one cluster, while Attach ends agent rails so it never interrupts keys.
export const BUILTIN_RAIL: RailItem[] = [
  { id: 'relaunch', type: 'action', action: 'relaunch', label: 'Relaunch' },
  { id: 'copyReply', type: 'action', action: 'copyReply', label: 'Copy reply' },
  { id: 'copyResume', type: 'action', action: 'copyResume', label: 'Copy resume' },
  { id: 'branch', type: 'action', action: 'branch', label: 'Branch', agentOnly: true },
  { id: 'paste', type: 'action', action: 'paste', label: 'Paste' },
  // Clipboard history picker. Paired with Paste because it is the paste path on
  // touch, where reading the system clipboard is unreliable or refused outright.
  { id: 'clipboardHistory', type: 'action', action: 'clipboardHistory', label: 'Clip' },
  { id: 'kbdToggle', type: 'action', action: 'toggleKeyboard', label: '⌨', className: 'term-key kbd-toggle' },
  { id: 'esc', type: 'key', bytes: '\x1b', label: 'Esc', className: 'term-key', title: 'Escape' },
  { id: 'enter', type: 'key', bytes: '\r', label: '⏎', className: 'term-key', title: 'Enter' },
  { id: 'tab', type: 'key', bytes: '\t', label: 'Tab', className: 'term-key', title: 'Tab' },
  { id: 'ctrlC', type: 'key', bytes: '\x03', label: '^C', className: 'term-key', title: 'Interrupt (Ctrl-C)' },
  { id: 'up', type: 'key', bytes: '\x1b[A', label: '↑', className: 'term-key', title: 'Up / previous command' },
  { id: 'down', type: 'key', bytes: '\x1b[B', label: '↓', className: 'term-key', title: 'Down / next command' },
  { id: 'markdownDivider', type: 'key', bytes: agentComposerSequence('\n\n---\n\n'), label: '---', agentOnly: true, title: 'Insert a Markdown divider with blank lines around it' },
  { id: 'markdownCodeFence', type: 'key', bytes: agentComposerSequence('\n\n```\n'), label: '```', agentOnly: true, title: 'Start a Markdown code fence after two newlines' },
  { id: 'clearInput', type: 'key', bytes: '\x15', label: '^U', className: 'term-key', title: 'Clear the current input (Ctrl+U)' },
  { id: 'restoreInput', type: 'key', bytes: '\x19', label: '^Y', className: 'term-key', title: 'Restore or yank input (Ctrl+Y)' },
  { id: 'left', type: 'key', bytes: '\x1b[D', label: '←', className: 'term-key', title: 'Left' },
  { id: 'right', type: 'key', bytes: '\x1b[C', label: '→', className: 'term-key', title: 'Right' },
  // Navigation + editing extras. These used to ship switched *off*, because the
  // strip has no room for them; they now ship on, in the drawer, where room is
  // not the constraint.
  { id: 'home', type: 'key', bytes: '\x1b[H', label: 'Home', className: 'term-key', title: 'Home / start of line', placement: 'drawer' },
  { id: 'end', type: 'key', bytes: '\x1b[F', label: 'End', className: 'term-key', title: 'End / end of line', placement: 'drawer' },
  { id: 'ctrlHome', type: 'key', bytes: '\x1b[1;5H', label: '^Home', className: 'term-key', title: 'Ctrl+Home / top', placement: 'drawer' },
  { id: 'ctrlEnd', type: 'key', bytes: '\x1b[1;5F', label: '^End', className: 'term-key', title: 'Ctrl+End / bottom', placement: 'drawer' },
  // ESC+CR is the one newline sequence both agent composers accept. Raw LF works
  // in Claude but Codex treats it as ordinary input instead of editor.newline.
  { id: 'newline', type: 'key', bytes: AGENT_NEWLINE, label: '↵ nl', className: 'term-key', title: 'Insert newline without submitting', placement: 'drawer' },
  // Opens Claude's interactive /rewind picker (there is no one-shot,
  // conversation-only variant, so this just launches the picker).
  { id: 'rewind', type: 'slash', text: 'rewind', label: 'Rewind…', submit: true, backends: ['claude'], title: 'Open Claude /rewind (interactive checkpoint picker)', placement: 'drawer' },
  // Ends the session the rail belongs to. Destructive, so it defaults to the
  // drawer rather than the strip — a kill button one mis-tap away from the arrow
  // keys is the wrong default even with the two-click confirm behind it.
  { id: 'endSession', type: 'action', action: 'endSession', label: 'End session', className: 'rail-danger', title: 'End this session (click twice to confirm)', placement: 'drawer' },
  { id: 'attach', type: 'action', action: 'attach', label: 'Attach', agentOnly: true, title: 'Attach files to this chat without sending' },
]

const EDITING_CLUSTER_IDS = ['markdownDivider', 'markdownCodeFence', 'clearInput', 'restoreInput'] as const
const NEW_EDITING_CLUSTER_IDS = ['markdownDivider', 'markdownCodeFence', 'restoreInput'] as const

/** Custom items (skills, slash commands, literal text) default to the drawer:
 *  they are unbounded in number and would otherwise crowd the arrows off the strip. */
export const DEFAULT_CUSTOM_PLACEMENT: RailPlacement = 'drawer'

/** Resolve a saved entry's enabled/placement pair, migrating pre-placement saves.
 *
 *  Before the drawer existed, "off" was the only way to get an item out of a full
 *  strip, so a save that predates `placement` and says `enabled: false` means
 *  "not on the strip", not "never show me this". Those become drawer items; an
 *  explicit placement is always honoured. */
export function adoptPlacement(
  entry: Pick<RailItem, 'enabled' | 'placement'>,
  fallback: RailPlacementSetting = 'strip',
): { enabled: boolean | undefined; placement: RailPlacementSetting } {
  if (entry.placement === 'strip' || entry.placement === 'drawer' || entry.placement === 'both') {
    return { enabled: entry.enabled, placement: entry.placement }
  }
  if (entry.enabled === false) return { enabled: undefined, placement: 'drawer' }
  return { enabled: entry.enabled, placement: fallback }
}

/** True for built-in item ids whose behaviour (type/bytes/action/label) is
 *  owned by the app; users may reorder them and edit only enabled/filters. */
export function isBuiltinRailId(id: string): boolean {
  return BUILTIN_RAIL.some(item => item.id === id)
}

/** Merge a saved customization over the built-in catalog. Built-in items keep
 *  their authoritative behaviour fields (so defaults can evolve) while adopting
 *  the user's enabled/platforms/backends and order; custom items are kept
 *  verbatim; catalog migrations place newly introduced built-ins once. */
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
      out.push({ ...builtin, ...adoptPlacement(entry), platforms: entry.platforms, backends: entry.backends })
    } else if (entry.type === 'key' || entry.type === 'text' || entry.type === 'slash' || entry.type === 'skill' || entry.type === 'prompt') {
      // Custom items may only be safe injection types, never 'action'.
      out.push({ ...entry, ...adoptPlacement(entry, DEFAULT_CUSTOM_PLACEMENT) })
    }
  }
  for (const builtin of BUILTIN_RAIL) {
    if (!seen.has(builtin.id)) out.push({ ...builtin })
  }
  // Absence of any new editing helper identifies a layout from before this
  // catalog revision. Pull the complete cluster after Down, surface the existing
  // Ctrl+U helper on the strip, and move Attach to the end. Once Settings saves
  // the merged catalog all ids are present, so later user ordering is untouched.
  if (NEW_EDITING_CLUSTER_IDS.some(id => !seen.has(id))) {
    const cluster: RailItem[] = []
    for (const id of EDITING_CLUSTER_IDS) {
      const index = out.findIndex(item => item.id === id)
      if (index < 0) continue
      const [item] = out.splice(index, 1)
      cluster.push({ ...item, placement: item.id === 'clearInput' && item.enabled !== false ? 'strip' : item.placement })
    }
    const downIndex = out.findIndex(item => item.id === 'down')
    out.splice(downIndex < 0 ? out.length : downIndex + 1, 0, ...cluster)
    const attachIndex = out.findIndex(item => item.id === 'attach')
    if (attachIndex >= 0) out.push(...out.splice(attachIndex, 1))
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
  if (item.agentOnly && !isAgentBackend(ctx.backend)) return false
  if (item.backends && !item.backends.includes(ctx.backend)) return false
  return true
}

export function railItemPlacement(item: RailItem): RailPlacementSetting {
  return item.placement === 'drawer' || item.placement === 'both' ? item.placement : 'strip'
}

/** Does this item render in the given region? 'both' renders in each. */
export function railItemInPlacement(item: RailItem, placement: RailPlacement): boolean {
  const setting = railItemPlacement(item)
  return setting === 'both' || setting === placement
}

/** The ordered items to render for a given platform/backend and host.
 *  `placement` defaults to the strip so existing callers keep their meaning. */
export function resolveRail(
  items: RailItem[],
  ctx: RailContext,
  placement: RailPlacement = 'strip',
): RailItem[] {
  return items.filter(item => railItemVisible(item, ctx) && railItemInPlacement(item, placement))
}

/** Backend-aware injected payload for text/slash/skill items. Claude invokes
 *  skills as `/name`; Codex invokes them as `$name`. Slash commands are literal
 *  `/name` on both; a `text` item is passed through verbatim. A 'prompt' item has
 *  no local payload — its text lives in the library and is fetched on click
 *  (`promptRail.ts`), so this returns '' for it. */
export function railPayload(item: RailItem, backend: RailBackend): string {
  if (item.type === 'text') return item.text || ''
  const bare = (item.text || '').trim().replace(/^[/$]/, '')
  if (!bare) return ''
  if (item.type === 'slash') return `/${bare}`
  if (item.type === 'skill') return backend === 'codex' ? `$${bare}` : `/${bare}`
  return ''
}
