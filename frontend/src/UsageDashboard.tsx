import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { useModalFocus } from './modalFocus'

type UsageRow = {
  date?:string;month?:string;session_id?:string;model?:string;provider?:string
  input_tokens:number;output_tokens:number;cache_creation_tokens:number;cache_read_tokens:number
  total_tokens:number;cost_usd:number;cost_is_estimate?:boolean
}
type ProviderUsage = {
  provider:string;daily:UsageRow[];monthly:UsageRow[];sessions:UsageRow[];models:UsageRow[];model_daily?:UsageRow[]
  totals:UsageRow;provenance?:{adapter?:string;package?:string;command?:string[]}
}
export type UsageStatus = {
  enabled:boolean;refreshing:boolean;refresh_minutes:number;package:string;install_command:string
  states:Record<'claude'|'codex',{status:string;error?:string;refreshed_at?:number}>
  cache?:{version?:number;updated_at?:number;providers?:Partial<Record<'claude'|'codex',ProviderUsage>>}
}
type Provider = 'claude'|'codex'

const number = new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1})
const integer = new Intl.NumberFormat()
const money = new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2})
const fields = ['input_tokens','output_tokens','cache_creation_tokens','cache_read_tokens','total_tokens','cost_usd'] as const

function sumRows(rows:UsageRow[]):UsageRow {
  const total={input_tokens:0,output_tokens:0,cache_creation_tokens:0,cache_read_tokens:0,total_tokens:0,cost_usd:0}
  for(const row of rows)for(const field of fields)total[field]+=Number(row[field]||0)
  return total
}

function mergeDaily(providers:ProviderUsage[]):UsageRow[] {
  const grouped=new Map<string,UsageRow[]>()
  for(const provider of providers)for(const row of provider.daily){const items=grouped.get(row.date||'')||[];items.push(row);grouped.set(row.date||'',items)}
  return [...grouped.entries()].filter(([date])=>date).map(([date,rows])=>({...sumRows(rows),date})).sort((a,b)=>(b.date||'').localeCompare(a.date||''))
}

function aggregateRows(rows:UsageRow[],key:'month'|'provider'|'model',value:(row:UsageRow)=>string):UsageRow[] {
  const grouped=new Map<string,UsageRow[]>()
  for(const row of rows){const label=value(row);if(!label)continue;const items=grouped.get(label)||[];items.push(row);grouped.set(label,items)}
  return [...grouped.entries()].map(([label,items])=>({...sumRows(items),[key]:label})).sort((a,b)=>(b[key]||'').localeCompare(a[key]||''))
}

function Summary({totals}:{totals:UsageRow}) {
  return <div class="usage-summary">
    <article><span>estimated cost</span><strong>{money.format(totals.cost_usd||0)}</strong></article>
    <article><span>total tokens</span><strong>{number.format(totals.total_tokens||0)}</strong></article>
    <article><span>input</span><strong>{number.format(totals.input_tokens||0)}</strong></article>
    <article><span>output</span><strong>{number.format(totals.output_tokens||0)}</strong></article>
    <article><span>cache read</span><strong>{number.format(totals.cache_read_tokens||0)}</strong></article>
    <article><span>cache create</span><strong>{number.format(totals.cache_creation_tokens||0)}</strong></article>
  </div>
}

function UsageTable({title,rows,label}:{title:string;rows:UsageRow[];label:'date'|'month'|'model'|'provider'}) {
  return <section class="usage-table"><h3>{title}</h3>{rows.length?<div class="usage-table-scroll"><table><thead><tr><th>{label}</th><th>tokens</th><th>input</th><th>output</th><th>cache</th><th>cost est.</th></tr></thead><tbody>{rows.map(row=><tr><td>{row[label]||'unknown'}</td><td>{integer.format(row.total_tokens||0)}</td><td>{integer.format(row.input_tokens||0)}</td><td>{integer.format(row.output_tokens||0)}</td><td>{integer.format((row.cache_read_tokens||0)+(row.cache_creation_tokens||0))}</td><td>{money.format(row.cost_usd||0)}</td></tr>)}</tbody></table></div>:<p>No {title.toLowerCase()} data is cached.</p>}</section>
}

