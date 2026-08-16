import { useMemo } from 'preact/hooks'
import { ModelName } from './ModelName'
import { modelPeriodRows, type UsageRow, type UsageSource } from './usageAnalytics'

const integer = new Intl.NumberFormat()
const money = new Intl.NumberFormat(undefined,{
  style:'currency',
  currency:'USD',
  minimumFractionDigits:2,
  maximumFractionDigits:4,
})
const palette = ['#34d399','#60a5fa','#f59e0b','#c084fc','#f472b6','#22d3ee','#fb7185','#a3e635']

function costText(row:UsageRow):string {
  if(row.cost_method==='unavailable')return 'unavailable'
  const suffix = row.cost_method==='proportional'?' · allocated':row.cost_method==='mixed'?' · mixed est.':' · source est.'
  return `${money.format(row.cost_usd||0)}${suffix}`
}

export function UsageModelBreakdown({
  sources,
  visibleDates,
  resolution,
  metric,
}:{
  sources:UsageSource[]
  visibleDates:Set<string>
  resolution:'daily'|'monthly'
  metric:'tokens'|'cost'
}) {
  const rows = useMemo(
    ()=>modelPeriodRows(sources,visibleDates,resolution),
    [sources,visibleDates,resolution],
  )
  const labels = new Map(sources.map(source=>[source.source_id,source.source_label]))
  const models = [...new Set(rows.map(row=>`${row.source_id}\0${row.model}`))]
  const colors = new Map(models.map((model,index)=>[model,palette[index%palette.length]]))
  const periods = [...new Set(rows.map(row=>row.period))].sort()
  const maximum = Math.max(...periods.map(period=>rows.filter(row=>row.period===period).reduce(
    (sum,row)=>sum+(metric==='tokens'?row.total_tokens:row.cost_usd),0,
  )),1)

  if(!rows.length)return <section class="usage-table">
    <h3>Per-model history</h3>
    <p>The cached ccusage payload has no per-day model rows.</p>
  </section>

  return <div class="usage-model-breakdown">
    <p class="telemetry-caveat historical-caveat">
      Tokens are ccusage transcript aggregates. Costs retain the method reported by each source.
      Allocated costs are proportional shares of a daily estimate. These rows are not account-specific.
    </p>
    <section class="model-stack" aria-label={`Per-model ${resolution} ${metric} breakdown`}>
      <div class="model-legend">
        {models.map(model=>{
          const [source,name]=model.split('\0')
          return <span key={model}><i style={{background:colors.get(model)}}/>{labels.get(source)||source} · <ModelName model={name}/></span>
        })}
      </div>
      {[...periods].reverse().map(period=>{
        const periodRows=rows.filter(row=>row.period===period)
        const total=periodRows.reduce((sum,row)=>sum+(metric==='tokens'?row.total_tokens:row.cost_usd),0)
        return <div class="model-stack-row" key={period}>
          <span>{period}</span>
          <div class="model-stack-track" style={{width:`${Math.max(1,total/maximum*100)}%`}}>
            {periodRows.map(row=>{
              const value=metric==='tokens'?row.total_tokens:row.cost_usd
              const width=total?value/total*100:0
              const identity=`${row.source_id}\0${row.model}`
              return <i
                key={identity}
                style={{width:`${width}%`,background:colors.get(identity)}}
                title={`${labels.get(row.source_id)||row.source_id} · ${row.model}: ${metric==='tokens'?integer.format(value):money.format(value)}`}
              />
            })}
          </div>
          <strong>{metric==='tokens'?integer.format(total):money.format(total)}</strong>
        </div>
      })}
    </section>
    <section class="usage-table">
      <h3>{resolution==='daily'?'Daily':'Monthly'} model detail</h3>
      <div class="usage-table-scroll"><table><thead><tr>
        <th>{resolution==='daily'?'date':'month'}</th><th>source/model</th><th>tokens</th>
        <th>input</th><th>output</th><th>cache</th><th>cost</th>
      </tr></thead><tbody>{rows.map(row=><tr key={`${row.period}-${row.source_id}-${row.model}`}>
        <td>{row.period}</td><td>{labels.get(row.source_id)||row.source_id} · <ModelName model={row.model}/></td>
        <td>{integer.format(row.total_tokens)}</td><td>{integer.format(row.input_tokens)}</td>
        <td>{integer.format(row.output_tokens)}</td>
        <td>{integer.format(row.cache_read_tokens+row.cache_creation_tokens)}</td>
        <td>{costText(row)}</td>
      </tr>)}</tbody></table></div>
    </section>
  </div>
}
