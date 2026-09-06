import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { AnalyticsNav, useChartWidth } from './AnalyticsPrimitives'
import { formatResetRemaining } from './providerAccountDisplay'
import { OPERATIONAL_TELEMETRY_PATH, type OperationalStatus, type QuotaAttribution } from './operationalTelemetry'
import { serverNow } from './serverClock.ts'
import type { ProviderAccount, ProviderAccountsStatus } from './ProviderAccounts'
import {
  accountDisplayLabel,
  quotaPointTime,
  quotaPointValue,
  quotaSeriesPath,
  type QuotaDailyPoint,
  type QuotaRawPoint,
  type QuotaSeries,
  type QuotaSeriesStatus,
  type ResetEvent,
} from './usageAnalytics'

const palette = ['#34d399','#60a5fa','#f59e0b','#c084fc','#f472b6','#22d3ee','#fb7185','#a3e635']
const percent = (value:number|null|undefined)=>value==null?'unavailable':`${value.toFixed(1)}%`

function accountFor(accounts:ProviderAccount[],series:QuotaSeries):ProviderAccount|undefined {
  return accounts.find(account=>account.id===series.account_id)
}

function seriesLabel(accounts:ProviderAccount[],series:QuotaSeries):string {
  const account=accountFor(accounts,series)
  const label=account?accountDisplayLabel(account):`${series.provider} · ${series.account_id.slice(0,8)}`
  const owner=series.provider_account_uuid?'':' · unverified legacy identity'
  return `${label}${owner}`
}

function QuotaChart({
  title,
  windowName,
  series,
  accounts,
  resets,
}:{
  title:string
  windowName:'session'|'weekly'
  series:QuotaSeries[]
  accounts:ProviderAccount[]
  resets:ResetEvent[]
}) {
  const [chartRef,width]=useChartWidth()
  const times=series.flatMap(item=>item.points.map(quotaPointTime))
  // Daemon clock: `times` are daemon-stamped sample instants, and an axis bound
  // taken from a browser clock that disagrees would shift the whole plot.
  const minimum=times.reduce((minimum,time)=>Math.min(minimum,time),serverNow())
  const maximum=times.reduce((maximum,time)=>Math.max(maximum,time),minimum+1)
  const x=(time:number)=>52+(time-minimum)/(maximum-minimum)*(width-68)
  const y=(value:number)=>164-Math.max(0,Math.min(100,value))/100*136
  const colored=series.map((item,index)=>({item,color:palette[index%palette.length]}))
  return <section ref={chartRef} class="quota-chart">
    <h3>{title}</h3>
    <div class="quota-chart-legend">{colored.map(({item,color})=><span key={`${item.account_id}-${item.provider_account_uuid}`}>
      <i style={{background:color}}/>{seriesLabel(accounts,item)}
    </span>)}</div>
    {times.length?<svg viewBox={`0 0 ${width} 188`} role="img" aria-label={`${title} account timelines`}>
      {[0,25,50,75,100].map(value=><g key={value}>
        <line x1="52" x2={width-16} y1={y(value)} y2={y(value)} class="quota-grid-line"/>
        <text x="45" y={y(value)+3} text-anchor="end">{value}%</text>
      </g>)}
      {resets.filter(item=>item.window===windowName&&item.observed_at>=minimum&&item.observed_at<=maximum).map(item=><line
        key={item.id}
        x1={x(item.observed_at)} x2={x(item.observed_at)} y1="22" y2="168"
        class={`quota-reset-marker ${item.classification}`}
      ><title>{`${item.classification} reset: ${item.before_value}% to ${item.after_value}%`}</title></line>)}
      {colored.map(({item,color})=>{
        const points=item.points.map(point=>({time:quotaPointTime(point),value:quotaPointValue(point,windowName)})).filter(
          (point):point is {time:number;value:number}=>point.value!=null,
        )
        return <polyline
          key={`${item.account_id}-${item.provider_account_uuid}`}
          points={points.map(point=>`${x(point.time)},${y(point.value)}`).join(' ')}
          style={{stroke:color}}
        ><title>{seriesLabel(accounts,item)}</title></polyline>
      })}
      <text x="52" y="184">{new Date(minimum*1000).toLocaleDateString()}</text>
      <text x={width-16} y="184" text-anchor="end">{new Date(maximum*1000).toLocaleDateString()}</text>
    </svg>:<p>No {title.toLowerCase()} readings in this range.</p>}
  </section>
}

