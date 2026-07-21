import { useEffect, useState } from 'preact/hooks'
import { lastSoundReason, safeSoundFile, satisfyingSounds, setSoundPreferences, soundPreferences, testSessionSound, type SoundEvent, type SoundId } from './sessionSounds'

const labels:Record<SoundEvent,string>={complete:'Root turn complete',waiting:'Waiting for input',attention:'Approval or Q&A',failure:'Failure',reset:'Unexpected quota reset'}
const customSoundOption={id:'custom' as const,label:'Custom',description:'your uploaded sound',glyph:'♪'}
export function NotificationSoundSettings(){
  const [prefs,setPrefs]=useState(soundPreferences)
  const [lastReason,setLastReason]=useState(lastSoundReason)
  const [error,setError]=useState('')
  useEffect(()=>{const listener=(event:Event)=>setLastReason((event as CustomEvent).detail.reason);window.addEventListener('mux:sound-fired',listener);return()=>window.removeEventListener('mux:sound-fired',listener)},[])
  const change=(next:typeof prefs)=>{setPrefs(next);setSoundPreferences(next)}
  const preview=(next:typeof prefs,soundId:SoundId)=>void testSessionSound(next,soundId).then(()=>setError('')).catch(()=>setError('Could not play this sound. Check this site’s audio permission and try again.'))
  const sounds=prefs.customSound?[...satisfyingSounds,customSoundOption]:satisfyingSounds
  const choose=(event:SoundEvent,soundId:SoundId)=>{const next={...prefs,eventSounds:{...prefs.eventSounds,[event]:soundId}};change(next);preview(next,soundId)}
  const removeCustom=()=>{
    const eventSounds=Object.fromEntries(Object.entries(prefs.eventSounds).map(([event,soundId])=>[event,soundId==='custom'?'two-tone':soundId])) as Record<SoundEvent,SoundId>
    change({...prefs,customSound:undefined,eventSounds})
  }
  return <section class="notification-sound-settings"><h3>Session notification sounds</h3><p>Device-only preferences. Root-agent events are normalized and deduplicated; subagent stops are excluded.</p>
    <label class="check"><span>Enable sounds on this device</span><input type="checkbox" checked={prefs.enabled} onChange={event=>change({...prefs,enabled:event.currentTarget.checked})}/></label>
    <label>Volume<input type="range" min="0" max="1" step=".05" value={prefs.volume} onInput={event=>change({...prefs,volume:Number(event.currentTarget.value)})}/></label>
    <div class="sound-preset-heading"><strong>Available sounds</strong><span>Every choice is short and intentionally gentle. Click one to preview it.</span></div>
    <div class="sound-preset-grid" role="group" aria-label="Notification sound">
      {sounds.map(sound=><button type="button" key={sound.id} aria-label={`Preview ${sound.label}`} onClick={()=>preview(prefs,sound.id)}><span aria-hidden="true">{sound.glyph}</span><strong>{sound.label}</strong><small>{sound.description}</small></button>)}
    </div>
    <div class="quiet-hours"><label>Quiet from<input type="time" value={prefs.quietStart} onInput={event=>change({...prefs,quietStart:event.currentTarget.value})}/></label><label>Until<input type="time" value={prefs.quietEnd} onInput={event=>change({...prefs,quietEnd:event.currentTarget.value})}/></label></div>
    <div class="notification-event-sounds">
      <strong>Sounds by event</strong>
      {Object.entries(labels).map(([key,label])=>{const soundEvent=key as SoundEvent;return <div class="notification-event-sound" key={key}>
        <label class="check"><input type="checkbox" checked={prefs.events[soundEvent]} onChange={event=>change({...prefs,events:{...prefs.events,[key]:event.currentTarget.checked}})}/><span>{label}</span></label>
        <select aria-label={`Sound for ${label}`} value={prefs.eventSounds[soundEvent]} onChange={event=>choose(soundEvent,event.currentTarget.value as SoundId)}>{sounds.map(sound=><option key={sound.id} value={sound.id}>{sound.label}</option>)}</select>
        <button type="button" aria-label={`Preview sound for ${label}`} onClick={()=>preview(prefs,prefs.eventSounds[soundEvent])}>Preview</button>
      </div>})}
    </div>
    <label>Custom sound<input type="file" accept="audio/*" onChange={event=>{const file=event.currentTarget.files?.[0];if(file)void safeSoundFile(file).then(customSound=>{const next={...prefs,customSound};change(next);preview(next,'custom')}).catch(cause=>setError(cause.message))}}/></label>
    {prefs.customSound&&<div class="theme-actions"><button type="button" onClick={removeCustom}>Remove custom sound</button></div>}
    <p aria-live="polite">Last reason: {lastReason}</p>{error&&<p class="settings-inline-error">{error}</p>}
  </section>
}
