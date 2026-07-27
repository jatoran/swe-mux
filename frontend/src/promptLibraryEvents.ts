/** Local invalidation event for prompt-library views that keep their own list snapshot. */
export const PROMPT_LIBRARY_CHANGED_EVENT = 'mux:prompt-library-changed'

export function notifyPromptLibraryChanged(target: EventTarget = window): void {
  target.dispatchEvent(new Event(PROMPT_LIBRARY_CHANGED_EVENT))
}

export function subscribeToPromptLibraryChanges(
  listener: EventListener,
  target: EventTarget = window,
): () => void {
  target.addEventListener(PROMPT_LIBRARY_CHANGED_EVENT, listener)
  return () => target.removeEventListener(PROMPT_LIBRARY_CHANGED_EVENT, listener)
}
