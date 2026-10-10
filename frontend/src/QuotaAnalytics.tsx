import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { AnalyticsNav, useChartWidth } from './AnalyticsPrimitives'
import { formatResetRemaining } from './providerAccountDisplay'
import { OPERATIONAL_TELEMETRY_PATH, type OperationalStatus, type QuotaAttribution } from './operationalTelemetry'
import { serverNow } from './serverClock.ts'
import type { ProviderAccount, ProviderAccountsStatus } from './ProviderAccounts'
import {
  QUOTA_WINDOW_LABELS,
  formatQuotaDay,
  formatQuotaInstant,
  formatQuotaPercent,
  nearestInstant,
  quotaDomain,
  quotaDomainLabel,
  quotaGapLimit,
  quotaReadings,
  quotaTimeTicks,
  quotaWindowLabel,
  readingAsOf,
  readingInstants,
  readingSegments,
  utcDayOf,
  type QuotaReading,
  type QuotaWindowName,
} from './quotaTimeline'
import {
  accountDisplayLabel,
  quotaPointTime,
  quotaSeriesPath,
  type QuotaDailyPoint,
  type QuotaSeries,
  type QuotaSeriesStatus,
  type ResetEvent,
} from './usageAnalytics'

const palette = ['#34d399','#60a5fa','#f59e0b','#c084fc','#f472b6','#22d3ee','#fb7185','#a3e635']
// Plot geometry in viewBox units; the width is the measured container width.
const CHART = {height:244,top:12,bottom:30,left:44,right:14}
// Above this many daily points, per-day markers stop being readable and only the line is drawn.
const MAX_DAILY_MARKERS = 600

function accountFor(accounts:ProviderAccount[],series:QuotaSeries):ProviderAccount|undefined {
  return accounts.find(account=>account.id===series.account_id)
}

function seriesLabel(accounts:ProviderAccount[],series:QuotaSeries):string {
  const account=accountFor(accounts,series)
  const label=account?accountDisplayLabel(account):`${series.provider} · ${series.account_id.slice(0,8)}`
  const owner=series.provider_account_uuid?'':' · unverified legacy identity'
  return `${label}${owner}`
}

const seriesKey=(series:QuotaSeries)=>`${series.provider}-${series.account_id}-${series.provider_account_uuid||''}`

const accountName=(accounts:ProviderAccount[],accountId:string,fallback:string)=>accounts.find(account=>account.id===accountId)?.label||fallback

function resetTitle(item:ResetEvent,label:string,now:number):string {
  return `${item.classification} ${quotaWindowLabel(item.window).toLowerCase()} reset · ${label}: ${formatQuotaPercent(item.before_value)} to ${formatQuotaPercent(item.after_value)} used · ${formatQuotaInstant(item.observed_at,now)}`
}

/** The facts that qualify one readout value: when it was read, and what the window was doing. */
function readingDetail(reading:QuotaReading,instant:number,daily:boolean,now:number):string {
  const parts=daily
    ?[
      reading.day===utcDayOf(now)?'latest reading today':'last reading of the day',
      reading.first!=null&&reading.first!==reading.used?`day began at ${formatQuotaPercent(reading.first)}`:'',
      reading.peak!=null&&reading.peak!==reading.used?`peak ${formatQuotaPercent(reading.peak)}`:'',
      reading.samples!=null?`${reading.samples} readings`:'',
    ]
    :[
      Math.abs(instant-reading.time)>300?`as of ${formatQuotaInstant(reading.time,now)}`:'',
      reading.resetsAt?`next reset ${formatQuotaInstant(reading.resetsAt,now)}`:'',
    ]
  return parts.filter(Boolean).join(' · ')
}

