import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { GrantGate } from './GrantGate'
import { WorkloadTelemetry } from './WorkloadTelemetry'
import { AutomationSpendView } from './AutomationSpendView'
import { harnesses } from './harnessRegistry'
import { ModelName } from './ModelName'
import { QuotaAnalytics } from './QuotaAnalytics'
import { UsageModelBreakdown } from './UsageModelBreakdown'
import { sumUsageRows, type UsageRow, type UsageSource } from './usageAnalytics'

export type UsageStatus = {
  enabled:boolean
  refreshing:boolean
  refresh_minutes:number
  package:string
  install_command:string
  collector:{id:string;status:string;error?:string;refreshed_at?:number}
  cache?:{version?:number;updated_at?:number;sources?:Partial<Record<string,UsageSource>>}
}
type Attribution={sample_id:number;window:string;provider:string;account_id:string;interval_start:number;interval_end:number;quota_delta:number;correlated_estimate:number;correlated_low:number;correlated_high:number;external_estimate:number;external_low:number;external_high:number;confidence:string;sample_gap_seconds:number;concurrent_sessions:number;provider_lag_seconds:number;allocations:Array<{session_id:string;project_id?:string;model?:string;native_tokens?:number;quota_percent_estimate:number}>;caveats:string[]}
type OperationalStatus={schema_version:number;interpretation:string;quota:{samples:unknown[];resets:unknown[];attributions:Attribution[];rollups:unknown[]};tools:{metrics:Array<{backend:string;model:string;project_id:string;session_id:string;taxonomy:string;raw_tool:string;events:number;uses:number;errors:number;average_duration_ms?:number|null}>;skills:Array<{explicit_skill:string;backend:string;project_id:string;uses:number;last_used_at:number}>;unknown_or_unmapped:number;parser_version:string;parser_versions:Record<string,string>;coverage:Array<{session_id:string;backend:string;parser_version:string;status:string;recognized_records:number;unknown_records:number;tool_events:number;skill_events:number;compaction_events:number;reconciled_at:number;diagnostic?:string}>};compactions:Array<{session_id:string;backend:string;project_id:string;count:number;last_compaction_at:number;capability:string;confidence:string}>}

const compact = new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1})
const integer = new Intl.NumberFormat()
const money = new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2})

function mergeDaily(sources:UsageSource[]):UsageRow[] {
  const grouped=new Map<string,UsageRow[]>()
  for(const source of sources)for(const row of source.daily){
    if(!row.date)continue
    const items=grouped.get(row.date)||[]
    items.push(row)
    grouped.set(row.date,items)
  }
  return [...grouped.entries()].map(([date,rows])=>({...sumUsageRows(rows),date})).sort(
    (a,b)=>(b.date||'').localeCompare(a.date||''),
  )
}

