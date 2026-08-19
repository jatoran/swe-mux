import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import {
  buildSpendRows, callHealth, exactMoney, formatCount, formatMoney, formatPercent,
  type SpendBreakdown,
} from './automationCost'
import { ModelName } from './ModelName'

// What automation and the agents cost, drawn identically in two places.
//
// It is the Automation dashboard's `spend` view and it is a domain of Resources / Tokens,
// and it is deliberately the *same component* rather than two views over one endpoint.
// Both readings are legitimate and neither is the "real" one: from Automation you ask which
// rule burned this, and the rules are right there; from Resources you ask what you are
// burning in total, and the other three meters are right there. Duplicating the markup to
// serve both would have reproduced exactly the drift this consolidation removed elsewhere,
// where one table under two names disagreed with itself.
//
// The two pots are never summed. Observers bill a metered OpenRouter key by the call;
// agents bill a subscription and their figures are estimates. A total across them would be
// a number that is true of nothing.

type SpendData = {
  spend_today: { tokens: number; cost_usd: number }
  observer_calls: Record<string, number>
  spend_breakdown?: SpendBreakdown
}
type CostDimension = {
  backend: string; model: string; tokens: number; cost_usd: number
  cost_is_estimate: boolean; attribution: string
}
type WorkloadCost = { provider_cost_dimensions: CostDimension[]; cost_note: string }

const integer = new Intl.NumberFormat()

