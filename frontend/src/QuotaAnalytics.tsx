import { isComposingKey } from './inputComposition.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { AnalyticsNav, useChartWidth } from './AnalyticsPrimitives'
import { harnessMark } from './harnessIcons'
import { harnessDisplayName } from './harnessRegistry'
import { formatResetRemaining } from './providerAccountDisplay'
import { OPERATIONAL_TELEMETRY_PATH, type OperationalStatus } from './operationalTelemetry'
import { serverNow } from './serverClock.ts'
import type { ProviderAccount, ProviderAccountsStatus } from './ProviderAccounts'
import {
  QUOTA_BILLING_LABELS,
  QUOTA_SHARE_DETAIL,
  QUOTA_SHARE_LABELS,
  QUOTA_SOURCE_DETAIL,
  QUOTA_SOURCE_LABELS,
  QUOTA_SOURCES,
  QUOTA_WINDOW_LABELS,
  describePoints,
  describeShares,
  describeWindowShares,
  formatQuotaDay,
  formatQuotaInstant,
  formatQuotaPercent,
  mergeUsageCells,
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
  usageCellAt,
  usageCells,
  usageTotals,
  utcDayOf,
  windowShares,
  type QuotaReading,
  type QuotaWindowName,
  type UsageCell,
} from './quotaTimeline'
import {
  accountDisplayLabel,
  accountNameLabel,
  quotaPointTime,
  quotaSeriesPath,
  type QuotaDailyPoint,
  type QuotaSeries,
  type QuotaSeriesStatus,
  type QuotaUsageInterval,
  type QuotaWindowSources,
  type ResetEvent,
} from './usageAnalytics'

const palette = ['#34d399','#60a5fa','#f59e0b','#c084fc','#f472b6','#22d3ee','#fb7185','#a3e635']
// Plot geometry in viewBox units; the width is the measured container width.
const CHART = {height:244,top:12,bottom:30,left:44,right:14}
// One source lane per account under the time axis, aligned to the same x scale.
const LANES = {top:8,step:13,height:9}
// Rises of one source this close together are one period (`mergeUsageCells`); longer
// quiet stretches, and every day boundary at daily resolution, stay visible as gaps.
const RAW_LANE_JOIN_SECONDS = 90*60
// Above this many daily points, per-day markers stop being readable and only the line is drawn.
const MAX_DAILY_MARKERS = 600

function accountFor(accounts:ProviderAccount[],series:QuotaSeries):ProviderAccount|undefined {
  return accounts.find(account=>account.id===series.account_id)
}

const legacyOwner=(series:QuotaSeries)=>series.provider_account_uuid?'':t("QuotaAnalytics.82274d4336")

/** A series' account with its provider in words, for text that cannot carry a mark. */
function seriesLabel(accounts:ProviderAccount[],series:QuotaSeries):string {
  const account=accountFor(accounts,series)
  const label=account?accountDisplayLabel(account):`${series.provider} · ${series.account_id.slice(0,8)}`
  return t("QuotaAnalytics.be9e11092d", { label: label, value2: legacyOwner(series) })
}

/** The same without the provider, for a row that draws it as `AccountName`'s mark. */
function seriesName(accounts:ProviderAccount[],series:QuotaSeries):string {
  const account=accountFor(accounts,series)
  return `${account?accountNameLabel(account):series.account_id.slice(0,8)}${legacyOwner(series)}`
}

/**
 * An account's name led by its provider's mark - Anthropic's burst or OpenAI's knot - so
 * every place this view names an account says whose quota it is without spelling the
 * provider out. The mark carries the provider's accessible name, because the words no
 * longer do.
 */
function AccountName({provider,name}:{provider:string;name:string}) {
  const vendor=harnessDisplayName(provider)
  return <span class="quota-account-name">
    <i class={`provider-glyph ${provider}`} role="img" aria-label={vendor} title={vendor}>{harnessMark(provider)}</i>
    <span>{name}</span>
  </span>
}

const seriesKey=(series:QuotaSeries)=>`${series.provider}-${series.account_id}-${series.provider_account_uuid||''}`

const accountName=(accounts:ProviderAccount[],accountId:string,fallback:string)=>accounts.find(account=>account.id===accountId)?.label||fallback

