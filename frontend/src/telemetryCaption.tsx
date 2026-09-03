// Every displayed telemetry total names four things: its time range, its cohort, its
// denominator, and how the window was answered (rolled-up days and hours against raw
// entity reads). A figure drawn without them is the bug this component exists to stop:
// "4,821 calls" means nothing until the reader knows over which days, for whose runs,
// out of what, and whether a rebuild is still pending.

export type Coverage = { rolled_days: number; rolled_hours: number; raw_spans: number; raw_seconds: number }

export function rangeLabel(days: number): string {
  if (days === 1) return 'last 24 hours'
  if (days === 0) return 'all retained time'
  return `last ${days} days`
}

export function cohortLabel(origin: string): string {
  return origin === 'all' ? 'mux-owned and imported runs' : origin === 'imported' ? 'imported runs' : 'mux-owned runs'
}

export function coverageLabel(coverage: Coverage | undefined): string {
  if (!coverage) return 'coverage unavailable'
  const parts: string[] = []
  if (coverage.rolled_days) parts.push(`${coverage.rolled_days} rolled-up day${coverage.rolled_days === 1 ? '' : 's'}`)
  if (coverage.rolled_hours) parts.push(`${coverage.rolled_hours} rolled-up hour${coverage.rolled_hours === 1 ? '' : 's'}`)
  if (coverage.raw_seconds > 0) parts.push(`${Math.round(coverage.raw_seconds / 3600 * 10) / 10}h read from entities`)
  return parts.length ? parts.join(' · ') : 'no data in range'
}

export function TelemetryCaption({ days, origin, denominator, coverage, filters }: {
  days: number; origin: string; denominator: string; coverage?: Coverage; filters?: Record<string, string>
}) {
  const named = Object.entries(filters || {}).filter(([, value]) => value)
  return <small class="telemetry-total-caption" data-telemetry-caption>
    {rangeLabel(days)} · {cohortLabel(origin)}{named.length ? ` · ${named.map(([key, value]) => `${key.replace(/_/g, ' ')} = ${value}`).join(', ')}` : ''} · {denominator} · {coverageLabel(coverage)}
  </small>
}
