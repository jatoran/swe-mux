/** Wheel-to-horizontal scrolling for strips that only overflow sideways.
 *
 * A tab strip or action rail overflows on the x axis and never on the y, so a
 * plain wheel over one produces an event nothing consumes. Translating it to
 * `scrollLeft` is what the user means by "scroll this strip", and it saves
 * reaching for Shift, which is the only native way to scroll an x-overflowing
 * box with a wheel.
 */
export type WheelDeltas = { deltaX: number; deltaY: number; deltaMode?: number }

/** A wheel notch in `deltaMode: 'line'`, which Firefox reports instead of pixels. */
export const WHEEL_LINE_PX = 16

/** How far a wheel event should move a strip's `scrollLeft`; `0` to pass it through.
 *
 * Passed through when the strip does not overflow (there is nothing to scroll, and
 * consuming it would strand a wheel that some ancestor may want) or when the event
 * already carries horizontal intent — a trackpad swipe or Shift+wheel arrives as
 * `deltaX` and the browser scrolls it natively.
 */
export function horizontalWheelDelta(
  event: WheelDeltas,
  strip: { scrollWidth: number; clientWidth: number },
): number {
  if (strip.scrollWidth <= strip.clientWidth) return 0
  if (!event.deltaY || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return 0
  // deltaMode 1 counts lines and 2 counts pages; only mode 0 is already pixels.
  // Firefox reports `deltaY: 3` per notch in line mode, which would crawl.
  const scale =
    event.deltaMode === 1 ? WHEEL_LINE_PX : event.deltaMode === 2 ? strip.clientWidth : 1
  return event.deltaY * scale
}
