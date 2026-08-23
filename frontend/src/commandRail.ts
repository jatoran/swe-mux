// Data model for configurable Actions on the terminal command rail.
//
// Two halves, deliberately separate:
//
//  * the **catalog** (`RailConfig.items`) says what a command *is* — its label,
//    what it injects, and which backends it means anything for. Identity and
//    behaviour, nothing about position.
//  * the **layouts** (`RailConfig.layouts`) say where commands *appear*. One
//    layout per device class, each holding rows for the rail under the terminal.
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
// Rendering lives in TerminalPane, which owns the terminal handles and clipboard
// handlers. This module owns only the pure data
// model, the built-in defaults, the resolve helpers, and the one-way migration
// from the pre-layout format, so it all stays unit testable under the node
// type-stripping runner (no browser dependencies here).

import { AGENT_NEWLINE } from './terminalKeys.ts'
import { allBackendNames, isAgentBackend, skillInvocationPrefix } from './harnessRegistry.ts'

/** Device classes with independent layouts. Matches `deviceSettings.currentProfile()`. */
export type RailDevice = 'desktop' | 'mobile'
/** The command rail is the single placement surface. Kept as an address type because
 *  scoped row operations use the same device/surface/row coordinate throughout. */
export type RailSurface = 'strip'
export type RailBackend = string

export const RAIL_DEVICES: readonly RailDevice[] = ['desktop', 'mobile']
export const RAIL_SURFACES: readonly RailSurface[] = ['strip']

// 'key'   → inject a raw byte sequence (arrow keys, Esc, Ctrl-C, newline…)
// 'action'→ invoke a named built-in handler (copy/paste/relaunch/toggle…)
// 'text'  → inject literal text, optionally submitting with Enter
// 'slash' → inject a provider slash command (/name), optionally submitting
// 'skill' → inject a skill invocation, backend-aware (/name on Claude, $name on Codex)
// 'prompt'→ insert a prompt-library template, resolved from the server by key at
//           click time so the button always injects the template's current text
// 'pad'   → a directional container: one chip holding up to four other catalog
//           items, reached by dragging a direction off it (see the pad section below)
export type RailItemType = 'key' | 'action' | 'text' | 'slash' | 'skill' | 'prompt' | 'pad'
export type RailItemDisplay = 'auto' | 'icon' | 'label' | 'icon-label'

/** Injection types a user may author. 'action' is app-owned and never custom. */
export const CUSTOM_RAIL_TYPES: readonly RailItemType[] = ['key', 'text', 'slash', 'skill', 'prompt', 'pad']

// ---------------------------------------------------------------------------
// Pads
// ---------------------------------------------------------------------------
//
// A pad is one chip that holds up to four other catalog items plus a centre, each
// reached by pressing the chip and dragging a direction off it. It is a *container*
// and never a behaviour of its own: every slot names an ordinary catalog id, so a
// pad composes with backend filtering, project deltas, splices and hides without any
// of them learning what a pad is.
//
// Four directions, never eight. Eight halves the angular tolerance to 22.5° and
// destroys the eyes-free property the control exists for; where four is not enough
// the answer is a modifier chip re-labelling the same pad, not more wedges.
//
// Two orientations, because the two useful four-way carvings of a circle put their
// boundaries in different places. `cardinal` reads the dominant axis and so its
// boundaries are the diagonals; `diagonal` reads the sign of each axis and so its
// boundaries are the axes themselves. A set with a natural up/down/left/right
// meaning wants the first; a 2x2 matrix of two independent binary choices - which is
// exactly what Home/End crossed with plain/Ctrl is - wants the second, because then
// each axis carries one of the choices.

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
 *
 * It sits above the catalog rather than beside the other normalization helpers
 * because the shipped pads canonicalize their own slots at their definition site, and
 * that runs `migratedRailItemId` while `BUILTIN_RAIL` is still being evaluated.
 */
const RETIRED_RAIL_IDS: Readonly<Record<string, string>> = {
  clearInput: 'ctrlU',
}

/** Resolve a stored id through the retirement table. Unknown ids pass through
 *  unchanged; whether they exist is the caller's separate question. */
export function migratedRailItemId(id: string): string {
  return RETIRED_RAIL_IDS[id] ?? id
}

export type RailPadOrientation = 'cardinal' | 'diagonal'
export type RailPadDirection = 'up' | 'right' | 'down' | 'left' | 'upLeft' | 'upRight' | 'downRight' | 'downLeft'
export type RailPadSlotKey = RailPadDirection | 'center'

