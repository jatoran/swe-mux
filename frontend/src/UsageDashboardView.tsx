import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { GrantGate } from './GrantGate'
import { AnalyticsNav, MetricValue, UsageBreakdown, UsageDetails, UsageTrend } from './AnalyticsPrimitives'
import { usagePeriods, usageWindow } from './analyticsPresentation'
import { UsageModelBreakdown } from './UsageModelBreakdown'
import { sumUsageRows, type UsageSource } from './usageAnalytics'

export type UsageStatus = {
  enabled:boolean
  refreshing:boolean
  refresh_minutes:number
  package:string
  install_command:string
  collector:{id:string;status:string;error?:string;refreshed_at?:number}
  cache?:{version?:number;updated_at?:number;sources?:Partial<Record<string,UsageSource>>}
}


export function UsageAgentsView({onConfigure}:{onConfigure:()=>void}) {
  const [usage,setUsage]=useState<UsageStatus|null>(null)
  const [hiddenSources,setHiddenSources]=useState<string[]>([])
  const [view,setView]=useState<'summary'|'trends'|'models'>('summary')
  const [resolution,setResolution]=useState<'daily'|'monthly'>('daily')
  const [range,setRange]=useState('30')
  const [metric,setMetric]=useState<'tokens'|'cost'>('tokens')
  const [refreshing,setRefreshing]=useState(false)
  const [error,setError]=useState('')
  const [confirmClear,setConfirmClear]=useState(false)
  useEffect(()=>{
    let cancelled=false
    api<UsageStatus>('GET','/api/usage').then(value=>{if(!cancelled)setUsage(value)}).catch(cause=>{if(!cancelled)setError(String(cause))})
    return()=>{cancelled=true}
  },[])
  const sourceList=useMemo(()=>Object.values(usage?.cache?.sources||{}).filter((source):source is UsageSource=>!!source).sort((a,b)=>a.source_label.localeCompare(b.source_label)),[usage])
  const visibleSources=sourceList.filter(source=>!hiddenSources.includes(source.source_id))
  const daily=usageWindow(visibleSources,Number(range))
  const dates=new Set(daily.map(row=>row.date!))
  const totals=sumUsageRows(daily)
  const periods=usagePeriods(daily,resolution==='monthly')
  const sourceRows=visibleSources.map(source=>({id:source.source_id,label:source.source_label,row:sumUsageRows(source.daily.filter(row=>dates.has(row.date!)))})).sort((a,b)=>b.row.total_tokens-a.row.total_tokens)
  const refresh=async()=>{
    setRefreshing(true);setError('')
    try {setUsage(await api<UsageStatus>('POST','/api/usage/refresh',{}))}
    catch(cause){setError(String(cause))}
    finally{setRefreshing(false)}
  }
  const clear=async()=>{
    if(!confirmClear){setConfirmClear(true);return}
    setConfirmClear(false);setError('')
    try{setUsage(await api<UsageStatus>('DELETE','/api/usage/cache'))}catch(cause){setError(String(cause))}
  }
  return <>
    <AnalyticsNav label="Agent usage views" value={view} onChange={setView} items={[{id:'summary',label:'Summary'},{id:'trends',label:'Trends'},{id:'models',label:'Models'}]}/>
    <div class="analytics-toolbar">
      <label>Range<select value={range} onChange={event=>setRange(event.currentTarget.value)}><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option><option value="0">All cached</option></select></label>
      <details class="analytics-filter-menu"><summary>Sources ({visibleSources.length}/{sourceList.length})</summary><div>
        <button onClick={()=>setHiddenSources([])}>Select all</button>
        {sourceList.map(source=><label key={source.source_id}><input type="checkbox" checked={!hiddenSources.includes(source.source_id)} onChange={()=>setHiddenSources(current=>current.includes(source.source_id)?current.filter(id=>id!==source.source_id):[...current,source.source_id])}/>{source.source_label}</label>)}
      </div></details>
      {view!=='summary'&&<label>Metric<select value={metric} onChange={event=>setMetric(event.currentTarget.value as typeof metric)}><option value="tokens">Tokens</option><option value="cost">Estimated cost</option></select></label>}
      {view==='trends'&&<label>Interval<select value={resolution} onChange={event=>setResolution(event.currentTarget.value as typeof resolution)}><option value="daily">Daily</option><option value="monthly">Monthly</option></select></label>}
      <button class="analytics-refresh" disabled={!usage?.enabled||refreshing||usage.refreshing} onClick={()=>void refresh()}>{refreshing||usage?.refreshing?'Refreshing…':'Refresh'}</button>
    </div>
    <main class="analytics-content">
      {error&&<div class="usage-error" role="alert">{error}</div>}
      {usage?.collector.error&&<div class="usage-error" role="alert">{usage.collector.error} Cached results are preserved.</div>}
      {!usage&&!error?<p>Loading agent usage…</p>:usage&&!usage.enabled?<GrantGate ids={['usage.ccusage']} heading="Agent usage collection is off" onGranted={async()=>setUsage(await api<UsageStatus>('GET','/api/usage'))}><p>Enable collection, then refresh to read local transcript totals.</p></GrantGate>:!sourceList.length?<p>No cached usage. Refresh to collect historical sources.</p>:!visibleSources.length?<p>Select at least one source.</p>:<>
        <p class="analytics-caption">{daily.length?`${daily[daily.length-1].date} to ${daily[0].date}`:'No dated records'} · transcript totals · estimated cost</p>
        {view==='summary'&&<>
          <div class="analytics-headlines"><article><span>Tokens</span><strong><MetricValue value={totals.total_tokens}/></strong></article><article><span>Estimated cost</span><strong><MetricValue value={totals.cost_method==='unavailable'?null:totals.cost_usd} money/></strong></article></div>
          <UsageDetails row={totals}/><UsageTrend rows={daily}/><UsageBreakdown title="By source" rows={sourceRows}/>
        </>}
        {view==='trends'&&<><UsageTrend rows={periods} metric={metric}/><UsageBreakdown title="Period details" rows={periods.map(row=>({id:row.date!,label:row.date!,row}))}/></>}
        {view==='models'&&<UsageModelBreakdown sources={visibleSources} visibleDates={dates} resolution={resolution} metric={metric}/>}
      </>}
      <details class="analytics-diagnostics"><summary>Source and collection details{usage?.cache?.updated_at?` · updated ${new Date(usage.cache.updated_at*1000).toLocaleString()}`:''}</summary>
        <p>ccusage reads local transcripts across sources. Historical totals cannot be attributed to saved accounts. Estimated token cost is not a subscription bill.</p>
        <p>{usage?.collector.status||'Loading'}{refreshing?' · Scanning transcripts; large histories can take up to two minutes.':''}</p>
        <div class="analytics-actions"><button onClick={onConfigure}>Collection settings</button><button disabled={refreshing} onClick={()=>void clear()}>{confirmClear?'Confirm clear cache':'Clear cache'}</button>{confirmClear&&<button onClick={()=>setConfirmClear(false)}>Cancel</button>}</div>
      </details>
    </main>
  </>
}