function resetTitle(item:ResetEvent,label:string,now:number):string {
  return t("QuotaAnalytics.2c27c324a2", { classification: item.classification, value2: quotaWindowLabel(item.window).toLowerCase(), label: label, value4: formatQuotaPercent(item.before_value), value5: formatQuotaPercent(item.after_value), value6: formatQuotaInstant(item.observed_at,now) })
}

/** The facts that qualify one readout value: when it was read, and what the window was doing. */
function readingDetail(reading:QuotaReading,instant:number,daily:boolean,now:number):string {
  const parts=daily
    ?[
      reading.day===utcDayOf(now)?t("QuotaAnalytics.695fac186e"):t("QuotaAnalytics.0ace2a1b69"),
      reading.first!=null&&reading.first!==reading.used?t("QuotaAnalytics.223b388d74", { value1: formatQuotaPercent(reading.first) }):'',
      reading.peak!=null&&reading.peak!==reading.used?`peak ${formatQuotaPercent(reading.peak)}`:'',
      reading.samples!=null?`${reading.samples} readings`:'',
    ]
    :[
      Math.abs(instant-reading.time)>300?t("QuotaAnalytics.273588ee03", { value1: formatQuotaInstant(reading.time,now) }):'',
      reading.resetsAt?t("QuotaAnalytics.63d64378f2", { value1: formatQuotaInstant(reading.resetsAt,now) }):'',
    ]
  return parts.filter(Boolean).join(' · ')
}

/** Where the rise that ends at this reading (or on this day) came from. */
function usageDetail(cell:UsageCell|null,daily:boolean):string {
  if(!cell)return ''
  const sources=describePoints(cell.parts)
  return daily?t("QuotaAnalytics.8fc900a349", { value1: formatQuotaPercent(cell.total), value2: sources }):t("QuotaAnalytics.27b3af5c0c", { value1: formatQuotaPercent(cell.total), value2: sources })
}

function usageTitle(label:string,cell:UsageCell,daily:boolean,now:number):string {
  const when=daily
    ?t("QuotaAnalytics.5fabb2d630", { value1: formatQuotaDay(utcDayOf((cell.start+cell.end)/2),now) })
    :`${formatQuotaInstant(cell.start,now)} to ${formatQuotaInstant(cell.end,now)}`
  const requests=cell.requests?t("QuotaAnalytics.793ae68975", { value1: cell.requests }):''
  const cost=cell.costUsd>0?` ($${cell.costUsd.toFixed(2)} API-equivalent)`:''
  return t("QuotaAnalytics.b5d76c34ef", { label: label, when: when, value3: formatQuotaPercent(cell.total), value4: describePoints(cell.parts), requests: requests, cost: cost })
}

/**
 * One account's window as one bar on the quota's own 0-100 scale, so the fill ends where
 * the window's used percentage does and the empty rest is what is left - there is no second
 * bar for remaining capacity to read against this one.
 *
 * Where attribution reaches the window the fill is split by where its usage came from: each
 * part's width is its share of the whole limit, the parts sum to the reading
 * (`quota_usage.window_shares` scales them to it), and the same parts follow in words. Where
 * it does not, the fill is the plain reading and nothing is said about its origin.
 */
