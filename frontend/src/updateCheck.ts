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
 * The version this daemon is actually running, for the Settings heading.
 *
 * Stated on its own line rather than left to be inferred from "This is the
 * latest release." An operator who has just pressed Install, or who is checking
 * whether a redeploy took, is asking *which build am I on* - and the update
 * check's verdict answers a different question, says nothing at all while the
 * check is off, and reads identically on a daemon three releases behind whose
 * check cannot reach the manifest.
 *
 * `null` when the daemon did not report one, which is an older daemon or a
 * partially-built app rather than a version of zero; the caller renders no line
 * instead of inventing a number.
 */
export function runningVersionLabel(status: UpdateStatus | null): string | null {
  const current = status?.current_version
  return typeof current === 'string' && current.length > 0 ? current : null
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

// ----------------------------------------------------------------------------
// Installing. The daemon owns every decision here too - which mode a release
// needs, whether that needs consent, what the delta costs - and the browser's
// job is to render the answer and to send the operator's press back with the
// same words the daemon used. Nothing below re-derives a verdict.
// ----------------------------------------------------------------------------

/** The gesture headers `POST /api/update/plan` and `POST /api/update/install` require. */
export const UPDATE_PLAN_GESTURE = 'update-plan'
export const UPDATE_INSTALL_GESTURE = 'update-install'

/** The one consent the install can ask for, as the daemon names it. */
export const CONSENT_SUPERVISOR_UPDATE = 'supervisor_update'

/** `swap` preserves sessions; `replace` ends every one and swaps the supervisor. */
export type UpdateMode = 'swap' | 'replace' | string

export type UpdateSupervisorVerdict = {
  mode: UpdateMode
  /** The refusal that stands until consent is given, or ''. */
  reason: string
  message: string
  running_protocol: number | null
  incoming_protocol: number | null
  /** False when the release published no metadata sidecar: decided after download. */
  known: boolean
  reaps_sessions: boolean
  consent: string
}

export type UpdateDelta = {
  eligible?: boolean
  reason?: string
  reuse_files?: number
  reuse_bytes?: number
  fetch_files?: number
  fetch_bytes?: number
  write_files?: number
  write_bytes?: number
  total_files?: number
  total_bytes?: number
}

/** `POST /api/update/plan`: what installing a version would do, before any download. */
export type UpdatePlan = {
  version: string
  current_version: string
  changelog: string
  published: string
  install_kind: string
  /** `checkout`, `installer`, or `portable` for a frozen install. */
  managed: string
  install_root: string
  artifact: { name: string; url: string; sha256: string }
  installer: { name: string; url: string; sha256: string } | null
  supervisor: UpdateSupervisorVerdict
  mode: UpdateMode
  reaps_sessions: boolean
  consent: string
  consent_reason: string
  delta: UpdateDelta
  archive_cached: boolean
  live_sessions?: number
}

/** `GET /api/update/install`: the attempt in flight, or the last one. */
export type UpdateInstallStatus = {
  install_kind: string
  managed?: string
  swappable: boolean
  upgrade_command?: string
  current_version?: string
  running?: boolean
  phase: 'idle' | 'downloading' | 'verifying' | 'inspecting' | 'preparing' | 'handed_off'
    | 'refused' | 'failed' | string
  reason?: string
  message?: string
  version?: string
  bytes_downloaded?: number
  bytes_total?: number
  mode?: UpdateMode
  consent?: string
  delta?: UpdateDelta
}

/** A `409` from plan or install: the daemon's word, its sentence, and what would proceed. */
export type UpdateRefusal = UpdateInstallStatus & { error: string; message: string; consent?: string }

export class UpdateRefusedError extends Error {
  // A declared field rather than a constructor parameter property: the unit
  // suite runs under `node --experimental-strip-types`, which strips annotations
  // and refuses syntax that needs a transform, and `public readonly x` is one.
  readonly refusal: UpdateRefusal

  constructor(refusal: UpdateRefusal) {
    super(refusal.message || refusal.error)
    this.refusal = refusal
  }
}

async function post<T>(path: string, gesture: string, body: unknown): Promise<T> {
  // Direct fetch rather than `api()`: a 409 body is the answer here, not an error
  // to flatten - it carries the reason, the sentence, and the consent word.
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mux-User-Gesture': gesture },
    body: JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({})) as T & { error?: string; message?: string }
  if (response.status === 409 && payload.error) throw new UpdateRefusedError(payload as unknown as UpdateRefusal)
  if (!response.ok) throw new Error(payload.message || payload.error || `HTTP ${response.status}`)
  return payload
}

