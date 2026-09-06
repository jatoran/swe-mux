import { sumUsageRows, type UsageRow, type UsageSource } from './usageAnalytics.ts'

export const compactNumber = (value: number) => new Intl.NumberFormat('en', {notation:'compact', maximumFractionDigits:1}).format(value)
export const exactNumber = (value: number) => new Intl.NumberFormat().format(value)

/** Calendar windows end on the newest cached date, including days with no records. */
export function usageWindow(sources: UsageSource[], days: number): UsageRow[] {
  const dates = new Map<string, UsageRow[]>()
  for (const source of sources) for (const row of source.daily) {
    if (!row.date) continue
    const rows = dates.get(row.date) || []
    rows.push(row); dates.set(row.date, rows)
  }
  const ordered = [...dates.keys()].sort().reverse()
  const cutoff = days && ordered.length ? Date.parse(`${ordered[0]}T00:00:00Z`) - (days - 1) * 86400000 : -Infinity
  return ordered.filter(date => Date.parse(`${date}T00:00:00Z`) >= cutoff)
    .map(date => ({...sumUsageRows(dates.get(date)!), date}))
}

export function usagePeriods(rows: UsageRow[], monthly: boolean): UsageRow[] {
  if (!monthly) return rows
  const groups = new Map<string, UsageRow[]>()
  for (const row of rows) {
    const month = row.date!.slice(0, 7)
    const group = groups.get(month) || []; group.push(row); groups.set(month, group)
  }
  return [...groups].map(([date, values]) => ({...sumUsageRows(values), date}))
}