/**
 * When a slot fires.
 *
 *  * `enter`        - the instant the press crosses into this direction, once.
 *  * `enter-repeat` - the same, then repeating while held.
 *  * `release`      - only if the press is *still* latched here when it ends, which
 *                     is what makes dragging back out an escape hatch. The mode for
 *                     anything a mis-flick must not be able to do.
 */
export type RailPadTriggerMode = 'enter' | 'enter-repeat' | 'release'

export const RAIL_PAD_ORIENTATIONS: readonly RailPadOrientation[] = ['cardinal', 'diagonal']
export const RAIL_PAD_TRIGGER_MODES: readonly RailPadTriggerMode[] = ['enter', 'enter-repeat', 'release']

export const CARDINAL_PAD_DIRECTIONS: readonly RailPadDirection[] = ['up', 'right', 'down', 'left']
export const DIAGONAL_PAD_DIRECTIONS: readonly RailPadDirection[] = ['upLeft', 'upRight', 'downRight', 'downLeft']

export const RAIL_PAD_DIRECTION_LABELS: Readonly<Record<RailPadDirection, string>> = {
  up: 'Up',
  right: 'Right',
  down: 'Down',
  left: 'Left',
  upLeft: 'Up-left',
  upRight: 'Up-right',
  downRight: 'Down-right',
  downLeft: 'Down-left',
}

/** The four directions this orientation carves the circle into. */
export function padDirections(orientation: RailPadOrientation): readonly RailPadDirection[] {
  return orientation === 'diagonal' ? DIAGONAL_PAD_DIRECTIONS : CARDINAL_PAD_DIRECTIONS
}

/** Every addressable slot: the four directions, then the centre. */
export function padSlotKeys(orientation: RailPadOrientation): readonly RailPadSlotKey[] {
  return [...padDirections(orientation), 'center']
}

const PAD_UNITS: Readonly<Record<RailPadDirection, { x: number; y: number }>> = {
  up: { x: 0, y: -1 },
  right: { x: 1, y: 0 },
  down: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
  upLeft: { x: -Math.SQRT1_2, y: -Math.SQRT1_2 },
  upRight: { x: Math.SQRT1_2, y: -Math.SQRT1_2 },
  downRight: { x: Math.SQRT1_2, y: Math.SQRT1_2 },
  downLeft: { x: -Math.SQRT1_2, y: Math.SQRT1_2 },
}

/** Unit vector pointing down the middle of a direction's wedge. Screen axes, so `y` grows down. */
export const padDirectionUnit = (direction: RailPadDirection): { x: number; y: number } => PAD_UNITS[direction]

/** True for the directions whose travel spends vertical room below the finger, which is the
 *  room a rail at the bottom of the screen does not have. */
export const padDirectionDescends = (direction: RailPadDirection): boolean => PAD_UNITS[direction].y > 0

export interface RailPadSlot {
  /** Catalog item id this direction runs. */
  item: string
  /** Omitted means the item's own default (`defaultPadTriggerMode`). */
  mode?: RailPadTriggerMode
}

export interface RailPadConfig {
  orientation: RailPadOrientation
  slots: Partial<Record<RailPadSlotKey, RailPadSlot>>
}

/**
 * A slot's trigger mode when the binding does not name one.
 *
 * Derived from the item rather than fixed per pad, so dropping an action into a pad
 * arrives already safe: an arrow repeats because repetition is what an arrow is for,
 * and anything wearing the rail's danger treatment waits for release because a
 * mis-flick must not be able to do it.
 */
export function defaultPadTriggerMode(item: RailItem | undefined): RailPadTriggerMode {
  if (!item) return 'enter'
  if (item.repeatable) return 'enter-repeat'
  if (item.className?.includes('rail-danger')) return 'release'
  return 'enter'
}

/** The mode a bound slot actually runs at. */
export function railPadSlotMode(slot: RailPadSlot | undefined, item: RailItem | undefined): RailPadTriggerMode {
  if (slot?.mode && RAIL_PAD_TRIGGER_MODES.includes(slot.mode)) return slot.mode
  return defaultPadTriggerMode(item)
}