/** The confirm dialog's content. Reaches the network for the manifest and two small sidecars. */
export const requestUpdatePlan = (version: string): Promise<UpdatePlan> =>
  post<UpdatePlan>('/api/update/plan', UPDATE_PLAN_GESTURE, { version })

/** The press. `acceptSupervisorUpdate` is consent to end every session *if needed*. */
export const requestUpdateInstall = (
  version: string, acceptSupervisorUpdate: boolean,
): Promise<UpdateInstallStatus> =>
  post<UpdateInstallStatus>('/api/update/install', UPDATE_INSTALL_GESTURE, {
    version, accept_supervisor_update: acceptSupervisorUpdate,
  })

/** The passive read, polled while a download runs. Never reaches the network past the daemon. */
export const fetchUpdateInstall = (): Promise<UpdateInstallStatus> =>
  api<UpdateInstallStatus>('GET', '/api/update/install', undefined, { timeoutMs: 10_000 })

/** Whether a refusal is one the same request with consent would get past. */
export function refusalNeedsConsent(refusal: UpdateRefusal | UpdateInstallStatus | null): boolean {
  return !!refusal && refusal.consent === CONSENT_SUPERVISOR_UPDATE
}

/** Terminal phases: the attempt is over, one way or another. */
export function installFinished(phase: string): boolean {
  return phase === 'handed_off' || phase === 'refused' || phase === 'failed'
}

export function bytesLabel(bytes: number | undefined): string {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(0)} KB`
  return `${bytes} B`
}

/** What the progress line says for an attempt in flight. */
export function installPhaseLabel(status: UpdateInstallStatus | null): string {
  if (!status) return ''
  switch (status.phase) {
    case 'downloading': {
      const done = bytesLabel(status.bytes_downloaded)
      const total = bytesLabel(status.bytes_total)
      if (done && total && (status.bytes_total ?? 0) > 0) return `Downloading ${done} of ${total}`
      return done ? `Downloading ${done}` : 'Downloading'
    }
    case 'verifying': return 'Verifying the download'
    case 'inspecting': return 'Reading the release'
    case 'preparing': return 'Unpacking the updater'
    case 'handed_off': return 'Installing'
    case 'refused': return 'Not installed'
    case 'failed': return 'Failed'
    default: return ''
  }
}

/** The one sentence about the operator's sessions, from the daemon's verdict. */
export function planSessionsLine(plan: UpdatePlan): string {
  const count = plan.live_sessions ?? 0
  const sessions = count === 1 ? '1 live session' : `${count} live sessions`
  if (plan.reaps_sessions) {
    return count > 0
      ? `This release replaces the PTY supervisor, which ends every live terminal session - ${sessions} right now.`
      : 'This release replaces the PTY supervisor, which ends every live terminal session. None are running right now.'
  }
  if (!plan.supervisor.known) {
    return 'Whether your sessions survive is decided after the download: this release published no '
      + 'metadata sidecar. If it would replace the PTY supervisor, the install stops and asks first.'
  }
  return count > 0
    ? `Your ${sessions} keep running: the PTY supervisor holds them while the app restarts around them.`
    : 'Live sessions keep running: the PTY supervisor holds them while the app restarts around them.'
}

/** How much of the bundle the install writes, or '' when the plan could not say. */
export function planCostLine(plan: UpdatePlan): string {
  const delta = plan.delta
  if (delta.eligible) {
    const fetched = bytesLabel(delta.fetch_bytes)
    const files = delta.fetch_files ?? 0
    const reused = delta.reuse_files ?? 0
    return `Downloads the release and rewrites ${files} file${files === 1 ? '' : 's'}`
      + `${fetched ? ` (${fetched})` : ''}; ${reused} already on this machine are reused.`
  }
  if (plan.archive_cached) return 'The release is already downloaded and verified.'
  return ''
}

/** What the install kind means for the operator, in one line. */
export function planInstallLine(plan: UpdatePlan): string {
  switch (plan.managed) {
    case 'installer':
      return 'Installed with the Windows installer. The app is replaced in place and Add/Remove Programs is updated.'
    case 'checkout':
      return 'Running from a source checkout’s dist/. The built app is replaced in place.'
    case 'portable':
      return 'A portable install. The app is replaced in place.'
    default:
      return ''
  }
}
