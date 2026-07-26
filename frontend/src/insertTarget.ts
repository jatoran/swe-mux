// Where inserted text goes.
//
// The clipboard-history picker (and anything else that injects text) must land in
// whatever the user was last typing into, which can be a terminal or a Continuity
// markdown editor in another pane. Opening the picker takes focus, so the choice
// cannot be made from `document.activeElement` at insert time; each insertable
// surface reports its own focus here and the most recent one wins.
//
// The decision is a pure function so the routing is unit-testable without a DOM.

export type EditorHandle = {
  insertText: (text: string) => void
  /** DOM nodes expose this; a detached editor must never be inserted into. */
  isConnected?: boolean
}

export type InsertTarget =
  | { kind: 'terminal'; sessionId: string; at: number }
  | { kind: 'editor'; editor: EditorHandle; at: number }

export type InsertDecision =
  | { kind: 'terminal'; sessionId: string }
  | { kind: 'editor'; editor: EditorHandle }
  | { kind: 'none' }

let current: InsertTarget | null = null

/**
 * Resolve where text should go.
 *
 * A recorded editor target that has since detached is discarded rather than
 * inserted into; the focused terminal (when there is one) is the fallback, so a
 * closed note never swallows a paste.
 */
export function chooseInsertTarget(
  target: InsertTarget | null,
  fallbackSessionId: string | null,
): InsertDecision {
  if (target?.kind === 'editor' && target.editor.isConnected !== false) {
    return { kind: 'editor', editor: target.editor }
  }
  if (target?.kind === 'terminal') return { kind: 'terminal', sessionId: target.sessionId }
  if (fallbackSessionId) return { kind: 'terminal', sessionId: fallbackSessionId }
  return { kind: 'none' }
}

export function noteTerminalFocus(sessionId: string, at: number = Date.now()): void {
  if (!sessionId) return
  current = { kind: 'terminal', sessionId, at }
}

export function noteEditorFocus(editor: EditorHandle, at: number = Date.now()): void {
  current = { kind: 'editor', editor, at }
}

/** Forget an editor as it unmounts, so a stale handle cannot win the routing. */
export function forgetEditorFocus(editor: EditorHandle): void {
  if (current?.kind === 'editor' && current.editor === editor) current = null
}

export function currentInsertTarget(): InsertTarget | null {
  return current
}

/**
 * Insert text into the last-focused surface. Returns what received it, so the
 * caller can report "nothing focused" rather than silently dropping the text.
 */
export function insertIntoFocusedSurface(
  text: string,
  fallbackSessionId: string | null = null,
): InsertDecision['kind'] {
  const decision = chooseInsertTarget(current, fallbackSessionId)
  if (decision.kind === 'editor') {
    decision.editor.insertText(text)
    return 'editor'
  }
  if (decision.kind === 'terminal') {
    window.dispatchEvent(
      new CustomEvent('mux:terminal-action', {
        detail: { sessionId: decision.sessionId, action: 'insertText', text },
      }),
    )
    return 'terminal'
  }
  return 'none'
}
