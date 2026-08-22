// The bytes that put authored text into an agent's composer without submitting it.
//
// One builder for every path that inserts text a person wrote somewhere else - a
// prompt template, a skill, a clipboard entry, a dictated draft, a note selection -
// because they all face the same hazard and used to each reinvent the answer. The
// daemon's half of it is `composer_input.composer_insertion`; the two are kept in
// step by the harness registry rather than by matching constants, and
// `frontend/test/composerInsertion.test.ts` asserts the wire bytes both produce.
//
// Multi-line text travels as a bracketed paste with newlines as CR. That is what
// xterm writes for a real paste, and it is why an agent reads the newlines as
// content instead of as a run of submits.
//
// The paste wrapper does not protect the *first* character. Measured 2026-08-22
// against Codex v0.149.0 over a real pseudoterminal, with `PREVIOUSTEXT` sitting
// unsent in the composer:
//
//   * `ESC[200~A\rB\rC ESC[201~`     → a three-line draft, nothing submitted
//   * `ESC[200~\rA\rB\rC ESC[201~`   → **PREVIOUSTEXT submitted**, rest pasted
//   * the same body with a leading LF → the LF is dropped and the paste is
//     concatenated onto the standing draft
//
// Claude Code v2.1.240 keeps the draft on all three. So a leading newline run is
// lifted out of the paste and sent as the harness's own composer-newline key
// first, which is also exactly what an author who began a template with a newline
// meant: start below whatever is already there. Only a harness measured as
// needing it gets that treatment; everything else keeps the bytes mux has always
// sent.

// Explicit extension: this module is unit-tested under node's type-stripping
// runner, which does not resolve extensionless specifiers.
import { composerNewline, pasteLeadingNewlineSubmits } from './harnessRegistry.ts'

export const BRACKETED_PASTE_START = '\x1b[200~'
export const BRACKETED_PASTE_END = '\x1b[201~'

/** Wrap text as a bracketed paste, newlines as CR. The raw wrapper, for callers
 *  that have already decided the payload (an attachment path, a manual re-bracket
 *  of text xterm was going to send unwrapped). */
export function bracketedPaste(text: string): string {
  const normalized = text.replace(/\r\n?/g, '\n').replace(/\n/g, '\r')
  return `${BRACKETED_PASTE_START}${normalized}${BRACKETED_PASTE_END}`
}

/** An insertion split into the keys that must precede the paste and the text the
 *  paste carries. `body` keeps its `\n` newlines so a caller can hand it to
 *  xterm's own `paste`, which does the CR rewrite and the bracketing itself. */
export interface ComposerInsertionParts {
  leading: string
  body: string
}

/**
 * Split `text` into the leading newline keys and the body to paste.
 *
 * `leading` is empty unless this harness is measured as reading a paste's first
 * newline as Enter, so nothing changes for a harness that never had the problem.
 */
export function composerInsertionParts(text: string, backend?: string): ComposerInsertionParts {
  const normalized = text.replace(/\r\n?/g, '\n')
  const newlineKeys = composerNewline(backend)
  if (!pasteLeadingNewlineSubmits(backend) || !newlineKeys) {
    return { leading: '', body: normalized }
  }
  const body = normalized.replace(/^\n+/, '')
  return { leading: newlineKeys.repeat(normalized.length - body.length), body }
}

/**
 * The whole insertion as one write.
 *
 * For the callers that write raw bytes rather than driving a mounted xterm - the
 * daemon's `POST /api/sessions/{id}/input`. Measured as a single write: Codex
 * reads `ESC+CR` followed by a bracketed paste as "newline, then paste", keeping
 * the draft above it.
 */
export function composerInsertion(text: string, backend?: string): string {
  const { leading, body } = composerInsertionParts(text, backend)
  return body ? `${leading}${bracketedPaste(body)}` : leading
}