function UsageSeries({rows,label,metric}:{rows:UsageRow[];label:'date'|'month';metric:'tokens'|'cost'}) {
  const values=rows.map(row=>metric==='tokens'?row.total_tokens:row.cost_usd)
  const maximum=Math.max(...values,1)
  return <section class="usage-series" aria-label={`${label} ${metric} time series`}>
    {[...rows].reverse().map(row=>{const value=metric==='tokens'?row.total_tokens:row.cost_usd;return <div class="usage-series-row"><span>{row[label]}</span><i><b style={{width:`${Math.max(1,value/maximum*100)}%`}}/></i><strong>{metric==='tokens'?integer.format(value):money.format(value)}</strong></div>})}
  </section>
}

export function UsageDashboard({onClose,onConfigure}:{onClose:()=>void;onConfigure:()=>void}) {
  const [usage,setUsage]=useState<UsageStatus|null>(null)
  const [selected,setSelected]=useState<'all'|Provider>('all')
  const [view,setView]=useState<'overview'|'timeline'|'models'>('overview')
  const [resolution,setResolution]=useState<'daily'|'monthly'>('daily')
  const [range,setRange]=useState<'7'|'30'|'90'|'all'>('30')
  const [metric,setMetric]=useState<'tokens'|'cost'>('tokens')
  const [refreshing,setRefreshing]=useState<Provider|null>(null)
  const [message,setMessage]=useState('Loading usage cache…')
  const [error,setError]=useState('')
  const [confirmClear,setConfirmClear]=useState(false)
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)

  const load=async()=>{try{const next=await api<UsageStatus>('GET','/api/usage');setUsage(next);setMessage(next.cache?.updated_at?`cache updated ${new Date(next.cache.updated_at*1000).toLocaleString()}`:'No cached usage yet.')}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  useEffect(()=>{void load()},[])
  useEffect(()=>{if(!confirmClear)return;const timer=window.setTimeout(()=>setConfirmClear(false),2000);return()=>clearTimeout(timer)},[confirmClear])

  const providers=usage?.cache?.providers||{}
  const visibleProviders=useMemo(()=>selected==='all'?[providers.claude,providers.codex].filter((item):item is ProviderUsage=>!!item):providers[selected]?[providers[selected]!]:[],[providers,selected])
  const allDaily=mergeDaily(visibleProviders)
  const daily=range==='all'?allDaily:allDaily.slice(0,Number(range))
  const monthly=aggregateRows(daily,'month',row=>(row.date||'').slice(0,7))
  const timeline=resolution==='daily'?daily:monthly
  const visibleDates=new Set(daily.map(row=>row.date))
  const providerTotals=visibleProviders.map(provider=>({...sumRows(provider.daily.filter(row=>visibleDates.has(row.date))),provider:provider.provider}))
  const models=visibleProviders.flatMap(provider=>{
    const source=provider.model_daily?.length?aggregateRows(provider.model_daily.filter(row=>visibleDates.has(row.date)),'model',row=>row.model||'unknown'):provider.models
    return source.map(row=>({...row,model:selected==='all'?`[${provider.provider}] ${row.model}`:row.model}))
  }).sort((a,b)=>(b.total_tokens||0)-(a.total_tokens||0))

  const refreshProvider=async(provider:Provider)=>{
    setRefreshing(provider);setError('');setMessage(`Refreshing ${provider} usage… ccusage may take up to 30 seconds.`)
    try{const next=await api<UsageStatus>('POST','/api/usage/refresh',{provider});setUsage(next);const state=next.states[provider];setMessage(state.error?`${provider} refresh failed; existing cache was preserved.`:`${provider} usage refreshed ${new Date().toLocaleTimeString()}.`)}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause));setMessage(`${provider} refresh failed.`)}
    finally{setRefreshing(null)}
  }
  const refreshAll=async()=>{for(const provider of ['claude','codex'] as Provider[])await refreshProvider(provider)}
  const clear=async()=>{if(!confirmClear){setConfirmClear(true);return}try{const next=await api<UsageStatus>('DELETE','/api/usage/cache');setUsage(next);setMessage('Usage cache cleared.');setConfirmClear(false)}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}

  return <div class="usage-layer" role="dialog" aria-modal="true" aria-label="Usage analytics" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="usage-panel" ref={panel}>
      <header><div><span>USAGE::ANALYTICS</span><strong>Claude + Codex historical tokens and estimated cost</strong></div><div class="usage-header-actions"><button onClick={onConfigure}>configure</button><button aria-label="Close usage analytics" onClick={onClose}>×</button></div></header>
      <div class="usage-actions"><div role="tablist" aria-label="Usage provider"><button role="tab" aria-selected={selected==='all'} class={selected==='all'?'active':''} onClick={()=>setSelected('all')}>all</button><button role="tab" aria-selected={selected==='claude'} class={selected==='claude'?'active':''} onClick={()=>setSelected('claude')}>claude</button><button role="tab" aria-selected={selected==='codex'} class={selected==='codex'?'active':''} onClick={()=>setSelected('codex')}>codex</button></div><button disabled={!usage?.enabled||!!refreshing} onClick={()=>void refreshAll()}>{refreshing?`refreshing ${refreshing}…`:'refresh all'}</button><button class={confirmClear?'confirming':''} disabled={!!refreshing} onClick={()=>void clear()}>{confirmClear?'✓ clear cache':'clear cache'}</button></div>
      <div class={`usage-progress ${refreshing?'running':''}`} role="status" aria-live="polite"><span>{refreshing?'◌':'·'}</span><strong>{message}</strong></div>
      {error&&<div class="usage-error" role="alert">{error}</div>}
      <div class="usage-provider-status">{(['claude','codex'] as Provider[]).map(provider=>{const state=usage?.states[provider];return <article><span class={`state-dot ${state?.status==='ready'?'idle':state?.status==='refreshing'?'working':state?.error?'crashed':'running'}`}/><div><strong>{provider}</strong><small>{refreshing===provider?'refreshing now':state?.status||'loading'}{state?.refreshed_at?` · ${new Date(state.refreshed_at*1000).toLocaleString()}`:''}</small>{state?.error&&<em>{state.error}</em>}</div><button disabled={!usage?.enabled||!!refreshing} onClick={()=>void refreshProvider(provider)}>refresh</button></article>})}</div>
      <main>{!usage?.enabled?<div class="usage-empty"><strong>Usage analytics is disabled.</strong><p>Enable ccusage in Settings, save, then refresh this dashboard.</p><button onClick={onConfigure}>Configure usage analytics</button></div>:visibleProviders.length===0?<div class="usage-empty"><strong>No usage has been cached.</strong><p>Refresh a provider to run its configured ccusage command.</p><button disabled={!!refreshing} onClick={()=>void refreshAll()}>Refresh Claude + Codex</button></div>:<>
        <div class="usage-view-tabs" role="tablist" aria-label="Analytics view"><button role="tab" aria-selected={view==='overview'} class={view==='overview'?'active':''} onClick={()=>setView('overview')}>overview</button><button role="tab" aria-selected={view==='timeline'} class={view==='timeline'?'active':''} onClick={()=>setView('timeline')}>time series</button><button role="tab" aria-selected={view==='models'} class={view==='models'?'active':''} onClick={()=>setView('models')}>model breakdown</button></div>
        <div class="usage-view-controls"><label>range<select value={range} onChange={event=>setRange(event.currentTarget.value as typeof range)}><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option><option value="all">all cached</option></select></label>{view==='timeline'&&<><label>interval<select value={resolution} onChange={event=>setResolution(event.currentTarget.value as typeof resolution)}><option value="daily">daily</option><option value="monthly">monthly</option></select></label><label>metric<select value={metric} onChange={event=>setMetric(event.currentTarget.value as typeof metric)}><option value="tokens">tokens</option><option value="cost">estimated cost</option></select></label></>}</div>
        {view==='overview'&&<><Summary totals={sumRows(daily)}/><div class="usage-tables"><UsageTable title="Daily aggregate" rows={daily} label="date"/><UsageTable title="Provider aggregate" rows={providerTotals} label="provider"/></div></>}
        {view==='timeline'&&<><UsageSeries rows={timeline} label={resolution==='daily'?'date':'month'} metric={metric}/><UsageTable title={`${resolution} detail`} rows={timeline} label={resolution==='daily'?'date':'month'}/></>}
        {view==='models'&&<UsageTable title="Model aggregate" rows={models} label="model"/>}
      </>}</main>
      <footer><span>Historical analytics from unified {usage?.package||'ccusage'} · costs are estimates</span></footer>
    </section>
  </div>
}
