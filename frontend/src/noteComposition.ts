/**
 * Recovering the text an IME composition is still holding when an editor is torn down.
 *
 * Continuity deliberately withholds composed text from the engine: its `beforeinput` handler
 * returns early while a composition is open, and the `input` handler only paints a preview of
 * the composing run. That run reaches the engine - and therefore our `continuity-change` and
 * the autosave queue behind it - only when `compositionend` folds it in. `destroy()` does not
 * fold it in, so whatever was composing when the element goes away is dropped before anything
 * downstream can see it: flushing the save queue on unmount faithfully commits everything the
 * editor handed us, and that word was never handed to us.
 *
 * This is not an exotic IME case. Android keyboards hold one composition open across ordinary
 * typing, so on a phone the composing run is simply "the word being typed" and closing the
 * notes drawer mid-sentence loses it every time.
 *
 * The textarea is the recovery point: it holds the whole document rather than a viewport
 * window, it carries the live composing run, and it is an exported shadow part. Continuity's
 * own composition commit reconciles the engine from exactly this value.
 *
 * Pure, and with no runtime import from the editor package, so the node type-stripping test
 * runner can load it: the element shape is restated structurally below and is assignable from
 * `ContinuityEditorElement`.
 */

export type CompositionHost = {
  /** Continuity's public read-only flag: is an IME composition open right now? */
  composing: boolean
  /** `unknown` so a real `ShadowRoot` (which answers `Element`) satisfies this; the textarea
   *  is recognised by a runtime check rather than by a cast that assumes the part exists. */
  shadowRoot: { querySelector(selectors: string): unknown } | null
  snapshot(): { text: string }
}

/** Continuity exports its textarea as a shadow part; this is the sanctioned handle on it. */
export const EDITOR_INPUT_PART = '[part="input"]'

/**
 * The visible document when an open composition has put it ahead of the engine, else null.
 *
 * Gated on `composing` rather than on a plain textarea-vs-engine comparison, because outside a
 * composition the engine is the authority and the textarea is merely its mirror: rescuing from
 * the mirror there would risk writing back a transiently stale reflection over good text. The
 * composing case is the one place the ordering is reversed and the textarea is ahead.
 *
 * Newlines are normalised the way `replaceValue` would normalise them, because this text goes
 * to the save queue directly rather than through the engine.
 */
export function uncommittedEditorText(element: CompositionHost | null | undefined): string | null {
  if (!element || !element.composing) return null
  let engineText: string
  try {
    // Throws once the element has been destroyed, which is not worth reporting: an editor that
    // is already gone has nothing left to rescue.
    engineText = element.snapshot().text
  } catch {
    return null
  }
  const input: unknown = element.shadowRoot?.querySelector(EDITOR_INPUT_PART)
  const value = (input as { value?: unknown } | null | undefined)?.value
  if (typeof value !== 'string') return null
  const visible = value.replace(/\r\n?/g, '\n')
  return visible === engineText ? null : visible
}
