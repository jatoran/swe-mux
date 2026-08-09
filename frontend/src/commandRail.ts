// Data model for the configurable terminal command rail.
//
// Two halves, deliberately separate:
//
//  * the **catalog** (`RailConfig.items`) says what a command *is* — its label,
//    what it injects, and which backends it means anything for. Identity and
//    behaviour, nothing about position.
//  * the **layouts** (`RailConfig.layouts`) say where commands *appear*. One
//    layout per device class, each holding rows for two surfaces: the `strip`
//    under the terminal and the `panel` (the utility drawer's Commands tab).
//
// Desktop and mobile therefore have genuinely independent arrangements: their
// own rows, their own order, their own membership. A command in no row on a
// device simply does not appear there, which is why there is no separate
// enabled flag and no per-item platform tag — those were the old model's way of
// saying "not here", and both collapse into row membership.
//
// The same item id may appear in several rows, and more than once within a row;
// a rendered entry therefore carries a `key` of its own rather than reusing the
// item id.
//
// Rendering lives in TerminalPane (strip) and CommandsTab (panel), which own the
// terminal handles and clipboard handlers. This module owns only the pure data
// model, the built-in defaults, the resolve helpers, and the one-way migration
// from the pre-layout format, so it all stays unit testable under the node
// type-stripping runner (no browser dependencies here).

import { AGENT_NEWLINE } from './terminalKeys.ts'
import { allBackendNames, isAgentBackend } from './harnessRegistry.ts'

/** Device classes with independent layouts. Matches `deviceSettings.currentProfile()`. */
export type RailDevice = 'desktop' | 'mobile'
/** The two regions a row can belong to: the strip under a terminal, or the
 *  utility drawer's Commands tab. Nothing is hidden by surface — placement in
 *  neither is what hides a command on that device. */
export type RailSurface = 'strip' | 'panel'
export type RailBackend = string

export const RAIL_DEVICES: readonly RailDevice[] = ['desktop', 'mobile']
export const RAIL_SURFACES: readonly RailSurface[] = ['strip', 'panel']

// 'key'   → inject a raw byte sequence (arrow keys, Esc, Ctrl-C, newline…)
// 'action'→ invoke a named built-in handler (copy/paste/relaunch/toggle…)
// 'text'  → inject literal text, optionally submitting with Enter
// 'slash' → inject a provider slash command (/name), optionally submitting
// 'skill' → inject a skill invocation, backend-aware (/name on Claude, $name on Codex)
// 'prompt'→ insert a prompt-library template, resolved from the server by key at
//           click time so the button always injects the template's current text
export type RailItemType = 'key' | 'action' | 'text' | 'slash' | 'skill' | 'prompt'

/** Injection types a user may author. 'action' is app-owned and never custom. */
export const CUSTOM_RAIL_TYPES: readonly RailItemType[] = ['key', 'text', 'slash', 'skill', 'prompt']

/** A catalog entry: what the command is, never where it sits. */
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
  /** Deterministic aliases that may expose this item through Talk. Omission is
   *  deliberate for destructive and UI-only built-ins. Configured skills and
   *  slash commands receive conservative name-derived aliases separately. */
  voicePhrases?: string[]
  /** Extra CSS class (e.g. 'term-key' styling, 'kbd-toggle'). */
  className?: string
  /** Restrict to these backends; undefined = all. Unlike position, this is a
   *  property of the command itself: `/rewind` means nothing outside Claude. */
  backends?: RailBackend[]
  /** Restrict a built-in to registered agent harnesses without freezing their names. */
  agentOnly?: boolean
  /** Which surface this item is seeded into when a layout is first built, and
   *  where a newly shipped built-in lands in an existing layout. Never consulted
   *  once the item is placed — the layouts are authoritative from then on. */
  defaultSurface?: RailSurface
}

/** One row of a surface. Rows are ordered, and so are the items inside them. */
export interface RailRow {
  id: string
  /** Optional caption. Rendered as a group heading in the panel; the strip has
   *  no room for one, so it is editor-only there. */
  label?: string
  /** Ordered catalog item ids. Duplicates are legal, here and across rows. */
  items: string[]
}

export interface RailDeviceLayout {
  strip: RailRow[]
  panel: RailRow[]
}

export type RailLayouts = Record<RailDevice, RailDeviceLayout>

