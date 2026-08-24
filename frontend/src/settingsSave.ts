// What a Settings save is allowed to claim after it fails.
//
// Save was two requests — `PATCH /api/config` and `PUT /api/keybindings` — fired together,
// with one catch that reported either failure as "invalid · nothing was changed". That is
// a statement about the daemon's disk, and the client was in no position to make it: a
// `_revision` conflict raised by another device produced exactly that message while the
// keybindings file had already been rewritten by the sibling request.
//
// `POST /api/settings/apply` now commits both halves or neither, and says which in a
// `committed` array on every answer it gives. The status line is derived from that array
// rather than assumed, and the one shape the daemon cannot answer at all — a request that
// never came back — says so instead of guessing in the reassuring direction.

import type { ApiError } from './api'

/** The daemon's answer to `POST /api/settings/apply`, over whatever config shape the caller holds. */
export type SettingsApplyResponse<C> = {
  config: C & { hot_applied: string[]; restart_required: string[] }
  keybindings?: { bindings: Record<string, string> } | null
  committed: string[]
}

const SECTION_LABELS: Readonly<Record<string, string>> = {
  config: 'settings',
  keybindings: 'shortcuts',
}

const label = (section: string) => SECTION_LABELS[section] || section

/** The sections a failed save's body admits to having committed, in the daemon's order. */
export function committedSections(error: ApiError): string[] {
  const committed = error.detail?.committed
  if (!Array.isArray(committed)) return []
  return committed.filter((section): section is string => typeof section === 'string')
}

/**
 * The footer status for a save that did not fully succeed.
 *
 * Three cases, and they are genuinely different:
 *  - the daemon committed part of the transaction (only the final keybindings rename can
 *    do this, and only on a disk fault) — name what landed;
 *  - the daemon answered and committed nothing — "nothing was changed" is now a fact;
 *  - no answer arrived (offline, timeout, abort) — the outcome is unknown, and saying
 *    nothing changed would be the same unfounded reassurance this replaced.
 */
export function saveFailureStatus(error: ApiError): string {
  const committed = committedSections(error)
  if (committed.length) {
    const failed = (Array.isArray(error.detail?.failed) ? error.detail.failed : [])
      .filter((section): section is string => typeof section === 'string')
    const landed = committed.map(label).join(' and ')
    const missing = failed.length ? ` · ${failed.map(label).join(' and ')} did not` : ''
    return `partly saved · ${landed} committed${missing} · ${error.message}`
  }
  if (error.status) return 'invalid · nothing was changed'
  return `save failed · the daemon did not answer, so nothing is confirmed · ${error.message}`
}
