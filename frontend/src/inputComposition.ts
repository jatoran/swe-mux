/**
 * IME candidate selection belongs to the input method, including its final Enter.
 * keyCode 229 covers browsers whose commit key is no longer marked isComposing.
 * Keep this independent of UI language: Chinese input can be used in English UI.
 */
export type CompositionKey = {
  isComposing?: boolean
  keyCode?: number
  key?: string
}

export function isComposingKey(event: CompositionKey): boolean {
  return event.isComposing === true || event.keyCode === 229 || event.key === 'Process'
}
