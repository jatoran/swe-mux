// Which worktrees a click selects on Map, when the click carries Shift.
//
// One click checks one box. That is fine for three worktrees and it is the whole
// interaction cost of a bulk act for thirty, which is the case the bulk bar exists for.
// Shift is the usual answer: click one row, Shift-click another, and everything the
// reader can see between them takes the state the second click produced.
//
// Three rules, all of which the surface would otherwise get wrong:
//
//  * **The range is over the *visible* list.** Map has a search box, so the rows
//    between two clicks are not the rows between two entries in the inventory. Sweeping
//    the unfiltered order would select checkouts the reader had filtered away and cannot
//    see to un-select - the worst possible failure for a control whose next press
//    removes things.
//  * **A blocked checkout is still blocked.** The main tree, a locked one, and one with
//    a live session in it cannot be checked by hand, so a range passing over them must
//    step over them too. Shift is a faster way to press the same checkboxes, not a way
//    around what they refuse.
//  * **The range takes the state the click produced, and the anchor follows the click.**
//    Every press moves the anchor, Shift or not, so one sentence covers every case:
//    *from the last box you touched to this one, inclusive, becomes whatever this box
//    just became*. Shift-clicking an unchecked box selects through; Shift-clicking a
//    checked one un-selects back, which is how an overshoot is walked in. Pinning the
//    anchor to the last *plain* click instead reads fine until the reader overshoots
//    and Shift-clicks back: the box they land on is already checked, so the press
//    un-selects the near half of the range they were trying to shorten and keeps the
//    far half - the opposite of the correction they asked for.
//
// Explicit `.ts` extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import { normalizePath } from './gitWorktrees.ts'

/** Selected worktrees, keyed by normalized path - the shape Map's `selected` state has. */
export type Selection = Readonly<Record<string, true>>

/** A row as the range walk sees it: where it sits in the visible order, and whether its
 *  checkbox would accept a press at all. */
export type SelectableRow = { readonly path: string; readonly selectable: boolean }

export type SelectionClick = {
  /** The new selection. Referentially new only when something actually changed. */
  selected: Selection
  /** The origin for the *next* Shift-click - the row just clicked, normalized. */
  anchor: string
}

/**
 * Resolve one press of a selection checkbox.
 *
 * `rows` is the visible list in the order it is drawn. `anchor` is what a previous
 * plain click left behind; pass `''` when there is none. `extend` is the Shift key.
 *
 * A Shift-click with no usable anchor - none recorded, or one the search box has since
 * filtered away - is a plain click. Falling back is the only safe reading: the
 * alternative is to guess an origin, and a guessed range on this surface selects
 * checkouts the reader did not point at.
 */
export function applySelectionClick(
  selected: Selection,
  rows: readonly SelectableRow[],
  path: string,
  options: { readonly extend: boolean; readonly anchor: string },
): SelectionClick {
  const key = normalizePath(path)
  // The state the click produces on the row that was clicked, and therefore the state
  // the whole range takes. Shift-clicking a checked box un-selects the range.
  const wanted = !selected[key]
  const anchorKey = normalizePath(options.anchor)
  const index = rows.findIndex(row => normalizePath(row.path) === key)
  const anchorIndex = anchorKey ? rows.findIndex(row => normalizePath(row.path) === anchorKey) : -1
  const ranged = options.extend && index >= 0 && anchorIndex >= 0 && anchorIndex !== index

  // The clicked row is in the span unconditionally, whatever the walk says about it: a
  // press the browser has already applied to the checkbox but that leaves this state
  // untouched renders as a box that is checked and not selected.
  const span = ranged
    ? [
        key,
        ...rows
          .slice(Math.min(index, anchorIndex), Math.max(index, anchorIndex) + 1)
          .filter(row => row.selectable)
          .map(row => normalizePath(row.path)),
      ]
    : [key]

  const next: Record<string, true> = { ...selected }
  let changed = false
  for (const member of span) {
    if (wanted && !next[member]) { next[member] = true; changed = true }
    else if (!wanted && next[member]) { delete next[member]; changed = true }
  }
  // Every press is the next press's origin, so a run of Shift-clicks walks the list
  // rather than re-ranging from a point the reader has to remember.
  return { selected: changed ? next : selected, anchor: key }
}
