import { currentProfile, rawDomain, saveDomain, type SettingsProfile } from './deviceSettings.ts'
export type SoundEvent='complete'|'waiting'|'attention'|'failure'|'reset'
export type SoundId='two-tone'|'bong'|'thump'|'blip'|'sonar'|'blop'|'ding'|'custom'
export type SoundPreferences={enabled:boolean;volume:number;quietStart:string;quietEnd:string;events:Record<SoundEvent,boolean>;eventSounds:Record<SoundEvent,SoundId>;customSound?:string}
export type NormalizedMuxEvent={id?:string;type:string;session_id?:string;payload?:Record<string,unknown>;timestamp?:number;ts?:number;seq?:number;replay?:boolean}

export type SoundOption={id:Exclude<SoundId,'custom'>;label:string;description:string;glyph:string}
export const satisfyingSounds:SoundOption[]=[
  {id:'two-tone',label:'Two Tone',description:'warm, clean double note',glyph:'⌁'},
  {id:'bong',label:'Soft Bong',description:'rounded and resonant',glyph:'◉'},
  {id:'thump',label:'Low Thump',description:'quiet, muted low hit',glyph:'●'},
  {id:'blip',label:'Tiny Blip',description:'minimal and unobtrusive',glyph:'·'},
  {id:'sonar',label:'Soft Sonar',description:'airy outward ping',glyph:'◌'},
  {id:'blop',label:'Bubble',description:'soft and playful',glyph:'○'},
  {id:'ding',label:'Gentle Ding',description:'light glassy note',glyph:'◇'},
]

const soundEvents:SoundEvent[]=['complete','waiting','attention','failure','reset']
const defaultEvents:Record<SoundEvent,boolean>={complete:true,waiting:true,attention:true,failure:true,reset:true}
const defaultEventSounds:Record<SoundEvent,SoundId>={complete:'two-tone',waiting:'two-tone',attention:'two-tone',failure:'two-tone',reset:'two-tone'}
const defaults:SoundPreferences={enabled:false,volume:.4,quietStart:'',quietEnd:'',events:defaultEvents,eventSounds:defaultEventSounds}
const knownSoundIds=new Set<SoundId>([...satisfyingSounds.map(item=>item.id),'custom'])
const played=new Map<string,number>()
let audio:HTMLAudioElement|null=null

function validSoundId(value:unknown,customSound?:string):SoundId|null{
  return typeof value==='string'&&knownSoundIds.has(value as SoundId)&&(value!=='custom'||Boolean(customSound))?value as SoundId:null
}
export function normalizeSoundPreferences(value:unknown):SoundPreferences{
  const stored=value&&typeof value==='object'?value as Record<string,unknown>:{}
  const customSound=typeof stored.customSound==='string'&&stored.customSound?stored.customSound:undefined
  const legacySoundId=validSoundId(stored.soundId,customSound)||'two-tone'
  const storedEvents=stored.events&&typeof stored.events==='object'?stored.events as Record<string,unknown>:{}
  const storedEventSounds=stored.eventSounds&&typeof stored.eventSounds==='object'?stored.eventSounds as Record<string,unknown>:{}
  const events={...defaultEvents}
  const eventSounds={...defaultEventSounds}
  for(const event of soundEvents){
    if(typeof storedEvents[event]==='boolean')events[event]=storedEvents[event] as boolean
    eventSounds[event]=validSoundId(storedEventSounds[event],customSound)||legacySoundId
  }
  const volume=typeof stored.volume==='number'&&Number.isFinite(stored.volume)?Math.max(0,Math.min(1,stored.volume)):defaults.volume
  return {
    enabled:typeof stored.enabled==='boolean'?stored.enabled:defaults.enabled,
    volume,
    quietStart:typeof stored.quietStart==='string'?stored.quietStart:defaults.quietStart,
    quietEnd:typeof stored.quietEnd==='string'?stored.quietEnd:defaults.quietEnd,
    events,
    eventSounds,
    ...(customSound?{customSound}:{}),
  }
}
export function soundPreferencesFor(profile:SettingsProfile):SoundPreferences{return normalizeSoundPreferences(rawDomain(profile,'sounds'))}
export function soundPreferences():SoundPreferences{return soundPreferencesFor(currentProfile())}
export function setSoundPreferencesFor(profile:SettingsProfile,value:SoundPreferences):void{void saveDomain(profile,'sounds',value as unknown as Record<string,unknown>).catch(()=>{/* persisted on next edit; UI already updated optimistically */})}
export function setSoundPreferences(value:SoundPreferences):void{setSoundPreferencesFor(currentProfile(),value)}
let lastReason='No sound fired on this device yet.'
export function lastSoundReason():string{return lastReason}

