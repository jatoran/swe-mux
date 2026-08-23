// Sticky Ctrl / Alt / Shift for the command rail.
//
// One chip per modifier, and the phone-keyboard shift model rather than a chord: tap to
// arm it for the next key, tap again to lock it, tap a locked one to clear it. That is
// what makes three chips multiply the whole rail instead of adding a Ctrl-prefixed
// duplicate of every key on it, and it is why pads carry no outer ring - a live modifier
// re-labels a pad's four slots, so one pad covers four modifier states at the size it
// already is.
//
// Armed modifiers are consumed by the next thing that sends bytes; locked ones are not.
// Anything that is not a key sequence - a skill, a prompt, opening a picker - consumes
// nothing and is left alone, because "Ctrl" has no meaning for it and silently eating
// the arm would be a modifier that vanished for no visible reason.
//
// DOM-free. `TerminalPane` owns the state, the chips and the wiring; everything here is
// the algebra.

export type RailModifier = 'ctrl' | 'alt' | 'shift'

export const RAIL_MODIFIERS: readonly RailModifier[] = ['ctrl', 'alt', 'shift']

export const RAIL_MODIFIER_LABELS: Readonly<Record<RailModifier, string>> = {
  ctrl: 'Ctrl',
  alt: 'Alt',
  shift: 'Shift',
}

/** Catalog id of the chip that works each modifier, and the reverse lookup. */
export const RAIL_MODIFIER_ITEM_IDS: Readonly<Record<RailModifier, string>> = {
  ctrl: 'modCtrl',
  alt: 'modAlt',
  shift: 'modShift',
}

const BY_ITEM_ID: Readonly<Record<string, RailModifier>> = {
  modCtrl: 'ctrl',
  modAlt: 'alt',
  modShift: 'shift',
}

export const railModifierForItem = (id: string): RailModifier | null => BY_ITEM_ID[id] ?? null

/** `armed` falls away after one key; `locked` does not. A modifier is never in both. */
export interface RailModifierState {
  armed: readonly RailModifier[]
  locked: readonly RailModifier[]
}

export const EMPTY_RAIL_MODIFIERS: RailModifierState = { armed: [], locked: [] }

export const railModifierActive = (state: RailModifierState, modifier: RailModifier): boolean =>
  state.armed.includes(modifier) || state.locked.includes(modifier)

/** Everything currently applying, in a stable order so a label reads the same every time. */
export const activeRailModifiers = (state: RailModifierState): RailModifier[] =>
  RAIL_MODIFIERS.filter(modifier => railModifierActive(state, modifier))

export type RailModifierPhase = 'off' | 'armed' | 'locked'

export function railModifierPhase(state: RailModifierState, modifier: RailModifier): RailModifierPhase {
  if (state.locked.includes(modifier)) return 'locked'
  if (state.armed.includes(modifier)) return 'armed'
  return 'off'
}

const without = (list: readonly RailModifier[], modifier: RailModifier): RailModifier[] =>
  list.filter(entry => entry !== modifier)

/** off → armed → locked → off. Three states in one control, in the order a hand expects
 *  them: the common case is one key, the next most common is a run of them. */
export function toggleRailModifier(state: RailModifierState, modifier: RailModifier): RailModifierState {
  switch (railModifierPhase(state, modifier)) {
    case 'off': return { armed: [...state.armed, modifier], locked: without(state.locked, modifier) }
    case 'armed': return { armed: without(state.armed, modifier), locked: [...state.locked, modifier] }
    default: return { armed: without(state.armed, modifier), locked: without(state.locked, modifier) }
  }
}

/** What is left after a key has consumed the armed set. Locks survive. */
export const consumeRailModifiers = (state: RailModifierState): RailModifierState =>
  state.armed.length ? { armed: [], locked: state.locked } : state

export const clearRailModifiers = (): RailModifierState => EMPTY_RAIL_MODIFIERS

