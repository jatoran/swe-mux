import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { harnesses } from './harnessRegistry'
import { ModelName } from './ModelName'
import { useModalFocus } from './modalFocus'
import { QuotaAnalytics } from './QuotaAnalytics'
import { UsageModelBreakdown } from './UsageModelBreakdown'
import { sumUsageRows, type ProviderUsage, type UsageRow } from './usageAnalytics'

export type UsageStatus = {
  enabled:boolean
  refreshing:boolean
  refresh_minutes:number
  package:string
  install_command:string
  states:Record<string,{status:string;error?:string;refreshed_at?:number}>
  cache?:{version?:number;updated_at?:number;providers?:Partial<Record<string,ProviderUsage>>}
}
type Attribution={sample_id:number;window:string;provider:string;account_id:string;interval_start:number;interval_end:number;quota_delta:number;correlated_estimate:number;correlated_low:number;correlated_high:number;external_estimate:number;external_low:number;external_high:number;confidence:string;sample_gap_seconds:number;concurrent_sessions:number;provider_lag_seconds:number;allocations:Array<{session_id:string;project_id?:string;model?:string;native_tokens?:number;quota_percent_estimate:number}>;caveats:string[]}
type OperationalStatus={schema_version:number;interpretation:string;quota:{samples:unknown[];resets:unknown[];attributions:Attribution[];rollups:unknown[]};tools:{metrics:Array<{backend:string;model:string;project_id:string;session_id:string;taxonomy:string;raw_tool:string;events:number;uses:number;errors:number;average_duration_ms?:number|null}>;skills:Array<{explicit_skill:string;backend:string;project_id:string;uses:number;last_used_at:number}>;unknown_or_unmapped:number;parser_version:string;parser_versions:Record<string,string>;coverage:Array<{session_id:string;backend:string;parser_version:string;status:string;recognized_records:number;unknown_records:number;tool_events:number;skill_events:number;compaction_events:number;reconciled_at:number;diagnostic?:string}>};compactions:Array<{session_id:string;backend:string;project_id:string;count:number;last_compaction_at:number;capability:string;confidence:string}>}

const compact = new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1})
const integer = new Intl.NumberFormat()
const money = new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2})

function mergeDaily(providers:ProviderUsage[]):UsageRow[] {
  const grouped=new Map<string,UsageRow[]>()
  for(const provider of providers)for(const row of provider.daily){
    if(!row.date)continue
    const items=grouped.get(row.date)||[]
    items.push(row)
    grouped.set(row.date,items)
  }
  return [...grouped.entries()].map(([date,rows])=>({...sumUsageRows(rows),date})).sort(
    (a,b)=>(b.date||'').localeCompare(a.date||''),
  )
}

function aggregateRows(rows:UsageRow[],key:'month'|'provider',value:(row:UsageRow)=>string):UsageRow[] {
  const grouped=new Map<string,UsageRow[]>()
  for(const row of rows){
    const label=value(row)
    if(!label)continue
    const items=grouped.get(label)||[]
    items.push(row)
    grouped.set(label,items)
  }
  return [...grouped.entries()].map(([label,items])=>({...sumUsageRows(items),[key]:label})).sort(
    (a,b)=>(b[key]||'').localeCompare(a[key]||''),
  )
}

function Summary({totals}:{totals:UsageRow}) {
  return <div class="usage-summary">
    <article><span>estimated cost</span><strong>{money.format(totals.cost_usd||0)}</strong></article>
    <article><span>total tokens</span><strong>{compact.format(totals.total_tokens||0)}</strong></article>
    <article><span>input</span><strong>{compact.format(totals.input_tokens||0)}</strong></article>
    <article><span>output</span><strong>{compact.format(totals.output_tokens||0)}</strong></article>
    <article><span>cache read</span><strong>{compact.format(totals.cache_read_tokens||0)}</strong></article>
    <article><span>cache create</span><strong>{compact.format(totals.cache_creation_tokens||0)}</strong></article>
  </div>
}

function UsageTable({title,rows,label}:{title:string;rows:UsageRow[];label:'date'|'month'|'provider'}) {
  return <section class="usage-table"><h3>{title}</h3>{rows.length?<div class="usage-table-scroll"><table>
    <thead><tr><th>{label}</th><th>tokens</th><th>input</th><th>output</th><th>cache</th><th>cost est.</th></tr></thead>
    <tbody>{rows.map(row=><tr key={row[label]}><td>{row[label]||'unknown'}</td>
      <td>{integer.format(row.total_tokens||0)}</td><td>{integer.format(row.input_tokens||0)}</td>
      <td>{integer.format(row.output_tokens||0)}</td>
      <td>{integer.format((row.cache_read_tokens||0)+(row.cache_creation_tokens||0))}</td>
      <td>{money.format(row.cost_usd||0)}</td>
    </tr>)}</tbody>
  </table></div>:<p>No {title.toLowerCase()} data is cached.</p>}</section>
}