/**
 * Sanitize a stored pad config.
 *
 * Slots naming an item that is not in the catalog are **kept**, not dropped, and go
 * dead at render time instead. Same durability rule the retirement table exists for:
 * a layout is arbitrarily old and per-Project, so a slot pointing at an action that
 * happens to be missing from *this* resolution - a project item seen from another
 * project, a built-in mid-rename - must survive being loaded and saved again rather
 * than being quietly deleted by the round trip. Ids are migrated on the way through,
 * for the same reason rows migrate theirs.
 *
 * A slot key that does not belong to the orientation is dropped, because that one is
 * unreachable by construction rather than merely unresolved right now.
 */
export function normalizeRailPad(raw: unknown): RailPadConfig {
  // Slots are rebuilt in `padDirections` order rather than the order they were written
  // in. That is not tidiness: a fork stores a *copy* of a shipped item and reattach asks
  // whether the copy still equals the shipped definition, so a pad whose literal was
  // authored in reading order and whose saved copy came back in canonical order would
  // report as edited by the operator when nothing had been. The shipped pads are run
  // through here at their definition site for exactly that reason.
  const source = isRecord(raw) ? raw : {}
  const orientation = RAIL_PAD_ORIENTATIONS.includes(source.orientation as RailPadOrientation)
    ? source.orientation as RailPadOrientation
    : 'cardinal'
  const slots: Partial<Record<RailPadSlotKey, RailPadSlot>> = {}
  const rawSlots = isRecord(source.slots) ? source.slots : {}
  for (const key of padSlotKeys(orientation)) {
    const entry = rawSlots[key]
    if (!isRecord(entry) || typeof entry.item !== 'string' || !entry.item) continue
    const mode = RAIL_PAD_TRIGGER_MODES.includes(entry.mode as RailPadTriggerMode)
      ? entry.mode as RailPadTriggerMode
      : undefined
    slots[key] = { item: migratedRailItemId(entry.item), ...(mode ? { mode } : {}) }
  }
  return { orientation, slots }
}

/** Catalog ids a pad reaches. Used by placement checks and the voice adapter, both of
 *  which have to see a padded action as placed. */
export function railPadSlotItemIds(item: RailItem): string[] {
  if (item.type !== 'pad' || !item.pad) return []
  return padSlotKeys(item.pad.orientation)
    .map(key => item.pad?.slots[key]?.item)
    .filter((id): id is string => !!id)
}

/** A catalog entry: what the command is, never where it sits. */
export interface RailItem {
  id: string
  type: RailItemType
  label: string
  /** Presentation only. `auto` uses an available built-in icon and otherwise the label. */
  display?: RailItemDisplay
  /** Optional visible label override. The command's identity and payload stay unchanged. */
  displayLabel?: string
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
  /** 'pad' items: the orientation and the four-plus-centre slot bindings. */
  pad?: RailPadConfig
  /** Holding this item down repeats it. The one source for both the standalone
   *  hold-to-repeat key (`isRepeatableRailKey`) and a pad slot's default trigger
   *  mode, which must never be able to disagree about the same button. */
  repeatable?: boolean
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
}

/** One rail row. Rows are ordered, and so are the items inside them. */
export interface RailRow {
  id: string
  /** Optional caption used by the editor to identify grouped rows. */
  label?: string
  /** Ordered catalog item ids. Duplicates are legal, here and across rows. */
  items: string[]
}

export interface RailDeviceLayout {
  strip: RailRow[]
}

export type RailLayouts = Record<RailDevice, RailDeviceLayout>

export interface RailConfig {
  items: RailItem[]
  layouts: RailLayouts
}

export const allRailBackends = (): readonly RailBackend[] => allBackendNames()

/** Every custom item is placed on the rail. Horizontal overflow and the permanent
 *  drawer popover keep an unbounded catalog reachable without a second layout. */