export function AutomationSpendView() {
  const [data, setData] = useState<SpendData | null>(null)
  const [telemetry, setTelemetry] = useState<WorkloadCost | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let stale = false
    Promise.all([
      // The dashboard payload, not the bare status one: `spend_breakdown` is assembled
      // there (`server.py:automation_dashboard`) because it needs the engine's rule
      // metadata to label each row with what to turn off.
      api<SpendData>('GET', '/api/automation/dashboard'),
      api<WorkloadCost>('GET', '/api/telemetry/workloads'),
    ])
      .then(([dashboard, workloads]) => { if (!stale) { setData(dashboard); setTelemetry(workloads) } })
      .catch(cause => { if (!stale) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [])

  const breakdown = data?.spend_breakdown
  const spendRows = useMemo(() => buildSpendRows(breakdown), [breakdown])
  const spendDays = breakdown?.days || 7
  const spendTotals = breakdown?.totals
  const agentSpend = (telemetry?.provider_cost_dimensions || []).reduce((total, row) => total + (row.cost_usd || 0), 0)
  const agentTokens = (telemetry?.provider_cost_dimensions || []).reduce((total, row) => total + (row.tokens || 0), 0)
  const calls = callHealth(data?.observer_calls)
  const costShareTotal = spendRows.reduce((total, row) => total + (row.cost_usd || 0), 0)

  return <div class="automation-cost">
    {error && <div class="usage-error" role="alert">{error}</div>}
        {/* The two pots are never summed. Observers bill a metered OpenRouter key by the
            call; agents bill a subscription and their figures are estimates. Adding them
            would produce a number that is true of nothing. */}
        <div class="usage-summary cost-summary">
          <article><span>observers today</span><strong title={exactMoney(data?.spend_today.cost_usd||0)}>{formatMoney(data?.spend_today.cost_usd||0)}</strong><small>{formatCount(spendTotals?.today_calls||0)} calls · {formatCount(spendTotals?.today_tokens||0)} tokens</small></article>
          <article><span>observers · {spendDays}d</span><strong title={exactMoney(spendTotals?.cost_usd||0)}>{formatMoney(spendTotals?.cost_usd||0)}</strong><small>{formatCount(spendTotals?.calls||0)} calls · {formatCount(spendTotals?.tokens||0)} tokens</small></article>
          <article><span>call outcomes</span><strong>{formatCount(calls.total)}</strong><small class={calls.failed?'warn':''}>{formatCount(calls.failed)} failed or cancelled · {formatPercent(calls.failureRate)}</small></article>
          <article><span>agent models</span><strong title={exactMoney(agentSpend)}>{formatMoney(agentSpend)}</strong><small>estimated · {formatCount(agentTokens)} tokens</small></article>
        </div>
        <section class="usage-table">
          <h3>What automation is costing</h3>
          <p>Every billed observer call of the last {spendDays} days, grouped by what asked for it and ranked by the window rather than by today. Same ledger as the headline, so the rows add up to it exactly.</p>
          {spendRows.length?<div class="usage-table-scroll"><table class="data-table cost-table">
            <thead><tr><th>automation</th><th>today</th><th>{spendDays} days</th><th>calls</th><th>tokens</th><th>model</th></tr></thead>
            <tbody>{spendRows.map(row=><tr class={row.enabled?'':'disabled'} key={row.rule_id}>
              <td data-label="automation">
                <div class="cost-name"><strong>{row.label}</strong><span class={`automation-pill ${row.kind}`}>{row.kind}</span>{row.enabled?null:<span class="automation-pill off">off</span>}</div>
                <div class="cost-bar" style={`--share:${Math.max(0.015,costShareTotal>0?row.share:row.callShare)}`}/>
                <small title={row.rule_id}>{row.detail||row.rule_id}</small>
              </td>
              <td data-label="today" title={exactMoney(row.today_cost_usd)}>{formatMoney(row.today_cost_usd)}</td>
              <td data-label={`${spendDays} days`} title={exactMoney(row.cost_usd)}><strong>{formatMoney(row.cost_usd)}</strong>{costShareTotal>0?<em>{formatPercent(row.share)}</em>:null}</td>
              <td data-label="calls" title={integer.format(row.calls)}>{formatCount(row.calls)}</td>
              <td data-label="tokens" title={integer.format(row.tokens)}>{formatCount(row.tokens)}</td>
              <td data-label="model" class="cost-model">{row.models?.length?row.models.map(model=><ModelName model={model}/>):'—'}</td>
            </tr>)}</tbody>
            <tfoot><tr><td data-label="automation">all automation</td><td data-label="today" title={exactMoney(spendTotals?.today_cost_usd||0)}>{formatMoney(spendTotals?.today_cost_usd||0)}</td><td data-label={`${spendDays} days`} title={exactMoney(spendTotals?.cost_usd||0)}>{formatMoney(spendTotals?.cost_usd||0)}</td><td data-label="calls">{formatCount(spendTotals?.calls||0)}</td><td data-label="tokens">{formatCount(spendTotals?.tokens||0)}</td><td/></tr></tfoot>
          </table></div>:<div class="automation-empty"><strong>No observer spend in the last {spendDays} days</strong><span>Enabled observers that never fired, and deterministic health checks, cost nothing and do not appear here.</span></div>}
        </section>
        <section class="usage-table">
          <h3>Agent model spend</h3>
          <p>Your agent subscription usage, which is a different pot of money from the observer spend above and is never added to it. {telemetry?.cost_note?`${telemetry.cost_note}.`:'Backend/model aggregates from the harness, not attributed to individual runs.'}</p>
          {telemetry?.provider_cost_dimensions.length?<div class="usage-table-scroll"><table class="data-table">
            <thead><tr><th>backend / model</th><th>cost</th><th>tokens</th><th>source</th></tr></thead>
            <tbody>{telemetry.provider_cost_dimensions.map(row=><tr key={`${row.backend}:${row.model}`}>
              <td data-label="backend / model"><strong>{row.backend}</strong> · <ModelName model={row.model}/></td>
              <td data-label="cost" title={exactMoney(row.cost_usd)}>{formatMoney(row.cost_usd)}{row.cost_is_estimate?<em>est</em>:null}</td>
              <td data-label="tokens" title={integer.format(row.tokens)}>{formatCount(row.tokens)}</td>
              <td data-label="source" class="cost-source">{row.attribution}</td>
            </tr>)}</tbody>
            <tfoot><tr><td data-label="backend / model">all backends</td><td data-label="cost" title={exactMoney(agentSpend)}>{formatMoney(agentSpend)}</td><td data-label="tokens">{formatCount(agentTokens)}</td><td/></tr></tfoot>
          </table></div>:<div class="automation-empty"><strong>No agent cost aggregates</strong><span>Harness usage reporting has not produced per-model figures yet.</span></div>}
        </section>
  </div>
}
