import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { GrantGate } from './GrantGate'
import { AnalyticsNav, MetricValue, UsageBreakdown, UsageDetails, UsageTrend } from './AnalyticsPrimitives'
import { usagePeriods, usageWindow } from './analyticsPresentation'
import { UsageModelBreakdown } from './UsageModelBreakdown'
import { sumUsageRows, type UsageSource } from './usageAnalytics'
import { describeLastRun, describeUsageActivity, pollUsageRefresh, type UsageCollector, type UsageRefreshMode } from './usageRefresh'

export type UsageStatus = {
  enabled:boolean
  refreshing:boolean
  refresh_minutes:number
  package:string
  install_command:string
  started?:boolean
  collector:UsageCollector
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
  const [confirmRebuild,setConfirmRebuild]=useState(false)
  const [now,setNow]=useState(()=>Date.now()/1000)
  const mounted=useRef(true)
  const readUsage=()=>api<UsageStatus>('GET','/api/usage')
  // A refresh runs in the daemon's background, so the view follows it by reading the
  // status until it ends - whether this view started it or the schedule did.
  const follow=async(first:UsageStatus)=>{
    setRefreshing(first.refreshing)
    try{
      await pollUsageRefresh(first,readUsage,status=>{if(mounted.current){setUsage(status);setNow(Date.now()/1000)}},{cancelled:()=>!mounted.current})
    }catch(cause){if(mounted.current)setError(String(cause))}
    finally{if(mounted.current)setRefreshing(false)}
  }
  useEffect(()=>{
    mounted.current=true
    readUsage().then(value=>{if(mounted.current)void follow(value)}).catch(cause=>{if(mounted.current)setError(String(cause))})
    return()=>{mounted.current=false}
  },[])
  const sourceList=useMemo(()=>Object.values(usage?.cache?.sources||{}).filter((source):source is UsageSource=>!!source).sort((a,b)=>a.source_label.localeCompare(b.source_label)), [usage, uiLocale.value, catalogRevision.value])
  const visibleSources=sourceList.filter(source=>!hiddenSources.includes(source.source_id))
  const daily=usageWindow(visibleSources,Number(range))
  const dates=new Set(daily.map(row=>row.date!))
  const totals=sumUsageRows(daily)
  const periods=usagePeriods(daily,resolution==='monthly')
  const sourceRows=visibleSources.map(source=>({id:source.source_id,label:source.source_label,row:sumUsageRows(source.daily.filter(row=>dates.has(row.date!)))})).sort((a,b)=>b.row.total_tokens-a.row.total_tokens)
  const refresh=async(mode:UsageRefreshMode)=>{
    setRefreshing(true);setError('');setConfirmRebuild(false)
    try{await follow(await api<UsageStatus>('POST','/api/usage/refresh',{mode}))}
    catch(cause){setError(String(cause));setRefreshing(false)}
  }
  const rebuild=()=>{
    if(!confirmRebuild){setConfirmRebuild(true);return}
    void refresh('rebuild')
  }
  const activity=describeUsageActivity(usage?.collector,now)
  const lastRun=describeLastRun(usage?.collector.last_run)
  const busy=refreshing||!!usage?.refreshing
  const clear=async()=>{
    if(!confirmClear){setConfirmClear(true);return}
    setConfirmClear(false);setError('')
    try{setUsage(await api<UsageStatus>('DELETE','/api/usage/cache'))}catch(cause){setError(String(cause))}
  }
  return <>
    <AnalyticsNav label={t("UsageDashboardView.e0a4c655e0")} value={view} onChange={setView} items={[{id:'summary',get label() { return t("UsageDashboardView.8e76a94ac8") }},{id:'trends',get label() { return t("UsageDashboardView.e856c6c6a4") }},{id:'models',get label() { return t("UsageDashboardView.d17d2d78d7") }}]}/>
    <div class="analytics-toolbar">
      <label>{rich("UsageDashboardView.6287ca43ed", { value1: <select value={range} onChange={event=>setRange(event.currentTarget.value)}><option value="7">{t("UsageDashboardView.7f920bb639")}</option><option value="30">{t("UsageDashboardView.ffd7280513")}</option><option value="90">{t("UsageDashboardView.38825b0f64")}</option><option value="0">{t("UsageDashboardView.4410994e21")}</option></select> })}</label>
      <details class="analytics-filter-menu"><summary>{rich("UsageDashboardView.7dea462dd5", { length: visibleSources.length, value2: sourceList.length })}</summary><div>
        <button onClick={()=>setHiddenSources([])}>{t("UsageDashboardView.1fc9a38765")}</button>
        {sourceList.map(source=><label key={source.source_id}><input type="checkbox" checked={!hiddenSources.includes(source.source_id)} onChange={()=>setHiddenSources(current=>current.includes(source.source_id)?current.filter(id=>id!==source.source_id):[...current,source.source_id])}/>{source.source_label}</label>)}
      </div></details>
      {view!=='summary' && <label>{rich("UsageDashboardView.965cfaa58b", { value1: <select value={metric} onChange={event=>setMetric(event.currentTarget.value as typeof metric)}><option value="tokens">{t("UsageDashboardView.a039dfb962")}</option><option value="cost">{t("UsageDashboardView.9ccba222f8")}</option></select> })}</label>}
      {view==='trends' && <label>{rich("UsageDashboardView.bdfbbe633f", { value1: <select value={resolution} onChange={event=>setResolution(event.currentTarget.value as typeof resolution)}><option value="daily">{t("UsageDashboardView.b36c2611dc")}</option><option value="monthly">{t("UsageDashboardView.9b11f6b707")}</option></select> })}</label>}
      <button class="analytics-refresh" disabled={!usage?.enabled||busy} onClick={()=>void refresh('auto')}>{busy ? t("UsageDashboardView.1c0def7be0") : t("UsageDashboardView.0e91610117")}</button>
    </div>
    <main class="analytics-content">
      {activity && <div class={`usage-progress ${busy?'running':''}`} role="status" aria-live="polite"><span>{busy ? '◌' : '·'}</span><strong>{activity}</strong></div>}
      {error && <div class="usage-error" role="alert">{error}</div>}
      {usage?.collector.error && <div class="usage-error" role="alert">{rich("UsageDashboardView.acc9f66289", { error: usage.collector.error })}</div>}
      {!usage&&!error ? <p>{t("UsageDashboardView.80003db053")}</p> : usage&&!usage.enabled ? <GrantGate ids={['usage.ccusage']} heading={t("UsageDashboardView.c4ea661e29")} onGranted={async()=>setUsage(await api<UsageStatus>('GET','/api/usage'))}><p>{t("UsageDashboardView.b0eee02beb")}</p></GrantGate> : !sourceList.length ? <p>{t("UsageDashboardView.9663469dea")}</p> : !visibleSources.length ? <p>{t("UsageDashboardView.507aad6df1")}</p> : <>
        <p class="analytics-caption">{rich("UsageDashboardView.7070e8490d", { value1: daily.length ? t("UsageDashboardView.f942c5e30c", { date: daily[daily.length-1].date, value2: daily[0].date }) : t("UsageDashboardView.cbe5243f42") })}</p>
        {view==='summary' && <>
          <div class="analytics-headlines"><article><span>{t("UsageDashboardView.a039dfb962")}</span><strong><MetricValue value={totals.total_tokens}/></strong></article><article><span>{t("UsageDashboardView.9ccba222f8")}</span><strong><MetricValue value={totals.cost_method==='unavailable'?null:totals.cost_usd} money/></strong></article></div>
          <UsageDetails row={totals}/><UsageTrend rows={daily}/><UsageBreakdown title={t("UsageDashboardView.9504fd56c1")} rows={sourceRows}/>
        </>}
        {view==='trends' && <><UsageTrend rows={periods} metric={metric}/><UsageBreakdown title={t("UsageDashboardView.1a00666222")} rows={periods.map(row=>({id:row.date!,label:row.date!,row}))}/></>}
        {view==='models' && <UsageModelBreakdown sources={visibleSources} visibleDates={dates} resolution={resolution} metric={metric}/>}
      </>}
      <details class="analytics-diagnostics"><summary>{rich("UsageDashboardView.2aef7fb94b", { value1: usage?.cache?.updated_at ? t("UsageDashboardView.1381120916", { value1: new Date(usage.cache.updated_at*1000).toLocaleString(uiLocale.value) }) : '' })}</summary>
        <p>{t("UsageDashboardView.940bf60fd2")}</p>
        <p>{t("UsageDashboardView.e723264605")}</p>
        <p>{usage?.collector.status || t("UsageDashboardView.dc380888c4")}{usage?.collector.ccusage_version ? t("UsageDashboardView.ce22511b38", { ccusage_version: usage.collector.ccusage_version }) : ''}</p>
        {lastRun && <p class="analytics-muted">{lastRun}</p>}
        {!!usage?.collector.advisories?.length && <ul class="usage-advisories">{usage.collector.advisories.map(note=><li key={note}>{note}</li>)}</ul>}
        <div class="analytics-actions"><button onClick={onConfigure}>{t("UsageDashboardView.8f21abd48a")}</button><button disabled={!usage?.enabled||busy} onClick={rebuild}>{confirmRebuild ? t("UsageDashboardView.b19931e61c") : t("UsageDashboardView.47f819abaa")}</button>{confirmRebuild && <button onClick={()=>setConfirmRebuild(false)}>{t("UsageDashboardView.2dd5b9cfa7")}</button>}<button disabled={busy} onClick={()=>void clear()}>{confirmClear ? t("UsageDashboardView.8f0cf24220") : t("UsageDashboardView.d33461e6c0")}</button>{confirmClear && <button onClick={()=>setConfirmClear(false)}>{t("UsageDashboardView.19766ed6cc")}</button>}</div>
      </details>
    </main>
  </>
}