export interface RailConfig {
  items: RailItem[]
  layouts: RailLayouts
}

export const allRailBackends = (): readonly RailBackend[] => allBackendNames()

/** Custom items (skills, slash commands, literal text) are seeded into the panel:
 *  they are unbounded in number and would otherwise crowd the arrows off the strip. */
export const DEFAULT_CUSTOM_SURFACE: RailSurface = 'panel'

/** Encode line breaks as the composer-newline key understood by both agent TUIs.
 *  Raw LF/CR is submission input in these composers, so multiline editing helpers
 *  must travel through the key path rather than the terminal paste path. */
function agentComposerSequence(text: string): string {
  return text.replace(/\r\n?/g, '\n').replace(/\n/g, AGENT_NEWLINE)
}

// Built-in rail items, in the order the default layout seeds them. The
// Task/Project-Action Relaunch button and the agent-only Copy reply / Copy resume
// are mutually exclusive at render time (see TerminalPane). Editing helpers follow
// Up/Down as one cluster, while Attach ends the default strip row so it never
// interrupts the keys.
export const BUILTIN_RAIL: RailItem[] = [
  { id: 'relaunch', type: 'action', action: 'relaunch', label: 'Relaunch' },
  { id: 'copyReply', type: 'action', action: 'copyReply', label: 'Copy reply' },
  { id: 'copyResume', type: 'action', action: 'copyResume', label: 'Copy resume' },
  { id: 'branch', type: 'action', action: 'branch', label: 'Branch', agentOnly: true },
  { id: 'paste', type: 'action', action: 'paste', label: 'Paste', voicePhrases: ['paste', 'paste clipboard'] },
  // Clipboard history picker. Paired with Paste because it is the paste path on
  // touch, where reading the system clipboard is unreliable or refused outright.
  { id: 'clipboardHistory', type: 'action', action: 'clipboardHistory', label: 'Clip' },
  { id: 'kbdToggle', type: 'action', action: 'toggleKeyboard', label: '⌨', className: 'term-key kbd-toggle' },
  { id: 'esc', type: 'key', bytes: '\x1b', label: 'Esc', className: 'term-key', title: 'Escape', voicePhrases: ['escape', 'press escape', 'escape key'] },
  { id: 'enter', type: 'key', bytes: '\r', label: '⏎', className: 'term-key', title: 'Enter', voicePhrases: ['enter', 'press enter', 'enter key'] },
  { id: 'tab', type: 'key', bytes: '\t', label: 'Tab', className: 'term-key', title: 'Tab', voicePhrases: ['tab', 'press tab', 'tab key'] },
  { id: 'ctrlC', type: 'key', bytes: '\x03', label: '^C', className: 'term-key', title: 'Interrupt (Ctrl-C)', voicePhrases: ['control c', 'press control c'] },
  { id: 'up', type: 'key', bytes: '\x1b[A', label: '↑', className: 'term-key', title: 'Up / previous command', voicePhrases: ['up arrow', 'press up', 'previous terminal command'] },
  { id: 'down', type: 'key', bytes: '\x1b[B', label: '↓', className: 'term-key', title: 'Down / next command', voicePhrases: ['down arrow', 'press down', 'next terminal command'] },
  { id: 'markdownDivider', type: 'key', bytes: agentComposerSequence('\n\n---\n\n'), label: '---', agentOnly: true, title: 'Insert a Markdown divider with blank lines around it', voicePhrases: ['insert markdown divider'] },
  { id: 'markdownCodeFence', type: 'key', bytes: agentComposerSequence('\n\n```\n'), label: '```', agentOnly: true, title: 'Start a Markdown code fence after two newlines', voicePhrases: ['insert code fence', 'start code fence'] },
  { id: 'clearInput', type: 'key', bytes: '\x15', label: '^U', className: 'term-key', title: 'Clear the current input (Ctrl+U)' },
  { id: 'restoreInput', type: 'key', bytes: '\x19', label: '^Y', className: 'term-key', title: 'Restore or yank input (Ctrl+Y)', voicePhrases: ['restore input', 'yank input'] },
  { id: 'left', type: 'key', bytes: '\x1b[D', label: '←', className: 'term-key', title: 'Left', voicePhrases: ['left arrow', 'press left'] },
  { id: 'right', type: 'key', bytes: '\x1b[C', label: '→', className: 'term-key', title: 'Right', voicePhrases: ['right arrow', 'press right'] },
  // Navigation + editing extras. The strip has no room for them, so they seed into
  // the panel, where room is not the constraint.
  { id: 'home', type: 'key', bytes: '\x1b[H', label: 'Home', className: 'term-key', title: 'Home / start of line', defaultSurface: 'panel', voicePhrases: ['home key', 'press home'] },
  { id: 'end', type: 'key', bytes: '\x1b[F', label: 'End', className: 'term-key', title: 'End / end of line', defaultSurface: 'panel', voicePhrases: ['end key', 'press end'] },
  { id: 'ctrlHome', type: 'key', bytes: '\x1b[1;5H', label: '^Home', className: 'term-key', title: 'Ctrl+Home / top', defaultSurface: 'panel', voicePhrases: ['control home', 'press control home'] },
  { id: 'ctrlEnd', type: 'key', bytes: '\x1b[1;5F', label: '^End', className: 'term-key', title: 'Ctrl+End / bottom', defaultSurface: 'panel', voicePhrases: ['control end', 'press control end'] },
  // ESC+CR is the one newline sequence both agent composers accept. Raw LF works
  // in Claude but Codex treats it as ordinary input instead of editor.newline.
  { id: 'newline', type: 'key', bytes: AGENT_NEWLINE, label: '↵ nl', className: 'term-key', title: 'Insert newline without submitting', defaultSurface: 'panel', voicePhrases: ['new line', 'insert new line'] },
  // Opens Claude's interactive /rewind picker (there is no one-shot,
  // conversation-only variant, so this just launches the picker).
  { id: 'rewind', type: 'slash', text: 'rewind', label: 'Rewind…', submit: true, backends: ['claude'], title: 'Open Claude /rewind (interactive checkpoint picker)', defaultSurface: 'panel' },
  // Ends the session the rail belongs to. Destructive, so it seeds into the panel
  // rather than the strip — a kill button one mis-tap away from the arrow keys is
  // the wrong default even with the two-click confirm behind it.
  { id: 'endSession', type: 'action', action: 'endSession', label: 'End session', className: 'rail-danger', title: 'End this session (click twice to confirm)', defaultSurface: 'panel' },
  { id: 'attach', type: 'action', action: 'attach', label: 'Attach', agentOnly: true, title: 'Attach files to this chat without sending' },
]

