// Dispatching keystrokes against a resolved keymap: the trie, the sequence state
// machine, and the `when` evaluator.
//
// The daemon resolves rules into `sequence -> [{command, when}]` for THIS host
// (`routes/settings._keybindings_payload`), so nothing here decides policy. What
// this module owns is the part a browser has to own: remembering that a leader was
// pressed, deciding whether the next keystroke continues a sequence or abandons it,
// and answering "does this rule's `when` hold right now".
//
// The prefix engine is what makes the whole design work. 200 commands need one
// interceptable chord rather than 200, because every keystroke after the leader is
// a plain keypress no browser and no window manager competes for. That asymmetry is
// deliberate and is asserted on the Python side too: only a binding's FIRST chord is
// judged against the host.

export type WhenFlags = Record<string, boolean>

export type BindingEntry = { command: string; when: string }
export type ResolvedBindings = Record<string, BindingEntry[]>

export type KeymapState = {
  /** Chords typed so far in an unfinished sequence; empty when nothing is armed. */
  pending: string[]
}

export const emptyKeymapState = (): KeymapState => ({ pending: [] })

export type KeymapOutcome =
  | { kind: 'idle' }
  /** The chord began or continued a sequence; swallow it and show the overlay. */
  | { kind: 'pending'; pending: string[]; options: TrieOption[] }
  | { kind: 'run'; command: string; sequence: string }
  /** A sequence was armed and this chord does not continue it. Swallowed, not passed
   *  through: forwarding it would type a stray character into a terminal that the
   *  user believed was listening for the second half of a shortcut. */
  | { kind: 'abandon'; pending: string[]; chord: string }

export type TrieNode = {
  /** Bindings that fire exactly here, most specific (`when`-carrying) last. */
  entries: BindingEntry[]
  children: Map<string, TrieNode>
}

export type TrieOption = {
  chord: string
  /** The command a single further chord would run, when there is exactly one. */
  command: string | null
  /** How many bindings live below this chord. */
  count: number
}

const node = (): TrieNode => ({ entries: [], children: new Map() })

/** Build the dispatch trie from the daemon's resolved map. */
export function buildTrie(bindings: ResolvedBindings): TrieNode {
  const root = node()
  for (const [sequence, entries] of Object.entries(bindings)) {
    let cursor = root
    for (const chord of sequence.split(' ')) {
      let next = cursor.children.get(chord)
      if (!next) { next = node(); cursor.children.set(chord, next) }
      cursor = next
    }
    cursor.entries = entries
  }
  return root
}

function descend(root: TrieNode, chords: string[]): TrieNode | null {
  let cursor: TrieNode | undefined = root
  for (const chord of chords) {
    cursor = cursor?.children.get(chord)
    if (!cursor) return null
  }
  return cursor ?? null
}

function countBelow(target: TrieNode): number {
  let total = target.entries.length ? 1 : 0
  for (const child of target.children.values()) total += countBelow(child)
  return total
}

/** What a reader can press next from here, for the which-key overlay. */
export function optionsAt(root: TrieNode, pending: string[]): TrieOption[] {
  const cursor = descend(root, pending)
  if (!cursor) return []
  return [...cursor.children.entries()]
    .map(([chord, child]) => ({
      chord,
      command: child.children.size === 0 && child.entries.length ? child.entries[0].command : null,
      count: countBelow(child),
    }))
    .sort((left, right) => left.chord.localeCompare(right.chord))
}

/**
 * Evaluate a `when` clause.
 *
 * The grammar is a `&&`-joined list of optionally `!`-negated flags: no `||`, no
 * parentheses, no comparisons. That is deliberate and matches `keybindings.py`'s
 * validator - it is a total function over a closed flag set, cheap enough to run on
 * every keystroke, and it cannot grow into a second expression language nobody can
 * check. An unknown flag reads as false, which fails closed.
 */
export function whenHolds(expression: string, flags: WhenFlags): boolean {
  if (!expression) return true
  return expression.split('&&').every(term => {
    const trimmed = term.trim()
    if (!trimmed) return false
    const negated = trimmed.startsWith('!')
    const flag = negated ? trimmed.slice(1) : trimmed
    return negated ? !flags[flag] : !!flags[flag]
  })
}

/** The binding that applies here, preferring the most specific `when` that holds. */
export function pickEntry(entries: BindingEntry[], flags: WhenFlags): BindingEntry | null {
  for (let index = entries.length - 1; index >= 0; index--) {
    if (whenHolds(entries[index].when, flags)) return entries[index]
  }
  return null
}

/**
 * Advance the state machine by one chord.
 *
 * A node that has children *and* a binding resolves as a prefix: the daemon already
 * drops such leaves and reports them, so reaching one here means a stale payload,
 * and arming is the reading that loses nothing.
 */
export function step(
  root: TrieNode,
  state: KeymapState,
  chord: string,
  flags: WhenFlags,
): { state: KeymapState; outcome: KeymapOutcome } {
  if (!chord) return { state, outcome: { kind: 'idle' } }
  const attempt = [...state.pending, chord]
  const cursor = descend(root, attempt)
  if (!cursor) {
    if (state.pending.length) {
      return {
        state: emptyKeymapState(),
        outcome: { kind: 'abandon', pending: state.pending, chord },
      }
    }
    return { state, outcome: { kind: 'idle' } }
  }
  if (cursor.children.size) {
    return {
      state: { pending: attempt },
      outcome: { kind: 'pending', pending: attempt, options: optionsAt(root, attempt) },
    }
  }
  const entry = pickEntry(cursor.entries, flags)
  if (!entry) {
    // The chord exists but every binding on it is scoped away right now. With a
    // sequence armed that is still a swallow (see `abandon`); at the top level it
    // belongs to whatever is focused.
    return state.pending.length
      ? { state: emptyKeymapState(), outcome: { kind: 'abandon', pending: state.pending, chord } }
      : { state, outcome: { kind: 'idle' } }
  }
  return {
    state: emptyKeymapState(),
    outcome: { kind: 'run', command: entry.command, sequence: attempt.join(' ') },
  }
}

/** The binding shown beside a command in menus and tooltips. */
export function bindingFor(commandId: string, bindings: ResolvedBindings): string | undefined {
  let best: string | undefined
  for (const [sequence, entries] of Object.entries(bindings)) {
    if (!entries.some(entry => entry.command === commandId)) continue
    // Prefer the shortest route, then the alphabetically first, so the hint a user
    // sees is stable across reloads rather than dependent on object order.
    if (
      best === undefined
      || sequence.split(' ').length < best.split(' ').length
      || (sequence.split(' ').length === best.split(' ').length && sequence < best)
    ) best = sequence
  }
  return best
}

/** Every command the map can reach, for coverage checks and the Settings list. */
export function boundCommands(bindings: ResolvedBindings): Set<string> {
  const found = new Set<string>()
  for (const entries of Object.values(bindings)) {
    for (const entry of entries) found.add(entry.command)
  }
  return found
}