function QuotaChart({
  windowName,
  status,
  accounts,
}:{
  windowName:QuotaWindowName
  status:QuotaSeriesStatus
  accounts:ProviderAccount[]
}) {
  const [chartRef,width]=useChartWidth()
  // The moment being read, snapped to a reading; null follows the newest one.
  const [inspected,setInspected]=useState<number|null>(null)
  const daily=status.resolution==='daily'
  const windowLabel=QUOTA_WINDOW_LABELS[windowName]
  const lines=useMemo(()=>status.series.map((series,index)=>({
    key:seriesKey(series),
    label:seriesLabel(accounts,series),
    color:palette[index%palette.length],
    readings:quotaReadings(series.points,windowName),
  })),[status,windowName,accounts])
  const instants=useMemo(()=>readingInstants(lines.map(line=>line.readings),daily?0:60),[lines,daily])
  // Daemon clock: readings are daemon-stamped, and an axis bound taken from a browser
  // clock that disagrees would shift the whole plot.
  const now=serverNow()
  const [start,end]=useMemo(()=>quotaDomain(status,lines.flatMap(line=>line.readings),serverNow()),[status,lines])
  const plotWidth=Math.max(1,width-CHART.left-CHART.right)
  const plotBottom=CHART.height-CHART.bottom
  const ticks=useMemo(
    ()=>quotaTimeTicks(start,end,Math.max(2,Math.floor(plotWidth/92)),{zone:daily?'utc':'local',daily}),
    [start,end,plotWidth,daily],
  )
  const x=(time:number)=>CHART.left+(time-start)/(end-start)*plotWidth
  const y=(value:number)=>CHART.top+(1-Math.max(0,Math.min(100,value))/100)*(plotBottom-CHART.top)
  const paths=useMemo(()=>lines.map(line=>readingSegments(line.readings,quotaGapLimit(line.readings,status.resolution)).map(segment=>
    // A lone reading between two outages is a zero-length run; the round cap draws it as a dot.
    segment.length===1
      ?`M${x(segment[0].time).toFixed(1)},${y(segment[0].used).toFixed(1)}l0.01,0`
      :segment.map((reading,index)=>`${index?'L':'M'}${x(reading.time).toFixed(1)},${y(reading.used).toFixed(1)}`).join(''),
  ).join('')),[lines,status.resolution,start,end,plotWidth])
  const active=inspected!=null?nearestInstant(instants,inspected):instants.length?instants[instants.length-1]:null
  const atLatest=active!=null&&active===instants[instants.length-1]
  const current=lines.map(line=>({line,reading:active==null?null:readingAsOf(line.readings,active,daily?0:Infinity)}))
  const markers=daily&&lines.reduce((total,line)=>total+line.readings.length,0)<=MAX_DAILY_MARKERS
  const resets=status.resets.filter(item=>item.window===windowName&&item.observed_at>=start&&item.observed_at<=end)
  const range=quotaDomainLabel(start,end,daily,now)
  const timeAt=(event:{clientX:number;currentTarget:Element})=>{
    const rect=event.currentTarget.getBoundingClientRect()
    return start+((event.clientX-rect.left)*(width/rect.width)-CHART.left)/plotWidth*(end-start)
  }
  const when=active==null?'':daily?`${formatQuotaDay(utcDayOf(active),now)} (UTC day)`:formatQuotaInstant(active,now)
  return <section ref={chartRef} class="quota-chart">
    <header class="quota-chart-header">
      <h3>{windowLabel} window · % of limit used</h3>
      <small>{range}</small>
    </header>
    <p class="quota-chart-explainer">{daily
      ?`One point per account per UTC day, at that day's last reading.`
      :`One line per account, through every reading.`} 100% means the {windowLabel.toLowerCase()} limit is used up.</p>
    {instants.length?<>
      <div
        class="quota-plot"
        tabIndex={0}
        aria-label={`${windowLabel} window chart. Left and right arrow keys step through readings.`}
        onKeyDown={event=>{
          const index=active==null?instants.length-1:instants.indexOf(active)
          const next=event.key==='ArrowLeft'?Math.max(0,index-1)
            :event.key==='ArrowRight'?Math.min(instants.length-1,index+1)
            :event.key==='Home'?0
            :event.key==='End'?instants.length-1
            :null
          if(next==null)return
          event.preventDefault()
          setInspected(instants[next])
        }}
      >
        <svg
          viewBox={`0 0 ${width} ${CHART.height}`}
          role="img"
          aria-label={`${windowLabel} window percent used per account, ${range}`}
          onPointerDown={event=>setInspected(timeAt(event))}
          onPointerMove={event=>{if(event.pointerType==='mouse'||event.buttons)setInspected(timeAt(event))}}
        >
          {[0,25,50,75,100].map(value=><g key={value}>
            <line x1={CHART.left} x2={width-CHART.right} y1={y(value)} y2={y(value)} class="quota-grid-line"/>
            <text x={CHART.left-6} y={y(value)+4} text-anchor="end">{value}%</text>
          </g>)}
          {ticks.map(tick=>{
            const position=x(tick.time)
            const anchor=position-CHART.left<40?'start':width-CHART.right-position<40?'end':'middle'
            return <g key={tick.time}>
              <line x1={position} x2={position} y1={CHART.top} y2={plotBottom+4} class="quota-tick-line"/>
              <text x={position} y={CHART.height-9} text-anchor={anchor} class="quota-tick-label">{tick.label}</text>
            </g>
          })}
          {resets.map(item=><line
            key={item.id}
            x1={x(item.observed_at)} x2={x(item.observed_at)} y1={CHART.top} y2={plotBottom}
            class={`quota-reset-marker ${item.classification}`}
          ><title>{resetTitle(item,accountName(accounts,item.account_id,item.provider),now)}</title></line>)}
          {lines.map((line,index)=><path key={line.key} d={paths[index]} class="quota-line" style={{stroke:line.color}}/>)}
          {markers&&lines.map(line=>line.readings.map(reading=><circle
            key={`${line.key}-${reading.time}`}
            cx={x(reading.time)} cy={y(reading.used)} r="3"
            class="quota-point" style={{fill:line.color}}
          ><title>{`${line.label}: ${formatQuotaPercent(reading.used)} used · ${reading.day?formatQuotaDay(reading.day,now):''} (UTC day)`}</title></circle>))}
          {active!=null&&<line x1={x(active)} x2={x(active)} y1={CHART.top} y2={plotBottom} class="quota-inspect-line"/>}
          {current.map(({line,reading})=>reading&&<circle
            key={line.key}
            cx={x(reading.time)} cy={y(reading.used)} r="4.5"
            class="quota-inspect-point" style={{fill:line.color}}
          />)}
        </svg>
      </div>
      <div class="quota-readout" aria-live="polite">
        <header>
          <strong>{when}</strong>
          {atLatest?<span class="analytics-badge">Latest</span>:<button type="button" onClick={()=>setInspected(null)}>Show latest</button>}
        </header>
        <table><tbody>{current.map(({line,reading})=><tr key={line.key}>
          <th scope="row"><i style={{background:line.color}}/>{line.label}</th>
          <td class="quota-value">{reading?`${formatQuotaPercent(reading.used)} used`:daily?'no reading that day':'no reading yet'}</td>
          <td class="quota-detail">{reading&&active!=null?readingDetail(reading,active,daily,now):''}</td>
        </tr>)}</tbody></table>
        <small>Hover or tap the chart to read another moment; arrow keys step between readings.{resets.length?' Dashed lines mark observed resets: green scheduled, pink unexpected, grey uncertain.':''}</small>
      </div>
    </>:<p>No {windowLabel.toLowerCase()} readings in this range.</p>}
  </section>
}

