import { useEffect, useState } from 'preact/hooks'
import { lastSoundReason, safeSoundFile, satisfyingSounds, setSoundPreferences, soundPreferences, testSessionSound, type SoundEvent, type SoundId } from './sessionSounds'

const labels:Record<SoundEvent,string>={complete:'Root turn complete',waiting:'Waiting for input',attention:'Approval or Q&A',failure:'Failure',reset:'Unexpected quota reset'}
export function NotificationSoundSettings(){
  const [prefs,setPrefs]=useState(soundPreferences)
  const [lastReason,setLastReason]=useState(lastSoundReason)
  const [error,setError]=useState('')
  useEffect(()=>{const listener=(event:Event)=>setLastReason((event as CustomEvent).detail.reason);window.addEventListener('mux:sound-fired',listener);return()=>window.removeEventListener('mux:sound-fired',listener)},[])
  const change=(next:typeof prefs)=>{setPrefs(next);setSoundPreferences(next)}
  const preview=(next:typeof prefs)=>void testSessionSound(next).then(()=>setError('')).catch(()=>setError('Could not play this sound. Check this site’s audio permission and try again.'))
  const choose=(soundId:SoundId)=>{const next={...prefs,soundId};change(next);preview(next)}
  return <section class="notification-sound-settings"><h3>Session notification sounds</h3><p>Device-only preferences. Root-agent events are normalized and deduplicated; subagent stops are excluded.</p>
    <label class="check"><span>Enable sounds on this device</span><input type="checkbox" checked={prefs.enabled} onChange={event=>change({...prefs,enabled:event.currentTarget.checked})}/></label>
    <label>Volume<input type="range" min="0" max="1" step=".05" value={prefs.volume} onInput={event=>change({...prefs,volume:Number(event.currentTarget.value)})}/></label>
    <div class="sound-preset-heading"><strong>Sound</strong><span>Every choice is short and intentionally gentle. Click one to preview it.</span></div>
    <div class="sound-preset-grid" role="group" aria-label="Notification sound">
      {satisfyingSounds.map(sound=><button type="button" key={sound.id} class={prefs.soundId===sound.id?'active':''} aria-pressed={prefs.soundId===sound.id} onClick={()=>choose(sound.id)}><span aria-hidden="true">{sound.glyph}</span><strong>{sound.label}</strong><small>{sound.description}</small></button>)}
      {prefs.customSound&&<button type="button" class={prefs.soundId==='custom'?'active':''} aria-pressed={prefs.soundId==='custom'} onClick={()=>choose('custom')}><span aria-hidden="true">♪</span><strong>Custom</strong><small>your uploaded sound</small></button>}
    </div>
    <div class="quiet-hours"><label>Quiet from<input type="time" value={prefs.quietStart} onInput={event=>change({...prefs,quietStart:event.currentTarget.value})}/></label><label>Until<input type="time" value={prefs.quietEnd} onInput={event=>change({...prefs,quietEnd:event.currentTarget.value})}/></label></div>
    {Object.entries(labels).map(([key,label])=><label class="check" key={key}><span>{label}</span><input type="checkbox" checked={prefs.events[key as SoundEvent]} onChange={event=>change({...prefs,events:{...prefs.events,[key]:event.currentTarget.checked}})}/></label>)}
    <label>Custom sound<input type="file" accept="audio/*" onChange={event=>{const file=event.currentTarget.files?.[0];if(file)void safeSoundFile(file).then(customSound=>{const next={...prefs,customSound,soundId:'custom' as const};change(next);preview(next)}).catch(cause=>setError(cause.message))}}/></label>
    <div class="theme-actions"><button onClick={()=>preview(prefs)}>Preview selected sound</button>{prefs.customSound&&<button onClick={()=>change({...prefs,customSound:undefined,soundId:'two-tone'})}>Remove custom sound</button>}</div>
    <p aria-live="polite">Last reason: {lastReason}</p>{error&&<p class="settings-inline-error">{error}</p>}
  </section>
}
