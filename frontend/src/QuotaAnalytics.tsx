import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
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

type Attribution = {
  sample_id:number
  window:string
  provider:string
  account_id:string
  quota_delta:number
  correlated_estimate:number
  correlated_low:number
  correlated_high:number
  external_estimate:number
  external_low:number
  external_high:number
  confidence:string
  sample_gap_seconds:number
  concurrent_sessions:number
  provider_lag_seconds:number
  allocations:Array<{session_id:string;model?:string;native_tokens?:number;quota_percent_estimate:number}>
}

const palette = ['#34d399','#60a5fa','#f59e0b','#c084fc','#f472b6','#22d3ee','#fb7185','#a3e635']
const percent = (value:number|null|undefined)=>value==null?'unavailable':`${value.toFixed(1)}%`

function accountFor(accounts:ProviderAccount[],series:QuotaSeries):ProviderAccount|undefined {
  return accounts.find(account=>account.id===series.account_id)
}

function seriesLabel(accounts:ProviderAccount[],series:QuotaSeries):string {
  const account=accountFor(accounts,series)
  const label=account?accountDisplayLabel(account):`${series.provider} · ${series.account_id.slice(0,8)}`
  const owner=series.provider_account_uuid?` · verified ${series.provider_account_uuid.slice(-6)}`:' · legacy identity'
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
  const times=series.flatMap(item=>item.points.map(quotaPointTime))
  // Daemon clock: `times` are daemon-stamped sample instants, and an axis bound
  // taken from a browser clock that disagrees would shift the whole plot.
  const minimum=Math.min(...times,serverNow())
  const maximum=Math.max(...times,minimum+1)
  const x=(time:number)=>52+(time-minimum)/(maximum-minimum)*692
  const y=(value:number)=>164-Math.max(0,Math.min(100,value))/100*136
  const colored=series.map((item,index)=>({item,color:palette[index%palette.length]}))
  return <section class="quota-chart">
    <h3>{title}</h3>
    <div class="quota-chart-legend">{colored.map(({item,color})=><span key={`${item.account_id}-${item.provider_account_uuid}`}>
      <i style={{background:color}}/>{seriesLabel(accounts,item)}
    </span>)}</div>
    {times.length?<svg viewBox="0 0 760 188" role="img" aria-label={`${title} account timelines`}>
      {[0,25,50,75,100].map(value=><g key={value}>
        <line x1="52" x2="744" y1={y(value)} y2={y(value)} class="quota-grid-line"/>
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
      <text x="744" y="184" text-anchor="end">{new Date(maximum*1000).toLocaleDateString()}</text>
    </svg>:<p>No {title.toLowerCase()} readings in this range.</p>}
  </section>
}

function QuotaDetail({status,accounts}:{status:QuotaSeriesStatus;accounts:ProviderAccount[]}) {
  const rows=status.series.flatMap(series=>series.points.map(point=>({series,point}))).sort(
    (a,b)=>quotaPointTime(b.point)-quotaPointTime(a.point),
  )
  return <section class="usage-table">
    <h3>{status.resolution==='daily'?'Daily quota summaries':'Durable quota samples'}</h3>
    {rows.length?<div class="usage-table-scroll"><table><thead><tr>
      <th>{status.resolution==='daily'?'day':'time'}</th><th>account</th>
      <th>5h used</th><th>weekly used</th><th>sample evidence</th>
    </tr></thead><tbody>{rows.map(({series,point})=>{
      const daily=!('sampled_at' in point)
      const sample=point as QuotaRawPoint
      const rollup=point as QuotaDailyPoint
      return <tr key={`${series.account_id}-${series.provider_account_uuid}-${quotaPointTime(point)}`}>
        <td>{daily?rollup.day:new Date(sample.sampled_at*1000).toLocaleString()}</td>
        <td>{seriesLabel(accounts,series)}</td>
        <td>{daily?`${percent(rollup.session_first)} to ${percent(rollup.session_last)} · range ${percent(rollup.session_min)}-${percent(rollup.session_max)}`:percent(sample.session?.used_percent)}</td>
        <td>{daily?`${percent(rollup.weekly_first)} to ${percent(rollup.weekly_last)} · range ${percent(rollup.weekly_min)}-${percent(rollup.weekly_max)}`:percent(sample.weekly?.used_percent)}</td>
        <td>{daily?`${rollup.samples} samples · ${rollup.errors} errors`:`${sample.freshness}${sample.error?` · ${sample.error}`:''}`}</td>
      </tr>
    })}</tbody></table></div>:<p>No account-specific quota evidence in this range.</p>}
  </section>
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

export function QuotaAnalytics({
  provider,
  attribution,
}:{
  provider:'all'|string
  attribution:Attribution[]
}) {
  const [accountsStatus,setAccountsStatus]=useState<ProviderAccountsStatus|null>(null)
  const [status,setStatus]=useState<QuotaSeriesStatus|null>(null)
  const [account,setAccount]=useState('all')
  const [range,setRange]=useState<'7'|'30'|'90'|'all'>('30')
  const [resolution,setResolution]=useState<'raw'|'daily'>('daily')
  const [error,setError]=useState('')
  const accounts=useMemo(()=>
    (accountsStatus?.accounts||[]).filter(item=>(provider==='all'||item.provider===provider)&&(!item.conflict||item.conflict.is_primary)),
    [accountsStatus,provider],
  )

  useEffect(()=>{
    void api<ProviderAccountsStatus>('GET','/api/provider-accounts').then(setAccountsStatus).catch(
      cause=>setError(cause instanceof Error?cause.message:String(cause)),
    )
  },[])
  useEffect(()=>{
    if(account!=='all'&&!accounts.some(item=>item.id===account))setAccount('all')
  },[provider,accounts,account])
  useEffect(()=>{
    const path=quotaSeriesPath({
      provider:provider==='all'?undefined:provider,
      account:account==='all'?undefined:account,
      range,
      resolution,
    })
    setError('')
    void api<QuotaSeriesStatus>('GET',path).then(setStatus).catch(
      cause=>setError(cause instanceof Error?cause.message:String(cause)),
    )
  },[provider,account,range,resolution])

  const visibleAttribution=attribution.filter(item=>(provider==='all'||item.provider===provider)&&(account==='all'||item.account_id===account))
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">
      These charts show provider quota utilization percentages, not token usage. Account names
      come from saved provider identities. Legacy rows without a provider ID are marked explicitly.
      Correlation remains observational.
    </p>
    <div class="usage-view-controls quota-controls">
      <label>account<select value={account} onChange={event=>setAccount(event.currentTarget.value)}>
        <option value="all">all saved accounts</option>
        {accounts.map(item=><option value={item.id} key={item.id}>{accountDisplayLabel(item)}</option>)}
      </select></label>
      <label>range<select value={range} onChange={event=>setRange(event.currentTarget.value as typeof range)}>
        <option value="7">7 days</option><option value="30">30 days</option>
        <option value="90">90 days</option><option value="all">all retained</option>
      </select></label>
      <label>detail<select value={resolution} onChange={event=>setResolution(event.currentTarget.value as typeof resolution)}>
        <option value="daily">daily summaries</option><option value="raw">raw samples</option>
      </select></label>
    </div>
    {error&&<div class="usage-error" role="alert">{error}</div>}
    {status?<>
      <div class="quota-chart-grid">
        <QuotaChart title="5h quota" windowName="session" series={status.series} accounts={accounts} resets={status.resets}/>
        <QuotaChart title="Weekly quota" windowName="weekly" series={status.series} accounts={accounts} resets={status.resets}/>
      </div>
      <QuotaDetail status={status} accounts={accounts}/>
      <ResetLog items={status.resets} accounts={accounts}/>
    </>:<p>Loading account quota history...</p>}
    <section class="attribution-log"><h3>Correlated mux activity</h3>{visibleAttribution.length?visibleAttribution.map(item=><article key={`${item.sample_id}-${item.window}`}>
      <strong>{item.provider} {item.window} +{item.quota_delta.toFixed(2)}%</strong>
      <span>correlated estimate {item.correlated_estimate.toFixed(2)}% (range {item.correlated_low.toFixed(2)}-{item.correlated_high.toFixed(2)}%) · external/unassigned {item.external_estimate.toFixed(2)}% (range {item.external_low.toFixed(2)}-{item.external_high.toFixed(2)}%)</span>
      <small>confidence {item.confidence} · {item.concurrent_sessions} overlapping mux session(s) · sample gap {Math.round(item.sample_gap_seconds/60)}m · provider lag allowance {Math.round(item.provider_lag_seconds)}s</small>
    </article>):<p>No positive quota deltas have attribution evidence yet.</p>}</section>
  </div>
}