function UsageSeries({rows,label,metric}:{rows:UsageRow[];label:'date'|'month';metric:'tokens'|'cost'}) {
  const values=rows.map(row=>metric==='tokens'?row.total_tokens:row.cost_usd)
  const maximum=Math.max(...values,1)
  return <section class="usage-series" aria-label={`${label} ${metric} time series`}>
    {[...rows].reverse().map(row=>{
      const value=metric==='tokens'?row.total_tokens:row.cost_usd
      return <div class="usage-series-row" key={row[label]}><span>{row[label]}</span>
        <i><b style={{width:`${Math.max(1,value/maximum*100)}%`}}/></i>
        <strong>{metric==='tokens'?integer.format(value):money.format(value)}</strong>
      </div>
    })}
  </section>
}

function ToolsView({status}:{status:OperationalStatus|null}) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Only explicit provider transcript or hook records are counted. Raw names are preserved beside a versioned normalized taxonomy. Prompt similarity and file reads never imply skill usage.</p>
    <section class="usage-table"><h3>Cross-project tool metrics</h3>{status?.tools.metrics.length?<div class="usage-table-scroll"><table>
      <thead><tr><th>backend/model</th><th>project/session</th><th>tool to taxonomy</th><th>events</th><th>uses</th><th>errors</th><th>avg duration</th></tr></thead>
      <tbody>{status.tools.metrics.map(item=><tr key={`${item.session_id}-${item.raw_tool}`}>
        <td>{item.backend} · <ModelName model={item.model}/></td><td>{item.project_id?item.project_id.slice(0,8):'unassigned'} · {item.session_id.slice(0,8)}</td>
        <td>{item.raw_tool} to {item.taxonomy}</td><td>{item.events}</td><td>{item.uses}</td><td>{item.errors}</td>
        <td>{item.average_duration_ms==null?'unavailable':`${Math.round(item.average_duration_ms)}ms`}</td>
      </tr>)}</tbody>
    </table></div>:<p>No explicit tool records yet.</p>}</section>
    <section class="attribution-log"><h3>Explicit skill invocations</h3>{status?.tools.skills.length?status.tools.skills.map(item=><article key={`${item.backend}-${item.project_id}-${item.explicit_skill}`}>
      <strong>{item.explicit_skill}</strong><span>{item.backend} · {item.uses} use(s) · last {new Date(item.last_used_at*1000).toLocaleString()}</span>
    </article>):<p>No provider-native skill invocation evidence. Skills are never inferred from Markdown or generic file reads.</p>}</section>
    <section class="usage-table"><h3>Parser and reconciled-history coverage · {status?.tools.parser_version}</h3>
      <p>{Object.entries(status?.tools.parser_versions||{}).map(([backend,version])=>`${backend}: ${version}`).join(' · ')||'provider parser versions unavailable'} · {status?.tools.unknown_or_unmapped||0} unknown or unmapped tool events.</p>
      {status?.tools.coverage.length?<div class="usage-table-scroll"><table><thead><tr><th>run</th><th>backend/parser</th><th>status</th><th>recognized</th><th>unknown</th><th>tools</th><th>skills</th><th>compactions</th></tr></thead>
        <tbody>{status.tools.coverage.map(item=><tr key={item.session_id}><td>{item.session_id.slice(0,8)}</td><td>{item.backend} · {item.parser_version}</td>
          <td>{item.status}{item.diagnostic?` · ${item.diagnostic}`:''}</td><td>{item.recognized_records}</td><td>{item.unknown_records}</td>
          <td>{item.tool_events}</td><td>{item.skill_events}</td><td>{item.compaction_events}</td>
        </tr>)}</tbody></table></div>:<p>Historical transcript reconciliation has not run yet.</p>}
    </section>
  </div>
}

function ContextView({status}:{status:OperationalStatus|null}) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Compaction counts require explicit provider-native evidence. Token drops alone remain unknown and are never counted.</p>
    <section class="attribution-log"><h3>Compaction history</h3>{status?.compactions.length?status.compactions.map(item=><article key={item.session_id}>
      <strong>{item.backend} run {item.session_id.slice(0,8)} · {item.count} compaction(s)</strong>
      <span>last {new Date(item.last_compaction_at*1000).toLocaleString()} · {item.capability}</span>
      <small>confidence {item.confidence}</small>
    </article>):<p>No explicit compaction records are available.</p>}</section>
  </div>
}

