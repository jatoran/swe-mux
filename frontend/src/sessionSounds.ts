export type SoundEvent='complete'|'waiting'|'attention'|'failure'|'reset'
export type SoundId='two-tone'|'bong'|'thump'|'blip'|'sonar'|'blop'|'ding'|'custom'
export type SoundPreferences={enabled:boolean;volume:number;quietStart:string;quietEnd:string;events:Record<SoundEvent,boolean>;soundId:SoundId;customSound?:string}
export type NormalizedMuxEvent={id?:string;type:string;session_id?:string;payload?:Record<string,unknown>;timestamp?:number;ts?:number;seq?:number}

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

const KEY='swe-mux:session-sounds-v1'
const LAST_KEY='swe-mux:last-session-sound-v1'
const defaults:SoundPreferences={enabled:false,volume:.4,quietStart:'',quietEnd:'',soundId:'two-tone',events:{complete:true,waiting:true,attention:true,failure:true,reset:true}}
const played=new Map<string,number>()
let audio:HTMLAudioElement|null=null

export function soundPreferences():SoundPreferences{
  try{
    const stored=JSON.parse(localStorage.getItem(KEY)||'{}') as Partial<SoundPreferences>
    const known=new Set<SoundId>([...satisfyingSounds.map(item=>item.id),'custom'])
    const soundId=stored.soundId&&known.has(stored.soundId)?stored.soundId:stored.customSound?'custom':defaults.soundId
    return {...defaults,...stored,soundId,events:{...defaults.events,...stored.events}}
  }catch{return {...defaults,events:{...defaults.events}}}
}
export function setSoundPreferences(value:SoundPreferences):void{try{localStorage.setItem(KEY,JSON.stringify(value))}catch{/* private mode */}window.dispatchEvent(new CustomEvent('mux:sound-preferences'))}
export function lastSoundReason():string{try{return JSON.parse(localStorage.getItem(LAST_KEY)||'{}').reason||'No sound fired on this device yet.'}catch{return 'No sound fired on this device yet.'}}

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

export async function testSessionSound(preferences=soundPreferences()):Promise<void>{
  const source=preferences.soundId==='custom'&&preferences.customSound
    ?preferences.customSound
    :`/notification-sounds/${preferences.soundId==='custom'?'two-tone':preferences.soundId}.mp3`
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
  void testSessionSound(preferences).catch(()=>{/* automatic browser audio is best-effort */})
  try{localStorage.setItem(LAST_KEY,JSON.stringify({...match,sessionId:event.session_id,at:now}))}catch{/* private mode */}
  window.dispatchEvent(new CustomEvent('mux:sound-fired',{detail:{...match,sessionId:event.session_id,at:now}}))
}

export async function safeSoundFile(file:File):Promise<string>{
  if(!file.type.startsWith('audio/')||file.size>512*1024)throw new Error('Choose an audio file no larger than 512 KiB.')
  return await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('Could not read sound file.'));reader.readAsDataURL(file)})
}
