import { useState } from 'preact/hooks'
import { ModelPicker } from './ModelPicker'
import { perMillionTokens } from './modelPricing'
import type { ModelOption } from './modelFilter'
import type { ModelRoutingConfig, ProviderOverride } from './modelRouting'

export function SetupModelRow({label,model,catalog}:{label:string;model:string;catalog:ModelOption[]}) {
  const entry=catalog.find(item=>item.id===model)
  const input=perMillionTokens(entry?.prompt_price),output=perMillionTokens(entry?.completion_price)
  return <div class="setup-model-row"><div><strong>{label}</strong><code>{model||'Choose a model'}</code></div><small>{input||output?`${input||'?'} input / ${output||'?'} output per 1M tokens`:'Price not reported'}</small></div>
}
export function SetupModelSummary({draft,catalog,override,onChange}:{draft:ModelRoutingConfig;catalog:ModelOption[];override:ProviderOverride;onChange:(key:'openrouter_cheap_model'|'openrouter_standard_model',value:string)=>void}) {
  const [editing,setEditing]=useState<string|null>(null)
  return <div class="setup-model-summary">{(['openrouter_cheap_model','openrouter_standard_model'] as const).map((key,index)=><div key={key}>
    <SetupModelRow label={index?'Regular model':'Cheap model'} model={override?.model||draft[key]} catalog={catalog}/>
    {!override&&<><button class="link" aria-expanded={editing===key} onClick={()=>setEditing(editing===key?null:key)}>Change {index?'regular':'cheap'} model</button>{editing===key&&<ModelPicker id={`setup-${key}`} value={draft[key]} options={catalog} emptyLabel="Choose a model…" required onChange={value=>{onChange(key,value);setEditing(null)}}/>}</>}
  </div>)}</div>
}
