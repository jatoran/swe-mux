import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import {
  buildSpendRows, cacheEconomics, cacheEconomicsDetail, cacheHit, cacheHitDetail, callHealth,
  exactMoney, formatCount, formatMoney, formatPercent, type SpendBreakdown,
} from './automationCost'
import { ModelName } from './ModelName'

// What automation costs, drawn identically in two places.
//
// It is the Automation dashboard's `cost breakdown` view and it is the Usage dialog's
// `Automation` segment, and it is deliberately the *same component* rather than two views
// over one endpoint. Both readings are legitimate and neither is the "real" one: from
// Automation you ask which rule burned this, and the rules are right there; from Usage you
// ask what you are burning in total, and the other two pots are right there. Duplicating
// the markup to serve both would have reproduced exactly the drift this consolidation
// removed elsewhere, where one table under two names disagreed with itself.
//
// The pots are never summed. Observers bill a metered OpenRouter key by the call; agents
// bill a subscription and their figures are estimates. A total across them would be a
// number that is true of nothing.
//
// The agent table is a *subset*, and saying so is a correction rather than a caption.
// `provider_cost_dimensions` covers only runs mux observed, while the Usage dialog's Agents
// segment reads ccusage over every transcript the harness wrote - the same pot, two
// denominators. Drawn as a bare total beside a bare total it read as a second, competing
// answer to "what did the agents cost", which is precisely the one-number-under-two-names
// failure the shared component above exists to prevent. It is therefore labelled by its
// denominator everywhere it appears and never as the agent total.

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
  // Prompt caching, measured rather than assumed. A provider that needs an explicit
  // breakpoint and never got one reads 0% here, which is the whole point of showing it.
  const windowCache = cacheHit(spendTotals?.input_tokens, spendTotals?.cached_tokens)
  const todayCache = cacheHit(spendTotals?.today_input_tokens, spendTotals?.today_cached_tokens)
  // What the cache did to the bill, which the hit rate alone cannot say. A run
  // that writes on every call and never reads reports 0% and costs 25% *more*
  // per prompt token than not caching at all, because a write bills at 1.25x
  // input on GPT-5.6 and Anthropic.
  const todayEconomics = cacheEconomics(
    spendTotals?.today_cache_discount_usd,
    spendTotals?.today_cache_write_tokens,
    spendTotals?.today_cached_tokens,
    spendTotals?.today_cache_saving_usd,
  )
  // Calls the provider never priced. Their contribution to every dollar figure on this
  // page is zero because nobody measured it, so saying "$0.0043" without saying "and N
  // calls we could not price" would present a floor as a total.
  const unpricedToday = spendTotals?.today_unpriced_calls || 0
  const unpricedWindow = spendTotals?.unpriced_calls || 0

  return <div class="automation-cost">
    {error && <div class="usage-error" role="alert">{error}</div>}
        {/* The two pots are never summed. Observers bill a metered OpenRouter key by the
            call; agents bill a subscription and their figures are estimates. Adding them
            would produce a number that is true of nothing. */}
        <div class="usage-summary cost-summary">
          <article><span>observers today</span><strong title={exactMoney(data?.spend_today.cost_usd||0)}>{unpricedToday?'≥ ':''}{formatMoney(data?.spend_today.cost_usd||0)}</strong><small class={unpricedToday?'warn':''}>{formatCount(spendTotals?.today_calls||0)} calls · {formatCount(spendTotals?.today_tokens||0)} tokens{unpricedToday?` · ${formatCount(unpricedToday)} reported no cost`:''}</small></article>
          <article><span>observers · {spendDays}d</span><strong title={exactMoney(spendTotals?.cost_usd||0)}>{unpricedWindow?'≥ ':''}{formatMoney(spendTotals?.cost_usd||0)}</strong><small class={unpricedWindow?'warn':''}>{formatCount(spendTotals?.calls||0)} calls · {formatCount(spendTotals?.tokens||0)} tokens{unpricedWindow?` · ${formatCount(unpricedWindow)} reported no cost`:''}</small></article>
          {/* Beside the money, because the hit rate is only ever read as "is this spend
              avoidable". Today first: a breakpoint that started working this morning is
              invisible in a seven-day average. */}
          <article><span>prompt cache</span><strong title={cacheHitDetail(todayCache)}>{todayCache?formatPercent(todayCache.rate):'—'}</strong><small class={todayEconomics?.netLoss?'warn':''} title={cacheEconomicsDetail(todayEconomics)}>{todayEconomics&&todayEconomics.discount!==null
            // Money displaces the window rate only when there is money to state.
            // An unpriced cache still has a hit rate worth reading, and "unpriced"
            // in its place would be a worse line than the one it replaced.
            ?`${todayEconomics.discount>=0?formatMoney(todayEconomics.discount)+' saved':formatMoney(Math.abs(todayEconomics.discount))+' write premium'} today · ${formatCount(todayEconomics.written)} written`
            :windowCache?`${formatPercent(windowCache.rate)} over ${spendDays}d · ${formatCount(windowCache.cached)} tokens cached`:'no billed prompt tokens yet'}</small></article>
          <article><span>call outcomes</span><strong>{formatCount(calls.total)}</strong><small class={calls.failed?'warn':''}>{formatCount(calls.failed)} failed or cancelled · {formatPercent(calls.failureRate)}</small></article>
          {/* Named for its denominator, not for its subject. This is what the runs mux
              *watched* cost, which is a floor under the agent pot rather than the pot. */}
          <article><span>agents · observed runs</span><strong title={exactMoney(agentSpend)}>{formatMoney(agentSpend)}</strong><small>estimated subset · {formatCount(agentTokens)} tokens</small></article>
        </div>
        <section class="usage-table">
          <h3>What automation is costing</h3>
          <p>Every billed observer call of the last {spendDays} days, grouped by what asked for it and ranked by the window rather than by today. Same ledger as the headline, so the rows add up to it exactly.</p>
          {spendRows.length?<div class="usage-table-scroll"><table class="data-table cost-table">
            <thead><tr><th>automation</th><th>today</th><th>{spendDays} days</th><th>calls</th><th>tokens</th><th>cached</th><th>cache $</th><th>model</th></tr></thead>
            <tbody>{spendRows.map(row=>{const hit=cacheHit(row.input_tokens,row.cached_tokens); const economics=cacheEconomics(row.cache_discount_usd,row.cache_write_tokens,row.cached_tokens,row.cache_saving_usd); return <tr class={row.enabled?'':'disabled'} key={row.rule_id}>
              <td data-label="automation">
                <div class="cost-name"><strong>{row.label}</strong><span class={`automation-pill ${row.kind}`}>{row.kind}</span>{row.enabled?null:<span class="automation-pill off">off</span>}</div>
                <div class="cost-bar" style={`--share:${Math.max(0.015,costShareTotal>0?row.share:row.callShare)}`}/>
                <small title={row.rule_id}>{row.detail||row.rule_id}</small>
              </td>
              <td data-label="today" title={exactMoney(row.today_cost_usd)}>{row.today_unpriced_calls?'≥ ':''}{formatMoney(row.today_cost_usd)}</td>
              <td data-label={`${spendDays} days`} title={row.unpriced_calls?`${exactMoney(row.cost_usd)} measured; ${row.unpriced_calls} calls reported no cost`:exactMoney(row.cost_usd)}><strong>{row.unpriced_calls?'≥ ':''}{formatMoney(row.cost_usd)}</strong>{costShareTotal>0?<em>{formatPercent(row.share)}</em>:null}</td>
              <td data-label="calls" title={integer.format(row.calls)}>{formatCount(row.calls)}</td>
              <td data-label="tokens" title={integer.format(row.tokens)}>{formatCount(row.tokens)}</td>
              <td data-label="cached" title={cacheHitDetail(hit)}>{hit?formatPercent(hit.rate):'—'}</td>
              {/* Signed, and the sign is the point: a negative row is paying a write
                  premium for a prefix nothing reads back. */}
              <td data-label="cache $" class={economics?.netLoss?'warn':''} title={cacheEconomicsDetail(economics)}>{economics&&economics.discount!==null?formatMoney(economics.discount):'—'}</td>
              <td data-label="model" class="cost-model">{row.models?.length?row.models.map(model=><ModelName model={model}/>):'—'}</td>
            </tr>})}</tbody>
            <tfoot><tr><td data-label="automation">all automation</td><td data-label="today" title={exactMoney(spendTotals?.today_cost_usd||0)}>{formatMoney(spendTotals?.today_cost_usd||0)}</td><td data-label={`${spendDays} days`} title={exactMoney(spendTotals?.cost_usd||0)}>{formatMoney(spendTotals?.cost_usd||0)}</td><td data-label="calls">{formatCount(spendTotals?.calls||0)}</td><td data-label="tokens">{formatCount(spendTotals?.tokens||0)}</td><td/></tr></tfoot>
          </table></div>:<div class="automation-empty"><strong>No observer spend in the last {spendDays} days</strong><span>Enabled observers that never fired, and deterministic health checks, cost nothing and do not appear here.</span></div>}
        </section>
        <section class="usage-table">
          <h3>Agent model spend · observed runs only</h3>
          <p>A different pot of money from the observer spend, and never added to it: this is subscription usage, estimated. It is also a <strong>subset</strong> - only runs swe-mux observed - so it is a floor under the agent total rather than the total. Usage → Agents reads ccusage over every transcript the harness wrote, and that is the figure to compare a bill against. {telemetry?.cost_note?`${telemetry.cost_note}.`:'Backend/model aggregates from the harness, not attributed to individual runs.'}</p>
          {telemetry?.provider_cost_dimensions.length?<div class="usage-table-scroll"><table class="data-table">
            <thead><tr><th>backend / model</th><th>cost</th><th>tokens</th><th>source</th></tr></thead>
            <tbody>{telemetry.provider_cost_dimensions.map(row=><tr key={`${row.backend}:${row.model}`}>
              <td data-label="backend / model"><strong>{row.backend}</strong> · <ModelName model={row.model}/></td>
              <td data-label="cost" title={exactMoney(row.cost_usd)}>{formatMoney(row.cost_usd)}{row.cost_is_estimate?<em>est</em>:null}</td>
              <td data-label="tokens" title={integer.format(row.tokens)}>{formatCount(row.tokens)}</td>
              <td data-label="source" class="cost-source">{row.attribution}</td>
            </tr>)}</tbody>
            <tfoot><tr><td data-label="backend / model">all observed runs</td><td data-label="cost" title={exactMoney(agentSpend)}>≥ {formatMoney(agentSpend)}</td><td data-label="tokens">{formatCount(agentTokens)}</td><td/></tr></tfoot>
          </table></div>:<div class="automation-empty"><strong>No agent cost aggregates</strong><span>Harness usage reporting has not produced per-model figures yet.</span></div>}
        </section>
  </div>
}
