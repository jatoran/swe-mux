// The one place that decides whether a keystroke belongs to swe-mux or to the pane.
//
// Two handlers see every key: xterm's `attachCustomKeyEventHandler`, which runs first
// and decides whether the byte reaches the PTY, and App's window listener, which runs
// after it and dispatches commands. With a flat chord map those two could each answer
// independently. With sequences they cannot: the terminal has no way to know that
// `p` is the second half of `leader p` rather than a letter the shell wants, and the
// sequence state lives in exactly one place by necessity.
//
// So the state lives here, App drives it, and the terminal only *asks*. The split is
// the important part:
//
//  - `claims(chord)` is a pure question. The terminal calls it to decide whether to
//    swallow the key, and calling it twice must be the same as calling it once.
//  - `advance(chord)` is the single mutation, called once per keydown by App.
//
// Getting that backwards - letting the terminal advance the machine too - would
// consume the first chord twice and make every second keystroke of a sequence
// mysterious, which is the class of bug this module's shape exists to prevent.

import {
  buildTrie, emptyKeymapState, optionsAt, step,
  type KeymapOutcome, type KeymapState, type ResolvedBindings, type TrieNode, type WhenFlags,
} from './keymap.ts'

let trie: TrieNode = buildTrie({})
let state: KeymapState = emptyKeymapState()
let flags: () => WhenFlags = () => ({})

export function installKeymap(bindings: ResolvedBindings, whenFlags: () => WhenFlags): void {
  trie = buildTrie(bindings)
  flags = whenFlags
  // A map that changed under an armed sequence leaves the pending chords meaning
  // something else, or nothing. Dropping them is the only answer that cannot fire
  // a command the user did not ask for.
  state = emptyKeymapState()
}

export function pendingChords(): string[] {
  return state.pending
}

// Whether the focused terminal has a selection, which is a `when` flag and is
// knowable only inside xterm. The pane writes it on every keydown, and xterm's
// handler always runs before App's window listener, so the value App reads a
// moment later is this keystroke's rather than the previous one's. That ordering
// is a fact about how the two handlers are registered, not a race: `keydown` on
// the terminal element bubbles to `window`, in that order, synchronously.
let selection = false

export function setTerminalSelection(value: boolean): void {
  selection = value
}

export function terminalSelection(): boolean {
  return selection
}

/**
 * Would swe-mux take this chord? Pure - safe to call from a render or a handler
 * that may not be the one that ends up dispatching.
 *
 * True while a sequence is armed regardless of the chord, because an armed
 * sequence swallows its own abandonment: forwarding the stray key would type a
 * character into a terminal the user believed was listening for a shortcut.
 */
export function claims(chord: string): boolean {
  if (state.pending.length) return true
  if (!chord) return false
  const outcome = step(trie, state, chord, flags()).outcome
  return outcome.kind === 'run' || outcome.kind === 'pending'
}

/** Advance the machine by one chord. Exactly one caller: App's window handler. */
export function advance(chord: string): KeymapOutcome {
  const result = step(trie, state, chord, flags())
  state = result.state
  return result.outcome
}

/** Abandon an armed sequence - Escape, a lost window, a keymap that changed. */
export function cancel(): void {
  state = emptyKeymapState()
}

/** What a reader can press next, for the which-key overlay. */
export function options(): ReturnType<typeof optionsAt> {
  return optionsAt(trie, state.pending)
}
