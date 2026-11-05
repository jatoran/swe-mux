import { t } from './localization/index.ts'
import { useState } from 'preact/hooks'
import { ModelPicker } from './ModelPicker'
import { perMillionTokens } from './modelPricing'
import type { ModelOption } from './modelFilter'
import type { ModelRoutingConfig, ProviderOverride } from './modelRouting'

export function SetupModelRow({label,model,catalog}:{label:string;model:string;catalog:ModelOption[]}) {
  const entry=catalog.find(item=>item.id===model)
  const input=perMillionTokens(entry?.prompt_price),output=perMillionTokens(entry?.completion_price)
  return <div class="setup-model-row"><div><strong>{label}</strong><code>{model || t("SetupModelSummary.78a7ab5e31")}</code></div><small>{input||output ? t("SetupModelSummary.600fe2e33e", { value1: input||'?', value2: output||'?' }) : t("SetupModelSummary.6dbeacd58b")}</small></div>
}
export function SetupModelSummary({draft,catalog,override,onChange}:{draft:ModelRoutingConfig;catalog:ModelOption[];override:ProviderOverride;onChange:(key:'openrouter_cheap_model'|'openrouter_standard_model',value:string)=>void}) {
  const [editing,setEditing]=useState<string|null>(null)
  return <div class="setup-model-summary">{(['openrouter_cheap_model','openrouter_standard_model'] as const).map((key,index)=><div key={key}>
    <SetupModelRow label={index ? t("SetupModelSummary.c813b1b129") : t("SetupModelSummary.f2a72ce9a5")} model={override?.model||draft[key]} catalog={catalog}/>
    {!override && <><button class="link" aria-expanded={editing===key} onClick={()=>setEditing(editing===key?null:key)}>{t("SetupModelSummary.83c34bc095", { value1: (index) ? 'yes' : 'no' })}</button>{editing===key && <ModelPicker id={`setup-${key}`} value={draft[key]} options={catalog} emptyLabel={t("SetupModelSummary.9bd3b1e7c0")} required onChange={value=>{onChange(key,value);setEditing(null)}}/>}</>}
  </div>)}</div>
}