function aggregateRows(rows:UsageRow[],key:'month'|'source_id',value:(row:UsageRow)=>string):UsageRow[] {
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

function UsageTable({title,rows,label}:{title:string;rows:UsageRow[];label:'date'|'month'|'source_id'}) {
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

// The Tokens segment of the Resources modal: historical model spend, subscription
// windows, resets, tools, and context evidence. Tokens sit beside processes, bandwidth,
// and disk because all four answer one question - what is this thing consuming - even
// though only this one is metered in money rather than bytes. `ResourcesModal.tsx` owns
// the dialog, the focus trap, and the close control that used to live here.
export function UsageTokensView({onConfigure}:{onConfigure:()=>void}) {
  const [usage,setUsage]=useState<UsageStatus|null>(null)
  const [operational,setOperational]=useState<OperationalStatus|null>(null)
  const [domain,setDomain]=useState<'historical'|'quota'|'spend'|'workloads'|'tools'|'context'>('historical')
  const [hiddenSources,setHiddenSources]=useState<string[]>([])
  const [quotaProvider,setQuotaProvider]=useState<'all'|string>('all')
  const [view,setView]=useState<'overview'|'timeline'|'models'>('overview')
  const [resolution,setResolution]=useState<'daily'|'monthly'>('daily')
  const [range,setRange]=useState<'7'|'30'|'90'|'all'>('30')
  const [metric,setMetric]=useState<'tokens'|'cost'>('tokens')
  const [refreshing,setRefreshing]=useState(false)
  const [message,setMessage]=useState('Loading usage cache...')
  const [error,setError]=useState('')
  const [confirmClear,setConfirmClear]=useState(false)

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

  const sources=usage?.cache?.sources||{}
  const sourceList=useMemo(()=>Object.values(sources).filter((item):item is UsageSource=>!!item).sort(
    (a,b)=>a.source_label.localeCompare(b.source_label),
  ),[sources])
  const visibleSources=useMemo(()=>sourceList.filter(source=>!hiddenSources.includes(source.source_id)),[sourceList,hiddenSources])
  const quotaProviders=harnesses().filter(item=>item.capabilities.provider_accounts).map(item=>item.name)
  const allDaily=mergeDaily(visibleSources)
  const daily=range==='all'?allDaily:allDaily.slice(0,Number(range))
  const monthly=aggregateRows(daily,'month',row=>(row.date||'').slice(0,7))
  const timeline=resolution==='daily'?daily:monthly
  const visibleDates=new Set(daily.map(row=>row.date).filter((date):date is string=>!!date))
  const sourceTotals=visibleSources.map(source=>({
    ...sumUsageRows(source.daily.filter(row=>!!row.date&&visibleDates.has(row.date))),
    source_id:source.source_label,
  }))

  const refreshAll=async()=>{
    setRefreshing(true)
    setError('')
    setMessage('Refreshing historical sources... ccusage may take up to 30 seconds.')
    try{
      const next=await api<UsageStatus>('POST','/api/usage/refresh',{})
      setUsage(next)
      setMessage(next.collector.error?'Refresh failed; existing cache was preserved.':`Historical sources refreshed ${new Date().toLocaleTimeString()}.`)
    }catch(cause){
      setError(cause instanceof Error?cause.message:String(cause))
      setMessage('Historical source refresh failed.')
    }finally{setRefreshing(false)}
  }
  const toggleSource=(source:string)=>setHiddenSources(current=>current.includes(source)
    ?current.filter(item=>item!==source)
    :[...current,source])
  const clear=async()=>{
    if(!confirmClear){setConfirmClear(true);return}
    try{
      const next=await api<UsageStatus>('DELETE','/api/usage/cache')
      setUsage(next)
      setMessage('Usage cache cleared.')
      setConfirmClear(false)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  // "Enable ccusage in Settings, save, then refresh this dashboard" was three steps in
  // two overlays for one boolean, and it is the whole reason gates exist.
  const historical=!usage?.enabled?<GrantGate ids={['usage.ccusage']}
    heading="Usage analytics is switched off."
    onGranted={async()=>{setUsage(await api<UsageStatus>('GET','/api/usage'))}}>
    <p>swe-mux reads historical token totals by running the installed <code>ccusage</code>
    against each harness's own records. Turning it on reads nothing until you refresh
    below, and it never sends a transcript anywhere.</p>
  </GrantGate>:sourceList.length===0?<div class="usage-empty"><strong>No usage sources have been detected.</strong>
    <p>Refresh to scan every source supported by the installed ccusage version.</p>
    <button disabled={!!refreshing} onClick={()=>void refreshAll()}>Refresh agent usage</button>
  </div>:visibleSources.length===0?<div class="usage-empty"><strong>Every historical source is hidden.</strong>
    <p>Select at least one source from the source filter.</p>
  </div>:<>
    <div class="usage-view-tabs" role="tablist" aria-label="Analytics view">
      {(['overview','timeline','models'] as const).map(item=><button role="tab" key={item} aria-selected={view===item} class={view===item?'active':''} onClick={()=>setView(item)}>{item==='timeline'?'time series':item==='models'?'model breakdown':item}</button>)}
    </div>
    <div class="usage-view-controls">
      <label>range<Dropdown value={range} onChange={value=>setRange(value as typeof range)} options={[
        {value:'7',label:'7 days'},{value:'30',label:'30 days'},
        {value:'90',label:'90 days'},{value:'all',label:'all cached'},
      ]}/></label>
      {(view==='timeline'||view==='models')&&<>
        <label>interval<Dropdown value={resolution} onChange={value=>setResolution(value as typeof resolution)} options={[
          {value:'daily',label:'daily'},{value:'monthly',label:'monthly'},
        ]}/></label>
        <label>metric<Dropdown value={metric} onChange={value=>setMetric(value as typeof metric)} options={[
          {value:'tokens',label:'tokens'},{value:'cost',label:'estimated cost'},
        ]}/></label>
      </>}
    </div>
    {view==='overview'&&<><p class="telemetry-caveat historical-caveat">Historical ccusage totals are source and model aggregates. Transcript history does not identify saved provider account slots.</p>
      <Summary totals={sumUsageRows(daily)}/><div class="usage-tables"><UsageTable title="Daily aggregate" rows={daily} label="date"/><UsageTable title="Source aggregate" rows={sourceTotals} label="source_id"/></div>
    </>}
    {view==='timeline'&&<><UsageSeries rows={timeline} label={resolution==='daily'?'date':'month'} metric={metric}/><UsageTable title={`${resolution} detail`} rows={timeline} label={resolution==='daily'?'date':'month'}/></>}
    {view==='models'&&<UsageModelBreakdown sources={visibleSources} visibleDates={visibleDates} resolution={resolution} metric={metric}/>}
  </>

  return <>
      <div class="usage-domain-tabs" role="tablist" aria-label="Telemetry category">
        {([['historical','historical cost + tokens'],['quota','quota + resets'],['spend','automation spend'],['workloads','runs + workload'],['tools','tools + skills'],['context','context + compaction']] as const).map(([item,label])=><button role="tab" key={item} aria-selected={domain===item} class={domain===item?'active':''} onClick={()=>setDomain(item)}>{label}</button>)}
      </div>
      <div class="usage-actions">
        {domain==='historical'?<details class="usage-source-picker"><summary>{hiddenSources.length?`${visibleSources.length}/${sourceList.length} sources`:sourceList.length?`all ${sourceList.length} sources`:'sources'}</summary>
          <div><header><strong>Historical sources</strong><small>{usage?.collector.status||'loading'}{usage?.collector.refreshed_at?` · ${new Date(usage.collector.refreshed_at*1000).toLocaleString()}`:''}</small></header>
            <button onClick={()=>setHiddenSources([])}>select all</button>
            {sourceList.map(source=><label key={source.source_id}><input type="checkbox" checked={!hiddenSources.includes(source.source_id)} onChange={()=>toggleSource(source.source_id)}/><span>{source.source_label}</span><small>{source.source_id}</small></label>)}
            {usage?.collector.error&&<p>{usage.collector.error}</p>}
          </div>
        </details>:domain==='quota'?<label>provider<Dropdown value={quotaProvider} onChange={setQuotaProvider} options={[{value:'all',label:'all providers'},...quotaProviders.map(provider=>({value:provider,label:provider}))]}/></label>:<span>Cross-source operational telemetry</span>}
        <div>{domain==='historical'&&<button disabled={!usage?.enabled||refreshing} onClick={()=>void refreshAll()}>{refreshing?'refreshing...':'refresh'}</button>}
          <details class="usage-overflow"><summary aria-label="Usage actions">•••</summary><div><button onClick={onConfigure}>configure</button><button class={confirmClear?'confirming':''} disabled={refreshing} onClick={()=>void clear()}>{confirmClear?'confirm clear cache':'clear cache'}</button></div></details>
        </div>
      </div>
      <div class="usage-status-stack"><div class={`usage-progress ${refreshing?'running':''}`} role="status" aria-live="polite"><span>{refreshing?'◌':'·'}</span><strong>{domain==='historical'?message:'Filters below apply only to this telemetry category.'}</strong></div>
        {error&&<div class="usage-error" role="alert">{error}</div>}
      </div>
      <main>{domain==='quota'?<QuotaAnalytics provider={quotaProvider} attribution={operational?.quota.attributions||[]}/>:domain==='spend'?<AutomationSpendView/>:domain==='workloads'?<WorkloadTelemetry/>:domain==='tools'?<ToolsView status={operational}/>:domain==='context'?<ContextView status={operational}/>:historical}</main>
      <footer><span>Durable local telemetry · bounded retention · historical model data is not account-specific</span></footer>
  </>
}
