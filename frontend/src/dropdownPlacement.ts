// Explicit extensions: the node test runner imports this module directly and resolves no
// extensionless specifiers, the same reason `dismissStack.ts` and `serverClock.ts` are
// spelled out at their call sites.
import { railOverlayView, watchRailOverlayPlacement } from './railOverlayPlacement.ts'
import type { RailOverlayView } from './railOverflow.ts'

/**
 * Where a dropdown's list goes.
 *
 * Two decisions are baked in here and are worth stating, because they are what make one
 * component safe to drop into twenty different surfaces.
 *
 * **The list is portalled to `document.body` and positioned `fixed`.** Native `<select>` popups
 * are painted by the platform and are clipped by nothing; a replacement that renders inside its
 * own form is clipped by every `overflow:auto` panel in the app — the Settings scroller, the
 * drawer, a modal body, the Git map — and a dropdown near the bottom of one of those would show
 * two rows and stop. Escaping to the body also disposes of the transformed-ancestor trap
 * `railOverlayPlacement.ts` documents: a rail overlay is mounted *inside* `.terminal-surface`,
 * which the soft keyboard translates, so its `position:fixed` resolves against that transform.
 * Nothing between the body and the list is transformable, so `fixedContainingBlock` has no work
 * to do here and is deliberately not called. The other half of that module — what "visible"
 * means with a keyboard up — applies exactly as it does there, so {@link railOverlayView} and
 * {@link watchRailOverlayPlacement} are reused rather than reimplemented.
 *
 * **It opens below and flips above only when below cannot hold it.** A select opens where the
 * eye already is. Flipping is measured against the visual viewport, so with the keyboard up a
 * dropdown near the fold flips instead of unrolling behind the keys.
 */

/** Smallest a list may be before flipping is preferable to squeezing. */
export const DROPDOWN_MIN_HEIGHT_PX = 120
/** Tallest a list may grow on a roomy screen, before the viewport clamp. */
export const DROPDOWN_MAX_HEIGHT_PX = 380
/** Most of the visible height a list may take, so the control it belongs to stays on screen. */
export const DROPDOWN_MAX_HEIGHT_RATIO = 0.6
/** Narrowest a list may be, however narrow its trigger. */
export const DROPDOWN_MIN_WIDTH_PX = 160
const DROPDOWN_MARGIN_PX = 6
const DROPDOWN_GAP_PX = 2

/** Standard clamp, with the minimum winning a range too narrow to hold the value. */
const clamp = (value: number, minimum: number, maximum: number): number =>
  Math.min(Math.max(value, minimum), Math.max(minimum, maximum))

/** The trigger, in layout-viewport coordinates. */
export interface DropdownAnchor { left: number; right: number; top: number; bottom: number }

/**
 * The list's box, as edges rather than CSS insets.
 *
 * `edge` is the panel's *top* when placed below and its *bottom* when placed above, which is
 * what lets an above-placed list hug its trigger and grow upward only as far as its content
 * needs. Converting an edge to the inset a `position:fixed` element wants is
 * {@link dropdownCss}'s job, and keeping the two apart is what makes the geometry testable
 * without a window.
 */
export interface DropdownBox {
  left: number
  width: number
  maxHeight: number
  placement: 'below' | 'above'
  edge: number
}

export interface DropdownPlacementOptions {
  /**
   * Cap the list's width. Unset it matches the trigger, which is what a native select's popup
   * does and what makes the two read as one control; a caller sets this only when its trigger
   * is far wider than its content deserves.
   */
  maxWidth?: number
}

/**
 * Place a dropdown list against its trigger inside what is visible.
 *
 * The list is the trigger's width, floored so a tiny toolbar control still gets a readable
 * list and ceilinged by what is on screen. Left alignment follows the trigger and is clamped
 * into view, so a control at the right edge of a narrow screen gets a list that is fully
 * readable rather than one hanging off it.
 */
export function dropdownBox(
  anchor: DropdownAnchor,
  view: RailOverlayView,
  { maxWidth = Number.POSITIVE_INFINITY }: DropdownPlacementOptions = {},
): DropdownBox {
  const viewRight = view.left + view.width
  const viewBottom = view.top + view.height
  const room = Math.max(0, view.width - DROPDOWN_MARGIN_PX * 2)
  const width = Math.min(
    Math.max(anchor.right - anchor.left, Math.min(DROPDOWN_MIN_WIDTH_PX, room)),
    Math.min(maxWidth, room),
  )
  const left = clamp(anchor.left, view.left + DROPDOWN_MARGIN_PX, viewRight - DROPDOWN_MARGIN_PX - width)

  const cap = Math.min(DROPDOWN_MAX_HEIGHT_PX, Math.round(view.height * DROPDOWN_MAX_HEIGHT_RATIO))
  const below = viewBottom - DROPDOWN_MARGIN_PX - (anchor.bottom + DROPDOWN_GAP_PX)
  const above = anchor.top - DROPDOWN_GAP_PX - (view.top + DROPDOWN_MARGIN_PX)
  // Below unless below cannot hold a usable list *and* above can hold more. A short view where
  // neither side reaches the minimum keeps the roomier one rather than flipping to a worse
  // place on a technicality, and the height floor then guarantees a scrollable list either way.
  const placement: DropdownBox['placement'] = below >= Math.min(cap, DROPDOWN_MIN_HEIGHT_PX) || below >= above
    ? 'below'
    : 'above'
  const space = placement === 'below' ? below : above
  return {
    left: Math.round(left),
    width: Math.round(width),
    maxHeight: Math.round(Math.max(DROPDOWN_MIN_HEIGHT_PX, Math.min(cap, space))),
    placement,
    edge: Math.round(placement === 'below' ? anchor.bottom + DROPDOWN_GAP_PX : anchor.top - DROPDOWN_GAP_PX),
  }
}

/**
 * The box as CSS for a `position:fixed` element mounted on the body.
 *
 * Keyed by real CSS property names rather than camelCase, because the component writes these
 * straight onto the node with `style.setProperty` instead of handing them to the renderer as
 * a style object — see `Dropdown.tsx` for why the round-trip through state had to go.
 *
 * `viewportHeight` is the *layout* viewport (`window.innerHeight`), because that is what a
 * fixed element's `bottom` is measured against — the visual viewport only decides where the
 * box should be, which {@link dropdownBox} has already done.
 */
export function dropdownCss(box: DropdownBox, viewportHeight: number): Record<string, string> {
  const common = { left: `${box.left}px`, width: `${box.width}px`, 'max-height': `${box.maxHeight}px` }
  return box.placement === 'below'
    ? { ...common, top: `${box.edge}px` }
    : { ...common, bottom: `${Math.round(viewportHeight - box.edge)}px` }
}

/** Measure a live trigger and produce the list's inline style. */
export function dropdownStyle(trigger: HTMLElement, options: DropdownPlacementOptions = {}): Record<string, string> {
  const rect = trigger.getBoundingClientRect()
  const box = dropdownBox(rect, railOverlayView(), options)
  return dropdownCss(box, typeof window === 'undefined' ? 0 : window.innerHeight)
}

/** Re-place on everything that can move a list out from under its trigger. */
export const watchDropdownPlacement = watchRailOverlayPlacement
