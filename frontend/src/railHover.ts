/**
 * The hover-only desktop rail: when the mode applies, and whether the rail is showing.
 *
 * With `rail_hover_desktop` on, the Action rail leaves the pane's flow and lies over the
 * bottom of the terminal instead, hidden until the pointer reaches the strip it would have
 * occupied. The terminal keeps those rows; nothing about the PTY grid moves when the rail
 * appears, because an overlay is stacked rather than placed.
 *
 * This module owns the *decision* and nothing else. Every input is a plain reading
 * `TerminalPane` takes off the DOM - where the pointer is, what it is over, whether one of
 * the rail's own panels is open - so the rule can be pinned in the unit suite, and the
 * pane's job is only to re-read at the moments any of those inputs can change.
 *
 * Two rules the decision turns on, both of which are the whole usability of the feature:
 *
 *  * **The reveal zone is the rail's own footprint.** Hovering where the rail would be is
 *    what brings it, and once it is up the pointer is by construction inside it, so nothing
 *    flickers at the boundary. A floor keeps a rail that is momentarily zero-height
 *    (mid-mount, or emptied by a backend filter) reachable at all.
 *  * **Engagement beats position.** A rail that hid because the pointer went up into its
 *    complete-row popover, a drop-up, a pad's dial, or the arrange panel would be pulling
 *    the floor out from under the very control the operator just opened. So an open rail
 *    panel, arrange mode, and the rail's own context menu each hold it up wherever the
 *    pointer is, and the pointer being *over* one of those overlays counts as being over
 *    the rail. Keyboard focus inside the rail is the same rule expressed in CSS
 *    (`:focus-within`), so a Tab into the rail shows it without this module's help.
 */

import type { SettingsProfile } from './deviceSettings.ts'

/**
 * The narrowest reveal zone. A rail is never shorter than a chip row, so this is only the
 * answer for a rail that has no height yet; it exists so the zone can never be zero.
 */
export const RAIL_HOVER_ZONE_MIN_PX = 24

/**
 * Everything an overlay of the rail's may be drawn in. A pointer over any of these is a
 * pointer on the rail: the popover and the arrange panel are descendants of the rail
 * element, the drop-ups are siblings inside the terminal surface, the pad dial is portalled
 * to the body, and the context menu is drawn by the pane.
 */
export const RAIL_HOVER_OVERLAY_SELECTOR =
  '.terminal-action-rail, .rail-dropup, .rail-pad-dial, .rail-overflow-popover, .rail-arrange, .terminal-menu'
/**
 * The panels that hold the rail up while they are *open*, regardless of the pointer. Two
 * selectors because they live in two places: the complete-row popover is inside the rail
 * element, and a standing pad dial is portalled to the body.
 */
export const RAIL_HOVER_STANDING_IN_RAIL_SELECTOR = '.rail-overflow-popover'
export const RAIL_HOVER_STANDING_IN_BODY_SELECTOR = '.rail-pad-dial-standing'

/**
 * Controls that live in the reveal zone without being the rail: the jump-to-latest and
 * peek chips sit in the terminal's bottom-right corner, which is exactly where the rail
 * appears. Revealing the rail over one of them would cover the control the pointer is on,
 * so a pointer on one of these never reveals.
 */
export const RAIL_HOVER_TERMINAL_CONTROL_SELECTOR = '.terminal-jump-latest, .terminal-peek-top'

export interface RailHoverScope {
  /** The per-device master switch (`rail_enabled_*`) for this device class. */
  railOn: boolean
  /** `rail_hover_desktop`. */
  hoverSetting: boolean
  /** The device class this window resolves to. */
  profile: SettingsProfile
  /** `(hover: hover)`. A touch tablet at desktop width has nothing to hover with, and a rail
   *  that only appears on hover would be a rail it could never reach. */
  hoverCapable: boolean
}

/** Whether the hover-only rail applies to this pane at all. Otherwise the rail is in flow. */
export function railHoverApplies(scope: RailHoverScope): boolean {
  return scope.railOn && scope.hoverSetting && scope.profile === 'desktop' && scope.hoverCapable
}

export interface RailHoverBox {
  top: number
  bottom: number
  left: number
  right: number
}

export interface RailHoverReading {
  /** The last pointer position over the terminal surface, or null once it has left. */
  pointer: { x: number; y: number } | null
  /** The terminal surface's box, in the same coordinates as the pointer. */
  surface: RailHoverBox
  /** The rail's laid-out height. Read from layout rather than its rect, because a hidden
   *  rail is translated out of its own box. */
  railHeight: number
  /** The pointer's target is the rail or one of its overlays (`RAIL_HOVER_OVERLAY_SELECTOR`). */
  pointerOnRail: boolean
  /** The pointer's target is a terminal control that shares the zone
   *  (`RAIL_HOVER_TERMINAL_CONTROL_SELECTOR`). */
  pointerOnTerminalControl: boolean
  /** A button is held with the pointer off the rail: a selection drag or a pane drag
   *  passing through the zone, which is not a request for the rail. */
  dragging: boolean
  /** A rail panel is open, the rail is arranging, or its context menu is up. */
  engaged: boolean
}

/** The height of the strip along the surface's bottom edge that reveals the rail. */
export function railHoverZonePx(railHeight: number): number {
  return Math.max(RAIL_HOVER_ZONE_MIN_PX, railHeight)
}

/** Whether the hover-only rail is showing for this reading. */
export function railHoverShown(reading: RailHoverReading): boolean {
  if (reading.engaged || reading.pointerOnRail) return true
  if (!reading.pointer || reading.dragging || reading.pointerOnTerminalControl) return false
  const { x, y } = reading.pointer
  const { surface } = reading
  if (x < surface.left || x > surface.right || y > surface.bottom || y < surface.top) return false
  return y >= surface.bottom - railHoverZonePx(reading.railHeight)
}
