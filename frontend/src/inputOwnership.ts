/** Client half of terminal input ownership.
 *
 * The daemon lets exactly one connection write to a session (see the backend's
 * terminal_arbitration for why, and for the rules). This module holds the pane's
 * belief about that ownership and the decisions that belief drives, kept pure so the
 * races that made multi-device typing unreliable can be tested without a socket.
 *
 * Two of those races are the reason this file exists:
 *
 * * Ownership notifications can overtake each other. Every frame carries the daemon's
 *   transfer epoch, and an older epoch describes a world that has already been
 *   replaced, so it is discarded rather than applied.
 * * `document.activeElement` does not clear when a window is minimized. A pane that
 *   re-claimed input purely because its terminal still held DOM focus therefore stole
 *   the keyboard back from the phone the user had walked away with, forever. Passive
 *   re-claims now require the document to be visible *and* the window focused.
 */

export type ClaimReason = 'gesture' | 'passive'

/** How long after a real interaction a focus event still counts as the user's intent
 *  rather than as the pane restoring its own focus (attach, tab switch, overlay close). */
export const GESTURE_WINDOW_MS = 1500

export type OwnershipView = {
  owns: boolean
  /** Daemon transfer counter; -1 before the first ownership frame arrives. */
  epoch: number
  /** Label of the device that holds input, when it is not this one. */
  ownerDevice: string | null
  /** Daemon's reason for the most recent refusal, or null while this pane owns input. */
  denied: string | null
}

export const UNOWNED: OwnershipView = { owns: false, epoch: -1, ownerDevice: null, denied: null }

export type OwnerFrame = {
  active?: boolean
  epoch?: number
  reason?: string
  owner_device?: string | null
}

export type RejectedFrame = {
  epoch?: number
  owner_device?: string | null
  data?: string
  broadcast?: boolean
  retry?: boolean
}

function nextEpoch(current: OwnershipView, epoch: unknown): number | null {
  const value = typeof epoch === 'number' ? epoch : current.epoch
  return value < current.epoch ? null : value
}

/** Apply an `input_owner` frame, ignoring one that lost a race with a newer transfer. */
export function applyOwnerFrame(current: OwnershipView, frame: OwnerFrame): OwnershipView {
  const epoch = nextEpoch(current, frame.epoch)
  if (epoch === null) return current
  const owns = frame.active === true
  return {
    owns,
    epoch,
    ownerDevice: frame.owner_device ?? null,
    denied: owns ? null : (frame.reason ?? null),
  }
}

/** Apply an `input_rejected` frame: proof this pane's belief was stale. */
export function applyRejectedFrame(current: OwnershipView, frame: RejectedFrame): OwnershipView {
  const epoch = nextEpoch(current, frame.epoch)
  if (epoch === null) return current
  return {
    owns: false,
    epoch,
    ownerDevice: frame.owner_device ?? null,
    denied: 'input_rejected',
  }
}

/** Apply `input_owner_released`: the owning device detached and nobody holds input. */
export function applyOwnerReleased(current: OwnershipView, epoch: unknown): OwnershipView {
  const next = nextEpoch(current, epoch)
  // Only the owner's own detach publishes this, so a pane that believes it owns input
  // is looking at a frame about some earlier owner and must keep what it has.
  if (next === null || current.owns) return current
  return { owns: false, epoch: next, ownerDevice: null, denied: null }
}

/** Whether a displaced pane may take input back on its own.
 *
 * Only a pane the user can actually see and has actually focused. A minimized window
 * keeps DOM focus inside its terminal and would otherwise re-claim forever. */
export function shouldReclaimAfterDisplacement(input: {
  focusInHost: boolean
  documentHidden: boolean
  windowFocused: boolean
}): boolean {
  return input.focusInHost && !input.documentHidden && input.windowFocused
}

/** Classify a focus event. Programmatic focus (attach, tab switch, closing an overlay)
 *  is not the user asking for the keyboard, so it may not displace another device. */
export function claimReasonForFocus(lastInteractionAt: number | null, now: number): ClaimReason {
  if (lastInteractionAt === null) return 'passive'
  return now - lastInteractionAt <= GESTURE_WINDOW_MS ? 'gesture' : 'passive'
}

/** Whether rejected keystrokes should be re-sent after re-claiming input.
 *
 * A frame that was already a retry is not retried again: at that point the other
 * device is genuinely holding the keyboard, and the pane says so instead of looping. */
export function shouldReplayRejectedInput(frame: RejectedFrame): boolean {
  return frame.retry !== true && typeof frame.data === 'string' && frame.data.length > 0
}

export function terminalDeviceLabel(isMobile: boolean): string {
  return isMobile ? 'mobile' : 'desktop'
}

/** Text for the strip a non-owning pane shows instead of pretending to accept input. */
export function inputOwnerNotice(view: OwnershipView): string | null {
  if (view.owns || !view.ownerDevice) return null
  return `Input active on ${view.ownerDevice}`
}
