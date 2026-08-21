// Data model for configurable Actions across the terminal rail and utility drawer.
//
// Two halves, deliberately separate:
//
//  * the **catalog** (`RailConfig.items`) says what a command *is* — its label,
//    what it injects, and which backends it means anything for. Identity and
//    behaviour, nothing about position.
//  * the **layouts** (`RailConfig.layouts`) say where commands *appear*. One
//    layout per device class, each holding rows for two surfaces: the `strip`
//    under the terminal and the `panel` (Quick actions in the Actions drawer).
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
// Rendering lives in TerminalPane (strip) and ActionsTab (panel), which own the
// terminal handles and clipboard handlers. This module owns only the pure data
// model, the built-in defaults, the resolve helpers, and the one-way migration
// from the pre-layout format, so it all stays unit testable under the node
// type-stripping runner (no browser dependencies here).

import { AGENT_NEWLINE } from './terminalKeys.ts'
import { allBackendNames, isAgentBackend, skillInvocationPrefix } from './harnessRegistry.ts'

/** Device classes with independent layouts. Matches `deviceSettings.currentProfile()`. */
export type RailDevice = 'desktop' | 'mobile'
/** The two regions a row can belong to: the rail under a terminal, or Quick actions
 *  in the utility drawer. Nothing is hidden by surface - placement in
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
  /** 'prompt' items: this `label` was derived from the template's title rather than
   *  typed by anyone, so the *live* title wins wherever one can be resolved and the
   *  stored copy is only the offline/dangling fallback (`railItemLabel`).
   *
   *  Its absence means the label is the operator's own and is never overridden —
   *  which is also what an item saved before this field existed means, so a name
   *  deliberately set back then survives. Clearing the label in the editor is how
   *  such an item opts in. */
  autoLabel?: boolean
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
  // Answers the approval the pane is showing right now. Deliberately *not* a
  // voice alias: Talk keeps its own two-step challenge, which exists because a
  // spoken caller cannot see the dialog it is confirming, and a one-tap
  // duplicate reachable by voice would route around that guard.
  {
    id: 'approveOnce',
    type: 'action',
    action: 'approveOnce',
    label: 'Approve',
    agentOnly: true,
    title: 'Approve the request this session is showing',
  },
  { id: 'paste', type: 'action', action: 'paste', label: 'Paste', voicePhrases: ['paste', 'paste clipboard'] },
  // Clipboard history picker. Paired with Paste because it is the paste path on
  // touch, where reading the system clipboard is unreliable or refused outright.
  { id: 'clipboardHistory', type: 'action', action: 'clipboardHistory', label: 'Clip' },
  // The session's own skills, as a drop-up. Next to Clip because they are the same
  // gesture — a short list of the recent/relevant, with a link to the full section.
  { id: 'skills', type: 'action', action: 'skills', label: 'Skills', agentOnly: true, title: 'Insert one of this session’s skills' },
  // The prompt library, as a drop-up, beside Clip and Skills because it is the
  // third picker with the same shape. It is the *whole* library rather than the
  // handful of templates somebody remembered to pin: a per-template pin button
  // still exists and still earns a template its own dedicated button, but a
  // library reachable only through pinning means the templates nobody pinned are
  // three taps away in a drawer.
  //
  // Not `agentOnly`: a template is text, and text goes into a shell composer as
  // readily as into an agent's. Templates that mean something to only one harness
  // carry their own `backends` in the library and are filtered by it.
  { id: 'prompts', type: 'action', action: 'prompts', label: 'Prompts', title: 'Insert one of your prompt templates' },
  { id: 'actionsDrawer', type: 'action', action: 'openActions', label: 'Actions', title: 'Open Actions temporarily' },
  { id: 'kbdToggle', type: 'action', action: 'toggleKeyboard', label: '⌨', className: 'term-key kbd-toggle' },
  { id: 'esc', type: 'key', bytes: '\x1b', label: 'Esc', className: 'term-key', title: 'Escape', voicePhrases: ['escape', 'press escape', 'escape key'] },
  { id: 'enter', type: 'key', bytes: '\r', label: '⏎', className: 'term-key', title: 'Enter', voicePhrases: ['enter', 'press enter', 'enter key'] },
  { id: 'tab', type: 'key', bytes: '\t', label: 'Tab', className: 'term-key', title: 'Tab', voicePhrases: ['tab', 'press tab', 'tab key'] },
  // Back-tab (ESC[Z). Both agent TUIs read it as "cycle permission mode" — the
  // "(shift+tab to cycle)" footer — and shells treat it as reverse focus/completion,
  // so it pairs with Tab on the strip rather than hiding in the panel.
  { id: 'shiftTab', type: 'key', bytes: '\x1b[Z', label: '⇧Tab', className: 'term-key', title: 'Shift+Tab (cycle mode / back-tab)', voicePhrases: ['shift tab', 'press shift tab', 'cycle mode'] },
  { id: 'ctrlC', type: 'key', bytes: '\x03', label: '^C', className: 'term-key', title: 'Interrupt (Ctrl-C)', voicePhrases: ['control c', 'press control c'] },
  { id: 'up', type: 'key', bytes: '\x1b[A', label: '↑', className: 'term-key', title: 'Up / previous command', voicePhrases: ['up arrow', 'press up', 'previous terminal command'] },
  { id: 'down', type: 'key', bytes: '\x1b[B', label: '↓', className: 'term-key', title: 'Down / next command', voicePhrases: ['down arrow', 'press down', 'next terminal command'] },
  { id: 'markdownDivider', type: 'key', bytes: agentComposerSequence('\n\n---\n\n'), label: '---', agentOnly: true, title: 'Insert a Markdown divider with blank lines around it', voicePhrases: ['insert markdown divider'] },
  { id: 'markdownCodeFence', type: 'key', bytes: agentComposerSequence('\n\n```\n'), label: '```', agentOnly: true, title: 'Start a Markdown code fence after two newlines', voicePhrases: ['insert code fence', 'start code fence'] },
  // Copy the composer. Reads the draft off the terminal grid, because no harness
  // publishes its composer and the daemon's write log deliberately keeps only a
  // count (`composerText.ts`, `composer_input.py`).
  //
  // It carries no voice phrase, and adding one would be dead config: the voice
  // adapter passes `action` items through only for Paste (`railVoice.ts`). Copy
  // would also be a poor spoken command — its whole result is on a clipboard the
  // speaker cannot see.
  { id: 'copyInput', type: 'action', action: 'copyInput', label: 'Copy input', agentOnly: true, title: 'Copy the text sitting unsent in this composer' },
  // The raw kill-line key, restored in place of the Clear button that briefly
  // occupied this slot (`clearInput`, migrated below).
  //
  // Clear sent the harness's declared whole-composer discard sequence
  // (`composer_clear_keys`), and for Claude that sequence is a double Esc — which
  // interrupts a running turn. A button that can abort work while claiming to tidy
  // a draft is the wrong shape of mistake to leave one tap from the arrow keys, and
  // making it turn-state-aware was declined in favour of removing it: the operator
  // is better served by a key that does exactly and only what its label says.
  //
  // So this is honest about being Ctrl+U and nothing more. It kills to the start of
  // the line, which clears a single-line draft outright and leaves the other lines
  // of a multi-line one standing (measured against Claude Code v2.1.238).
  //
  // No voice phrase, deliberately, and that is the pre-existing rule rather than a
  // new one: composer-clearing is on Talk's excluded list (`design/features/voice.md`)
  // because a spoken caller cannot see the draft they would be destroying, and
  // `restoreInput` below is voiced precisely because it is the recovering half.
  { id: 'ctrlU', type: 'key', bytes: '\x15', label: '^U', className: 'term-key', title: 'Kill to start of line (Ctrl+U) — clears a single-line draft' },
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

