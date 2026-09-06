import type { ComponentChildren } from 'preact'
import { useEffect, useState } from 'preact/hooks'
import { compactNumber, exactNumber } from './analyticsPresentation'
import { exactMoney, formatMoney } from './automationCost'
import type { UsageRow } from './usageAnalytics'

export function useChartWidth() {
  const [element,setElement]=useState<HTMLElement|null>(null)
  const [width,setWidth]=useState(750)
  useEffect(()=>{
    if(!element)return
    const observer=new ResizeObserver(entries=>setWidth(Math.max(240,entries[0].contentRect.width)))
    observer.observe(element)
    return()=>observer.disconnect()
  },[element])
  return [setElement,width] as const
}

export function AnalyticsNav<T extends string>({label, value, items, onChange, primary=false}: {
  label:string; value:T; items:readonly {id:T; label:string}[]; onChange:(value:T)=>void; primary?:boolean
}) {
  return <div class={`analytics-nav ${primary?'analytics-primary':'analytics-secondary'}`}>
    <div class="analytics-tab-row" role="tablist" aria-label={label} onKeyDown={event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return
      event.preventDefault()
      const current=items.findIndex(item=>item.id===value)
      const next=event.key==='Home'?0:event.key==='End'?items.length-1:(current+(event.key==='ArrowRight'?1:-1)+items.length)%items.length
      onChange(items[next].id)
      event.currentTarget.querySelectorAll('button')[next]?.focus()
    }}>
      {items.map(item=><button key={item.id} role="tab" tabIndex={value===item.id?0:-1} aria-selected={value===item.id} class={value===item.id?'active':''} onClick={()=>onChange(item.id)}>{item.label}</button>)}
    </div>
    {primary&&<label class="analytics-section-picker">Section<select aria-label={label} value={value} onChange={event=>onChange(event.currentTarget.value as T)}>{items.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
  </div>
}

/** Exact figures remain available on touch and keyboard as well as hover. */
export function MetricValue({value, money=false}: {value:number|null|undefined; money?:boolean}) {
  const [expanded,setExpanded]=useState(false)
  if(value==null||!Number.isFinite(value))return <span class="analytics-muted">Not measured</span>
  const exact=money?exactMoney(value):exactNumber(value)
  return <button type="button" class="metric-value" title={exact} aria-label={`${money?'Amount':'Count'}: ${exact}. Toggle exact value`} aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?exact:money?formatMoney(value):compactNumber(value)}</button>
}

export function UsageDetails({row}:{row:UsageRow}) {
  return <dl class="analytics-facts">{([
    ['Input',row.input_tokens],['Output',row.output_tokens],['Cache read',row.cache_read_tokens],['Cache write',row.cache_creation_tokens],
  ] as const).map(([label,value])=><div key={label}><dt>{label}</dt><dd><MetricValue value={value}/></dd></div>)}
    <div><dt>Cost basis</dt><dd>{row.cost_method==='unavailable'?'Not measured':row.cost_method==='proportional'?'Allocated estimate':row.cost_method==='mixed'?'Mixed estimates':'Source estimate'}</dd></div>
  </dl>
}

export function UsageBreakdown({title,rows}:{title:string;rows:Array<{id:string;label:ComponentChildren;row:UsageRow}>}) {
  return <section class="analytics-breakdown"><h3>{title}</h3>
    <div class="analytics-list-heading"><span>Name</span><span>Tokens</span><span>Est. cost</span></div>
    {rows.length?rows.map(({id,label,row})=><details class="analytics-record" key={id}>
      <summary><strong>{label}</strong><span>{compactNumber(row.total_tokens)}</span><span>{row.cost_method==='unavailable'?'Not measured':formatMoney(row.cost_usd)}</span></summary>
      <div class="analytics-record-body"><div class="analytics-exact-totals"><span>Total tokens <MetricValue value={row.total_tokens}/></span><span>Estimated cost <MetricValue money value={row.cost_method==='unavailable'?null:row.cost_usd}/></span></div><UsageDetails row={row}/></div>
    </details>):<p>No records in this period.</p>}
  </section>
}

/** Time is positioned by date, not by row count; chart height never grows with history. */
export function UsageTrend({rows,metric='tokens'}:{rows:UsageRow[];metric?:'tokens'|'cost'}) {
  const [selected,setSelected]=useState<string|null>(null)
  const [chartRef,width]=useChartWidth()
  const points=[...rows].sort((a,b)=>(a.date||'').localeCompare(b.date||''))
  if(!points.length)return <p>No history in this period.</p>
  const amount=(row:UsageRow)=>metric==='tokens'?row.total_tokens:row.cost_usd
  const maximum=Math.max(...points.map(amount),1)
  const times=points.map(row=>Date.parse(`${row.date}${row.date!.length===7?'-01':''}T00:00:00Z`))
  const first=times[0],span=Math.max(times[times.length-1]-first,86400000)
  const x=(index:number)=>40+(times[index]-first)/span*(width-60)
  const y=(row:UsageRow)=>160-amount(row)/maximum*130
  const active=points.find(row=>row.date===selected)||points[points.length-1]
  return <section ref={chartRef} class="analytics-trend" aria-label={`${metric==='tokens'?'Token':'Estimated cost'} history`}>
    <div class="analytics-chart-value"><span>{active.date}</span><MetricValue value={amount(active)} money={metric==='cost'}/></div>
    <svg viewBox={`0 0 ${width} 190`} role="img" aria-label={`${metric} history from ${points[0].date} to ${points[points.length-1].date}`}>
      {[0,.5,1].map(fraction=><g key={fraction}><line x1="40" x2={width-20} y1={160-fraction*130} y2={160-fraction*130} class="quota-grid-line"/><text x="40" y={153-fraction*130}>{metric==='tokens'?compactNumber(maximum*fraction):formatMoney(maximum*fraction)}</text></g>)}
      <polyline points={points.map((row,i)=>`${x(i)},${y(row)}`).join(' ')} fill="none" stroke="var(--accent)" stroke-width="2"/>
      {points.map((row,i)=><g key={row.date} onClick={()=>setSelected(row.date!)}><circle cx={x(i)} cy={y(row)} r="12" fill="transparent"/><circle cx={x(i)} cy={y(row)} r={active===row?4:2} fill="var(--accent)"/><title>{row.date}: {metric==='tokens'?exactNumber(amount(row)):exactMoney(amount(row))}</title></g>)}
      <text x="40" y="184">{points[0].date}</text><text x={width-20} y="184" text-anchor="end">{points[points.length-1].date}</text>
    </svg>
    <label class="analytics-chart-picker">Inspect period<select value={active.date} onChange={event=>setSelected(event.currentTarget.value)}>{points.map(row=><option key={row.date} value={row.date}>{row.date}</option>)}</select></label>
  </section>
}
