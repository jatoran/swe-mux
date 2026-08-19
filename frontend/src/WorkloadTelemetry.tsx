import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { formatCount, formatDuration, formatPercent } from './automationCost'
import { ModelName } from './ModelName'

// Observed workload per backend and model: runs, durations, context pressure, turn and stall
// rates, completion evidence, and tokens.
//
// This lived in the Automation dashboard's "all-session health" view, which was three
// unrelated things under one name — an explainer of the deterministic checks, this table,
// and an away report. It is here because it answers the Resources question (what have the
// agents consumed) and because its cost column had already left for the spend view on
// exactly that reasoning; the rest of the table simply followed. Money is deliberately not
// repeated here — the `automation spend` domain beside this one is the whole cost picture,
// including the subscription figures, and two tables of one number is what this whole
// consolidation exists to stop.
//
// Descriptive correlation only. It does not rank agents and does not claim that one model
// caused an outcome.

type Dimension = {
  backend: string; model: string; runs: number; ended_runs: number
  average_duration_s?: number; tokens_in: number; tokens_out: number
  average_final_context_pct?: number; average_peak_context_pct?: number
  turns_per_run: number; stalls_per_run: number; approvals_per_run: number
  completion_evidence_count: number; completion_evidence_runs: number
}
type Workloads = {
  since: number
  dimensions: Dimension[]
  interpretation: string
}

const integer = new Intl.NumberFormat()

export function WorkloadTelemetry() {
  const [data, setData] = useState<Workloads | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let stale = false
    api<Workloads>('GET', '/api/telemetry/workloads')
      .then(next => { if (!stale) setData(next) })
      .catch(cause => { if (!stale) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [])

  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Descriptive correlation only. These figures do not rank agents and do not claim that one model caused an outcome. {data?.interpretation || ''}</p>
    {error && <div class="usage-error" role="alert">{error}</div>}
    <section class="usage-table">
      <h3>Observed workload</h3>
      {data?.dimensions.length
        ? <div class="usage-table-scroll"><table class="data-table">
          <thead><tr><th>backend / model</th><th>runs</th><th>avg duration</th><th>context final → peak</th><th>per run</th><th>completion evidence</th><th>tokens</th></tr></thead>
          <tbody>{data.dimensions.map(row => <tr key={`${row.backend}:${row.model}`}>
            <td data-label="backend / model"><strong>{row.backend}</strong> · <ModelName model={row.model}/></td>
            <td data-label="runs">{formatCount(row.ended_runs)}<em>/{formatCount(row.runs)} ended</em></td>
            <td data-label="avg duration">{formatDuration(row.average_duration_s)}</td>
            <td data-label="context final → peak">{formatPercent(row.average_final_context_pct)} → {formatPercent(row.average_peak_context_pct)}</td>
            <td data-label="per run" class="telemetry-rates">
              <span>{row.turns_per_run.toFixed(1)}<em>turns</em></span>
              <span>{row.stalls_per_run.toFixed(2)}<em>stalls</em></span>
              <span>{row.approvals_per_run.toFixed(2)}<em>approvals</em></span>
            </td>
            <td data-label="completion evidence">{formatCount(row.completion_evidence_runs)}<em>/{formatCount(row.runs)} runs · {formatCount(row.completion_evidence_count)} signals</em></td>
            <td data-label="tokens" title={integer.format((row.tokens_in || 0) + (row.tokens_out || 0))}>{formatCount((row.tokens_in || 0) + (row.tokens_out || 0))}</td>
          </tr>)}</tbody>
        </table></div>
        : <p>No workload telemetry yet. Figures appear once runs have started and ended under an observed harness.</p>}
    </section>
  </div>
}
