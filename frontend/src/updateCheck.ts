/**
 * The release update check, browser side: the payload shape and the rules for
 * turning it into something a person reads.
 *
 * The *decision* - whether a newer release exists and whether it has already
 * been declined - belongs to the daemon and is `banner` in the payload. This
 * module deliberately does not re-derive it: a second comparison here would be a
 * second implementation of the one thing in this feature that is hard to get
 * right, and desktop and phone would eventually disagree about whether an update
 * is available. What lives here is the narrow guard the browser genuinely owns -
 * a payload that failed to arrive, or arrived from an older daemon that has no
 * such endpoint, must render nothing rather than a half-filled banner.
 */

// Extension-qualified so this module resolves under `node --experimental-strip-types`,
// which is what runs the unit suite: an extensionless specifier is fine for the bundler
// and unresolvable there, and a module the runner cannot load takes its test file with it.
import { api } from './api.ts'

export type UpdateRelease = {
  version: string
  tag: string
  published: string
  changelog: string
  source: 'manifest' | 'github' | string
}

export type UpdateStatus = {
  enabled: boolean
  /**
   * `ok`, `never_checked`, `disabled`, `unavailable`, or a failure reason
   * (`unreachable`, `malformed`, `unsupported_schema`, `incomparable`). Kept as
   * the daemon's own word rather than collapsed into a boolean, because "we have
   * not looked yet" and "we looked and could not tell" read the same otherwise.
   */
  status: string
  current_version?: string
  checked_at?: number | null
  next_check_at?: number | null
  update_available: boolean
  latest: UpdateRelease | null
  dismissed?: string[]
  /** The daemon's verdict: newer, and not already declined. */
  banner: boolean
  /**
   * The exact URL the daemon would fetch. Reported rather than duplicated here,
   * so the address Settings shows an operator is the one that would actually be
   * requested even if a build changes it.
   */
  manifest_url?: string
}

/** The gesture header `POST /api/update/check` requires. */
export const UPDATE_CHECK_GESTURE = 'update-check'

/**
 * Whether there is a banner to draw.
 *
 * Every clause is a real case rather than defensive padding: `null` is a fetch
 * that failed or has not returned, a payload without `banner` is an older daemon
 * that predates this endpoint, and a `banner` of true without a `latest.version`
 * would be a banner with nothing to name or link to.
 */
export function shouldShowUpdateBanner(status: UpdateStatus | null): boolean {
  if (!status || status.banner !== true) return false
  return typeof status.latest?.version === 'string' && status.latest.version.length > 0
}

/**
 * The one line the banner says.
 *
 * A version and nothing else. Deliberately not "critical", "recommended", or a
 * count of releases behind: the manifest carries no severity, and inventing one
 * would be the banner making a claim the daemon cannot support.
 */
export function updateBannerText(status: UpdateStatus): string {
  const latest = status.latest?.version ?? ''
  const current = status.current_version
  return current ? `swe-mux ${latest} is available. You are running ${current}.`
    : `swe-mux ${latest} is available.`
}

/**
 * How the last check went, for the Settings row. Returns `null` when there is
 * nothing worth saying, so the caller renders no line at all rather than an
 * empty one.
 */
export function updateStatusSummary(status: UpdateStatus | null): string | null {
  if (!status) return null
  switch (status.status) {
    case 'disabled': return 'Turned off. Nothing is requested.'
    case 'unavailable': return null
    case 'never_checked': return 'Not checked yet.'
    case 'unreachable': return 'The last check could not reach the update manifest or GitHub.'
    case 'malformed': return 'The last check reached a server that did not answer with a manifest.'
    case 'unsupported_schema':
      return 'The manifest uses a newer format than this build understands.'
    case 'incomparable': return 'The manifest named a version this build could not compare.'
    case 'ok':
      return status.update_available
        ? `swe-mux ${status.latest?.version ?? ''} is available.`
        : 'This is the latest release.'
    default: return null
  }
}

/**
 * Absolute rather than relative ("2 hours ago"): this is read once, in Settings,
 * beside a control, and a relative label would need a ticking timer to stay
 * honest for a number nobody watches change.
 */
export function lastCheckedLabel(status: UpdateStatus | null): string | null {
  const seconds = status?.checked_at
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return null
  return new Date(seconds * 1000).toLocaleString()
}

/** The passive read. Never reaches the network past the daemon. */
export const fetchUpdateStatus = (): Promise<UpdateStatus> =>
  api<UpdateStatus>('GET', '/api/update', undefined, { timeoutMs: 10_000 })

/** The explicit press. The daemon refuses this without the gesture header. */
export async function requestUpdateCheck(): Promise<UpdateStatus> {
  const response = await fetch('/api/update/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mux-User-Gesture': UPDATE_CHECK_GESTURE },
  })
  const payload = await response.json() as UpdateStatus & { error?: string }
  if (!response.ok) throw new Error(payload.error || 'The update check could not run.')
  return payload
}

export const dismissUpdate = (version: string): Promise<UpdateStatus> =>
  api<UpdateStatus>('POST', '/api/update/dismiss', { version }, { timeoutMs: 10_000 })