export const DEFAULT_CUSTOM_SURFACE: RailSurface = 'strip'

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
  // handful of templates placed as dedicated buttons. A library reachable only
  // through configured buttons would leave every other template three taps away.
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
  // so it pairs with Tab on the rail.
  { id: 'shiftTab', type: 'key', bytes: '\x1b[Z', label: '⇧Tab', className: 'term-key', title: 'Shift+Tab (cycle mode / back-tab)', voicePhrases: ['shift tab', 'press shift tab', 'cycle mode'] },
  { id: 'ctrlC', type: 'key', bytes: '\x03', label: '^C', className: 'term-key', title: 'Interrupt (Ctrl-C)', voicePhrases: ['control c', 'press control c'] },
  { id: 'up', type: 'key', bytes: '\x1b[A', label: '↑', className: 'term-key', repeatable: true, title: 'Up / previous command', voicePhrases: ['up arrow', 'press up', 'previous terminal command'] },
  { id: 'down', type: 'key', bytes: '\x1b[B', label: '↓', className: 'term-key', repeatable: true, title: 'Down / next command', voicePhrases: ['down arrow', 'press down', 'next terminal command'] },
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
  { id: 'left', type: 'key', bytes: '\x1b[D', label: '←', className: 'term-key', repeatable: true, title: 'Left', voicePhrases: ['left arrow', 'press left'] },
  { id: 'right', type: 'key', bytes: '\x1b[C', label: '→', className: 'term-key', repeatable: true, title: 'Right', voicePhrases: ['right arrow', 'press right'] },
  // Navigation + editing extras remain in the catalog and follow the main rail row.
  // The permanent drawer popover makes the long tail reachable without a second surface.
  { id: 'home', type: 'key', bytes: '\x1b[H', label: 'Home', className: 'term-key', title: 'Home / start of line', voicePhrases: ['home key', 'press home'] },
  { id: 'end', type: 'key', bytes: '\x1b[F', label: 'End', className: 'term-key', title: 'End / end of line', voicePhrases: ['end key', 'press end'] },
  { id: 'ctrlHome', type: 'key', bytes: '\x1b[1;5H', label: '^Home', className: 'term-key', title: 'Ctrl+Home / top', voicePhrases: ['control home', 'press control home'] },
  { id: 'ctrlEnd', type: 'key', bytes: '\x1b[1;5F', label: '^End', className: 'term-key', title: 'Ctrl+End / bottom', voicePhrases: ['control end', 'press control end'] },
  // ESC+CR is the one newline sequence both agent composers accept. Raw LF works
  // in Claude but Codex treats it as ordinary input instead of editor.newline.
  { id: 'newline', type: 'key', bytes: AGENT_NEWLINE, label: '↵ nl', className: 'term-key', title: 'Insert newline without submitting', voicePhrases: ['new line', 'insert new line'] },
  // Opens Claude's interactive /rewind picker (there is no one-shot,
  // conversation-only variant, so this just launches the picker).
  { id: 'rewind', type: 'slash', text: 'rewind', label: 'Rewind…', submit: true, backends: ['claude'], title: 'Open Claude /rewind (interactive checkpoint picker)' },
  // Ends the session the rail belongs to. The two-click confirm remains the guard;
  // operators who do not want it visible can remove its rail placement.
  { id: 'endSession', type: 'action', action: 'endSession', label: 'End session', className: 'rail-danger', title: 'End this session (click twice to confirm)' },
  { id: 'attach', type: 'action', action: 'attach', label: 'Attach', agentOnly: true, title: 'Attach files to this chat without sending' },
  // The three sticky modifiers. Tap arms one key, tap again locks, tap a locked one
  // clears it: the phone-keyboard shift model, which is what makes one chip multiply
  // the whole rail instead of adding a row of Ctrl-prefixed duplicates. They are also
  // why pads carry no outer ring - a live modifier re-labels a pad's four slots, so
  // one pad covers four modifier states at its original size.
  //
  // No voice phrases: a modifier is a *state* a spoken caller cannot see, and the
  // thing it would modify is a second command away.
  { id: 'modCtrl', type: 'action', action: 'modifier', label: 'Ctrl', className: 'term-key rail-modifier', title: 'Ctrl for the next key. Tap again to lock.' },
  { id: 'modAlt', type: 'action', action: 'modifier', label: 'Alt', className: 'term-key rail-modifier', title: 'Alt for the next key. Tap again to lock.' },
  { id: 'modShift', type: 'action', action: 'modifier', label: 'Shift', className: 'term-key rail-modifier', title: 'Shift for the next key. Tap again to lock.' },
  // The four shipped pads. Each one is an ordinary catalog entry whose slots name
  // ordinary catalog entries, so nothing downstream needs a special case: the items
  // they hold stay in the catalog, keep their own backend gating, and can still be
  // placed as individual chips by anyone who wants them back.
  {
    id: 'padArrows',
    type: 'pad',
    label: 'Arrows',
    className: 'term-key',
    title: 'Arrow keys. Drag a direction; hold to repeat.',
    pad: normalizeRailPad({
      orientation: 'cardinal',
      slots: { up: { item: 'up' }, right: { item: 'right' }, down: { item: 'down' }, left: { item: 'left' } },
    }),
  },
  // Diagonal, because this is not four directions but two independent binary choices -
  // start-or-end crossed with line-or-document - and the diagonal carving is the one
  // that gives each axis a choice: left/right is which end, up/down is which scope.
  {
    id: 'padJump',
    type: 'pad',
    label: 'Jump',
    className: 'term-key',
    title: 'Line and document jumps. Drag a corner.',
    pad: normalizeRailPad({
      orientation: 'diagonal',
      slots: {
        upLeft: { item: 'home' },
        upRight: { item: 'ctrlHome' },
        downLeft: { item: 'end' },
        downRight: { item: 'ctrlEnd' },
      },
    }),
  },
  // Not `agentOnly`, though two of its three slots effectively are: a slot whose item
  // this backend does not admit renders as a dead direction rather than removing the
  // pad, because directions are positional and a pad that rearranged itself per
  // backend would be worse than one with three live slots.
  {
    id: 'padCopy',
    type: 'pad',
    label: 'Copy',
    title: 'Copy from this session. Drag a direction.',
    pad: normalizeRailPad({
      orientation: 'cardinal',
      slots: { left: { item: 'copyReply' }, up: { item: 'copyInput' }, right: { item: 'copyResume' } },
    }),
  },
  {
    id: 'padPickers',
    type: 'pad',
    label: 'Pick',
    title: 'Clipboard, skills, prompts, Actions. Drag a direction.',
    pad: normalizeRailPad({
      orientation: 'cardinal',
      slots: {
        left: { item: 'clipboardHistory' },
        up: { item: 'skills' },
        right: { item: 'prompts' },
        down: { item: 'actionsDrawer' },
      },
    }),
  },
]

