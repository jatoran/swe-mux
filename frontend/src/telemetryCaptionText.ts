// The words a telemetry caption is made of, in one place so a unit test can exercise them
// without a renderer and so no view spells a range or a cohort its own way. Every total
// Usage & activity draws sits under a caption naming the window it was measured over and
// whose runs it counted: "4,821 calls" means nothing until the reader knows over which days
// and for whose runs.

export type Coverage = { rolled_days: number; rolled_hours: number; raw_spans: number; raw_seconds: number }

export function rangeLabel(days: number): string {
  if (days === 1) return 'last 24 hours'
  if (days === 0) return 'all retained time'
  return `last ${days} days`
}

export function cohortLabel(origin: string): string {
  return origin === 'all' ? 'mux-owned and imported runs' : origin === 'imported' ? 'imported runs' : 'mux-owned runs'
}

export function captionText(
  { days, origin, filters }: { days: number; origin: string; filters?: string[] },
): string {
  return [rangeLabel(days), cohortLabel(origin), ...(filters || []).filter(Boolean)].join(' · ')
}
