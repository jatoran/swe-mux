import type { ModelPricingFacts } from './modelPricing.ts'

/**
 * One entry of the cached OpenRouter catalog, as the daemon serves it.
 *
 * The price and capacity fields are optional because two other kinds of entry
 * travel through the same list without them: the placeholder
 * `includeSelectedModel` synthesises for a configured id the catalog no longer
 * knows, and any catalog entry whose pricing OpenRouter did not report. Filtering
 * and ranking read only `id` and `name`, so a priceless entry stays fully
 * searchable and selectable.
 */
export type ModelOption = ModelPricingFacts & {id:string;name:string}

export function includeSelectedModel(models:ModelOption[],selected:string):ModelOption[]{
  const items=[...models]
  if(selected&&!items.some(item=>item.id===selected)){
    items.unshift({id:selected,name:'Configured model (catalog unavailable)'})
  }
  return items
}

export function filterModelOptions(
  models:ModelOption[],query:string,limit=100,
):ModelOption[]{
  const needle=query.trim().toLocaleLowerCase()
  if(!needle)return models.slice(0,limit)
  return models
    .map((model,index)=>{
      const id=model.id.toLocaleLowerCase()
      const name=model.name.toLocaleLowerCase()
      const rank=id===needle?0:name===needle?1:id.startsWith(needle)?2:name.startsWith(needle)?3:id.includes(needle)?4:name.includes(needle)?5:-1
      return {model,index,rank}
    })
    .filter(item=>item.rank>=0)
    .sort((left,right)=>left.rank-right.rank||left.index-right.index)
    .slice(0,limit)
    .map(item=>item.model)
}