const BUILTIN_BY_ID = new Map(BUILTIN_RAIL.map(item => [item.id, item]))

/** True for built-in item ids whose behaviour (type/bytes/action/label) is
 *  owned by the app; users may place and reorder them but not edit them. */
export function isBuiltinRailId(id: string): boolean {
  return BUILTIN_BY_ID.has(id)
}

export function itemDefaultSurface(item: RailItem): RailSurface {
  return item.defaultSurface === 'panel' ? 'panel' : 'strip'
}

// Row ids only have to be unique within a config and stable across a save; they
// are never shown. Default rows get fixed ids so an untouched layout round-trips
// byte-identically instead of churning a new id on every load.
let rowCounter = 0
export function newRailRowId(): string {
  rowCounter += 1
  return `row-${rowCounter.toString(36)}-${Math.floor(Math.random() * 0xffffff).toString(36)}`
}
export const defaultRowId = (device: RailDevice, surface: RailSurface): string => `${device}-${surface}`

/** The layout every device starts with: one row per surface, seeded from each
 *  built-in's `defaultSurface`. Desktop and mobile start identical and diverge
 *  from there — they are separate rows from the first save, never a shared one. */
export function defaultRailLayouts(items: readonly RailItem[] = BUILTIN_RAIL): RailLayouts {
  const seed = (device: RailDevice, surface: RailSurface): RailRow => ({
    id: defaultRowId(device, surface),
    items: items.filter(item => itemDefaultSurface(item) === surface).map(item => item.id),
  })
  return {
    desktop: { strip: [seed('desktop', 'strip')], panel: [seed('desktop', 'panel')] },
    mobile: { strip: [seed('mobile', 'strip')], panel: [seed('mobile', 'panel')] },
  }
}