function dailyCell(point:QuotaDailyPoint,window:QuotaWindowName) {
  const first=point[`${window}_first`],last=point[`${window}_last`],peak=point[`${window}_max`]
  if(last==null)return 'no reading'
  return <>
    {first!=null&&first!==last?`${formatQuotaPercent(first)} → ${formatQuotaPercent(last)}`:formatQuotaPercent(last)}
    {peak!=null&&peak!==last&&<small>peak {formatQuotaPercent(peak)}</small>}
  </>
}

function QuotaReadings({status,accounts,windowName}:{status:QuotaSeriesStatus;accounts:ProviderAccount[];windowName:QuotaWindowName}) {
  const [limit,setLimit]=useState(50)
  const daily=status.resolution==='daily'
  const now=serverNow()
  const rows=useMemo(
    ()=>status.series.flatMap(series=>series.points.map(point=>({series,point}))).sort((a,b)=>quotaPointTime(b.point)-quotaPointTime(a.point)),
    [status],
  )
  // The charted window leads, so the column beside the time answers the chart's question.
  const windows:QuotaWindowName[]=windowName==='weekly'?['weekly','session']:['session','weekly']
  return <details class="analytics-diagnostics quota-readings">
    <summary>{daily?'Daily readings':'Every reading'} in this range ({rows.length.toLocaleString()})
      <small>Newest first. {daily
        ?'Days are UTC calendar days; a cell gives the first and last reading of that day.'
        :'Times are local.'}</small>
    </summary>
    <div class="quota-readings-scroll"><table>
      <thead><tr>
        <th scope="col">{daily?'Day (UTC)':'Time'}</th>
        <th scope="col">Account</th>
        {windows.map(window=><th scope="col" key={window} class="quota-value">{QUOTA_WINDOW_LABELS[window]} used</th>)}
      </tr></thead>
      <tbody>{rows.slice(0,limit).map(({series,point})=>'sampled_at' in point
        ?<tr key={`${seriesKey(series)}-${point.id}`}>
          <td>{formatQuotaInstant(point.sampled_at,now)}{point.freshness!=='fresh'&&<small>{point.error||point.freshness}</small>}</td>
          <td>{seriesLabel(accounts,series)}</td>
          {windows.map(window=><td key={window} class="quota-value">{formatQuotaPercent(point[window]?.used_percent)}</td>)}
        </tr>
        :<tr key={`${seriesKey(series)}-${point.day}`}>
          <td>{formatQuotaDay(point.day,now)}{point.errors>0&&<small>{point.errors} of {point.samples} readings failed</small>}</td>
          <td>{seriesLabel(accounts,series)}</td>
          {windows.map(window=><td key={window} class="quota-value">{dailyCell(point,window)}</td>)}
        </tr>)}</tbody>
    </table></div>
    {rows.length>limit&&<button onClick={()=>setLimit(limit+50)}>Show 50 more</button>}
  </details>
}