function minutes(value:string):number|null{const match=/^(\d{2}):(\d{2})$/.exec(value);return match?Number(match[1])*60+Number(match[2]):null}
export function isQuietTime(preferences:SoundPreferences,date=new Date()):boolean{
  const start=minutes(preferences.quietStart),end=minutes(preferences.quietEnd)
  if(start===null||end===null||start===end)return false
  const now=date.getHours()*60+date.getMinutes()
  return start<end?now>=start&&now<end:now>=start||now<end
}

export function classifySoundEvent(event:NormalizedMuxEvent):{event:SoundEvent;reason:string}|null{
  const payload=event.payload||{}
  if(payload.scope&&payload.scope!=='root')return null
  if(payload.sidechain===true||payload.subagent===true)return null
  if(event.type==='unexpected_quota_reset')return {event:'reset',reason:'confirmed unexpected quota reset'}
  if(event.type==='approval_needed')return {event:'attention',reason:payload.kind==='input'?'root agent asked a question':'root agent needs approval'}
  if(event.type==='turn_failed'||event.type==='turn_aborted'||event.type==='session_crashed'||(event.type==='state_changed'&&payload.state==='crashed'))return {event:'failure',reason:'root agent failed'}
  if(event.type==='turn_ended')return {event:'complete',reason:'root agent turn completed'}
  if(event.type==='state_changed'&&payload.state==='idle')return {event:'waiting',reason:'root agent is ready for input'}
  return null
}

export async function testSessionSound(preferences=soundPreferences(),soundId=preferences.eventSounds.complete):Promise<void>{
  const source=soundId==='custom'&&preferences.customSound
    ?preferences.customSound
    :`/notification-sounds/${soundId==='custom'?'two-tone':soundId}.mp3`
  audio??=new Audio()
  audio.pause();audio.volume=preferences.volume;audio.src=source;audio.load()
  await audio.play()
}
export function handleSessionSound(event:NormalizedMuxEvent,projectAllows=true):void{
  const match=classifySoundEvent(event),preferences=soundPreferences()
  if(!match||!projectAllows||!preferences.enabled||!preferences.events[match.event]||isQuietTime(preferences))return
  const key=event.id||`${event.type}:${event.session_id||''}:${String(event.seq||event.ts||event.timestamp||event.payload?.timestamp||'')}`
  const now=Date.now();if((played.get(key)||0)>now-10_000)return
  played.set(key,now);for(const [id,at] of played)if(at<now-60_000)played.delete(id)
  void testSessionSound(preferences,preferences.eventSounds[match.event]).catch(()=>{/* automatic browser audio is best-effort */})
  lastReason=match.reason
  window.dispatchEvent(new CustomEvent('mux:sound-fired',{detail:{...match,sessionId:event.session_id,at:now}}))
}

export async function safeSoundFile(file:File):Promise<string>{
  if(!file.type.startsWith('audio/')||file.size>512*1024)throw new Error('Choose an audio file no larger than 512 KiB.')
  return await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('Could not read sound file.'));reader.readAsDataURL(file)})
}
