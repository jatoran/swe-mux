import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { GrantGate } from './GrantGate'
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

// The Agents segment of the Usage dialog: historical model spend and tokens, read back out
// of each harness's own transcripts by the installed ccusage.
//
// This is one of the three pots and it is the estimated one. ccusage reconstructs what a
// subscription was spent on from records the provider wrote for its own purposes, so every
// figure here is an aggregate over sources and models and none of it identifies a saved
// provider account slot. The metered half of the picture is the Automation segment, and the
// two are never added together.
//
// It used to be the `historical` domain of `Resources -> Tokens`, sharing an actions row
// with five domains that had no use for a source picker, a collector refresh, or a usage
// cache. Those controls are all here now because there is nothing else in this segment for
// them to be wrong about. `UsageModal.tsx` owns the dialog, the focus trap, and the close.
export function UsageAgentsView({onConfigure}:{onConfigure:()=>void}) {
  const [usage,setUsage]=useState<UsageStatus|null>(null)
  const [hiddenSources,setHiddenSources]=useState<string[]>([])
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
      const next=await api<UsageStatus>('GET','/api/usage')
      setUsage(next)
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
  const body=!usage?.enabled?<GrantGate ids={['usage.ccusage']}
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
      <div class="usage-actions">
        <details class="usage-source-picker"><summary>{hiddenSources.length?`${visibleSources.length}/${sourceList.length} sources`:sourceList.length?`all ${sourceList.length} sources`:'sources'}</summary>
          <div><header><strong>Historical sources</strong><small>{usage?.collector.status||'loading'}{usage?.collector.refreshed_at?` · ${new Date(usage.collector.refreshed_at*1000).toLocaleString()}`:''}</small></header>
            <button onClick={()=>setHiddenSources([])}>select all</button>
            {sourceList.map(source=><label key={source.source_id}><input type="checkbox" checked={!hiddenSources.includes(source.source_id)} onChange={()=>toggleSource(source.source_id)}/><span>{source.source_label}</span><small>{source.source_id}</small></label>)}
            {usage?.collector.error&&<p>{usage.collector.error}</p>}
          </div>
        </details>
        <div><button disabled={!usage?.enabled||refreshing} onClick={()=>void refreshAll()}>{refreshing?'refreshing...':'refresh'}</button>
          <details class="usage-overflow"><summary aria-label="Usage actions">•••</summary><div><button onClick={onConfigure}>configure</button><button class={confirmClear?'confirming':''} disabled={refreshing} onClick={()=>void clear()}>{confirmClear?'confirm clear cache':'clear cache'}</button></div></details>
        </div>
      </div>
      <div class="usage-status-stack"><div class={`usage-progress ${refreshing?'running':''}`} role="status" aria-live="polite"><span>{refreshing?'◌':'·'}</span><strong>{message}</strong></div>
        {error&&<div class="usage-error" role="alert">{error}</div>}
      </div>
      <main>{body}</main>
  </>
}
