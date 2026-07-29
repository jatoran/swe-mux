// Who owns the pointer while a drag is running.
//
// The mobile gesture recognizer (`mobileGestures.ts`) and the pointer-drag contract
// (`workspace-layout.md` § Pointer drag contract) read the same finger. Dragging a drawer
// tab along the strip *is* a single-finger horizontal swipe as far as the recognizer is
// concerned, so rearranging tabs also fired whatever `swipe_left`/`swipe_right` are bound
// to. Coordinates cannot settle that — the two are the same motion over the same pixels —
// so instead of the gesture layer guessing, a drag states that it owns the pointer and the
// recognizer stands down for as long as that holds.
//
// Two details the shape has to carry:
//
// - Claims are counted, not a boolean. Drags are cancelled from several directions (pointer
//   cancel, lost capture, blur, Escape), and a second drag starting while a first is still
//   unwinding must not hand the pointer back early.
// - Every claim also bumps a generation, so a touch sequence can ask after the fact whether
//   a drag ran at any point during it. Pointer events are dispatched before their touch
//   counterparts, so the `pointerup` that ends a drag — and releases its claim — lands
//   *before* the `touchend` that would otherwise classify the very same motion as a swipe.
//   A live boolean read at `touchend` would therefore always say "no drag", which is the
//   bug this module exists to close.
//
// DOM-free and process-wide, like the single window it describes.

let activeClaims = 0
let claimsStarted = 0

/** A point in time to compare against — "no drag had started as of here". */
export type PointerDragClaimMark = { claimsStarted: number }

/** Claim the pointer for a drag. The returned release is idempotent. */
export function claimPointerDrag(): () => void {
  activeClaims += 1
  claimsStarted += 1
  let released = false
  return () => {
    if (released) return
    released = true
    activeClaims -= 1
  }
}

/** Mark taken when a touch sequence begins, to be handed back to `pointerDragOwnsPointer`. */
export function markPointerDragClaims(): PointerDragClaimMark {
  return { claimsStarted }
}

/**
 * Whether a drag owns the pointer: one is running now, or — given the mark taken when the
 * current touch sequence started — one ran and finished inside that sequence.
 */
export function pointerDragOwnsPointer(since?: PointerDragClaimMark): boolean {
  if (activeClaims > 0) return true
  return since !== undefined && since.claimsStarted !== claimsStarted
}
