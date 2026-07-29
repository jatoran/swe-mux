// Tells the daemon which device the human is actually at, so it can route
// notifications around it (see src/swe_mux/device_presence.py for the rules).
//
// This rides the `/events` socket rather than the push-presence endpoint for two
// reasons: every client holds that socket whether or not it can receive Web Push
// (the Windows desktop shell is a WebView that cannot subscribe, so it reported
// nothing at all), and the socket closing is itself the signal that this device
// is gone.
//
// Interaction is reported as an *age*, never a timestamp: a phone whose clock is
// minutes off would otherwise look permanently active and permanently silence
// itself.
import { currentProfile } from './deviceSettings.ts'

export interface PresenceFrame {
  type: 'presence'
  profile: string
  visible: boolean
  focused: boolean
  /** Seconds since the last pointer/key event here, or null if there has been none. */
  interaction_age: number | null
}

/** Heartbeat period. The daemon treats presence older than 90s as gone. */
export const PRESENCE_INTERVAL_MS = 30_000
/** Floor between interaction-triggered reports, so typing is not a frame per key. */
export const PRESENCE_MIN_REPORT_MS = 10_000

/** Whether this device should count as focused, per device class.
 *
 * `document.hasFocus()` is a desktop concept: it answers "is this window the one the
 * window manager gave focus to". A phone shows one app at a time and has no such
 * notion, and mobile engines answer it inconsistently — a visible foreground PWA can
 * report false. Believing that costs the phone both halves of this system: it is
 * never "in use" for notification routing, and every terminal claim it makes is
 * refused as unfocused, which is what made every session on the phone demand a
 * manual "take over" while gestures (which ignore the flag) still worked.
 *
 * So on mobile, visible *is* focused. On desktop both still have to hold. */
export function deviceIsFocused(input: {
  profile: string
  visible: boolean
  hasFocus: boolean
}): boolean {
  if (!input.visible) return false
  return input.profile === 'mobile' || input.hasFocus
}

export function presenceFrame(input: {
  profile: string
  visible: boolean
  focused: boolean
  now: number
  lastInteractionAt: number | null
}): PresenceFrame {
  return {
    type: 'presence',
    profile: input.profile,
    visible: input.visible,
    focused: input.focused,
    interaction_age: input.lastInteractionAt === null
      ? null
      : Math.max(0, (input.now - input.lastInteractionAt) / 1000),
  }
}

/** Whether an interaction should be reported immediately rather than left to the beat.
 *
 * The edge matters more than the steady state: sitting down at a quiet device and
 * starting to type must reach the daemon now, or a notification meant for the phone
 * fires while the user is demonstrably at the desk. Continuous typing does not need
 * to say so six times a minute. */
export function shouldReportInteraction(
  lastReportAt: number | null,
  now: number,
  minInterval = PRESENCE_MIN_REPORT_MS,
): boolean {
  return lastReportAt === null || now - lastReportAt >= minInterval
}

export interface PresenceWatcher {
  /** Send the current state now (used when a fresh socket opens). */
  report: () => void
  stop: () => void
}

export interface PresenceOptions {
  now?: () => number
  profile?: () => string
  interval?: number
  minReportInterval?: number
}

/** Start reporting this device's presence through `send`. Returns a disposer. */
export function watchDevicePresence(
  send: (frame: PresenceFrame) => void,
  options: PresenceOptions = {},
): PresenceWatcher {
  const now = options.now ?? (() => Date.now())
  const profile = options.profile ?? currentProfile
  let lastInteractionAt: number | null = null
  let lastReportAt: number | null = null
  const report = () => {
    lastReportAt = now()
    const visible = document.visibilityState === 'visible'
    const current = profile()
    send(presenceFrame({
      profile: current,
      visible,
      focused: deviceIsFocused({ profile: current, visible, hasFocus: document.hasFocus() }),
      now: lastReportAt,
      lastInteractionAt,
    }))
  }
  const interacted = () => {
    lastInteractionAt = now()
    if (shouldReportInteraction(lastReportAt, lastInteractionAt, options.minReportInterval)) report()
  }
  // Capture phase: a terminal or editor that stops propagation is still the user
  // being present at this device.
  document.addEventListener('pointerdown', interacted, true)
  document.addEventListener('keydown', interacted, true)
  document.addEventListener('visibilitychange', report)
  window.addEventListener('focus', report)
  window.addEventListener('blur', report)
  const timer = window.setInterval(report, options.interval ?? PRESENCE_INTERVAL_MS)
  return {
    report,
    stop: () => {
      window.clearInterval(timer)
      document.removeEventListener('pointerdown', interacted, true)
      document.removeEventListener('keydown', interacted, true)
      document.removeEventListener('visibilitychange', report)
      window.removeEventListener('focus', report)
      window.removeEventListener('blur', report)
    },
  }
}
