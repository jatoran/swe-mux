// The overlay that makes a leader key learnable instead of a memory test.
//
// With ~200 commands behind one prefix, a leader without this is worse than no
// leader: the user presses it, nothing visible happens, and the only way to find
// out what the second keystroke could be is to read a settings table. Emacs'
// which-key, Zellij's status bar and LazyVim all solved it the same way, and the
// shape is not really optional.
//
// Two rules it keeps. It appears only after a short delay, so somebody who already
// knows `leader p n` never sees it flash - the overlay is for learning, and
// interrupting fluency to teach is the failure mode of every hint UI. And it never
// takes focus: the sequence is still being typed, and moving focus would end it.

import { useEffect, useState } from 'preact/hooks'
import type { TrieOption } from './keymap.ts'
import { chordLabel, displayChord } from './keys.ts'

/** How long a prefix is held before the overlay appears. Long enough that fluent
 *  use never draws it, short enough that hesitating gets help. */
export const WHICH_KEY_DELAY_MS = 450

type Props = {
  pending: string[]
  options: TrieOption[]
  platform: string
  /** Command id to human label, for the leaf rows. */
  labelFor: (commandId: string) => string
  /** Rendered immediately rather than after the delay. Tests, and the tour. */
  immediate?: boolean
}

/** The group heading a chord implies, when a chord leads to several bindings. */
function optionLabel(option: TrieOption, labelFor: (id: string) => string): string {
  if (option.command) return labelFor(option.command)
  return `${option.count} more…`
}

export function WhichKey({ pending, options, platform, labelFor, immediate }: Props) {
  const [visible, setVisible] = useState(!!immediate)
  useEffect(() => {
    if (immediate) { setVisible(true); return }
    if (!pending.length) { setVisible(false); return }
    const timer = window.setTimeout(() => setVisible(true), WHICH_KEY_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [pending.join(' '), immediate])

  if (!pending.length || !visible || !options.length) return null
  return <div class="which-key" role="status" aria-live="polite">
    <div class="which-key-prefix">
      <kbd>{displayChord(pending.join(' '), platform)}</kbd>
      <span>then…</span>
    </div>
    <ul class="which-key-options">
      {options.map(option => <li key={option.chord} class={option.command ? 'leaf' : 'group'}>
        <kbd>{chordLabel(option.chord, platform)}</kbd>
        <span>{optionLabel(option, labelFor)}</span>
      </li>)}
    </ul>
    <p class="which-key-escape">Escape cancels.</p>
  </div>
}
