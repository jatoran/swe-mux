import type { ProviderAccount } from './ProviderAccounts'

export type CostMethod = 'source_estimate'|'proportional'|'unavailable'|'mixed'

export type UsageRow = {
  date?:string
  month?:string
  session_id?:string
  model?:string
  provider?:string
  input_tokens:number
  output_tokens:number
  cache_creation_tokens:number
  cache_read_tokens:number
  total_tokens:number
  cost_usd:number
  cost_is_estimate?:boolean
  cost_method?:CostMethod
}

export type ProviderUsage = {
  provider:string
  daily:UsageRow[]
  monthly:UsageRow[]
  sessions:UsageRow[]
  models:UsageRow[]
  model_daily?:UsageRow[]
  totals:UsageRow
  provenance?:{adapter?:string;package?:string;command?:string[]}
}

export type QuotaWindow = {used_percent:number;resets_at?:number}|null
export type QuotaRawPoint = {
  id:number
  provider:string
  account_id:string
  provider_account_uuid?:string|null
  sampled_at:number
  status:string
  session?:QuotaWindow
  weekly?:QuotaWindow
  freshness:string
  raw_precision:number
  error?:string|null
  active:boolean
}
export type QuotaDailyPoint = {
  provider:string
  account_id:string
  provider_account_uuid?:string|null
  day:string
  samples:number
  errors:number
  session_min?:number|null
  session_max?:number|null
  session_first?:number|null
  session_last?:number|null
  weekly_min?:number|null
  weekly_max?:number|null
  weekly_first?:number|null
  weekly_last?:number|null
}
export type ResetEvent = {
  id:string
  provider:string
  account_id:string
  window:'session'|'weekly'
  before_value:number
  after_value:number
  expected_reset_at?:number|null
  observed_at:number
  classification:'scheduled'|'unexpected'|'uncertain'
  confidence:string
  confirmed:number
  suppression_reason?:string|null
  confirmed_at?:number|null
  review_status?:'seen'|'manual_usage'|'discarded'|null
  reviewed_at?:number|null
}
export type QuotaSeries = {
  provider:string
  account_id:string
  provider_account_uuid?:string|null
  identity:'verified'|'legacy_unverified'
  points:Array<QuotaRawPoint|QuotaDailyPoint>
}
export type QuotaSeriesStatus = {
  schema_version:number
  interpretation:'quota_utilization_not_token_usage'
  resolution:'raw'|'daily'
  since?:number|null
  until?:number|null
  series:QuotaSeries[]
  resets:ResetEvent[]
}

const numericFields = [
  'input_tokens',
  'output_tokens',
  'cache_creation_tokens',
  'cache_read_tokens',
  'total_tokens',
  'cost_usd',
] as const

function mergedCostMethod(rows:UsageRow[]):CostMethod {
  const methods = new Set(rows.map(row=>row.cost_method||'unavailable'))
  return methods.size===1?[...methods][0]:'mixed'
}

export function sumUsageRows(rows:UsageRow[]):UsageRow {
  const result:UsageRow = {
    input_tokens:0,
    output_tokens:0,
    cache_creation_tokens:0,
    cache_read_tokens:0,
    total_tokens:0,
    cost_usd:0,
    cost_is_estimate:true,
    cost_method:mergedCostMethod(rows),
  }
  for(const row of rows)for(const field of numericFields)result[field]+=Number(row[field]||0)
  return result
}

export type ModelPeriodRow = UsageRow&{period:string;provider:string;model:string}

export function modelPeriodRows(
  providers:ProviderUsage[],
  visibleDates:Set<string>,
  resolution:'daily'|'monthly',
):ModelPeriodRow[] {
  const grouped = new Map<string,{period:string;provider:string;model:string;rows:UsageRow[]}>()
  for(const provider of providers){
    for(const row of provider.model_daily||[]){
      if(!row.date||!visibleDates.has(row.date))continue
      const period = resolution==='daily'?row.date:row.date.slice(0,7)
      const model = row.model||'unknown'
      const key = `${period}\0${provider.provider}\0${model}`
      const group = grouped.get(key)||{period,provider:provider.provider,model,rows:[]}
      group.rows.push(row)
      grouped.set(key,group)
    }
  }
  return [...grouped.values()].map(group=>({
    ...sumUsageRows(group.rows),
    period:group.period,
    provider:group.provider,
    model:group.model,
  })).sort((a,b)=>b.period.localeCompare(a.period)||a.provider.localeCompare(b.provider)||a.model.localeCompare(b.model))
}

export function accountDisplayLabel(account:Pick<ProviderAccount,'label'|'email'|'provider'>):string {
  const detail = account.email&&account.email!==account.label?` · ${account.email}`:''
  return `${account.label}${detail} · ${account.provider}`
}

export function quotaSeriesPath(options:{
  provider?:string
  account?:string
  range:'7'|'30'|'90'|'all'
  resolution:'raw'|'daily'
  now?:number
}):string {
  const query = new URLSearchParams({resolution:options.resolution})
  if(options.provider)query.set('provider',options.provider)
  if(options.account)query.set('account',options.account)
  if(options.range!=='all'){
    const now = options.now??Date.now()/1000
    query.set('since',String(Math.floor(now-Number(options.range)*86400)))
    query.set('until',String(Math.floor(now)))
  }
  return `/api/telemetry/quota-series?${query}`
}

export function quotaPointTime(point:QuotaRawPoint|QuotaDailyPoint):number {
  return 'sampled_at' in point?point.sampled_at:Date.parse(`${point.day}T00:00:00Z`)/1000
}

export function quotaPointValue(
  point:QuotaRawPoint|QuotaDailyPoint,
  window:'session'|'weekly',
):number|null {
  if('sampled_at' in point)return point[window]?.used_percent??null
  return point[`${window}_last`]??null
}