/**
 * The strip a fresh install starts with.
 *
 * Not every catalog id, which is what it used to be: the four pads hold fifteen of
 * them, and placing both the pad and its contents would spend the space the pad
 * exists to save. Everything omitted here is still in the catalog and one drag away
 * in the editor - a pad is a *placement* decision, and this is the default one.
 *
 * Only ids that exist in `BUILTIN_RAIL`; asserted below so a rename cannot silently
 * shorten the default rail.
 */
export const DEFAULT_RAIL_ORDER: readonly string[] = [
  'relaunch',
  'padCopy',
  'branch',
  'approveOnce',
  'paste',
  'padPickers',
  'kbdToggle',
  'esc',
  'enter',
  'tab',
  'shiftTab',
  'ctrlC',
  'padArrows',
  'padJump',
  'modCtrl',
  'modAlt',
  'modShift',
  'markdownDivider',
  'markdownCodeFence',
  'ctrlU',
  'restoreInput',
  'newline',
  'rewind',
  'endSession',
  'attach',
]

const BUILTIN_BY_ID = new Map(BUILTIN_RAIL.map(item => [item.id, item]))

const RAIL_ITEM_DISPLAYS: readonly RailItemDisplay[] = ['auto', 'icon', 'label', 'icon-label']

export function railItemDisplayLabel(item: RailItem, fallback = item.label): string {
  return item.displayLabel?.trim() || fallback
}

/** Resolve a requested presentation against whether this item actually has a registered icon. */
export function railItemDisplayMode(item: RailItem, hasIcon: boolean): Exclude<RailItemDisplay, 'auto'> {
  if (!hasIcon) return 'label'
  if (item.display === 'label' || item.display === 'icon-label') return item.display
  return 'icon'
}

/** True for built-in item ids whose behaviour (type/bytes/action/label) is
 *  owned by the app; users may place and reorder them but not edit them. */