/**
 * Where a stored Action id lands now.
 *
 * Every built-in this catalog has retired *and replaced* is one row here, and each
 * must stay forever: a rail layout is device-local, per-Project, and can be
 * arbitrarily old, and rows are normalized against the live catalog — so an id with
 * no entry here is silently dropped from whichever row the operator had dragged it
 * into. Same durability rule as the drawer's `migratedTabTarget` and the daemon's
 * `_COMMAND_MIGRATIONS`.
 *
 * Only a *replacement* belongs here. A built-in that was retired outright
 * (`draftToggle`) has nothing to migrate to, and dropping it is the correct answer.
 *
 * Mapping rather than dropping is also what keeps position: `clearInput` was
 * removed and Ctrl+U put back in its place, so the operator finds a key where the
 * button was instead of a hole plus a stranger appended to the end of row one.
 */
const RETIRED_RAIL_IDS: Readonly<Record<string, string>> = {
  clearInput: 'ctrlU',
}

/** Resolve a stored id through the retirement table. Unknown ids pass through
 *  unchanged; whether they exist is the caller's separate question. */
export function migratedRailItemId(id: string): string {
  return RETIRED_RAIL_IDS[id] ?? id
}

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
    if (!isRecord(raw) || typeof raw.id !== 'string') continue
    // Resolved before the dedupe, so a config holding both the retired id and its
    // replacement collapses to one catalog entry rather than two of the same item.
    const id = migratedRailItemId(raw.id)
    if (seen.has(id)) continue
    const builtin = BUILTIN_BY_ID.get(id)
    if (builtin) { seen.add(id); items.push({ ...builtin }); continue }
    // Custom items may only be safe injection types, never 'action'. They are never
    // migrated: the retirement table covers built-ins, whose behaviour this module owns.
    const entry = raw as unknown as RailItem
    if (!CUSTOM_RAIL_TYPES.includes(entry.type)) continue
    seen.add(id)
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
  // A retired id is resolved to its replacement *here*, where placement is decided,
  // so the replacement inherits the exact slot the retired button occupied.
  const items = Array.isArray(raw.items)
    ? raw.items
      .filter((id): id is string => typeof id === 'string')
      .map(migratedRailItemId)
      .filter(id => known.has(id))
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
    for (const device of RAIL_DEVICES) {
      const row = layouts[device][surface][0].items
      row.push(item.id)
    }
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

