// How far this device's clock sits from the daemon's, in seconds.
//
// Every timing the sidebar renders is a daemon timestamp aged against the
// browser's clock — `now - turn_started_at`, `now - state_since` — and those are
// only the same clock when the browser runs on the same machine. Remote access
// is a first-class way to use swe-mux, so a laptop or phone a few seconds behind
// the daemon made the age of every working session negative, and a row that
// clamped the negative away sat at "0s" for the entire turn with nothing else on
// screen looking wrong.
//
// Read from the HTTP `Date` header, which every response already carries and no
// endpoint has to opt into — including the error responses a struggling daemon
// is still answering with. Its whole-second resolution is far coarser than a
// timestamp and far finer than the skew it exists to correct.

/**
 * Below this, a new reading is round-trip noise rather than a real disagreement.
 *
 * Covers the `Date` header's one-second truncation plus the request latency the
 * midpoint estimate cannot fully remove. Holding the offset steady inside the
 * band matters more than tracking it precisely: the offset feeds a duration the
 * user watches count up, and one that jitters by a second every poll reads as a
 * broken timer even when each individual reading is defensible.
 */
export const CLOCK_OFFSET_NOISE_FLOOR_SECONDS = 2

let offsetSeconds = 0

/**
 * Fold one response's `Date` header into the held offset.
 *
 * `sentAt`/`receivedAt` are browser-clock milliseconds either side of the fetch.
 * The header was written somewhere inside that window, so its midpoint is the
 * best single estimate of when — the same halving NTP does, and enough to keep
 * request latency from being mistaken for clock skew.
 */
export function noteServerDate(header: string | null, sentAt: number, receivedAt: number): void {
  if (!header) return
  const serverMs = Date.parse(header)
  if (!Number.isFinite(serverMs)) return
  const clientMs = sentAt + (receivedAt - sentAt) / 2
  const reading = (serverMs - clientMs) / 1000
  if (Math.abs(reading - offsetSeconds) > CLOCK_OFFSET_NOISE_FLOOR_SECONDS) offsetSeconds = reading
}

/** Epoch seconds on the daemon's clock, as closely as this device can tell. */
export function serverNow(nowMs: number = Date.now()): number {
  return nowMs / 1000 + offsetSeconds
}

/** The held offset, for diagnostics and for tests that assert it settled. */
export function serverClockOffsetSeconds(): number {
  return offsetSeconds
}

/** Drop the held offset. Test seam; nothing in the app re-zeroes a live clock. */
export function resetServerClock(): void {
  offsetSeconds = 0
}
