export type VoiceConversationRole = 'you' | 'mux'

export type VoiceConversationEntry = {
  id:string
  role:VoiceConversationRole
  text:string
  at:number
}

export const VOICE_CONVERSATION_HISTORY_KEY='mux:voice-conversation-history:v1'
export const VOICE_CONVERSATION_HISTORY_OPEN_KEY='mux:voice-conversation-history-open:v1'
export const VOICE_CONVERSATION_HISTORY_LIMIT=120
const ENTRY_TEXT_LIMIT=12_000

type HistoryStorage=Pick<Storage,'getItem'|'setItem'|'removeItem'>
let fallbackEntryId=0

const newEntryId=(at:number):string=>globalThis.crypto?.randomUUID?.()||`${at}-${fallbackEntryId++}`

const cleanText=(value:string):string=>value
  .replace(/\r\n?/g,'\n')
  .split('')
  .filter(character=>character==='\n'||character==='\t'||character.charCodeAt(0)>=32)
  .join('')
  .trim()
  .slice(0,ENTRY_TEXT_LIMIT)

const validEntry=(value:unknown):VoiceConversationEntry|null=>{
  if(!value||typeof value!=='object')return null
  const item=value as Partial<VoiceConversationEntry>
  if((item.role!=='you'&&item.role!=='mux')||typeof item.text!=='string'||typeof item.at!=='number'||!Number.isFinite(item.at))return null
  const text=cleanText(item.text)
  if(!text)return null
  return{id:typeof item.id==='string'&&item.id?item.id:newEntryId(item.at),role:item.role,text,at:item.at}
}

export function loadVoiceConversationHistory(storage:HistoryStorage=localStorage):VoiceConversationEntry[]{
  try{
    const decoded=JSON.parse(storage.getItem(VOICE_CONVERSATION_HISTORY_KEY)||'[]')
    if(!Array.isArray(decoded))return[]
    return decoded.map(validEntry).filter((item):item is VoiceConversationEntry=>!!item).slice(-VOICE_CONVERSATION_HISTORY_LIMIT)
  }catch{return[]}
}

export function appendVoiceConversationEntry(
  history:VoiceConversationEntry[],
  role:VoiceConversationRole,
  value:string,
  at=Date.now(),
):VoiceConversationEntry[]{
  const text=cleanText(value)
  if(!text)return history
  const next=[...history,{id:newEntryId(at),role,text,at}]
  return next.slice(-VOICE_CONVERSATION_HISTORY_LIMIT)
}

export function saveVoiceConversationHistory(history:VoiceConversationEntry[],storage:HistoryStorage=localStorage):void{
  try{
    if(history.length)storage.setItem(VOICE_CONVERSATION_HISTORY_KEY,JSON.stringify(history.slice(-VOICE_CONVERSATION_HISTORY_LIMIT)))
    else storage.removeItem(VOICE_CONVERSATION_HISTORY_KEY)
  }catch{/* A full or private browser store must not break capture. */}
}

export function loadVoiceConversationHistoryOpen(storage:HistoryStorage=localStorage):boolean{
  try{return storage.getItem(VOICE_CONVERSATION_HISTORY_OPEN_KEY)!=='closed'}
  catch{return true}
}

export function saveVoiceConversationHistoryOpen(open:boolean,storage:HistoryStorage=localStorage):void{
  try{storage.setItem(VOICE_CONVERSATION_HISTORY_OPEN_KEY,open?'open':'closed')}
  catch{/* A display preference must not break capture. */}
}