export function defaultRailConfig(): RailConfig {
  return { items: BUILTIN_RAIL.map(item => ({ ...item })), layouts: defaultRailLayouts() }
}

// ---------------------------------------------------------------------------
// Normalization
// ---------------------------------------------------------------------------

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)

/** Merge a saved catalog over the built-in one. Built-ins keep their authoritative
 *  behaviour fields (so shipped defaults can evolve) while custom entries are kept
 *  verbatim; unknown-typed and duplicate entries are dropped. */
export function mergeRailCatalog(saved: unknown): { items: RailItem[]; addedBuiltins: RailItem[] } {
  const entries = Array.isArray(saved) ? saved : []
  const seen = new Set<string>()
  const items: RailItem[] = []
  for (const raw of entries) {
    if (!isRecord(raw) || typeof raw.id !== 'string' || seen.has(raw.id)) continue
    const builtin = BUILTIN_BY_ID.get(raw.id)
    if (builtin) { seen.add(raw.id); items.push({ ...builtin }); continue }
    // Custom items may only be safe injection types, never 'action'.
    const entry = raw as unknown as RailItem
    if (!CUSTOM_RAIL_TYPES.includes(entry.type)) continue
    seen.add(entry.id)
    items.push({ ...entry })
  }
  // Built-ins introduced after this config was saved. Returned separately so the
  // caller can also place them, which is the only way a new button reaches a user
  // who already has a saved layout.
  const addedBuiltins = BUILTIN_RAIL.filter(item => !seen.has(item.id)).map(item => ({ ...item }))
  return { items: [...items, ...addedBuiltins], addedBuiltins }
}

function normalizeRow(raw: unknown, known: Set<string>): RailRow | null {
  if (!isRecord(raw) || typeof raw.id !== 'string' || !raw.id) return null
  const items = Array.isArray(raw.items)
    ? raw.items.filter((id): id is string => typeof id === 'string' && known.has(id))
    : []
  const label = typeof raw.label === 'string' && raw.label.trim() ? raw.label.trim() : undefined
  return { id: raw.id, ...(label ? { label } : {}), items }
}

function normalizeSurface(raw: unknown, known: Set<string>, device: RailDevice, surface: RailSurface): RailRow[] {
  const rows: RailRow[] = []
  const seen = new Set<string>()
  if (Array.isArray(raw)) {
    for (const entry of raw) {
      const row = normalizeRow(entry, known)
      if (!row || seen.has(row.id)) continue
      seen.add(row.id)
      rows.push(row)
    }
  }
  // Every surface keeps at least one row so the editor always has a drop target
  // and the renderer always has somewhere for a new built-in to land.
  if (!rows.length) rows.push({ id: defaultRowId(device, surface), items: [] })
  return rows
}

/** Normalize any stored shape into a usable config: a v2 `{items, layouts}`
 *  object, a pre-layout item array, or nothing at all. */
export function normalizeRailConfig(saved: unknown): RailConfig {
  if (Array.isArray(saved)) return migrateLegacyRail(saved)
  if (!isRecord(saved)) return defaultRailConfig()
  if (!isRecord(saved.layouts)) {
    return Array.isArray(saved.items) ? migrateLegacyRail(saved.items) : defaultRailConfig()
  }
  const { items, addedBuiltins } = mergeRailCatalog(saved.items)
  const known = new Set(items.map(item => item.id))
  const rawLayouts = saved.layouts as Record<string, unknown>
  const layouts = {} as RailLayouts
  for (const device of RAIL_DEVICES) {
    const rawDevice = isRecord(rawLayouts[device]) ? rawLayouts[device] as Record<string, unknown> : {}
    layouts[device] = {
      strip: normalizeSurface(rawDevice.strip, known, device, 'strip'),
      panel: normalizeSurface(rawDevice.panel, known, device, 'panel'),
    }
  }
  // A built-in shipped after this save was written is appended to the first row
  // of its default surface on both devices. Doing nothing would leave it
  // permanently invisible to anyone with an existing layout.
  for (const item of addedBuiltins) {
    const surface = itemDefaultSurface(item)
    for (const device of RAIL_DEVICES) layouts[device][surface][0].items.push(item.id)
  }
  return { items, layouts }
}