function ResetLog({items,accounts}:{items:ResetEvent[];accounts:ProviderAccount[]}) {
  const now=serverNow()
  return <section class="reset-log"><h3>Reset evidence log</h3>{items.length?items.map(item=>{
    const account=accountName(accounts,item.account_id,item.account_id),window=quotaWindowLabel(item.window).toLowerCase()
    return <article key={item.id} class={item.classification==='unexpected'&&item.confirmed&&!item.suppression_reason&&!item.review_status?'confirmed-unexpected':''}>
      <strong>{item.review_status==='manual_usage'?'manual Codex usage':item.review_status==='discarded'?'discarded detection error':item.review_status==='seen'?`acknowledged ${window} reset · ${account}`:`${item.classification} ${window} reset · ${account}`}</strong>
      <span>{formatQuotaPercent(item.before_value)} → {formatQuotaPercent(item.after_value)} used · {formatQuotaInstant(item.observed_at,now)}</span>
      <small>confidence {item.confidence} · {item.confirmed?'confirmed':'awaiting confirmation'}{item.suppression_reason?` · suppressed: ${item.suppression_reason}`:''}{item.expected_reset_at?` · expected ${formatQuotaInstant(item.expected_reset_at,now)}`:''}</small>
    </article>
  }):<p>No reset movements recorded in this range.</p>}</section>
}