export function UsageDashboard({onClose,onConfigure}:{onClose:()=>void;onConfigure:()=>void}) {
  const [usage,setUsage]=useState<UsageStatus|null>(null)
  const [operational,setOperational]=useState<OperationalStatus|null>(null)
  const [domain,setDomain]=useState<'historical'|'quota'|'tools'|'context'>('historical')
  const [selected,setSelected]=useState<'all'|string>('all')
  const [view,setView]=useState<'overview'|'timeline'|'models'>('overview')
  const [resolution,setResolution]=useState<'daily'|'monthly'>('daily')
  const [range,setRange]=useState<'7'|'30'|'90'|'all'>('30')
  const [metric,setMetric]=useState<'tokens'|'cost'>('tokens')
  const [refreshing,setRefreshing]=useState<string|null>(null)
  const [message,setMessage]=useState('Loading usage cache...')
  const [error,setError]=useState('')
  const [confirmClear,setConfirmClear]=useState(false)
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)

  const load=async()=>{
    try{
      const [next,telemetry]=await Promise.all([
        api<UsageStatus>('GET','/api/usage'),
        api<OperationalStatus>('GET','/api/telemetry/operational?limit=300'),
      ])
      setUsage(next)
      setOperational(telemetry)
      setMessage(next.cache?.updated_at?`cache updated ${new Date(next.cache.updated_at*1000).toLocaleString()}`:'No cached historical usage yet.')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  useEffect(()=>{void load()},[])
  useEffect(()=>{
    if(!confirmClear)return
    const timer=window.setTimeout(()=>setConfirmClear(false),2000)
    return()=>clearTimeout(timer)
  },[confirmClear])

  const providers=usage?.cache?.providers||{}
  const usageProviders=harnesses().filter(item=>item.capabilities.external_usage).map(item=>item.name)
  const visibleProviders=useMemo(()=>selected==='all'
    ?usageProviders.map(provider=>providers[provider]).filter((item):item is ProviderUsage=>!!item)
    :providers[selected]?[providers[selected]!]:[],[providers,selected,usageProviders.join('\0')])
  const allDaily=mergeDaily(visibleProviders)
  const daily=range==='all'?allDaily:allDaily.slice(0,Number(range))
  const monthly=aggregateRows(daily,'month',row=>(row.date||'').slice(0,7))
  const timeline=resolution==='daily'?daily:monthly
  const visibleDates=new Set(daily.map(row=>row.date).filter((date):date is string=>!!date))
  const providerTotals=visibleProviders.map(provider=>({
    ...sumUsageRows(provider.daily.filter(row=>!!row.date&&visibleDates.has(row.date))),
    provider:provider.provider,
  }))

  const refreshProvider=async(provider:string)=>{
    setRefreshing(provider)
    setError('')
    setMessage(`Refreshing ${provider} usage... ccusage may take up to 30 seconds.`)
    try{
      const next=await api<UsageStatus>('POST','/api/usage/refresh',{provider})
      setUsage(next)
      const state=next.states[provider]
      setMessage(state.error?`${provider} refresh failed; existing cache was preserved.`:`${provider} usage refreshed ${new Date().toLocaleTimeString()}.`)
    }catch(cause){
      setError(cause instanceof Error?cause.message:String(cause))
      setMessage(`${provider} refresh failed.`)
    }finally{setRefreshing(null)}
  }
  const refreshAll=async()=>{for(const provider of usageProviders)await refreshProvider(provider)}
  const clear=async()=>{
    if(!confirmClear){setConfirmClear(true);return}
    try{
      const next=await api<UsageStatus>('DELETE','/api/usage/cache')
      setUsage(next)
      setMessage('Usage cache cleared.')
      setConfirmClear(false)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const historical=!usage?.enabled?<div class="usage-empty"><strong>Usage analytics is disabled.</strong>
    <p>Enable ccusage in Settings, save, then refresh this dashboard.</p>
    <button onClick={onConfigure}>Configure usage analytics</button>
  </div>:visibleProviders.length===0?<div class="usage-empty"><strong>No usage has been cached.</strong>
    <p>Refresh a provider to run its configured ccusage command.</p>
    <button disabled={!!refreshing} onClick={()=>void refreshAll()}>Refresh agent usage</button>
  </div>:<>
    <div class="usage-view-tabs" role="tablist" aria-label="Analytics view">
      {(['overview','timeline','models'] as const).map(item=><button role="tab" key={item} aria-selected={view===item} class={view===item?'active':''} onClick={()=>setView(item)}>{item==='timeline'?'time series':item==='models'?'model breakdown':item}</button>)}
    </div>
    <div class="usage-view-controls">
      <label>range<select value={range} onChange={event=>setRange(event.currentTarget.value as typeof range)}>
        <option value="7">7 days</option><option value="30">30 days</option>
        <option value="90">90 days</option><option value="all">all cached</option>
      </select></label>
      {(view==='timeline'||view==='models')&&<>
        <label>interval<select value={resolution} onChange={event=>setResolution(event.currentTarget.value as typeof resolution)}>
          <option value="daily">daily</option><option value="monthly">monthly</option>
        </select></label>
        <label>metric<select value={metric} onChange={event=>setMetric(event.currentTarget.value as typeof metric)}>
          <option value="tokens">tokens</option><option value="cost">estimated cost</option>
        </select></label>
      </>}
    </div>
    {view==='overview'&&<><p class="telemetry-caveat historical-caveat">Historical ccusage totals are provider and model aggregates. Native transcripts do not identify saved Claude or Codex account slots.</p>
      <Summary totals={sumUsageRows(daily)}/><div class="usage-tables"><UsageTable title="Daily aggregate" rows={daily} label="date"/><UsageTable title="Provider aggregate" rows={providerTotals} label="provider"/></div>
    </>}
    {view==='timeline'&&<><UsageSeries rows={timeline} label={resolution==='daily'?'date':'month'} metric={metric}/><UsageTable title={`${resolution} detail`} rows={timeline} label={resolution==='daily'?'date':'month'}/></>}
    {view==='models'&&<UsageModelBreakdown providers={visibleProviders} visibleDates={visibleDates} resolution={resolution} metric={metric}/>}
  </>

  return <div class="usage-layer" role="dialog" aria-modal="true" aria-label="Usage analytics" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="usage-panel" ref={panel}>
      <header><div><span>USAGE::TELEMETRY</span><strong>Historical model usage and account-specific quota evidence</strong></div>
        <div class="usage-header-actions"><button onClick={onConfigure}>configure</button><button aria-label="Close usage analytics" onClick={onClose}>×</button></div>
      </header>
      <div class="usage-actions"><div role="tablist" aria-label="Usage provider">
        <button role="tab" aria-selected={selected==='all'} class={selected==='all'?'active':''} onClick={()=>setSelected('all')}>all</button>
        {usageProviders.map(provider=><button role="tab" key={provider} aria-selected={selected===provider} class={selected===provider?'active':''} onClick={()=>setSelected(provider)}>{provider}</button>)}
      </div><button disabled={!usage?.enabled||!!refreshing} onClick={()=>void refreshAll()}>{refreshing?`refreshing ${refreshing}...`:'refresh all'}</button>
        <button class={confirmClear?'confirming':''} disabled={!!refreshing} onClick={()=>void clear()}>{confirmClear?'confirm clear cache':'clear cache'}</button>
      </div>
      <div class={`usage-progress ${refreshing?'running':''}`} role="status" aria-live="polite"><span>{refreshing?'◌':'·'}</span><strong>{message}</strong></div>
      {error&&<div class="usage-error" role="alert">{error}</div>}
      <div class="usage-provider-status">{usageProviders.map(provider=>{const state=usage?.states[provider];return <article key={provider}>
        <span class={`state-dot ${state?.status==='ready'?'idle':state?.status==='refreshing'?'working':state?.error?'crashed':'running'}`}/>
        <div><strong>{provider}</strong><small>{refreshing===provider?'refreshing now':state?.status||'loading'}{state?.refreshed_at?` · ${new Date(state.refreshed_at*1000).toLocaleString()}`:''}</small>{state?.error&&<em>{state.error}</em>}</div>
        <button disabled={!usage?.enabled||!!refreshing} onClick={()=>void refreshProvider(provider)}>refresh</button>
      </article>})}</div>
      <div class="usage-domain-tabs" role="tablist" aria-label="Telemetry category">
        {([['historical','historical cost + tokens'],['quota','quota + resets'],['tools','tools + skills'],['context','context + compaction']] as const).map(([item,label])=><button role="tab" key={item} aria-selected={domain===item} class={domain===item?'active':''} onClick={()=>setDomain(item)}>{label}</button>)}
      </div>
      <main>{domain==='quota'?<QuotaAnalytics provider={selected} attribution={operational?.quota.attributions||[]}/>:domain==='tools'?<ToolsView status={operational}/>:domain==='context'?<ContextView status={operational}/>:historical}</main>
      <footer><span>Durable local telemetry · bounded retention · historical model data is not account-specific</span></footer>
    </section>
  </div>
}