function WindowShares({used,sources,windowLabel}:{used:number;sources?:QuotaWindowSources|null;windowLabel:string}) {
  const shares=sources?windowShares(sources):[]
  const now=serverNow()
  const words=describeWindowShares(shares)
  const began=sources?`${sources.reset_seen?t("QuotaAnalytics.850aeb92dd"):t("QuotaAnalytics.bdf4c18daf")}, ${formatQuotaInstant(sources.since,now)}`:''
  return <div class="quota-window-shares">
    <div class="quota-share-bar" role="img" aria-label={shares.length ? t("QuotaAnalytics.e6da3438da", { windowLabel: windowLabel, began: began, words: words }) : t("QuotaAnalytics.13d0cde1dc", { windowLabel: windowLabel, value2: formatQuotaPercent(used) })}>
      {shares.length ? shares.map(share=><span
          key={share.source}
          class={`quota-source-${share.source}`}
          style={{width:`${Math.min(100,share.points)}%`}}
          title={t("QuotaAnalytics.19d646a699", { value1: formatQuotaPercent(share.points), value2: QUOTA_SHARE_LABELS[share.source], value3: QUOTA_SHARE_DETAIL[share.source] })}
        />) : <span class="quota-share-used" style={{width:`${Math.max(0,Math.min(100,used))}%`}} title={t("QuotaAnalytics.8706714b03", { value1: formatQuotaPercent(used) })}/>}
    </div>
    {shares.length>0 && <small>
      {shares.map(share=><span key={share.source} class="quota-share-part"><i class={`quota-source-${share.source}`}/>{describeWindowShares([share])}</span>)}
      <span class="quota-share-since">{began}</span>
    </small>}
  </div>
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
  const lines=useMemo(()=>status.series.map((series,index)=>{
    const cells=usageCells(series.usage,windowName)
    return {
      key:seriesKey(series),
      label:seriesLabel(accounts,series),
      name:seriesName(accounts,series),
      provider:series.provider,
      account:accountFor(accounts,series),
      color:palette[index%palette.length],
      readings:quotaReadings(series.points,windowName),
      // Each rising interval, for the readout; joined into stretches, for the lane.
      cells,
      lane:mergeUsageCells(cells,daily?1:RAW_LANE_JOIN_SECONDS),
    }
  }), [status, windowName, accounts, daily, uiLocale.value, catalogRevision.value])
  const lanes=lines.filter(line=>line.lane.length)
  const sources=QUOTA_SOURCES.filter(source=>lanes.some(line=>line.lane.some(cell=>cell.parts[source]>0)))
  // Each account's window as it stands now, from its latest reading, whatever range is charted.
  const windowsNow=lines.flatMap(line=>{
    const reading=line.account?.quota?.[windowName]
    return reading?.sources&&windowShares(reading.sources).length?[{line,used:reading.used_percent,sources:reading.sources}]:[]
  })
  const height=CHART.height+(lanes.length?LANES.top+lanes.length*LANES.step:0)
  const instants=useMemo(()=>readingInstants(lines.map(line=>line.readings),daily?0:60), [lines, daily, uiLocale.value, catalogRevision.value])
  // Daemon clock: readings are daemon-stamped, and an axis bound taken from a browser
  // clock that disagrees would shift the whole plot.
  const now=serverNow()
  const [start,end]=useMemo(()=>quotaDomain(status,lines.flatMap(line=>line.readings),serverNow()), [status, lines, uiLocale.value, catalogRevision.value])
  const plotWidth=Math.max(1,width-CHART.left-CHART.right)
  const plotBottom=CHART.height-CHART.bottom
  const ticks=useMemo(
    ()=>quotaTimeTicks(start,end,Math.max(2,Math.floor(plotWidth/92)),{zone:daily?'utc':'local',daily}), [start, end, plotWidth, daily, uiLocale.value, catalogRevision.value])
  const x=(time:number)=>CHART.left+(time-start)/(end-start)*plotWidth
  const y=(value:number)=>CHART.top+(1-Math.max(0,Math.min(100,value))/100)*(plotBottom-CHART.top)
  const paths=useMemo(()=>lines.map(line=>readingSegments(line.readings,quotaGapLimit(line.readings,status.resolution)).map(segment=>
    // A lone reading between two outages is a zero-length run; the round cap draws it as a dot.
    segment.length===1
      ?`M${x(segment[0].time).toFixed(1)},${y(segment[0].used).toFixed(1)}l0.01,0`
      :segment.map((reading,index)=>`${index?'L':'M'}${x(reading.time).toFixed(1)},${y(reading.used).toFixed(1)}`).join(''),
  ).join('')), [lines, status.resolution, start, end, plotWidth, uiLocale.value, catalogRevision.value])
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
  const when=active==null?'':daily?t("QuotaAnalytics.5fabb2d630", { value1: formatQuotaDay(utcDayOf(active),now) }):formatQuotaInstant(active,now)
  return <section ref={chartRef} class="quota-chart">
    <header class="quota-chart-header">
      <h3>{rich("QuotaAnalytics.e687d300d8", { windowLabel: windowLabel })}</h3>
      <small>{range}</small>
    </header>
    <p class="quota-chart-explainer">{rich("QuotaAnalytics.59896c2a63", { value1: (daily) ? 'yes' : 'no', value2: windowLabel.toLowerCase() })}</p>
    {instants.length ? <>
      <div
        class="quota-plot"
        tabIndex={0}
        aria-label={t("QuotaAnalytics.04c3a74307", { windowLabel: windowLabel })}
        onKeyDown={event=>{
      if (isComposingKey(event)) return

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
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={t("QuotaAnalytics.1336a3f522", { windowLabel: windowLabel, range: range })}
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
          {markers && lines.map(line=>line.readings.map(reading=><circle
            key={`${line.key}-${reading.time}`}
            cx={x(reading.time)} cy={y(reading.used)} r="3"
            class="quota-point" style={{fill:line.color}}
          ><title>{t("QuotaAnalytics.5fa2a38659", { label: line.label, value2: formatQuotaPercent(reading.used), value3: reading.day?formatQuotaDay(reading.day,now):'' })}</title></circle>))}
          {lanes.map((line,index)=>{
            const top=CHART.height+LANES.top+index*LANES.step
            return <g key={`lane-${line.key}`}>
              <rect x={CHART.left-12} y={top} width="8" height={LANES.height} style={{fill:line.color}}><title>{line.label}</title></rect>
              <rect x={CHART.left} y={top} width={plotWidth} height={LANES.height} class="quota-lane-track"/>
              {line.lane.map(cell=>{
                const left=Math.max(CHART.left,x(cell.start))
                const cellWidth=Math.max(1,Math.min(width-CHART.right,x(cell.end))-left)
                let stacked=0
                // Stacked bottom-up in source order, each part's height its share of the rise.
                return <g key={cell.start}>
                  <title>{usageTitle(line.label,cell,daily,now)}</title>
                  {QUOTA_SOURCES.filter(source=>cell.parts[source]>0).map(source=>{
                    const part=LANES.height*cell.parts[source]/cell.total
                    stacked+=part
                    return <rect key={source} x={left} y={top+LANES.height-stacked} width={cellWidth} height={part} class={`quota-source-${source}`}/>
                  })}
                </g>
              })}
            </g>
          })}
          {active!=null && <line x1={x(active)} x2={x(active)} y1={CHART.top} y2={lanes.length?height:plotBottom} class="quota-inspect-line"/>}
          {current.map(({line,reading})=>reading&&<circle
            key={line.key}
            cx={x(reading.time)} cy={y(reading.used)} r="4.5"
            class="quota-inspect-point" style={{fill:line.color}}
          />)}
        </svg>
      </div>
      {windowsNow.length>0 && <div class="quota-sources quota-window-now">
        <table><caption>{rich("QuotaAnalytics.70950e43de", { value1: windowLabel.toLowerCase() })}</caption><tbody>{windowsNow.map(({line,used,sources:split})=><tr key={line.key}>
          <th scope="row"><i style={{background:line.color}}/><AccountName provider={line.provider} name={line.name}/></th>
          <td><WindowShares used={used} sources={split} windowLabel={windowLabel}/></td>
          <td class="quota-value">{rich("QuotaAnalytics.8706714b03", { value1: formatQuotaPercent(used) })}</td>
        </tr>)}</tbody></table>
      </div>}
      {lanes.length>0 && <div class="quota-sources quota-range-sources">
        <ul class="quota-source-legend" aria-label={t("QuotaAnalytics.caf85b0888")}>{sources.map(source=><li key={source} title={QUOTA_SOURCE_DETAIL[source]}>
          <i class={`quota-source-${source}`}/>{QUOTA_SOURCE_LABELS[source]}
        </li>)}</ul>
        <table><caption>{rich("QuotaAnalytics.74621a881a", { value1: windowLabel.toLowerCase() })}</caption><tbody>{lanes.map(line=>{
          const totals=usageTotals(line.lane)
          return <tr key={line.key}>
            <th scope="row"><i style={{background:line.color}}/><AccountName provider={line.provider} name={line.name}/></th>
            <td>{describeShares(totals.parts,totals.total)}</td>
            <td class="quota-value">{rich("QuotaAnalytics.3858260bf8", { value1: Math.round(totals.total) })}</td>
          </tr>
        })}</tbody></table>
      </div>}
      <div class="quota-readout" aria-live="polite">
        <header>
          <strong>{when}</strong>
          {atLatest ? <span class="analytics-badge">{t("QuotaAnalytics.8730d3c202")}</span> : <button type="button" onClick={()=>setInspected(null)}>{t("QuotaAnalytics.3bf9acb6db")}</button>}
        </header>
        <table><tbody>{current.map(({line,reading})=><tr key={line.key}>
          <th scope="row"><i style={{background:line.color}}/><AccountName provider={line.provider} name={line.name}/></th>
          <td class="quota-value">{reading ? t("QuotaAnalytics.8706714b03", { value1: formatQuotaPercent(reading.used) }) : daily ? t("QuotaAnalytics.7ef0ebd6d9") : t("QuotaAnalytics.fb743d5508")}</td>
          <td class="quota-detail">{reading&&active!=null ? [readingDetail(reading,active,daily,now),usageDetail(usageCellAt(line.cells,reading.time,daily),daily)].filter(Boolean).join(' · ') : ''}</td>
        </tr>)}</tbody></table>
        <small>{t("QuotaAnalytics.ea9c3c7e08", { value1: (resets.length) ? 'yes' : 'no', value2: (lanes.length) ? 'yes' : 'no' })}</small>
      </div>
    </> : <p>{rich("QuotaAnalytics.acf177d84d", { value1: windowLabel.toLowerCase() })}</p>}
  </section>
}

function dailyCell(point:QuotaDailyPoint,window:QuotaWindowName) {
  const first=point[`${window}_first`],last=point[`${window}_last`],peak=point[`${window}_max`]
  if(last==null)return t("QuotaAnalytics.612a3d5f7e")
  return <>
    {first!=null&&first!==last ? t("QuotaAnalytics.6ae50ea0da", { value1: formatQuotaPercent(first), value2: formatQuotaPercent(last) }) : formatQuotaPercent(last)}
    {peak!=null&&peak!==last && <small>{rich("QuotaAnalytics.380df0b003", { value1: formatQuotaPercent(peak) })}</small>}
  </>
}

function QuotaReadings({status,accounts,windowName}:{status:QuotaSeriesStatus;accounts:ProviderAccount[];windowName:QuotaWindowName}) {
  const [limit,setLimit]=useState(50)
  const daily=status.resolution==='daily'
  const now=serverNow()
  const rows=useMemo(
    ()=>status.series.flatMap(series=>series.points.map(point=>({series,point}))).sort((a,b)=>quotaPointTime(b.point)-quotaPointTime(a.point)), [status, uiLocale.value, catalogRevision.value])
  // The charted window leads, so the column beside the time answers the chart's question.
  const windows:QuotaWindowName[]=windowName==='weekly'?['weekly','session']:['session','weekly']
  return <details class="analytics-diagnostics quota-readings">
    <summary>{rich("QuotaAnalytics.1864742e2d", { value1: (daily) ? 'yes' : 'no', value2: rows.length.toLocaleString(uiLocale.value), value3: <small>{t("QuotaAnalytics.bae959a68d", { value1: (daily) ? 'yes' : 'no' })}</small> })}</summary>
    <div class="quota-readings-scroll"><table>
      <thead><tr>
        <th scope="col">{daily ? t("QuotaAnalytics.c4c6a5e4ae") : t("QuotaAnalytics.33b93476cf")}</th>
        <th scope="col">{t("QuotaAnalytics.7e1b0d5641")}</th>
        {windows.map(window=><th scope="col" key={window} class="quota-value">{rich("QuotaAnalytics.8706714b03", { value1: QUOTA_WINDOW_LABELS[window] })}</th>)}
      </tr></thead>
      <tbody>{rows.slice(0,limit).map(({series,point})=>'sampled_at' in point
        ?<tr key={`${seriesKey(series)}-${point.id}`}>
          <td>{formatQuotaInstant(point.sampled_at,now)}{point.freshness!=='fresh' && <small>{point.error || point.freshness}</small>}</td>
          <td><AccountName provider={series.provider} name={seriesName(accounts,series)}/></td>
          {windows.map(window=><td key={window} class="quota-value">{formatQuotaPercent(point[window]?.used_percent)}</td>)}
        </tr>
        :<tr key={`${seriesKey(series)}-${point.day}`}>
          <td>{formatQuotaDay(point.day,now)}{point.errors>0 && <small>{rich("QuotaAnalytics.cb0bc140c1", { errors: point.errors, samples: point.samples })}</small>}</td>
          <td><AccountName provider={series.provider} name={seriesName(accounts,series)}/></td>
          {windows.map(window=><td key={window} class="quota-value">{dailyCell(point,window)}</td>)}
        </tr>)}</tbody>
    </table></div>
    {rows.length>limit && <button onClick={()=>setLimit(limit+50)}>{t("QuotaAnalytics.a363389e50")}</button>}
  </details>
}

function ResetLog({items,accounts}:{items:ResetEvent[];accounts:ProviderAccount[]}) {
  const now=serverNow()
  return <section class="reset-log"><h3 data-heading-message="QuotaAnalytics.f1c8e4a83e">{t("QuotaAnalytics.f1c8e4a83e")}</h3>{items.length ? items.map(item=>{
    const account=accountName(accounts,item.account_id,item.account_id),window=quotaWindowLabel(item.window).toLowerCase()
    // A review that ruled the movement out names no account: it is no longer a fact about one.
    const reviewedAway=item.review_status==='manual_usage'||item.review_status==='discarded'
    const heading=item.review_status==='manual_usage'?t("QuotaAnalytics.7f3867214e"):item.review_status==='discarded'?t("QuotaAnalytics.72767e692e"):item.review_status==='seen'?t("QuotaAnalytics.f874a5e9f8", { value1: window }):`${item.classification} ${window} reset`
    return <article key={item.id} class={item.classification==='unexpected'&&item.confirmed&&!item.suppression_reason&&!item.review_status?'confirmed-unexpected':''}>
      <strong>{heading}{!reviewedAway && <> · <AccountName provider={item.provider} name={account}/></>}</strong>
      <span>{rich("QuotaAnalytics.add24eb83c", { value1: formatQuotaPercent(item.before_value), value2: formatQuotaPercent(item.after_value), value3: formatQuotaInstant(item.observed_at,now) })}</span>
      <small>{rich("QuotaAnalytics.f7773ebd52", { confidence: item.confidence, value2: (item.confirmed) ? 'yes' : 'no', value3: item.suppression_reason ? t("QuotaAnalytics.9dfa2c69d0", { suppression_reason: item.suppression_reason }) : '', value4: item.expected_reset_at ? t("QuotaAnalytics.930a34f3ab", { value1: formatQuotaInstant(item.expected_reset_at,now) }) : '' })}</small>
    </article>
  }) : <p>{t("QuotaAnalytics.86f8175737")}</p>}</section>
}

function AttributionLog({items,accounts,loading}:{items:QuotaUsageInterval[];accounts:ProviderAccount[];loading:boolean}) {
  const now=serverNow()
  return <section class="attribution-log"><p class="analytics-caption">{t("QuotaAnalytics.6c25558aff")}</p>
    {loading ? <p>{t("QuotaAnalytics.7f056bcbb4")}</p> : items.length ? items.map(item=>{
      const windows=(['weekly','session'] as const).filter(window=>(item[window].rise??0)>0)
      return <details class="analytics-diagnostics" key={item.sample_id}>
        <summary><AccountName provider={item.provider} name={accountName(accounts,item.account_id,item.provider)}/> · {windows.map(window=>`${QUOTA_WINDOW_LABELS[window]} +${formatQuotaPercent(item[window].rise)}`).join(' · ')} · {formatQuotaInstant(item.end,now)}
          <small>{describePoints(item[windows[0]??'session'])}</small>
        </summary>
        <dl class="analytics-facts">
          {windows.map(window=><div key={window}><dt>{rich("QuotaAnalytics.a3e7564aa1", { value1: QUOTA_WINDOW_LABELS[window] })}</dt><dd>{describePoints(item[window])}</dd></div>)}
          <div><dt>{t("QuotaAnalytics.7c18ebbe65")}</dt><dd>{QUOTA_BILLING_LABELS[item.billing]}</dd></div>
          <div><dt>{t("QuotaAnalytics.81df7a2aaf")}</dt><dd>{item.requests}{item.cost_usd>0 ? t("QuotaAnalytics.bde18f63b4", { value1: item.cost_usd.toFixed(2) }) : ''}</dd></div>
        </dl>
        <small>{rich("QuotaAnalytics.7eebb8e658", { value1: formatQuotaInstant(item.start,now), value2: formatQuotaInstant(item.end,now) })}</small>
      </details>
    }) : <p>{t("QuotaAnalytics.2a48af7933")}</p>}
  </section>
}

function AccountCards({status,accounts}:{status:ProviderAccountsStatus;accounts:ProviderAccount[]}) {
  const now=serverNow()
  return <div class="quota-account-grid">{accounts.map(item=>{
    const quota=item.quota,ready=quota?.status==='ready'
    const stale=!!quota?.refreshed_at&&(now-quota.refreshed_at)>(status.stale_minutes||30)*60
    return <article class="quota-account-card" key={item.id}>
      <header><strong><AccountName provider={item.provider} name={accountNameLabel(item)}/></strong>{status.selected[item.provider]===item.id && <span class="analytics-badge">{t("QuotaAnalytics.57fd7a0cf3")}</span>}</header>
      {/* formatResetRemaining measures any interval; read backwards here it is the reading's age. */}
      <small>{quota?.refreshed_at ? t("QuotaAnalytics.3b3c5c86c8", { value1: formatQuotaInstant(quota.refreshed_at,now), value2: formatResetRemaining(now,quota.refreshed_at), value3: (stale) ? 'yes' : 'no' }) : t("QuotaAnalytics.afd4931a6b")}</small>
      {(['weekly','session','fable'] as const).filter(key=>key!=='fable'||quota?.fable).map(key=>{
        const window=ready?quota?.[key]:null
        // Percent used, the one figure every other quota surface prints. The bar's empty
        // rest is what is left, so a second "% left" figure would only restate it.
        return <div class="quota-capacity" key={key}>
          <div><span>{rich("QuotaAnalytics.a3e7564aa1", { value1: QUOTA_WINDOW_LABELS[key] })}</span><strong>{window ? t("QuotaAnalytics.8706714b03", { value1: formatQuotaPercent(window.used_percent) }) : t("QuotaAnalytics.ca18449697")}</strong></div>
          {window && <WindowShares used={window.used_percent} sources={window.sources} windowLabel={QUOTA_WINDOW_LABELS[key]}/>}
          <small>{window?.resets_at ? window.resets_at<=now ? t("QuotaAnalytics.1e1a895ec3") : t("QuotaAnalytics.94e7f9f1b2", { value1: formatResetRemaining(window.resets_at,now), value2: formatQuotaInstant(window.resets_at,now) }) : t("QuotaAnalytics.4166801799")}</small>
        </div>
      })}
      {quota?.error && <p class="usage-error">{quota.error}</p>}
    </article>
  })}</div>
}

export function QuotaAnalytics({onManage}:{onManage?:()=>void}) {
  const [view,setView]=useState<'current'|'history'|'resets'|'attribution'>('current')
  const [accountsStatus,setAccountsStatus]=useState<ProviderAccountsStatus|null>(null)
  const [status,setStatus]=useState<QuotaSeriesStatus|null>(null)
  const [attribution,setAttribution]=useState<QuotaUsageInterval[]|null>(null)
  const [provider,setProvider]=useState('all')
  const [account,setAccount]=useState('all')
  const [range,setRange]=useState<'7'|'30'|'90'|'all'>('7')
  const [resolution,setResolution]=useState<'raw'|'daily'>('daily')
  const [windowName,setWindowName]=useState<QuotaWindowName>('weekly')
  const [error,setError]=useState('')
  const [revision,setRevision]=useState(0)
  const accounts=useMemo(()=>(accountsStatus?.accounts||[]).filter(item=>(provider==='all'||item.provider===provider)&&(!item.conflict||item.conflict.is_primary)), [accountsStatus, provider, uiLocale.value, catalogRevision.value])
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
      // The shared path already carries a query; extend it rather than appending a second `?`.
      const [path,shared]=OPERATIONAL_TELEMETRY_PATH.split('?')
      const query=new URLSearchParams(shared)
      query.set('limit','500')
      if(provider!=='all')query.set('provider',provider)
      if(account!=='all')query.set('account',account)
      api<OperationalStatus>('GET',`${path}?${query}`).then(value=>{if(!cancelled)setAttribution(value.quota.usage_intervals)}).catch(cause=>{if(!cancelled)setError(String(cause))})
    }
    return()=>{cancelled=true}
  },[view,provider,account,range,resolution,revision])
  const visibleAccounts=accounts.filter(item=>account==='all'||item.id===account)
  const cutoff=range==='all'?0:serverNow()-Number(range)*86400
  const visibleAttribution=(attribution||[]).filter(item=>item.end>=cutoff)
  return <>
    <AnalyticsNav label={t("QuotaAnalytics.2c7e4ff512")} value={view} onChange={setView} items={[{id:'current',get label() { return t("QuotaAnalytics.e0d1b68224") }},{id:'history',get label() { return t("QuotaAnalytics.0e76960093") }},{id:'resets',get label() { return t("QuotaAnalytics.7816b9affc") }},{id:'attribution',get label() { return t("QuotaAnalytics.a902dcce08") }}]}/>
    <div class="analytics-toolbar">
      {view!=='current' && <label>{rich("QuotaAnalytics.6287ca43ed", { value1: <select value={range} onChange={event=>setRange(event.currentTarget.value as typeof range)}><option value="7">{t("QuotaAnalytics.0603deca4f")}</option><option value="30">{t("QuotaAnalytics.f8f03fb441")}</option><option value="90">{t("QuotaAnalytics.9902d7ae89")}</option><option value="all">{t("QuotaAnalytics.d81ae686f7")}</option></select> })}</label>}
      <details class="analytics-filter-menu"><summary>{rich("QuotaAnalytics.bf5cc1c15c", { value1: provider!=='all'||account!=='all' ? t("QuotaAnalytics.65de8fe226", { value1: Number(provider!=='all')+Number(account!=='all') }) : '' })}</summary><div>
        <label>{rich("QuotaAnalytics.bb5a4bd4ec", { value1: <select value={provider} onChange={event=>{setProvider(event.currentTarget.value);setAccount('all')}}><option value="all">{t("QuotaAnalytics.20e56db7df")}</option>{(accountsStatus?.providers||[]).map(name=><option key={name} value={name}>{name}</option>)}</select> })}</label>
        <label>{rich("QuotaAnalytics.5e38c26a52", { value1: <select value={account} onChange={event=>setAccount(event.currentTarget.value)}><option value="all">{t("QuotaAnalytics.e6ec41d4e4")}</option>{accounts.map(item=><option key={item.id} value={item.id}>{accountDisplayLabel(item)}</option>)}</select> })}</label>
      </div></details>
      {view==='history' && <>
        <label>{rich("QuotaAnalytics.3cf34d3d78", { value1: <select value={windowName} onChange={event=>setWindowName(event.currentTarget.value as QuotaWindowName)}><option value="weekly">{QUOTA_WINDOW_LABELS.weekly}</option><option value="session">{QUOTA_WINDOW_LABELS.session}</option></select> })}</label>
        <label>{rich("QuotaAnalytics.ccdc55ad47", { value1: <select value={resolution} onChange={event=>setResolution(event.currentTarget.value as typeof resolution)}><option value="daily">{t("QuotaAnalytics.f2e45de65d")}</option><option value="raw">{t("QuotaAnalytics.8c5431d3a9")}</option></select> })}</label>
      </>}
      <button class="analytics-refresh" onClick={()=>setRevision(value=>value+1)}>{t("QuotaAnalytics.7bdbac1847")}</button>
    </div>
    <main class="analytics-content">
      {error && <div class="usage-error" role="alert">{error}</div>}
      <p class="analytics-caption">{rich("QuotaAnalytics.a1b07423af", { value1: provider==='all' ? t("QuotaAnalytics.20e56db7df") : provider, value2: account==='all' ? t("QuotaAnalytics.e6ec41d4e4") : accounts.find(item=>item.id===account)?.label || account })}</p>
      {view==='current' && <>
        {onManage && <div class="analytics-actions"><button onClick={onManage}>{t("QuotaAnalytics.daeaf38abf")}</button></div>}
        {!accountsStatus ? <p>{t("QuotaAnalytics.2494bdb5bc")}</p> : !visibleAccounts.length ? <p>{t("QuotaAnalytics.f1f6a48bc3")}</p> : <AccountCards status={accountsStatus} accounts={visibleAccounts}/>}
      </>}
      {view==='history' && (status ? <><QuotaChart windowName={windowName} status={status} accounts={accounts}/><QuotaReadings status={status} accounts={accounts} windowName={windowName}/></> : !error && <p>{t("QuotaAnalytics.7270c7d3e3")}</p>)}
      {view==='resets' && (status ? <ResetLog items={status.resets} accounts={accounts}/> : !error && <p>{t("QuotaAnalytics.bbbc5432ab")}</p>)}
      {view==='attribution' && <AttributionLog items={visibleAttribution} accounts={accounts} loading={attribution===null&&!error}/>}
    </main>
  </>
}
