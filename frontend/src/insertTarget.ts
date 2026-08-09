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

export type TextSurfaceKind = 'note' | 'scratchpad' | 'file' | 'queue'

/** Stable identity shown by global input surfaces such as voice dictation. */
export type TextSurfaceIdentity = {
  id: string
  kind: TextSurfaceKind
  label: string
}

export type InsertTarget =
  | { kind: 'terminal'; sessionId: string; at: number }
  | { kind: 'editor'; editor: EditorHandle; surface?: TextSurfaceIdentity; at: number }

export type InsertDecision =
  | { kind: 'terminal'; sessionId: string }
  | { kind: 'editor'; editor: EditorHandle }
  | { kind: 'none' }

/** `terminalsOnly` refuses an editor outright rather than ranking it lower: a prompt
 *  template is written to be read by an agent, and landing one in the note or file the
 *  user happened to touch last is silent damage to a document, not a misplaced paste. */
export type InsertOptions = { terminalsOnly?: boolean }

let current: InsertTarget | null = null
const listeners = new Set<(target: InsertTarget | null) => void>()

function publish(next: InsertTarget | null): void {
  let same = current === next
  if (current?.kind === 'terminal' && next?.kind === 'terminal') {
    same = current.sessionId === next.sessionId
  } else if (current?.kind === 'editor' && next?.kind === 'editor') {
    same = current.editor === next.editor
      && current.surface?.id === next.surface?.id
      && current.surface?.kind === next.surface?.kind
      && current.surface?.label === next.surface?.label
  }
  current = next
  if (same) return
  for (const listener of listeners) listener(current)
}

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
  options: InsertOptions = {},
): InsertDecision {
  if (!options.terminalsOnly && target?.kind === 'editor' && target.editor.isConnected !== false) {
    return { kind: 'editor', editor: target.editor }
  }
  if (target?.kind === 'terminal') return { kind: 'terminal', sessionId: target.sessionId }
  if (fallbackSessionId) return { kind: 'terminal', sessionId: fallbackSessionId }
  return { kind: 'none' }
}

export function noteTerminalFocus(sessionId: string, at: number = Date.now()): void {
  if (!sessionId) return
  publish({ kind: 'terminal', sessionId, at })
}

export function noteEditorFocus(
  editor: EditorHandle,
  surface?: TextSurfaceIdentity,
  at: number = Date.now(),
): void {
  publish({ kind: 'editor', editor, surface, at })
}

/**
 * Claim an already-open editor for a one-shot navigation request without DOM
 * focus. Voice navigation must retarget Send/Append without opening the mobile
 * keyboard; a later pointer or keyboard focus event can override it normally.
 */
export function claimEditorInsertTarget(
  editor: EditorHandle | null,
  surface: TextSurfaceIdentity | undefined,
  token: number | undefined,
  consumed?: (token: number) => void,
): boolean {
  if(token===undefined||!editor||editor.isConnected===false||!surface)return false
  noteEditorFocus(editor,surface)
  consumed?.(token)
  return true
}

/** Forget an editor as it unmounts, so a stale handle cannot win the routing. */
export function forgetEditorFocus(editor: EditorHandle): void {
  if (current?.kind === 'editor' && current.editor === editor) publish(null)
}

export function currentInsertTarget(): InsertTarget | null {
  return current
}

/** React hosts subscribe once so a focus change can retarget an app-level surface. */
export function subscribeInsertTarget(listener: (target: InsertTarget | null) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/**
 * Insert text into the last-focused surface. Returns what received it, so the
 * caller can report "nothing focused" rather than silently dropping the text.
 */
export function insertIntoFocusedSurface(
  text: string,
  fallbackSessionId: string | null = null,
  options: InsertOptions = {},
): InsertDecision['kind'] {
  const decision = chooseInsertTarget(current, fallbackSessionId, options)
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
