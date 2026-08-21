// Reading a harness's unsent draft back out of the terminal grid.
//
// The daemon cannot answer this. `composer_input.py` holds every byte written to
// the pty and deliberately keeps only a *count* from them, because reconstructing
// text from a write log would have to emulate a line editor and would still be
// wrong the moment history recall (`↑`) puts characters in the composer that mux
// never sent. The screen, by contrast, is the truth: whatever the CLI decided to
// draw is what the operator is looking at.
//
// So this module assembles text from cells, over the composer rectangles
// `terminalCaretPlacement.ts` measures. It is pure and snapshot-driven so the
// assembly rules are unit-testable without a browser, and it holds no harness
// geometry of its own — a harness is readable here exactly when it has a region
// reader there, and adding one is a measurement, never a name check.
//
// Two limits are structural rather than bugs to be fixed later, and both are
// stated here because callers have to present them honestly:
//
//   * **Only what is drawn.** A draft taller than the composer's box scrolls
//     inside it, and no harness marks the scrolled state on screen. What comes
//     back is the visible draft, which may be its tail.
//   * **Soft wraps are inferred.** A row whose content reaches the editor's wrap
//     column is joined to the next; anything shorter ends a line. A hard newline
//     landing exactly on the wrap column is therefore read as a wrap. That is the
//     only ambiguity a cell grid cannot resolve, and it costs one newline rather
//     than any characters.

// Explicit extensions: this module is unit-tested under node's type-stripping
// runner, which does not resolve extensionless specifiers.
import { composerClearKeys } from './harnessRegistry.ts'
import {
  cellAt, composerRegionForBackend, contentBoundary,
  type ComposerRegion, type TerminalCaretSnapshot,
} from './terminalCaretPlacement.ts'

/** One row of a composer, as text plus whether it continues into the next row. */
export interface ComposerLine {
  text: string
  wrapped: boolean
}

/**
 * The characters on one row of a composer region.
 *
 * Dim cells are discarded, which is what removes a harness's placeholder hint
 * (`Try "…"` on Claude — measured as SGR 2 in its own output — and Codex's greyed
 * prompt) without matching its wording, and what removes the ghost completion a
 * composer paints *after* the caret while a `/command` is being typed. Nothing an
 * operator types arrives dim, so this cannot eat real text. Zero-width cells are
 * the trailing halves of wide characters and are skipped rather than doubled.
 */
export function composerRowText(
  snapshot: TerminalCaretSnapshot,
  region: ComposerRegion,
  row: number,
): ComposerLine {
  const end = contentBoundary(snapshot, row, region.textStart, true, region.textEnd)
  let text = ''
  for (let column = region.textStart; column < end; column += 1) {
    const cell = cellAt(snapshot, row, column)
    if (!cell || cell.width === 0 || cell.dim) continue
    text += cell.chars || ' '
  }
  return { text, wrapped: end >= region.textEnd }
}

/** Every row of a region, in screen order, before soft wraps are rejoined. */
export function composerLines(
  snapshot: TerminalCaretSnapshot,
  region: ComposerRegion,
): ComposerLine[] {
  const lines: ComposerLine[] = []
  for (let row = region.firstRow; row <= region.lastRow; row += 1) {
    lines.push(composerRowText(snapshot, region, row))
  }
  return lines
}

/**
 * Rejoin soft wraps and drop the empty tail the box pads itself out with.
 *
 * Trailing blank rows are dropped, not kept as newlines: a composer draws its
 * region at whatever height it has room for, so the blanks under the last typed
 * line are the box, not the draft. Blank rows *between* text are kept — those are
 * newlines the operator inserted.
 */
export function joinComposerLines(lines: readonly ComposerLine[]): string {
  let last = lines.length - 1
  while (last >= 0 && !lines[last].text.trim() && !lines[last].wrapped) last -= 1
  let text = ''
  for (let index = 0; index <= last; index += 1) {
    const line = lines[index]
    text += line.text
    if (index < last && !line.wrapped) text += '\n'
  }
  return text
}

/**
 * The draft sitting unsent in this session's composer, or null when it cannot be
 * read at all.
 *
 * Null means "this harness's composer is not readable from here" — an unmeasured
 * harness, a shell, or a screen the region reader refused because what is under
 * the cursor is a dialog rather than a draft. An empty string means "read it, and
 * it is empty", which is a different answer and callers must not collapse the two.
 */
export function readComposerText(
  backend: string,
  snapshot: TerminalCaretSnapshot,
): string | null {
  const read = composerRegionForBackend(backend)
  if (!read) return null
  const region = read(snapshot)
  if (!region) return null
  return joinComposerLines(composerLines(snapshot, region))
}

/** Whether a Copy/Clear input control can do anything at all on this backend. */
export function composerIsReadable(backend: string): boolean {
  return !!composerRegionForBackend(backend)
}

/**
 * The keys that discard a whole composer.
 *
 * Read off the harness registry rather than decided here, because it is a per-CLI
 * fact and the browser's own guess at it was wrong: measured 2026-08-20 against
 * Claude Code v2.1.238 on a four-line draft, `Ctrl+U` kills **one line** and
 * leaves the rest standing, and a bare `Esc` does nothing to the draft at all.
 * Only a double `Esc` clears it. A rail button that sent `Ctrl+U` and called
 * itself "Clear input" was telling the truth about single-line drafts only.
 *
 * The daemon holds the mapping (`harness.py`) so that the rail and the daemon's
 * own unsent-input accounting cannot disagree about what a key did.
 */
export function composerClearSequence(backend: string): string {
  return composerClearKeys(backend)
}
