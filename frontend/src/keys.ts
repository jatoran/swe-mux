// Turning a keystroke into a chord, and a chord back into something readable.
//
// The token comes from `event.code`, the PHYSICAL key, not from `event.key`, which
// is whatever the active layout produced. Three things follow, and all three were
// broken before:
//
//  - A binding recorded on Dvorak or AZERTY means the same physical key on QWERTY.
//    `key` would have moved it: the key QWERTY calls `k` is `t` on Dvorak.
//  - Shifted punctuation is expressible at all. `Ctrl+Shift+5` reports `key: '%'`,
//    so a `key`-derived chord could never match a table written as `ctrl+shift+5` -
//    which is the exact shape tmux's `prefix %` needs.
//  - AltGr is visible. On Windows and X11 AltGr synthesises Ctrl+Alt, so a chord
//    carrying both is one an international keyboard fires while typing. The tokenizer
//    reports it rather than hiding it (`altGrHazard`).
//
// `event.key` is still what the LABEL is drawn from where the browser offers one,
// because the reader wants to see the letter printed on their own keycap.
// `src/swe_mux/keychords.py` holds the same table; `tests/test_keychords.py` reads
// this file and fails when the two disagree, because a tokenizer that disagrees with
// its own recorder produces bindings that can never fire.

export const MODIFIER_ORDER = ['ctrl', 'shift', 'alt', 'meta'] as const

/** `code` spellings that are not simply the token upper-cased. */
const PUNCTUATION: Record<string, string> = {
  Minus: '-', Equal: '=', BracketLeft: '[', BracketRight: ']', Backslash: '\\',
  Semicolon: ';', Quote: "'", Backquote: '`', Comma: ',', Period: '.', Slash: '/',
  IntlBackslash: 'intlbackslash', IntlRo: 'intlro', IntlYen: 'intlyen',
}

const NAMED = new Set([
  'space', 'enter', 'tab', 'escape', 'backspace', 'delete', 'insert', 'home', 'end',
  'pageup', 'pagedown', 'arrowleft', 'arrowright', 'arrowup', 'arrowdown',
  'capslock', 'contextmenu', 'pause', 'printscreen', 'scrolllock',
])

const NUMPAD = new Set([
  ...Array.from({ length: 10 }, (_, index) => `numpad${index}`),
  'numpadadd', 'numpadsubtract', 'numpadmultiply', 'numpaddivide', 'numpaddecimal',
  'numpadenter', 'numpadequal',
])

const FUNCTION = new Set(Array.from({ length: 24 }, (_, index) => `f${index + 1}`))

/** Every key a binding may name. Closed: an unknown code is not a chord. */
export function tokenForCode(code: string): string | null {
  if (!code) return null
  if (code in PUNCTUATION) return PUNCTUATION[code]
  if (code.length === 4 && code.startsWith('Key')) return code[3].toLowerCase()
  if (code.length === 6 && code.startsWith('Digit')) return code[5]
  const lowered = code.toLowerCase()
  if (NAMED.has(lowered) || NUMPAD.has(lowered) || FUNCTION.has(lowered)) return lowered
  return null
}

export type ChordSource = Pick<KeyboardEvent, 'code' | 'ctrlKey' | 'shiftKey' | 'altKey' | 'metaKey'>

/** The canonical chord for a keystroke, or '' when the key is not bindable. */
export function keyChord(event: ChordSource): string {
  const token = tokenForCode(event.code)
  if (!token) return ''
  const parts: string[] = []
  if (event.ctrlKey) parts.push('ctrl')
  if (event.shiftKey) parts.push('shift')
  if (event.altKey) parts.push('alt')
  if (event.metaKey) parts.push('meta')
  parts.push(token)
  return parts.join('+')
}

/** True when the keystroke is a modifier being pressed on its own. */
export function isModifierOnly(code: string): boolean {
  return /^(Control|Shift|Alt|Meta|OS)(Left|Right)?$/.test(code)
}

/** True for Tab/Shift+Tab focus traversal, excluding app-level modified chords. */
export function isFocusTraversalKey(
  event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'altKey' | 'metaKey'>,
): boolean {
  return event.key === 'Tab' && !event.ctrlKey && !event.altKey && !event.metaKey
}

/**
 * True for a chord AltGr produces on international layouts.
 *
 * Windows and X11 both synthesise Ctrl+Alt for AltGr, so `ctrl+alt+n` fires while a
 * German, French, Polish, Spanish or Nordic user types a character on their own
 * keyboard. No shipped preset uses Ctrl+Alt; one a user chooses is reported.
 */
export function altGrHazard(chord: string): boolean {
  const parts = new Set(chord.split('+').slice(0, -1))
  return parts.has('ctrl') && parts.has('alt') && !parts.has('meta')
}

const PRETTY: Record<string, string> = {
  arrowleft: '←', arrowright: '→', arrowup: '↑', arrowdown: '↓',
  pageup: 'PgUp', pagedown: 'PgDn', escape: 'Esc', space: 'Space',
  enter: 'Enter', tab: 'Tab', delete: 'Del', backspace: 'Backspace',
}

const MODIFIER_LABELS: Record<'win' | 'mac', Record<string, string>> = {
  win: { ctrl: 'Ctrl', shift: 'Shift', alt: 'Alt', meta: 'Win' },
  mac: { ctrl: '⌃', shift: '⇧', alt: '⌥', meta: '⌘' },
}

// The order modifiers are *drawn* in, which is not `MODIFIER_ORDER`, the order they
// are stored in. Storage uses one fixed order so a chord has exactly one spelling;
// a reader is shown their own platform's convention - Apple documents ⌃⌥⇧⌘, and
// Windows and Linux write Ctrl+Shift+Alt. Mirrored by `keychords._LABEL_ORDER`.
const LABEL_ORDER: Record<'win' | 'mac', readonly string[]> = {
  win: ['ctrl', 'shift', 'alt', 'meta'],
  mac: ['ctrl', 'alt', 'shift', 'meta'],
}

/** How one chord is spelled to a reader. */
export function chordLabel(chord: string, platform = 'win'): string {
  if (!chord) return ''
  const parts = chord.split('+')
  const key = parts[parts.length - 1]
  const held = new Set(parts.slice(0, -1))
  const family = platform === 'mac' ? 'mac' : 'win'
  const names = MODIFIER_LABELS[family]
  const modifiers = LABEL_ORDER[family].filter(name => held.has(name))
  const label = PRETTY[key] || (key.length === 1 || FUNCTION.has(key) ? key.toUpperCase() : key)
  const joiner = platform === 'mac' ? '' : '+'
  return [...modifiers.map(name => names[name] || name), label].join(joiner)
}

/** How a whole binding - one to three chords - is spelled. */
export function displayChord(sequence?: string, platform = 'win'): string {
  if (!sequence) return ''
  return sequence.trim().split(/\s+/).filter(Boolean)
    .map(chord => chordLabel(chord, platform)).join(' ')
}