export function isBuiltinRailId(id: string): boolean {
  return BUILTIN_BY_ID.has(id)
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

/** The layout every device starts with: one rail row holding `DEFAULT_RAIL_ORDER`.
 *  Desktop and mobile start identical and diverge from there. */
export function defaultRailLayouts(items: readonly RailItem[] = BUILTIN_RAIL): RailLayouts {
  const known = new Set(items.map(item => item.id))
  // Intersected rather than assumed, so a caller seeding from a narrowed catalog
  // (the tests do) gets a layout that only references what it handed in.
  const ordered = DEFAULT_RAIL_ORDER.filter(id => known.has(id))
  const seed = (device: RailDevice): RailRow => ({
    id: defaultRowId(device, 'strip'),
    items: [...ordered],
  })
  return {
    desktop: { strip: [seed('desktop')] },
    mobile: { strip: [seed('mobile')] },
  }
}

export function defaultRailConfig(): RailConfig {
  return { items: BUILTIN_RAIL.map(item => ({ ...item })), layouts: defaultRailLayouts() }
}

// ---------------------------------------------------------------------------
// Normalization
// ---------------------------------------------------------------------------

// A declaration rather than a const, so the shipped pads can be canonicalized through
// `normalizeRailPad` at their definition site further up the file.
function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

/** Merge a saved catalog over the built-in one. Built-ins keep their authoritative
 *  behaviour fields (so shipped defaults can evolve) while custom entries are kept
 *  verbatim; unknown-typed and duplicate entries are dropped. */
export function mergeRailCatalog(saved: unknown): { items: RailItem[]; addedBuiltins: RailItem[] } {
  const entries = Array.isArray(saved) ? saved : []
  const knownBackends = new Set(allRailBackends())
  const seen = new Set<string>()
  const items: RailItem[] = []
  for (const raw of entries) {
    if (!isRecord(raw) || typeof raw.id !== 'string') continue
    // Resolved before the dedupe, so a config holding both the retired id and its
    // replacement collapses to one catalog entry rather than two of the same item.
    const id = migratedRailItemId(raw.id)
    if (seen.has(id)) continue
    const builtin = BUILTIN_BY_ID.get(id)
    if (builtin) {
      seen.add(id)
      const display = RAIL_ITEM_DISPLAYS.includes(raw.display as RailItemDisplay)
        ? raw.display as RailItemDisplay
        : undefined
      const displayLabel = typeof raw.displayLabel === 'string' && raw.displayLabel.trim()
        ? raw.displayLabel.trim()
        : undefined
      const backends = Array.isArray(raw.backends)
        ? raw.backends.filter((backend): backend is RailBackend => typeof backend === 'string' && knownBackends.has(backend))
        : undefined
      // A built-in pad's *slots* are the one behaviour field a save may override, and
      // deliberately so: a pad is a container, and which four things it holds is the
      // same kind of choice as which chips sit in a row. Locking it would leave the
      // slot editor able to build new pads and unable to adjust the four that ship.
      // The shipped config remains the fallback, so a pad the operator never touched
      // still tracks whatever it is re-slotted to in a later release.
      const pad = builtin.type === 'pad' && isRecord(raw.pad) ? normalizeRailPad(raw.pad) : undefined
      items.push({
        ...builtin,
        ...(display ? { display } : {}),
        ...(displayLabel ? { displayLabel } : {}),
        ...(backends?.length ? { backends } : {}),
        ...(pad ? { pad } : {}),
      })
      continue
    }
    // Custom items may only be safe injection types, never 'action'. They are never
    // migrated: the retirement table covers built-ins, whose behaviour this module owns.
    const entry = raw as unknown as RailItem
    if (!CUSTOM_RAIL_TYPES.includes(entry.type)) continue
    seen.add(id)
    items.push(entry.type === 'pad' ? { ...entry, pad: normalizeRailPad(entry.pad) } : { ...entry })
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

function normalizeRows(raw: unknown, known: Set<string>): RailRow[] {
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
  return rows
}

function normalizeSurface(raw: unknown, known: Set<string>, device: RailDevice, surface: RailSurface): RailRow[] {
  const rows = normalizeRows(raw, known)
  // The rail keeps at least one row so the editor always has a drop target
  // and the renderer always has somewhere for a new built-in to land.
  if (!rows.length) rows.push({ id: defaultRowId(device, surface), items: [] })
  return rows
}

/** Collapse the retired Quick-actions panel into the final rail row. Items already
 *  placed anywhere on the rail are not duplicated. Old pin-created catalog items
 *  need no special case: they become ordinary explicit rail entries here. */
function migrateLegacyPanel(strip: RailRow[], rawPanel: unknown, known: Set<string>): RailRow[] {
  const placed = new Set(strip.flatMap(row => row.items))
  const migrated = normalizeRows(rawPanel, known)
    .flatMap(row => row.items)
    .filter(id => {
      if (placed.has(id)) return false
      placed.add(id)
      return true
    })
  if (!migrated.length) return strip
  const target = strip.length - 1
  strip[target] = { ...strip[target], items: [...strip[target].items, ...migrated] }
  return strip
}

/** Normalize any stored shape into a usable config: a v3 rail-only object, a v2
 *  rail/panel object, a pre-layout item array, or nothing at all. */
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
    const strip = normalizeSurface(rawDevice.strip, known, device, 'strip')
    layouts[device] = { strip: migrateLegacyPanel(strip, rawDevice.panel, known) }
  }
  // A built-in shipped after this save was written is appended to the first row
  // of the rail on both devices. Doing nothing would leave it
  // permanently invisible to anyone with an existing layout.
  for (const item of addedBuiltins) {
    for (const device of RAIL_DEVICES) {
      const row = layouts[device].strip[0].items
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
// showed. The retired drawer placement now follows the strip placement into the
// single rail, preserving every enabled item once per device.

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

const legacyBuiltin = (item: RailItem): LegacyRailItem => ({ ...item, placement: 'strip' })

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
      out.push({
        ...legacyBuiltin(builtin),
        ...adoptLegacyPlacement(entry),
        platforms: entry.platforms,
        backends: entry.backends,
        display: entry.display,
        displayLabel: entry.displayLabel,
      })
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

function legacyShows(item: LegacyRailItem, device: RailDevice): boolean {
  if (item.enabled === false) return false
  if (item.platforms && !item.platforms.includes(device)) return false
  return true
}

/** Turn one pre-layout list into one rail row per device. */
export function migrateLegacyRail(saved: LegacyRailItem[] | undefined | null): RailConfig {
  const merged = mergeLegacyRail(saved)
  const items: RailItem[] = merged.map(({ platforms: _p, placement: _c, enabled: _e, ...item }) => ({ ...item }))
  const layouts = {} as RailLayouts
  for (const device of RAIL_DEVICES) {
    layouts[device] = { strip: [{
      id: defaultRowId(device, 'strip'),
      items: merged.filter(item => legacyShows(item, device)).map(item => item.id),
    }] }
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

export const RAIL_BLOB_VERSION = 3

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
    items.push(entry.type === 'pad' ? { ...entry, pad: normalizeRailPad(entry.pad) } : { ...entry })
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

const deltaRecords = <T>(map: RailDeltaMap<T> | undefined, device: RailDevice, surface: RailSurface | 'panel'): unknown[] => {
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
      const legacyTargetRow = baseRows[baseRows.length - 1]?.id
      // v2 project overlays may have edited the retired panel's shared row. The
      // global panel was appended to the final rail row during normalization, so
      // point those old records at the same destination before applying them.
      const legacyHides = legacyTargetRow
        ? deltaRecords(delta.hides, device, 'panel').map(raw => isRecord(raw) ? { ...raw, row: legacyTargetRow } : raw)
        : []
      const legacySplices = legacyTargetRow
        ? deltaRecords(delta.splices, device, 'panel').map(raw => isRecord(raw) ? { ...raw, row: legacyTargetRow } : raw)
        : []
      // A splice or hide may only name a *shared* row: a project row is wholly
      // owned, so it says the same thing by simply holding (or not holding) the id.
      const hides = [...deltaRecords(delta.hides, device, surface), ...legacyHides]
        .filter((raw): raw is RailHide =>
          isRecord(raw) && typeof raw.row === 'string' && typeof raw.item === 'string' && baseIds.has(raw.row))
      const splices = [...deltaRecords(delta.splices, device, surface), ...legacySplices]
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
      // Project-owned v2 panel rows become ordinary project rail placements. Add
      // only entries not already reachable on this device, then fold them into the
      // final project row so migration does not create another permanent rail row.
      const legacyPanel = isRecord(deviceRows)
        ? normalizeRows((deviceRows as Record<string, unknown>).panel, known)
        : []
      const placed = new Set([...resolvedBase, ...extra].flatMap(row => row.items))
      const migrated = legacyPanel.flatMap(row => row.items).filter(id => {
        if (placed.has(id)) return false
        placed.add(id)
        return true
      })
      if (migrated.length) {
        if (extra.length) {
          const target = extra.length - 1
          extra[target] = { ...extra[target], items: [...extra[target].items, ...migrated] }
        } else {
          const first = legacyPanel[0]
          const row: RailRow = {
            id: first && !seen.has(first.id) ? first.id : `${device}-project-migrated`,
            ...(first?.label ? { label: first.label } : { label: 'Project' }),
            items: migrated,
          }
          projectRowIds.add(row.id)
          extra.push(row)
        }
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
