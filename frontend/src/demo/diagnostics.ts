/** Demo-only, bounded diagnostics. No input content and no network reporting. */
const KEY = 'swemux-demo-diagnostics-v1'
export type DemoDiagnostic = {
  at: string; component: 'demo'; operation: string; severity: 'info' | 'error';
  id: string; detail?: string;
}

export function demoLog(operation: string, id: string, detail?: string, failed = false): void {
  const entry: DemoDiagnostic = {
    at: new Date().toISOString(), component: 'demo', operation,
    severity: failed ? 'error' : 'info', id, ...(detail ? { detail } : {}),
  }
  try {
    const previous: unknown = JSON.parse(localStorage.getItem(KEY) || '[]')
    const rows = Array.isArray(previous) ? previous.slice(-99) : []
    localStorage.setItem(KEY, JSON.stringify([...rows, entry]))
  } catch { /* Private storage may be unavailable; the demo remains usable. */ }
  if (failed) console.warn('[demo]', entry)
}

export function demoDiagnostics(): DemoDiagnostic[] {
  try {
    const rows: unknown = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(rows) ? rows.slice(-100) : []
  } catch { return [] }
}
