import { t } from './localization/index.ts'
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
  if (bytes >= 2 ** 30) return t("usageRefresh.93b74ca293", { value1: (bytes / 2 ** 30).toFixed(1) })
  if (bytes >= 2 ** 20) return t("usageRefresh.60e78b906d", { value1: Math.round(bytes / 2 ** 20) })
  if (bytes >= 2 ** 10) return t("usageRefresh.634b3dd62c", { value1: Math.round(bytes / 2 ** 10) })
  return t("usageRefresh.276b4e09cd", { value1: Math.round(bytes) })
}

function formatSeconds(seconds: number): string {
  if (seconds < 60) return t("usageRefresh.6732456ec2", { value1: Math.max(0, Math.round(seconds)) })
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest ? t("usageRefresh.565fc0fdc0", { value1: minutes, value2: rest }) : t("usageRefresh.10767db0b0", { value1: minutes })
}

function scope(kind: string | null | undefined, since: string | null | undefined): string {
  if (kind === 'incremental' && since) return t("usageRefresh.21a1e1c74a", { value1: since })
  if (kind === 'rebuild') return t("usageRefresh.674d360dc7")
  if (kind === 'configured_window') return t("usageRefresh.6546b16695")
  return 'transcripts'
}

/** What a running or deferred refresh is doing, in one line; null when idle. */
export function describeUsageActivity(collector: UsageCollector | undefined, nowSeconds: number): string | null {
  if (!collector) return null
  if (collector.phase === 'deferred') {
    return t("usageRefresh.9b39c5e7da", { value1: collector.deferred_reason ? ` (${collector.deferred_reason})` : '' })
  }
  const run = collector.run
  if (!run) return null
  const parts = [t("usageRefresh.d16f5ab608", { value1: scope(run.kind, run.since) })]
  const read = formatBytes(run.read_bytes)
  if (read) parts.push(`${read} read`)
  parts.push(formatSeconds(nowSeconds - run.started_at))
  return `${parts.join(' · ')}.`
}

/** The last finished refresh, in one line; null when there has not been one. */
export function describeLastRun(last: UsageLastRun | null | undefined): string | null {
  if (!last || !last.kind) return null
  const parts = [t("usageRefresh.df1e58fcab", { value1: scope(last.kind, last.since) })]
  if (typeof last.duration_ms === 'number') parts.push(formatSeconds(last.duration_ms / 1000))
  const read = formatBytes(last.resources?.read_bytes)
  if (read) parts.push(`${read} read`)
  if (last.resources?.mechanism) {
    const cap = last.resources.cpu_percent_cap
    parts.push(t("usageRefresh.920eb3c6a2", { value1: typeof cap === 'number' ? `, ≤${cap}% CPU` : '' }))
  }
  if (last.ccusage_version) parts.push(`ccusage ${last.ccusage_version}`)
  if (last.error) parts.push('failed')
  return parts.join(' · ')
}