function AttributionLog({items,accounts,loading}:{items:QuotaAttribution[];accounts:ProviderAccount[];loading:boolean}) {
  const now=serverNow()
  return <section class="attribution-log"><p class="analytics-caption">Estimates from the latest 500 recorded movements, filtered to this range. Correlation does not establish who used an account.</p>
    {loading?<p>Loading attribution…</p>:items.length?items.map(item=><details class="analytics-diagnostics" key={`${item.sample_id}-${item.window}`}>
      <summary>{accountName(accounts,item.account_id,item.provider)} · {quotaWindowLabel(item.window)} +{item.quota_delta.toFixed(1)}% · {formatQuotaInstant(item.interval_end,now)}</summary>
      <dl class="analytics-facts"><div><dt>Correlated mux activity</dt><dd>{item.correlated_estimate.toFixed(1)}% ({item.correlated_low.toFixed(1)}-{item.correlated_high.toFixed(1)}%)</dd></div><div><dt>External / unassigned</dt><dd>{item.external_estimate.toFixed(1)}% ({item.external_low.toFixed(1)}-{item.external_high.toFixed(1)}%)</dd></div><div><dt>Confidence</dt><dd>{item.confidence}</dd></div><div><dt>Overlapping sessions</dt><dd>{item.concurrent_sessions}</dd></div></dl>
      <small>Movement between {formatQuotaInstant(item.interval_start,now)} and {formatQuotaInstant(item.interval_end,now)} · sample gap {Math.round(item.sample_gap_seconds/60)}m · provider lag allowance {Math.round(item.provider_lag_seconds)}s</small>
    </details>):<p>No attribution evidence in these recent movements for this range.</p>}
  </section>
}

function AccountCards({status,accounts}:{status:ProviderAccountsStatus;accounts:ProviderAccount[]}) {
  const now=serverNow()
  return <div class="quota-account-grid">{accounts.map(item=>{
    const quota=item.quota,ready=quota?.status==='ready'
    const stale=!!quota?.refreshed_at&&(now-quota.refreshed_at)>(status.stale_minutes||30)*60
    return <article class="quota-account-card" key={item.id}>
      <header><strong>{accountDisplayLabel(item)}</strong>{status.selected[item.provider]===item.id&&<span class="analytics-badge">Selected</span>}</header>
      {/* formatResetRemaining measures any interval; read backwards here it is the reading's age. */}
      <small>{quota?.refreshed_at?`Last reading ${formatQuotaInstant(quota.refreshed_at,now)} (${formatResetRemaining(now,quota.refreshed_at)} ago)${stale?' · stale':''}`:'No successful reading'}</small>
      {(['weekly','session','fable'] as const).filter(key=>key!=='fable'||quota?.fable).map(key=>{
        const window=ready?quota?.[key]:null,remaining=window?Math.max(0,100-window.used_percent):null
        return <div class="quota-capacity" key={key}>
          <div><span>{QUOTA_WINDOW_LABELS[key]} window</span><strong>{remaining==null?'Unavailable':`${Math.round(remaining)}% left`}</strong></div>
          {remaining!=null&&<meter min="0" max="100" value={remaining} aria-label={`${QUOTA_WINDOW_LABELS[key]} window quota remaining`}/>}
          <small>{window?`${formatQuotaPercent(window.used_percent)} used · `:''}{window?.resets_at
            ?window.resets_at<=now?'reset due; awaiting a reading':`resets in ${formatResetRemaining(window.resets_at,now)} (${formatQuotaInstant(window.resets_at,now)})`
            :'reset time unavailable'}</small>
        </div>
      })}
      {quota?.error&&<p class="usage-error">{quota.error}</p>}
    </article>
  })}</div>
}

