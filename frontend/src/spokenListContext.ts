export type SpokenSessionHandle={id:string;agentRunId:string|null}

export type SpokenListContext=
  | {kind:'sessions';items:SpokenSessionHandle[];pageFrom:number;shownThrough:number;expiresAt:number;lastSpeech:string}
  | {kind:'projects';ids:string[];pageFrom:number;shownThrough:number;expiresAt:number;lastSpeech:string}

export const SPOKEN_LIST_CONTEXT_KEY='mux:spoken-list-context:v1'
export const SPOKEN_LIST_TTL_MS=5*60_000

type ContextStorage=Pick<Storage,'getItem'|'setItem'|'removeItem'>

const validIndex=(value:unknown):value is number=>Number.isInteger(value)&&Number(value)>=0
const validExpiry=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)
const validSpeech=(value:unknown):value is string=>typeof value==='string'&&value.length<=12_000

const parseContext=(value:unknown):SpokenListContext|null=>{
  if(!value||typeof value!=='object')return null
  const item=value as Partial<SpokenListContext>
  if(!validIndex(item.pageFrom)||!validIndex(item.shownThrough)||item.pageFrom>item.shownThrough
    ||!validExpiry(item.expiresAt)||!validSpeech(item.lastSpeech))return null
  if(item.kind==='sessions'){
    if(!Array.isArray(item.items)||item.items.length>1_000||item.shownThrough>item.items.length)return null
    const items:SpokenSessionHandle[]=[]
    for(const handle of item.items){
      if(!handle||typeof handle!=='object')return null
      const candidate=handle as Partial<SpokenSessionHandle>
      if(typeof candidate.id!=='string'||!candidate.id
        ||(candidate.agentRunId!==null&&typeof candidate.agentRunId!=='string'))return null
      items.push({id:candidate.id,agentRunId:candidate.agentRunId})
    }
    return{kind:'sessions',items,pageFrom:item.pageFrom,shownThrough:item.shownThrough,expiresAt:item.expiresAt,lastSpeech:item.lastSpeech}
  }
  if(item.kind==='projects'){
    if(!Array.isArray(item.ids)||item.ids.length>1_000||item.shownThrough>item.ids.length
      ||item.ids.some(id=>typeof id!=='string'||!id))return null
    return{kind:'projects',ids:[...item.ids],pageFrom:item.pageFrom,shownThrough:item.shownThrough,expiresAt:item.expiresAt,lastSpeech:item.lastSpeech}
  }
  return null
}

export function loadSpokenListContext(
  now=Date.now(),
  storage:ContextStorage=localStorage,
):SpokenListContext|null{
  try{
    const context=parseContext(JSON.parse(storage.getItem(SPOKEN_LIST_CONTEXT_KEY)||'null'))
    if(!context||context.expiresAt<=now){storage.removeItem(SPOKEN_LIST_CONTEXT_KEY);return null}
    return context
  }catch{
    try{storage.removeItem(SPOKEN_LIST_CONTEXT_KEY)}catch{}
    return null
  }
}

export function saveSpokenListContext(context:SpokenListContext,storage:ContextStorage=localStorage):void{
  try{storage.setItem(SPOKEN_LIST_CONTEXT_KEY,JSON.stringify(context))}catch{/* Voice follow-ups still use the in-memory copy. */}
}

export function clearSpokenListContext(storage:ContextStorage=localStorage):void{
  try{storage.removeItem(SPOKEN_LIST_CONTEXT_KEY)}catch{/* An inaccessible store is already effectively empty. */}
}

export function spokenSessionHandle(context:SpokenListContext|null,number:number):SpokenSessionHandle|null{
  const index=number-1
  if(!context||context.kind!=='sessions'||index<0||index>=context.shownThrough)return null
  return context.items[index]||null
}

export function spokenProjectId(context:SpokenListContext|null,number:number):string|null{
  const index=number-1
  if(!context||context.kind!=='projects'||index<0||index>=context.shownThrough)return null
  return context.ids[index]||null
}