function QuotaDetail({status,accounts}:{status:QuotaSeriesStatus;accounts:ProviderAccount[]}) {
  const [limit,setLimit]=useState(50)
  const rows=status.series.flatMap(series=>series.points.map(point=>({series,point}))).sort((a,b)=>quotaPointTime(b.point)-quotaPointTime(a.point))
  return <details class="analytics-diagnostics"><summary>Recorded readings ({rows.length})</summary>
    {rows.slice(0,limit).map(({series,point})=>{
      const daily=!('sampled_at' in point),sample=point as QuotaRawPoint,rollup=point as QuotaDailyPoint
      return <article class="quota-reading" key={`${series.account_id}-${series.provider_account_uuid}-${quotaPointTime(point)}`}>
        <strong>{daily?rollup.day:new Date(sample.sampled_at*1000).toLocaleString()}</strong><span>{seriesLabel(accounts,series)}</span>
        <dl class="analytics-facts"><div><dt>5h used</dt><dd>{daily?`${percent(rollup.session_first)} to ${percent(rollup.session_last)}`:percent(sample.session?.used_percent)}</dd></div><div><dt>Weekly used</dt><dd>{daily?`${percent(rollup.weekly_first)} to ${percent(rollup.weekly_last)}`:percent(sample.weekly?.used_percent)}</dd></div></dl>
        <small>{daily?`${rollup.samples} samples · ${rollup.errors} errors · 5h range ${percent(rollup.session_min)} to ${percent(rollup.session_max)} · weekly range ${percent(rollup.weekly_min)} to ${percent(rollup.weekly_max)}`:`${sample.freshness}${sample.error?` · ${sample.error}`:''}`}</small>
      </article>
    })}
    {rows.length>limit&&<button onClick={()=>setLimit(limit+50)}>Show more readings</button>}
  </details>
}

function ResetLog({items,accounts}:{items:ResetEvent[];accounts:ProviderAccount[]}) {
  return <section class="reset-log"><h3>Reset evidence log</h3>{items.length?items.map(item=>{
    const account=accounts.find(candidate=>candidate.id===item.account_id)
    return <article key={item.id} class={item.classification==='unexpected'&&item.confirmed&&!item.suppression_reason&&!item.review_status?'confirmed-unexpected':''}>
      <strong>{item.review_status==='manual_usage'?'manual Codex usage':item.review_status==='discarded'?'discarded detection error':item.review_status==='seen'?`acknowledged ${item.window} reset · ${account?.label||item.account_id}`:`${item.classification} ${item.window} reset · ${account?.label||item.account_id}`}</strong>
      <span>{item.before_value}% to {item.after_value}% · {new Date(item.observed_at*1000).toLocaleString()}</span>
      <small>confidence {item.confidence} · {item.confirmed?'confirmed':'awaiting confirmation'}{item.suppression_reason?` · suppressed: ${item.suppression_reason}`:''}{item.expected_reset_at?` · expected ${new Date(item.expected_reset_at*1000).toLocaleString()}`:''}</small>
    </article>
  }):<p>No reset movements recorded in this range.</p>}</section>
}