// `ctrlU` here is the migrated spelling of the cluster's third member, which these
// saves stored as `clearInput` (`RETIRED_RAIL_IDS`). The cluster is about position,
// so it names ids as they exist *now*; `mergeLegacyRail` migrates before it looks.
const LEGACY_EDITING_CLUSTER = ['markdownDivider', 'markdownCodeFence', 'ctrlU', 'restoreInput'] as const
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
    if (!entry || typeof entry.id !== 'string') continue
    const id = migratedRailItemId(entry.id)
    if (seen.has(id)) continue
    seen.add(id)
    const builtin = BUILTIN_BY_ID.get(id)
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
      cluster.push({ ...item, placement: item.id === 'ctrlU' && item.enabled !== false ? 'strip' : item.placement })
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
// A project override comes in two strengths, detected by shape rather than by a
// version field (an array is the pre-layout format, `mode: 'delta'` is an
// additive overlay, and an object with `layouts` is a detached copy):
//
//  * a **delta** adds project-owned actions and rows *on top of the live global
//    layout*, and overlays the shared rows with anchored **splices** and
//    **hides**. Global edits keep flowing into the project; only the overlay is
//    project state. This is the default a project accumulates, because the usual
//    need is "the shared rail plus this project's skills", not a divorce.
//    The invariant that survives all of it: a shared row's *definition* never
//    contains a project-owned item — only the resolved projection does, so every
//    other project renders that row exactly as it is written.
//  * a **fork** replaces the global config wholesale. It is the escape hatch for
//    a genuinely different layout, and the price is that global edits no longer
//    reach the project. Forks are only ever created deliberately (Detach).

export interface RailScopeBlob {
  items?: RailItem[]
  layouts?: RailLayouts
}

/** A per-device, per-surface bag of delta records. */
export type RailDeltaMap<T> = Partial<Record<RailDevice, Partial<Record<RailSurface, T[]>>>>