// ---------------------------------------------------------------------------
// Migration from the pre-layout format
// ---------------------------------------------------------------------------
//
// The old model was one ordered list shared by every device and both surfaces,
// with `platforms`, `placement` and `enabled` on each item deciding where it
// showed. Resolving that list once per device/surface reproduces exactly what
// the user saw, so an upgrade lands on four rows that render identically and
// only then diverge by hand.

type LegacyPlacement = 'strip' | 'drawer' | 'both'

export interface LegacyRailItem extends RailItem {
  platforms?: RailDevice[]
  placement?: LegacyPlacement
  enabled?: boolean
}

const LEGACY_EDITING_CLUSTER = ['markdownDivider', 'markdownCodeFence', 'clearInput', 'restoreInput'] as const
const LEGACY_NEW_EDITING_CLUSTER = ['markdownDivider', 'markdownCodeFence', 'restoreInput'] as const

const legacyBuiltin = (item: RailItem): LegacyRailItem =>
  ({ ...item, placement: itemDefaultSurface(item) === 'panel' ? 'drawer' : 'strip' })

/** Resolve a saved entry's enabled/placement pair, including saves that predate
 *  `placement` entirely. Back then "off" was the only way to get an item out of a
 *  full strip, so `enabled: false` without a placement means "not on the strip",
 *  not "never show me this". */
export function adoptLegacyPlacement(
  entry: Pick<LegacyRailItem, 'enabled' | 'placement'>,
  fallback: LegacyPlacement = 'strip',
): { enabled: boolean | undefined; placement: LegacyPlacement } {
  if (entry.placement === 'strip' || entry.placement === 'drawer' || entry.placement === 'both') {
    return { enabled: entry.enabled, placement: entry.placement }
  }
  if (entry.enabled === false) return { enabled: undefined, placement: 'drawer' }
  return { enabled: entry.enabled, placement: fallback }
}