export function QuotaAnalytics({onManage}:{onManage?:()=>void}) {
  const [view,setView]=useState<'current'|'history'|'resets'|'attribution'>('current')
  const [accountsStatus,setAccountsStatus]=useState<ProviderAccountsStatus|null>(null)
  const [status,setStatus]=useState<QuotaSeriesStatus|null>(null)
  const [attribution,setAttribution]=useState<QuotaAttribution[]|null>(null)
  const [provider,setProvider]=useState('all')
  const [account,setAccount]=useState('all')
  const [range,setRange]=useState<'7'|'30'|'90'|'all'>('7')
  const [resolution,setResolution]=useState<'raw'|'daily'>('daily')
  const [windowName,setWindowName]=useState<QuotaWindowName>('weekly')
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
  // A changed question discards the old answer; the minute refresh keeps it on screen until
  // the new one lands, so the chart neither flashes nor loses the moment being read.
  useEffect(()=>{setStatus(null)},[provider,account,range,resolution])
  useEffect(()=>{setAttribution(null)},[provider,account])
  useEffect(()=>{
    let cancelled=false
    setError('')
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
      {view!=='current'&&<label>Range<select value={range} onChange={event=>setRange(event.currentTarget.value as typeof range)}><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="all">All retained</option></select></label>}
      <details class="analytics-filter-menu"><summary>Filters{provider!=='all'||account!=='all'?` (${Number(provider!=='all')+Number(account!=='all')})`:''}</summary><div>
        <label>Provider<select value={provider} onChange={event=>{setProvider(event.currentTarget.value);setAccount('all')}}><option value="all">All providers</option>{(accountsStatus?.providers||[]).map(name=><option key={name} value={name}>{name}</option>)}</select></label>
        <label>Account<select value={account} onChange={event=>setAccount(event.currentTarget.value)}><option value="all">All saved accounts</option>{accounts.map(item=><option key={item.id} value={item.id}>{accountDisplayLabel(item)}</option>)}</select></label>
      </div></details>
      {view==='history'&&<>
        <label>Window<select value={windowName} onChange={event=>setWindowName(event.currentTarget.value as QuotaWindowName)}><option value="weekly">{QUOTA_WINDOW_LABELS.weekly}</option><option value="session">{QUOTA_WINDOW_LABELS.session}</option></select></label>
        <label>Points<select value={resolution} onChange={event=>setResolution(event.currentTarget.value as typeof resolution)}><option value="daily">One per day (UTC)</option><option value="raw">Every reading</option></select></label>
      </>}
      <button class="analytics-refresh" onClick={()=>setRevision(value=>value+1)}>Reload readings</button>
    </div>
    <main class="analytics-content">
      {error&&<div class="usage-error" role="alert">{error}</div>}
      <p class="analytics-caption">{provider==='all'?'All providers':provider} · {account==='all'?'All saved accounts':accounts.find(item=>item.id===account)?.label||account} · quota capacity</p>
      {view==='current'&&<>
        {onManage&&<div class="analytics-actions"><button onClick={onManage}>Manage accounts</button></div>}
        {!accountsStatus?<p>Loading accounts…</p>:!visibleAccounts.length?<p>No saved accounts match these filters.</p>:<AccountCards status={accountsStatus} accounts={visibleAccounts}/>}
      </>}
      {view==='history'&&(status?<><QuotaChart windowName={windowName} status={status} accounts={accounts}/><QuotaReadings status={status} accounts={accounts} windowName={windowName}/></>:!error&&<p>Loading quota history…</p>)}
      {view==='resets'&&(status?<ResetLog items={status.resets} accounts={accounts}/>:!error&&<p>Loading reset history…</p>)}
      {view==='attribution'&&<AttributionLog items={visibleAttribution} accounts={accounts} loading={attribution===null&&!error}/>}
    </main>
  </>
}