// ---------------------------------------------------------------------------
// Applying a modifier to a key sequence
// ---------------------------------------------------------------------------
//
// Terminals encode modified keys two different ways and the rail sends both kinds, so
// this has to know which it is holding.
//
//  * A CSI function key carries its modifiers as a numeric parameter: `ESC [ 1 ; n <final>`
//    for the cursor/Home/End family and `ESC [ <num> ; n ~` for the tilde family, where
//    `n` is 1 plus a bitmask. An unmodified `ESC[A` and an already-modified `ESC[1;5H`
//    are the same shape with the parameter left out, which is why the existing `^Home`
//    chip round-trips through here and gains rather than replaces its Ctrl.
//  * Everything else is bytes: Alt is an ESC prefix, Ctrl folds a letter into its control
//    code, and Shift upper-cases. Ctrl and Shift have no encoding at all for most byte
//    sequences, and inventing one would send noise, so they are dropped rather than
//    guessed at.

const MODIFIER_BITS: Readonly<Record<RailModifier, number>> = { shift: 1, alt: 2, ctrl: 4 }

/** The CSI modifier parameter for a set: 1 plus the bitmask, or 1 for none. */
export function railModifierParam(modifiers: readonly RailModifier[]): number {
  return modifiers.reduce((total, modifier) => total + MODIFIER_BITS[modifier], 1)
}

/**
 * `ESC [` optional-params final, where the final is a key that *has* a modified form.
 *
 * The final set is a closed list rather than any letter, and that is the bug this shape
 * exists to prevent: back-tab is `ESC[Z`, it has no `1;n` form at all, and a permissive
 * matcher rewrites it to `ESC[1;5Z` - a sequence no terminal reads, from a chip that
 * looked like it worked. `ABCD` are the cursor keys, `EFH` the Begin/End/Home family, and
 * `PQRS` the first four function keys; the tilde family is numbered and handled alongside.
 */
const CSI_PATTERN = /^\x1b\[(\d+)?(?:;(\d+))?([A-HP-S~])$/

function applyToCsi(sequence: string, modifiers: readonly RailModifier[]): string | null {
  const match = CSI_PATTERN.exec(sequence)
  if (!match) return null
  const [, rawFirst, rawSecond, final] = match
  const tilde = final === '~'
  // The tilde family numbers the key in the first parameter (`ESC[3~` is Delete); the
  // letter family has no key number, so a lone parameter there is already a modifier.
  const key = tilde ? rawFirst ?? '1' : '1'
  const existing = tilde ? rawSecond : rawSecond ?? rawFirst
  const current = Math.max(1, Number(existing) || 1) - 1
  const next = (current | modifiers.reduce((bits, modifier) => bits | MODIFIER_BITS[modifier], 0)) + 1
  if (next === 1) return sequence
  return `\x1b[${key};${next}${final}`
}

/** Ctrl folds `@`..`_` (and the lower-case letters) onto 0x00..0x1f. */
function controlCode(character: string): string | null {
  const upper = character.toUpperCase()
  const code = upper.charCodeAt(0)
  if (code >= 0x40 && code <= 0x5f) return String.fromCharCode(code - 0x40)
  if (character === '?') return '\x7f'
  return null
}

/**
 * The bytes a key sends with these modifiers applied.
 *
 * Unmodified input, an empty modifier set, and a sequence with no encoding for the
 * modifiers asked for all return the sequence unchanged, so this is safe to run over
 * everything the rail sends rather than only over what it knows how to modify.
 */
export function applyRailModifiers(sequence: string, modifiers: readonly RailModifier[]): string {
  if (!sequence || !modifiers.length) return sequence
  const csi = applyToCsi(sequence, modifiers)
  if (csi !== null) return csi
  const ctrl = modifiers.includes('ctrl')
  const alt = modifiers.includes('alt')
  const shift = modifiers.includes('shift')
  // Back-tab is what Shift+Tab *is* on a terminal, and the rail already ships it as its
  // own chip; going through the CSI path instead would send `ESC[1;2I`, which nothing reads.
  if (sequence === '\t' && shift) return alt ? '\x1b\x1b[Z' : '\x1b[Z'
  let out = sequence
  if (ctrl && out.length === 1) out = controlCode(out) ?? out
  else if (shift && out.length === 1) out = out.toUpperCase()
  if (alt) out = `\x1b${out}`
  return out
}

/** How a modified key reads in a tooltip or a pad petal: `Ctrl+Alt+Home`. */
export function railModifierPrefix(modifiers: readonly RailModifier[]): string {
  return modifiers.map(modifier => RAIL_MODIFIER_LABELS[modifier]).join('+')
}

export function railModifierLabel(base: string, modifiers: readonly RailModifier[]): string {
  return modifiers.length ? `${railModifierPrefix(modifiers)}+${base}` : base
}