/** The old catalog merge, kept only to feed the migration. */
export function mergeLegacyRail(saved: LegacyRailItem[] | undefined | null): LegacyRailItem[] {
  if (!saved || !saved.length) return BUILTIN_RAIL.map(legacyBuiltin)
  const seen = new Set<string>()
  const out: LegacyRailItem[] = []
  for (const entry of saved) {
    if (!entry || typeof entry.id !== 'string' || seen.has(entry.id)) continue
    seen.add(entry.id)
    const builtin = BUILTIN_BY_ID.get(entry.id)
    if (builtin) {
      out.push({ ...legacyBuiltin(builtin), ...adoptLegacyPlacement(entry), platforms: entry.platforms, backends: entry.backends })
    } else if (CUSTOM_RAIL_TYPES.includes(entry.type)) {
      out.push({ ...entry, ...adoptLegacyPlacement(entry, 'drawer') })
    }
  }
  for (const builtin of BUILTIN_RAIL) {
    if (!seen.has(builtin.id)) out.push(legacyBuiltin(builtin))
  }
  // Absence of any new editing helper identifies a layout from before that
  // catalog revision: pull the complete cluster after Down and move Attach last.
  if (LEGACY_NEW_EDITING_CLUSTER.some(id => !seen.has(id))) {
    const cluster: LegacyRailItem[] = []
    for (const id of LEGACY_EDITING_CLUSTER) {
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

function legacyShows(item: LegacyRailItem, device: RailDevice, surface: RailSurface): boolean {
  if (item.enabled === false) return false
  if (item.platforms && !item.platforms.includes(device)) return false
  const placement = item.placement === 'drawer' || item.placement === 'both' ? item.placement : 'strip'
  return placement === 'both' || (surface === 'panel' ? placement === 'drawer' : placement === 'strip')
}

/** Turn one pre-layout list into four rows that render exactly as it did. */
export function migrateLegacyRail(saved: LegacyRailItem[] | undefined | null): RailConfig {
  const merged = mergeLegacyRail(saved)
  const items: RailItem[] = merged.map(({ platforms: _p, placement: _c, enabled: _e, ...item }) => ({ ...item }))
  const layouts = {} as RailLayouts
  for (const device of RAIL_DEVICES) {
    const surfaces = {} as RailDeviceLayout
    for (const surface of RAIL_SURFACES) {
      surfaces[surface] = [{
        id: defaultRowId(device, surface),
        items: merged.filter(item => legacyShows(item, device, surface)).map(item => item.id),
      }]
    }
    layouts[device] = surfaces
  }
  return { items, layouts }
}

// ---------------------------------------------------------------------------
// Resolve
// ---------------------------------------------------------------------------

export interface RailContext {
  device: RailDevice
  backend: RailBackend
}

/** Backend gating only. Where an item appears is the layout's business. */
export function railItemVisible(item: RailItem, backend: RailBackend): boolean {
  if (item.agentOnly && !isAgentBackend(backend)) return false
  if (item.backends && !item.backends.includes(backend)) return false
  return true
}

/** One rendered button. `key` is unique per occurrence because the same item may
 *  legitimately appear in several rows, and twice within one. */
export interface RailEntry {
  key: string
  item: RailItem
  rowId: string
  /** Position in the stored row, before backend filtering. */
  index: number
}

export interface RailRenderRow {
  id: string
  label?: string
  entries: RailEntry[]
}

/** The rows to render for a device/backend on one surface. Rows left empty by
 *  backend filtering are dropped so a shell session shows no blank strip row. */
export function resolveRailRows(config: RailConfig, surface: RailSurface, ctx: RailContext): RailRenderRow[] {
  const byId = new Map(config.items.map(item => [item.id, item]))
  const rows: RailRenderRow[] = []
  for (const row of config.layouts[ctx.device]?.[surface] || []) {
    const entries: RailEntry[] = []
    row.items.forEach((id, index) => {
      const item = byId.get(id)
      if (!item || !railItemVisible(item, ctx.backend)) return
      entries.push({ key: `${row.id}:${index}:${id}`, item, rowId: row.id, index })
    })
    if (entries.length) rows.push({ id: row.id, label: row.label, entries })
  }
  return rows
}

/** Every rendered item on a surface, flattened. For callers that do not care
 *  about rows (emptiness checks, "is anything configured here"). */
export function resolveRailItems(config: RailConfig, surface: RailSurface, ctx: RailContext): RailEntry[] {
  return resolveRailRows(config, surface, ctx).flatMap(row => row.entries)
}

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------
//
// One blob holds the catalog, both device layouts, and any per-project
// overrides. Keeping it in a single settings domain (rather than splitting the
// layouts across the store's desktop/mobile profile buckets) is what makes a
// save atomic: the catalog and the layouts that reference it can never be
// written apart, so no read can see a layout pointing at an item that is not
// there yet.
//
// Scope shape is detected rather than versioned: an array is the pre-layout
// format, an object with `layouts` is current. That way a global save does not
// have to rewrite every project override to keep the file self-consistent.

export interface RailScopeBlob {
  items?: RailItem[]
  layouts?: RailLayouts
}

export interface RailBlob extends RailScopeBlob {
  version?: number
  projects?: Record<string, RailScopeBlob | LegacyRailItem[]>
}

export const RAIL_BLOB_VERSION = 2

const scopeConfig = (scope: unknown): RailConfig => normalizeRailConfig(scope)

/** Resolve the effective config for a project, falling back to the global one. */
export function railConfigFromBlob(blob: RailBlob | undefined, projectId?: string): RailConfig {
  const override = projectId ? blob?.projects?.[projectId] : undefined
  if (override !== undefined) return scopeConfig(override)
  if (!blob) return defaultRailConfig()
  return scopeConfig({ items: blob.items, layouts: blob.layouts })
}

/** Return a new blob with `config` written to the global scope or a project override. */
export function writeRailConfigBlob(blob: RailBlob | undefined, config: RailConfig, projectId?: string): RailBlob {
  const scope: RailScopeBlob = { items: config.items, layouts: config.layouts }
  const base: RailBlob = { ...(blob || {}), version: RAIL_BLOB_VERSION }
  if (projectId) base.projects = { ...(base.projects || {}), [projectId]: scope }
  else Object.assign(base, scope)
  return base
}

/** Return a new blob with a project's override removed (reverting it to global). */
export function clearProjectRailBlob(blob: RailBlob | undefined, projectId: string): RailBlob {
  const projects = { ...(blob?.projects || {}) }
  delete projects[projectId]
  return { ...(blob || {}), version: RAIL_BLOB_VERSION, projects }
}

export function railHasProjectOverride(blob: RailBlob | undefined, projectId: string): boolean {
  return blob?.projects?.[projectId] !== undefined
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
