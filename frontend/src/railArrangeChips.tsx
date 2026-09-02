import type { ComponentChildren, VNode } from 'preact'

// The insertion caret, spliced into a row's rendered chips.
//
// Shared by the two surfaces a chip can be dropped on - the rail's own scrolling rows and
// the arrange panel's wrap grids - because the caret is the only thing either of them draws
// differently while a drag is running, and two copies of "where does the marker go" is two
// places for the off-by-one to live.
//
// The dragged chip stays in place and dims rather than being lifted out of the flow. Removing
// it would reflow the row under the finger at the exact moment the finger is aiming at it,
// which is why the caret's position is counted over every chip including the dragged one
// (`caretPosition` in `railArrange.ts`) instead of over the peers the hit test measures.

/** `at` is a position among `chips`, `0..chips.length`, or null for no caret. */
export function arrangeChildren(chips: readonly VNode[], at: number | null): ComponentChildren[] {
  if (at === null) return [...chips]
  const bounded = Math.max(0, Math.min(Math.floor(at), chips.length))
  const caret = <span key="rail-arrange-caret" class="rail-arrange-caret" aria-hidden="true" />
  return [...chips.slice(0, bounded), caret, ...chips.slice(bounded)]
}
