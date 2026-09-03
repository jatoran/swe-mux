import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { ModelName } from './ModelName'
import { OPERATIONAL_TELEMETRY_PATH, type OperationalStatus } from './operationalTelemetry'

// The legacy tool table, kept reachable beside the canonical ledger until the operator
// has read the shadow comparison and turns it off (`canonical_telemetry_legacy_dashboard_enabled`).
//
// This is deliberately its own component and its own file: the Fleet activity view must
// never read the legacy snapshot again, and a test asserts it does not. What this tab adds
// is the comparison - every place the two disagree, classified - which is the evidence the
// retirement decision is supposed to rest on. The ledger never deletes the legacy rows
// either way.

type ShadowComparison = {
  from: number; to: number; runs: number; pairs: number
  classes: Record<string, number>
  examples: Array<{ run_id: string; raw_tool: string; verdict: string; legacy: { uses: number; results: number } | null; canonical: { calls: number; with_request: number; native: number } | null }>
  interpretation: string
  legacy_dashboard_enabled: boolean
}

export function LegacyToolTelemetry({ from }: { from: number }) {
  const [status, setStatus] = useState<OperationalStatus | null>(null)
  const [shadow, setShadow] = useState<ShadowComparison | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let stale = false
    Promise.all([
      api<OperationalStatus>('GET', OPERATIONAL_TELEMETRY_PATH),
      api<ShadowComparison>('GET', `/api/telemetry/v2/shadow?from=${from}`),
    ])
      .then(([nextStatus, nextShadow]) => { if (!stale) { setStatus(nextStatus); setShadow(nextShadow) } })
      .catch(cause => { if (!stale) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [from])
  const total = shadow ? Object.values(shadow.classes).reduce((sum, value) => sum + value, 0) : 0
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">The legacy tool table, drawn from the operational snapshot the ledger replaced. It stays here until the shadow comparison below has been read and the switch in Settings → Usage retires it; retiring it deletes nothing.</p>
    {error && <div class="usage-error" role="alert">{error}</div>}
    <section class="usage-table"><h3>Shadow comparison · legacy against canonical</h3>
      {shadow ? <div>
        <p>{shadow.runs.toLocaleString()} runs · {shadow.pairs.toLocaleString()} run/tool pairs compared · {total ? Math.round((shadow.classes.agree || 0) / total * 100) : 0}% agree.</p>
        <div class="usage-table-scroll"><table><thead><tr><th>class</th><th>pairs</th></tr></thead>
          <tbody>{Object.entries(shadow.classes).map(([name, count]) => <tr key={name}><td>{name.replace(/_/g, ' ')}</td><td>{count.toLocaleString()}</td></tr>)}</tbody></table></div>
        <p class="telemetry-caveat">{shadow.interpretation}</p>
        {shadow.examples.length ? <div class="usage-table-scroll"><table><thead><tr><th>run</th><th>tool</th><th>legacy uses</th><th>canonical requests</th><th>native</th><th>class</th></tr></thead>
          <tbody>{shadow.examples.map(item => <tr key={`${item.run_id}-${item.raw_tool}`}><td>{item.run_id.slice(0, 8)}</td><td>{item.raw_tool}</td><td>{item.legacy?.uses ?? '·'}</td><td>{item.canonical?.with_request ?? '·'}</td><td>{item.canonical?.native ?? '·'}</td><td>{item.verdict.replace(/_/g, ' ')}</td></tr>)}</tbody></table></div> : <p>Every pair agrees in this window.</p>}
      </div> : <p>Comparing…</p>}
    </section>
    <section class="usage-table"><h3>Legacy cross-project tool metrics</h3>{status?.tools.metrics.length ? <div class="usage-table-scroll"><table>
      <thead><tr><th>backend/model</th><th>project/session</th><th>tool to taxonomy</th><th>events</th><th>uses</th><th>errors</th><th>avg duration</th></tr></thead>
      <tbody>{status.tools.metrics.map(item => <tr key={`${item.session_id}-${item.raw_tool}`}>
        <td>{item.backend} · <ModelName model={item.model}/></td><td>{item.project_id ? item.project_id.slice(0, 8) : 'unassigned'} · {item.session_id.slice(0, 8)}</td>
        <td>{item.raw_tool} to {item.taxonomy}</td><td>{item.events}</td><td>{item.uses}</td><td>{item.errors}</td>
        <td>{item.average_duration_ms == null ? 'unavailable' : `${Math.round(item.average_duration_ms)}ms`}</td>
      </tr>)}</tbody>
    </table></div> : <p>No legacy tool records.</p>}<small>Legacy parser {status?.tools.parser_version || 'unknown'} · {status?.tools.unknown_or_unmapped || 0} unknown or unmapped events · no time range: the legacy table is all-time and capped at its row limit, which is one of the reasons it is being retired.</small></section>
  </div>
}
