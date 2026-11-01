// A usage refresh runs in the background on the daemon: `POST /api/usage/refresh`
// answers 202 at once and `GET /api/usage` reports `refreshing` until it ends. The
// scan is contained (idle priority, a CPU cap, very-low I/O), so it is slow by
// design - minutes on a large transcript history - and no request is held open for
// it. These helpers poll it to completion and describe what it is doing, for both
// surfaces that offer a refresh (Usage → Agent usage, and Settings → Usage).

export type UsageRefreshMode = 'auto' | 'incremental' | 'rebuild'

export type UsageRunResources = {
  mechanism?: string
  cpu_percent_cap?: number | null
  degraded?: string[]
  cpu_seconds?: number | null
  read_bytes?: number | null
  peak_memory_bytes?: number | null
}

export type UsageRunInFlight = {
  kind: string
  since?: string | null
  reason?: string
  trigger?: string
  started_at: number
  read_bytes?: number | null
  cpu_seconds?: number | null
}

export type UsageLastRun = {
  kind?: string | null
  since?: string | null
  trigger?: string
  at?: number
  duration_ms?: number
  ccusage_version?: string | null
  launcher?: string | null
  resources?: UsageRunResources | null
  error?: string | null
}

export type UsageCollector = {
  id: string
  status: string
  error?: string | null
  refreshed_at?: number | null
  phase?: 'deferred' | 'running' | null
  deferred_reason?: string | null
  run?: UsageRunInFlight | null
  last_run?: UsageLastRun | null
  ccusage_version?: string | null
  advisories?: string[]
}

export type RefreshableStatus = { refreshing: boolean }

export const USAGE_POLL_INTERVAL_MS = 2000

type PollOptions = {
  intervalMs?: number
  sleep?: (ms: number) => Promise<void>
  /** Stops polling (an unmounted view); the last status read is returned. */
  cancelled?: () => boolean
}

const defaultSleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

/** Read the status until the daemon says no refresh is running, reporting each read. */
export async function pollUsageRefresh<T extends RefreshableStatus>(
  first: T,
  read: () => Promise<T>,
  onUpdate: (status: T) => void,
  options: PollOptions = {},
): Promise<T> {
  const sleep = options.sleep ?? defaultSleep
  const interval = options.intervalMs ?? USAGE_POLL_INTERVAL_MS
  let status = first
  onUpdate(status)
  while (status.refreshing && !options.cancelled?.()) {
    await sleep(interval)
    if (options.cancelled?.()) break
    status = await read()
    onUpdate(status)
  }
  return status
}

/** Start a refresh and poll it to completion. */
export async function runUsageRefresh<T extends RefreshableStatus>(
  start: () => Promise<T>,
  read: () => Promise<T>,
  onUpdate: (status: T) => void,
  options: PollOptions = {},
): Promise<T> {
  return pollUsageRefresh(await start(), read, onUpdate, options)
}

export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return null
  if (bytes >= 2 ** 30) return `${(bytes / 2 ** 30).toFixed(1)} GB`
  if (bytes >= 2 ** 20) return `${Math.round(bytes / 2 ** 20)} MB`
  if (bytes >= 2 ** 10) return `${Math.round(bytes / 2 ** 10)} KB`
  return `${Math.round(bytes)} B`
}

function formatSeconds(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`
}

function scope(kind: string | null | undefined, since: string | null | undefined): string {
  if (kind === 'incremental' && since) return `days since ${since}`
  if (kind === 'rebuild') return 'full history'
  if (kind === 'configured_window') return 'the configured window'
  return 'transcripts'
}

/** What a running or deferred refresh is doing, in one line; null when idle. */
export function describeUsageActivity(collector: UsageCollector | undefined, nowSeconds: number): string | null {
  if (!collector) return null
  if (collector.phase === 'deferred') {
    return `Scheduled refresh waiting for a quieter moment${collector.deferred_reason ? ` (${collector.deferred_reason})` : ''}.`
  }
  const run = collector.run
  if (!run) return null
  const parts = [`Scanning ${scope(run.kind, run.since)} in the background`]
  const read = formatBytes(run.read_bytes)
  if (read) parts.push(`${read} read`)
  parts.push(formatSeconds(nowSeconds - run.started_at))
  return `${parts.join(' · ')}.`
}

/** The last finished refresh, in one line; null when there has not been one. */
export function describeLastRun(last: UsageLastRun | null | undefined): string | null {
  if (!last || !last.kind) return null
  const parts = [`Last refresh: ${scope(last.kind, last.since)}`]
  if (typeof last.duration_ms === 'number') parts.push(formatSeconds(last.duration_ms / 1000))
  const read = formatBytes(last.resources?.read_bytes)
  if (read) parts.push(`${read} read`)
  if (last.resources?.mechanism) {
    const cap = last.resources.cpu_percent_cap
    parts.push(`background priority${typeof cap === 'number' ? `, ≤${cap}% CPU` : ''}`)
  }
  if (last.ccusage_version) parts.push(`ccusage ${last.ccusage_version}`)
  if (last.error) parts.push('failed')
  return parts.join(' · ')
}
