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
  average_wall_duration_s?: number; average_turn_duration_ms?: number
  average_tool_duration_ms?: number;average_model_wait_ms?:number
  model_requests:number;model_request_failures:number
  input_tokens: number; output_tokens: number
  cache_read_tokens: number; cache_write_tokens: number
  average_final_context_pct?: number; average_peak_context_pct?: number
  turns: number; completed_turns: number; model_tool_calls: number; runtime_tool_calls: number
  completed_tool_calls: number; failed_tool_calls: number
  approval_events: number; stall_events: number; subagent_events: number
  verifications: number; successful_verifications: number
}
type Workloads = {
  from: number;to: number;origin: string
  dimensions: Dimension[]
  interpretation: string
  collection?:{backfilled:number;backfill_completed:boolean;backfill_stream:string;provider_dropped:number}
}

const integer = new Intl.NumberFormat()

/** The query string every Fleet activity read shares: window, cohort, and exact filters. */
export function telemetryQuery({days,origin,backend='',layer=''}:{days:number;origin:string;backend?:string;layer?:string}): string {
  const from=days>0?Math.floor(Date.now()/1000)-days*86400:0
  const parts=[`from=${from}`,`origin=${origin}`]
  if(backend)parts.push(`backend=${encodeURIComponent(backend)}`)
  if(layer)parts.push(`layer=${encodeURIComponent(layer)}`)
  return parts.join('&')
}

export function WorkloadTelemetry({days=7,origin='mux_owned',backend=''}:{days?:number;origin?:string;backend?:string}) {
  const [data, setData] = useState<Workloads | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let stale = false
    api<Workloads>('GET', `/api/telemetry/v2/workload?${telemetryQuery({days,origin,backend})}`)
      .then(next => { if (!stale) setData(next) })
      .catch(cause => { if (!stale) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [days,origin,backend])

  return <div class="operational-telemetry">
    <p class="telemetry-caveat">{days?`Last ${days} day${days===1?'':'s'}`:'All retained time'} of {origin==='all'?'mux-owned and imported':'mux-owned'} runs{backend?` on ${backend}`:''}. Descriptive correlation only. These figures do not rank agents and do not claim that one model caused an outcome. {data?.collection&&!data.collection.backfill_completed?`Historical import: ${data.collection.backfill_stream} · ${formatCount(data.collection.backfilled)} observations preserved. `:''}{data?.interpretation || ''}</p>
    {error && <div class="usage-error" role="alert">{error}</div>}
    <section class="usage-table">
      <h3>Observed workload</h3>
      {data?.dimensions.length
        ? <div class="usage-table-scroll"><table class="data-table">
          <thead><tr><th>backend / model</th><th>runs</th><th>active / wall</th><th>context final → peak</th><th>activity</th><th>verification</th><th>tokens</th></tr></thead>
          <tbody>{data.dimensions.map(row => <tr key={`${row.backend}:${row.model}`}>
            <td data-label="backend / model"><strong>{row.backend}</strong> · <ModelName model={row.model}/></td>
            <td data-label="runs">{formatCount(row.ended_runs)}<em>/{formatCount(row.runs)} ended</em></td>
            <td data-label="active / wall">{formatDuration(row.average_turn_duration_ms==null?undefined:row.average_turn_duration_ms/1000)}<em>turn / {formatDuration(row.average_model_wait_ms==null?undefined:row.average_model_wait_ms/1000)} model / {formatDuration(row.average_wall_duration_s)} wall</em></td>
            <td data-label="context final → peak">{formatPercent(row.average_final_context_pct)} → {formatPercent(row.average_peak_context_pct)}</td>
            <td data-label="activity" class="telemetry-rates">
              <span>{formatCount(row.completed_turns)}<em>/{formatCount(row.turns)} turns</em></span>
              <span>{formatCount(row.model_tool_calls)}<em>model tools</em></span>
              <span>{formatCount(row.runtime_tool_calls)}<em>runtime tools</em></span>
              <span>{formatCount(row.model_requests||0)}<em>model requests</em></span>
              <span>{formatCount(row.stall_events)}<em>stalls</em></span>
              <span>{formatCount(row.approval_events)}<em>approvals</em></span>
            </td>
            <td data-label="verification">{formatCount(row.successful_verifications)}<em>/{formatCount(row.verifications)} passed · {formatCount(row.failed_tool_calls)} tool failures</em></td>
            <td data-label="tokens" title={integer.format((row.input_tokens || 0) + (row.output_tokens || 0))}>{formatCount((row.input_tokens || 0) + (row.output_tokens || 0))}</td>
          </tr>)}</tbody>
        </table></div>
        : <p>No workload telemetry yet. Figures appear once runs have started and ended under an observed harness.</p>}
    </section>
  </div>
}