/**
 * An anchored insertion of one catalog item into a **shared** row.
 *
 * The shared row's *definition* stays global and keeps flowing through; the item
 * is placed when the project's config is resolved, so a later global reorder,
 * insert or removal re-anchors the splice rather than breaking it. That is the
 * whole reason the anchor is an item id and not an index: an index would quietly
 * mean somewhere else the moment global gained a button.
 *
 * `after` names the item this one follows in the row *as built so far*, so two
 * splices may chain. `null` is the head of the row; an absent anchor, or one no
 * longer in the row, falls back to its end.
 */
export interface RailSplice {
  /** The shared row's id. Device and surface come from where this is stored. */
  row: string
  /** Catalog item to place: a project-owned action, or a global one the same
   *  project hides from this row (which is how a project-local reorder is said). */
  item: string
  after?: string | null
}

/** The subtractive mirror of a splice: this project does not render `item` in
 *  shared row `row`. Every occurrence goes, the way placement toggles already
 *  treat duplicates, and every other project renders the row unchanged. */
export interface RailHide {
  row: string
  item: string
}

/** Additive project overlay: project-owned catalog items, project-owned rows
 *  appended after the global rows of each device/surface, and the two shared-row
 *  overlays (splices and hides) that let a project place and unplace things in a
 *  shared row without forking it. */
export interface RailProjectDelta {
  mode: 'delta'
  items?: RailItem[]
  layouts?: RailDeltaMap<RailRow>
  splices?: RailDeltaMap<RailSplice>
  hides?: RailDeltaMap<RailHide>
}

export type RailProjectScope = RailScopeBlob | LegacyRailItem[] | RailProjectDelta

export interface RailBlob extends RailScopeBlob {
  version?: number
  projects?: Record<string, RailProjectScope>
}

export const RAIL_BLOB_VERSION = 2

export function isProjectRailDelta(scope: unknown): scope is RailProjectDelta {
  return isRecord(scope) && scope.mode === 'delta'
}

/** How a project relates to the global config. `global` means it inherits with
 *  no additions; `delta` means additions overlay the live global layout; `fork`
 *  means a detached copy that no longer tracks global edits. */
export type RailScopeKind = 'global' | 'delta' | 'fork'

export function railProjectScopeKind(blob: RailBlob | undefined, projectId?: string): RailScopeKind {
  const override = projectId ? blob?.projects?.[projectId] : undefined
  if (override === undefined) return 'global'
  return isProjectRailDelta(override) ? 'delta' : 'fork'
}

/** Sanitize a delta's catalog additions: custom injection types only, ids that
 *  do not collide with the base catalog (the base wins, so a stale delta cannot
 *  shadow a built-in or a global custom action). */
function deltaItems(delta: RailProjectDelta, taken: Set<string>): RailItem[] {
  const items: RailItem[] = []
  for (const raw of Array.isArray(delta.items) ? delta.items : []) {
    if (!isRecord(raw) || typeof raw.id !== 'string' || taken.has(raw.id)) continue
    const entry = raw as unknown as RailItem
    if (!CUSTOM_RAIL_TYPES.includes(entry.type)) continue
    taken.add(entry.id)
    items.push({ ...entry })
  }
  return items
}

/** One occurrence a hide removed, with the index it held in the shared row's own
 *  definition — which is where the editors draw it back as a ghost, and where
 *  `applyScopedRail` puts it when it reconstructs that definition. */
export interface RailHiddenEntry {
  item: string
  index: number
}

/** Address of a row within a device layout. The editors' drag contract
 *  (`data-rail-row`) and the splice/hide bookkeeping use the same spelling. */
export const railRowKey = (device: RailDevice, surface: RailSurface, rowId: string): string =>
  `${device}|${surface}|${rowId}`

