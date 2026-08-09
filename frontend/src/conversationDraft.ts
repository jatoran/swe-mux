/**
 * The dictation draft is an append log of heard utterances, not a plain string.
 *
 * `undo` has to take back exactly the last thing that was recognized, and a typed
 * correction has to survive the next utterance landing on top of it — neither is
 * expressible once the phrases have been concatenated, so the segments are kept and
 * the visible text is derived from them. `text` is stored rather than re-derived on
 * read because a typed segment keeps its raw whitespace (deriving a trimmed join on
 * every keystroke would eat the space the user just pressed).
 */
export type Draft = { segments: string[]; text: string }

export const EMPTY_DRAFT: Draft = { segments: [], text: '' }

/** Phrases join with a single space; a typed segment's trailing run is absorbed by it. */
function joined(segments: string[]): string {
  return segments.reduce((accumulated, segment) => (
    accumulated.trim() ? `${accumulated.replace(/\s+$/, '')} ${segment}` : segment
  ), '')
}

/** One transcribed utterance. Blank recognitions (silence, a bare wake word) change nothing. */
export function appendUtterance(draft: Draft, value: string): Draft {
  const segment = value.trim()
  if (!segment) return draft
  const segments = [...draft.segments, segment]
  return { segments, text: joined(segments) }
}

/**
 * A typed edit flattens the log to one segment. The user has rewritten text that no
 * longer maps onto what was heard, so per-phrase undo would start removing wording
 * they can no longer see. A later utterance still appends as its own segment, which
 * is what keeps the correction alive through the rest of the dictation.
 */
export function editDraft(text: string): Draft {
  return text ? { segments: [text], text } : EMPTY_DRAFT
}

export function undoUtterance(draft: Draft): Draft {
  const segments = draft.segments.slice(0, -1)
  return segments.length ? { segments, text: joined(segments) } : EMPTY_DRAFT
}

export function clearDraft(): Draft {
  return EMPTY_DRAFT
}