export function QuotaAnalytics({onManage}:{onManage?:()=>void}) {
  const [view,setView]=useState<'current'|'history'|'resets'|'attribution'>('current')
  const [accountsStatus,setAccountsStatus]=useState<ProviderAccountsStatus|null>(null)
  const [status,setStatus]=useState<QuotaSeriesStatus|null>(null)
  const [attribution,setAttribution]=useState<QuotaAttribution[]|null>(null)
  const [provider,setProvider]=useState('all')
  const [account,setAccount]=useState('all')
  const [range,setRange]=useState<'7'|'30'|'90'|'all'>('30')
  const [resolution,setResolution]=useState<'raw'|'daily'>('daily')
  const [windowName,setWindowName]=useState<'session'|'weekly'>('session')
  const [error,setError]=useState('')
  const [revision,setRevision]=useState(0)
  const accounts=useMemo(()=>(accountsStatus?.accounts||[]).filter(item=>(provider==='all'||item.provider===provider)&&(!item.conflict||item.conflict.is_primary)),[accountsStatus,provider])
  useEffect(()=>{
    let cancelled=false
    api<ProviderAccountsStatus>('GET','/api/provider-accounts').then(value=>{if(!cancelled)setAccountsStatus(value)}).catch(cause=>{if(!cancelled)setError(String(cause))})
    return()=>{cancelled=true}
  },[revision])
  useEffect(()=>{const timer=window.setInterval(()=>{if(!document.hidden)setRevision(value=>value+1)},60000);return()=>clearInterval(timer)},[])
  useEffect(()=>{if(account!=='all'&&!accounts.some(item=>item.id===account))setAccount('all')},[accounts,account])
  useEffect(()=>{
    let cancelled=false
    setError('');setStatus(null);setAttribution(null)
    if(view==='history'||view==='resets'){
      api<QuotaSeriesStatus>('GET',quotaSeriesPath({provider:provider==='all'?undefined:provider,account:account==='all'?undefined:account,range,resolution}))
        .then(value=>{if(!cancelled)setStatus(value)}).catch(cause=>{if(!cancelled)setError(String(cause))})
    }else if(view==='attribution'){
      const query=new URLSearchParams({limit:'500'})
      if(provider!=='all')query.set('provider',provider)
      if(account!=='all')query.set('account',account)
      api<OperationalStatus>('GET',`${OPERATIONAL_TELEMETRY_PATH}?${query}`).then(value=>{if(!cancelled)setAttribution(value.quota.attributions)}).catch(cause=>{if(!cancelled)setError(String(cause))})
    }
    return()=>{cancelled=true}
  },[view,provider,account,range,resolution,revision])
  const visibleAccounts=accounts.filter(item=>account==='all'||item.id===account)
  const cutoff=range==='all'?0:serverNow()-Number(range)*86400
  const visibleAttribution=(attribution||[]).filter(item=>item.interval_end>=cutoff)
  return <>
    <AnalyticsNav label="Quota views" value={view} onChange={setView} items={[{id:'current',label:'Current'},{id:'history',label:'History'},{id:'resets',label:'Resets'},{id:'attribution',label:'Attribution'}]}/>
    <div class="analytics-toolbar">
      {view!=='current'&&<label>Range<select value={range} onChange={event=>setRange(event.currentTarget.value as typeof range)}><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option><option value="all">All retained</option></select></label>}
      <details class="analytics-filter-menu"><summary>Filters{provider!=='all'||account!=='all'?` (${Number(provider!=='all')+Number(account!=='all')})`:''}</summary><div>
        <label>Provider<select value={provider} onChange={event=>{setProvider(event.currentTarget.value);setAccount('all')}}><option value="all">All providers</option>{(accountsStatus?.providers||[]).map(name=><option key={name} value={name}>{name}</option>)}</select></label>
        <label>Account<select value={account} onChange={event=>setAccount(event.currentTarget.value)}><option value="all">All saved accounts</option>{accounts.map(item=><option key={item.id} value={item.id}>{accountDisplayLabel(item)}</option>)}</select></label>
      </div></details>
      {view==='history'&&<><label>Window<select value={windowName} onChange={event=>setWindowName(event.currentTarget.value as typeof windowName)}><option value="session">5 hours</option><option value="weekly">Weekly</option></select></label><label>Detail<select value={resolution} onChange={event=>setResolution(event.currentTarget.value as typeof resolution)}><option value="daily">Daily</option><option value="raw">Raw samples</option></select></label></>}
      <button class="analytics-refresh" onClick={()=>setRevision(value=>value+1)}>Reload readings</button>
    </div>
    <main class="analytics-content">
      {error&&<div class="usage-error" role="alert">{error}</div>}
      <p class="analytics-caption">{provider==='all'?'All providers':provider} · {account==='all'?'All saved accounts':accounts.find(item=>item.id===account)?.label||account} · quota capacity</p>
      {view==='current'&&<>
        {onManage&&<div class="analytics-actions"><button onClick={onManage}>Manage accounts</button></div>}
        {!accountsStatus?<p>Loading accounts…</p>:!visibleAccounts.length?<p>No saved accounts match these filters.</p>:<div class="quota-account-grid">{visibleAccounts.map(item=>{
          const quota=item.quota,ready=quota?.status==='ready',now=serverNow()
          const stale=!!quota?.refreshed_at&&(now-quota.refreshed_at)>(accountsStatus.stale_minutes||30)*60
          return <article class="quota-account-card" key={item.id}>
            <header><strong>{accountDisplayLabel(item)}</strong>{accountsStatus.selected[item.provider]===item.id&&<span class="analytics-badge">Selected</span>}</header>
            <small>{quota?.refreshed_at?`Read ${new Date(quota.refreshed_at*1000).toLocaleString()}${stale?' · stale':''}`:'No successful reading'}</small>
            {(['session','weekly','fable'] as const).filter(key=>key!=='fable'||quota?.fable).map(key=>{
              const window=ready?quota?.[key]:null,remaining=window?Math.max(0,100-window.used_percent):null
              return <div class="quota-capacity" key={key}><div><span>{key==='session'?'5 hours':key==='weekly'?'Weekly':'Fable'}</span><strong>{remaining==null?'Unavailable':`${Math.round(remaining)}% left`}</strong></div>
                {remaining!=null&&<meter min="0" max="100" value={remaining} aria-label={`${key} quota remaining`}/>}
                <small>{window?.resets_at?(window.resets_at<=now?'Reset due; awaiting a reading':`Resets in ${formatResetRemaining(window.resets_at,now)}`):'Reset time unavailable'}</small>
              </div>
            })}
            {quota?.error&&<p class="usage-error">{quota.error}</p>}
          </article>
        })}</div>}
      </>}
      {view==='history'&&(status?<><QuotaChart title={windowName==='session'?'5-hour utilization':'Weekly utilization'} windowName={windowName} series={status.series} accounts={accounts} resets={status.resets}/><QuotaDetail status={status} accounts={accounts}/></>:!error&&<p>Loading quota history…</p>)}
      {view==='resets'&&(status?<ResetLog items={status.resets} accounts={accounts}/>:!error&&<p>Loading reset history…</p>)}
      {view==='attribution'&&<section class="attribution-log"><p class="analytics-caption">Estimates from the latest 500 recorded movements, filtered to this range. Correlation does not establish who used an account.</p>
        {attribution===null&&!error?<p>Loading attribution…</p>:visibleAttribution.length?visibleAttribution.map(item=><details class="analytics-diagnostics" key={`${item.sample_id}-${item.window}`}>
          <summary>{accounts.find(account=>account.id===item.account_id)?.label||item.provider} · {item.window==='session'?'5h':'Weekly'} +{item.quota_delta.toFixed(1)}% · {new Date(item.interval_end*1000).toLocaleString()}</summary>
          <dl class="analytics-facts"><div><dt>Correlated mux activity</dt><dd>{item.correlated_estimate.toFixed(1)}% ({item.correlated_low.toFixed(1)}-{item.correlated_high.toFixed(1)}%)</dd></div><div><dt>External / unassigned</dt><dd>{item.external_estimate.toFixed(1)}% ({item.external_low.toFixed(1)}-{item.external_high.toFixed(1)}%)</dd></div><div><dt>Confidence</dt><dd>{item.confidence}</dd></div><div><dt>Overlapping sessions</dt><dd>{item.concurrent_sessions}</dd></div></dl>
          <small>Sample gap {Math.round(item.sample_gap_seconds/60)}m · provider lag allowance {Math.round(item.provider_lag_seconds)}s</small>
        </details>):<p>No attribution evidence in these recent movements for this range.</p>}
      </section>}
    </main>
  </>
}