export interface ResolvedDeltaScope {
  config: RailConfig
  /** Row ids owned by the project delta (unique per surface by construction). */
  projectRowIds: Set<string>
  /** Catalog item ids owned by the project delta. */
  projectItemIds: Set<string>
  /** What hides removed, by `railRowKey`. Only hides that actually applied are
   *  recorded, so a hide naming an item global has since dropped self-prunes on
   *  the next write. */
  hiddenEntries: Map<string, RailHiddenEntry[]>
  /** Per shared row (`railRowKey`): the catalog ids this project placed there
   *  itself. Everything else in that row belongs to its global definition, which
   *  is the split `applyScopedRail` writes back by. */
  projectPlacements: Map<string, Set<string>>
}

const deltaRecords = <T>(map: RailDeltaMap<T> | undefined, device: RailDevice, surface: RailSurface): unknown[] => {
  if (!isRecord(map)) return []
  const forDevice = (map as Record<string, unknown>)[device]
  if (!isRecord(forDevice)) return []
  const list = (forDevice as Record<string, unknown>)[surface]
  return Array.isArray(list) ? list : []
}

/**
 * Overlay a project delta on a resolved global config.
 *
 * Global rows come first on every surface; delta rows follow, so the project's
 * own rows read as a trailing "this project" section rather than interleaving
 * with shared ones. Within a shared row, hides are applied before splices — so a
 * splice anchored after an item this project also hides falls back to the end of
 * the row rather than vanishing with its anchor.
 *
 * One rule is enforced here rather than trusted, because everything downstream
 * reads ownership off it: **within one row, an id is either the project's or the
 * definition's, never both.** So a splice is applied only when its id cannot also
 * be arriving from that row's own definition — because the action is
 * project-owned, because the definition does not carry it, or because this
 * project hides it from there (which is how a project-local *move* is said).
 * That is what makes "whose entry is this?" answerable from the id alone, at any
 * position, which is in turn what lets `applyScopedRail` split an arbitrarily
 * dragged-about row back apart without tracking indices through the edit.
 */
export function resolveDeltaScope(global: RailConfig, delta: RailProjectDelta): ResolvedDeltaScope {
  const taken = new Set(global.items.map(item => item.id))
  const added = deltaItems(delta, taken)
  const items = [...global.items.map(item => ({ ...item })), ...added]
  const known = new Set(items.map(item => item.id))
  const projectRowIds = new Set<string>()
  const hiddenEntries = new Map<string, RailHiddenEntry[]>()
  const projectPlacements = new Map<string, Set<string>>()
  const globalItemIds = new Set(global.items.map(item => item.id))
  const layouts = {} as RailLayouts
  for (const device of RAIL_DEVICES) {
    const deviceRows = isRecord(delta.layouts) ? delta.layouts[device] : undefined
    layouts[device] = {} as RailDeviceLayout
    for (const surface of RAIL_SURFACES) {
      const baseRows = global.layouts[device]?.[surface] || []
      const baseIds = new Set(baseRows.map(row => row.id))
      // A splice or hide may only name a *shared* row: a project row is wholly
      // owned, so it says the same thing by simply holding (or not holding) the id.
      const hides = deltaRecords(delta.hides, device, surface)
        .filter((raw): raw is RailHide =>
          isRecord(raw) && typeof raw.row === 'string' && typeof raw.item === 'string' && baseIds.has(raw.row))
      const splices = deltaRecords(delta.splices, device, surface)
        .filter((raw): raw is RailSplice =>
          isRecord(raw) && typeof raw.row === 'string' && typeof raw.item === 'string'
          && baseIds.has(raw.row) && known.has(raw.item))
      const base = baseRows.map(row => {
        const key = railRowKey(device, surface, row.id)
        const drop = new Set(hides.filter(hide => hide.row === row.id).map(hide => hide.item))
        const removed: RailHiddenEntry[] = []
        const slots: string[] = []
        row.items.forEach((id, index) => {
          if (drop.has(id)) { removed.push({ item: id, index }); return }
          slots.push(id)
        })
        if (removed.length) hiddenEntries.set(key, removed)
        // A hidden id is this project's business in this row even before a splice
        // puts it back: that is what makes unhiding reachable and the write-back
        // able to tell the two apart.
        const mine = new Set(removed.map(entry => entry.item))
        return { row, key, slots, mine, defined: new Set(row.items) }
      })
      const byId = new Map(base.map(entry => [entry.row.id, entry]))
      for (const splice of splices) {
        const target = byId.get(splice.row)
        if (!target) continue
        // The one-owner-per-id rule, enforced rather than trusted.
        if (globalItemIds.has(splice.item) && target.defined.has(splice.item) && !target.mine.has(splice.item)) continue
        target.mine.add(splice.item)
        // The *last* matching anchor, not the first. Splices chain — each one's
        // anchor is the entry it followed when it was recorded, which may be an
        // earlier splice of the same id — and anchoring on the first occurrence
        // walks a run of duplicates backwards, reversing the run it is rebuilding.
        const at = splice.after === null ? 0
          : splice.after === undefined ? target.slots.length
            : (() => {
              const found = target.slots.lastIndexOf(splice.after)
              return found < 0 ? target.slots.length : found + 1
            })()
        target.slots.splice(at, 0, splice.item)
      }
      const resolvedBase: RailRow[] = base.map(({ row, key, slots, mine }) => {
        if (mine.size) projectPlacements.set(key, mine)
        return { ...row, items: slots }
      })
      const seen = new Set(resolvedBase.map(row => row.id))
      const extra: RailRow[] = []
      const rawRows: unknown = deviceRows?.[surface]
      for (const raw of Array.isArray(rawRows) ? rawRows : []) {
        const row = normalizeRow(raw, known)
        if (!row || seen.has(row.id)) continue
        seen.add(row.id)
        projectRowIds.add(row.id)
        extra.push(row)
      }
      layouts[device][surface] = [...resolvedBase, ...extra]
    }
  }
  return {
    config: { items, layouts },
    projectRowIds,
    projectItemIds: new Set(added.map(item => item.id)),
    hiddenEntries,
    projectPlacements,
  }
}

