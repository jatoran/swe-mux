import { rich, t } from './localization/index.ts'
import { useState } from 'preact/hooks'
import { ModelName } from './ModelName'
import { UsageBreakdown, UsageTrend } from './AnalyticsPrimitives'
import { modelPeriodRows, sumUsageRows, type UsageRow, type UsageSource } from './usageAnalytics'

export function UsageModelBreakdown({sources,visibleDates,resolution,metric}:{sources:UsageSource[];visibleDates:Set<string>;resolution:'daily'|'monthly';metric:'tokens'|'cost'}) {
  const [selected,setSelected]=useState('')
  const rows=modelPeriodRows(sources,visibleDates,resolution)
  const labels=new Map(sources.map(source=>[source.source_id,source.source_label]))
  const groups=new Map<string,typeof rows>()
  for(const row of rows){const id=JSON.stringify([row.source_id,row.model]);const group=groups.get(id)||[];group.push(row);groups.set(id,group)}
  const models=[...groups].map(([id,items])=>({id,source:items[0].source_id,model:items[0].model,row:sumUsageRows(items),items})).sort((a,b)=>metric==='tokens'?b.row.total_tokens-a.row.total_tokens:b.row.cost_usd-a.row.cost_usd)
  const active=models.find(model=>model.id===selected)||models[0]
  const history:UsageRow[]=active?.items.map(row=>({...row,date:row.period}))||[]
  return <div class="usage-model-breakdown">
    <UsageBreakdown title={t("UsageModelBreakdown.2fbd410f09")} rows={models.map(model=>({id:model.id,label:<>{labels.get(model.source) || model.source} · <ModelName model={model.model}/></>,row:model.row}))}/>
    {!!active && <section><label class="analytics-chart-picker">{rich("UsageModelBreakdown.31dfb21d34", { value1: <select value={active.id} onChange={event=>setSelected(event.currentTarget.value)}>{models.map(model=><option key={model.id} value={model.id}>{labels.get(model.source) || model.source} · {model.model}</option>)}</select> })}</label><UsageTrend rows={history} metric={metric}/></section>}
    {!models.length && <p>{t("UsageModelBreakdown.2f0b073599")}</p>}
  </div>
}