const scopeConfig = (scope: unknown): RailConfig => normalizeRailConfig(scope)

/** Resolve the effective config for a project, falling back to the global one.
 *  A fork replaces the global config; a delta overlays it. */
export function railConfigFromBlob(blob: RailBlob | undefined, projectId?: string): RailConfig {
  const override = projectId ? blob?.projects?.[projectId] : undefined
  if (override !== undefined) {
    if (isProjectRailDelta(override)) return resolveDeltaScope(railConfigFromBlob(blob), override).config
    return scopeConfig(override)
  }
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

/** Backend-aware injected payload for text/slash/skill items.
 *
 *  A skill's invocation spelling is a per-CLI grammar the daemon declares
 *  (`skill_invocation_prefix`): Codex uses `$`, oh-my-pi namespaces skills under
 *  `/skill:`, and the rest use `/`. This used to be re-derived here as
 *  "`$` for Codex, `/` for everything else", which typed `/name` into an oh-my-pi
 *  pane whose CLI wanted `/skill:name` - a rail button that ran nothing.
 *
 *  Slash commands are a literal `/name` everywhere, and a `text` item is passed
 *  through verbatim. A 'prompt' item has no local payload: its text lives in the
 *  library and is fetched on click (`promptRail.ts`), so this returns '' for it. */
export function railPayload(item: RailItem, backend: RailBackend): string {
  if (item.type === 'text') return item.text || ''
  const prefix = skillInvocationPrefix(backend)
  // Strip a leading invocation marker so an item authored as `$commit`, `/commit`,
  // or a bare `commit` all resolve to this harness's spelling.
  const bare = (item.text || '').trim().replace(/^[/$]/, '')
  if (!bare) return ''
  if (item.type === 'slash') return `/${bare}`
  if (item.type === 'skill') {
    // An item already carrying the harness's own namespace keeps it rather than
    // gaining a second copy (`/skill:commit` must not become `/skill:skill:commit`).
    const namespace = prefix.replace(/^[/$]/, '')
    return namespace && bare.startsWith(namespace) ? `${prefix[0]}${bare}` : `${prefix}${bare}`
  }
  return ''
}
