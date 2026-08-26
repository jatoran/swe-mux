import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'

export type EdgeVoice = {
  id:string;locale:string;gender:string;name:string;status:string;codec:string
  categories:string[];personalities:string[]
}
export type EdgeCatalog = {
  status:'not_loaded'|'ready';voices:EdgeVoice[];fetched_at?:number|null
  package_version?:string|null;error?:string|null;stale:boolean
  selected:string;selected_present:boolean
}
export type EdgeProviderStatus = {
  id:'edge';available:boolean;integration:'unconfigured'|'unknown'|'ready'|'error'
  diagnostic?:string|null;python:string;package_version?:string|null;tested_version:boolean
  last_probe_at?:number|null;risk_acknowledged:boolean;retry_after?:number|null
  catalog:EdgeCatalog
}
export type EdgeSettingsValue = {
  tts_edge_python:string;tts_edge_voice:string;tts_edge_rate_percent:number
  tts_edge_volume_percent:number;tts_edge_pitch_hz:number;tts_edge_risk_ack_version:number
}
type EdgeField = keyof EdgeSettingsValue

export function EdgeTtsSettings({value,onChange}:{
  value:EdgeSettingsValue
  onChange:(field:EdgeField,value:string|number)=>void
}){
  const [provider,setProvider]=useState<EdgeProviderStatus|null>(null)
  const [catalog,setCatalog]=useState<EdgeCatalog|null>(null)
  const [busy,setBusy]=useState('')
  const [error,setError]=useState('')
  const [query,setQuery]=useState('')
  const [locale,setLocale]=useState('')
  const audioRef=useRef<HTMLAudioElement|null>(null)

  useEffect(()=>{
    void api<EdgeProviderStatus>('GET','/api/voice/providers/edge').then(next=>{
      setProvider(next);setCatalog(next.catalog)
    }).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
    return()=>audioRef.current?.pause()
  },[])

  const run=async(kind:'probe'|'refresh')=>{
    setBusy(kind);setError('')
    try{
      if(kind==='probe'){
        const next=await api<EdgeProviderStatus>('POST','/api/voice/providers/edge/probe')
        setProvider(next);setCatalog(next.catalog)
      }else{
        const next=await api<EdgeCatalog>('POST','/api/voice/providers/edge/voices/refresh')
        setCatalog(next)
        setProvider(current=>current?{...current,catalog:next}:current)
      }
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }

  const locales=useMemo(()=>[...new Set((catalog?.voices||[]).map(voice=>voice.locale))].sort(),[catalog])
  const filtered=useMemo(()=>{
    const needle=query.trim().toLowerCase()
    return (catalog?.voices||[]).filter(voice=>(!locale||voice.locale===locale)&&(
      !needle||`${voice.id} ${voice.name} ${voice.locale} ${voice.gender} ${voice.categories.join(' ')} ${voice.personalities.join(' ')}`.toLowerCase().includes(needle)
    ))
  },[catalog,locale,query])
  const selected=(catalog?.voices||[]).find(voice=>voice.id===value.tts_edge_voice)

  const preview=()=>{
    audioRef.current?.pause()
    const audio=new Audio(`/api/voice/providers/edge/preview?voice=${encodeURIComponent(value.tts_edge_voice)}`)
    audioRef.current=audio
    void audio.play().catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
  }

  return <div class="edge-tts-settings">
    <div class="provider-disclosure">
      <strong>Experimental external integration</strong>
      <p>Edge TTS sends each spoken segment to Microsoft through an undocumented consumer endpoint. It has no API key, SLA, or published third-party commercial-use grant. Microsoft can change or refuse it at any time. Summary mode may send session-derived text to OpenRouter first and the resulting summary to Microsoft.</p>
      <label class="check" data-setting="tts_edge_risk_ack_version"><span>I understand the service, privacy, reliability, and commercial-use uncertainty</span><input type="checkbox" checked={value.tts_edge_risk_ack_version>=1} onChange={event=>onChange('tts_edge_risk_ack_version',event.currentTarget.checked?1:0)}/></label>
    </div>
    <label data-setting="tts_edge_python">External Python interpreter<input value={value.tts_edge_python} placeholder="Blank uses this Python in a source install" onInput={event=>onChange('tts_edge_python',event.currentTarget.value)}/><small>The frozen app never bundles the LGPL client. Install <code>edge-tts==7.2.8</code> in this interpreter, save, then check the integration.</small></label>
    <p aria-live="polite"><span class={`state-dot ${provider?.integration==='ready'?'idle':busy?'running':'stopped'}`}/> integration::{provider?.integration||'unknown'}{provider?.package_version?` · edge-tts ${provider.package_version}${provider.tested_version?' tested':' untested'}`:''}{provider?.diagnostic?` · ${provider.diagnostic}`:''}</p>
    <div class="theme-actions">
      <button type="button" disabled={!!busy} onClick={()=>void run('probe')}>{busy==='probe'?'Checking…':'Check integration'}</button>
      <button type="button" disabled={!!busy} onClick={()=>void run('refresh')}>{busy==='refresh'?'Refreshing…':catalog?.voices.length?'Refresh voices':'Load voices from Microsoft'}</button>
    </div>
    {catalog?.voices.length?<>
      <p>catalog::{catalog.status}{catalog.stale?' · stale':''} · voices::{catalog.voices.length}{catalog.package_version?` · edge-tts ${catalog.package_version}`:''}{catalog.error?` · last refresh failed: ${catalog.error}`:''}</p>
      <div class="edge-voice-filters">
        <label>Find a voice<input value={query} placeholder="Name, locale, personality" onInput={event=>setQuery(event.currentTarget.value)}/></label>
        <label>Locale<select value={locale} onChange={event=>setLocale(event.currentTarget.value)}><option value="">All locales</option>{locales.map(item=><option value={item}>{item}</option>)}</select></label>
      </div>
      <label data-setting="tts_edge_voice">Edge voice<select size={Math.min(9,Math.max(3,filtered.length))} value={value.tts_edge_voice} onChange={event=>onChange('tts_edge_voice',event.currentTarget.value)}>
        {!filtered.some(voice=>voice.id===value.tts_edge_voice)&&<option value={value.tts_edge_voice}>{selected?`${selected.locale} · ${selected.name} · selected`:`${value.tts_edge_voice} · not in latest catalog`}</option>}
        {filtered.map(voice=><option value={voice.id}>{voice.locale} · {voice.name} · {voice.gender}{voice.status&&voice.status!=='GA'?` · ${voice.status}`:''}</option>)}
      </select><small>{selected?`${selected.id}${selected.personalities.length?` · ${selected.personalities.join(', ')}`:''}`:`${value.tts_edge_voice} is preserved but missing from the latest catalog.`}</small></label>
      <button type="button" title={!provider?.risk_acknowledged?'Save the acknowledgement before previewing':undefined} disabled={!provider?.risk_acknowledged||provider.integration!=='ready'||!value.tts_edge_voice} onClick={preview}>♪ Preview selected voice</button>
    </>:<p>No cached service catalog. Loading voices is an explicit network request and never happens merely because Settings opened.</p>}
    <div class="edge-prosody-grid">
      <label data-setting="tts_edge_rate_percent">Rate (%)<input type="number" min="-100" max="100" value={value.tts_edge_rate_percent} onInput={event=>onChange('tts_edge_rate_percent',Number(event.currentTarget.value))}/></label>
      <label data-setting="tts_edge_volume_percent">Volume (%)<input type="number" min="-100" max="100" value={value.tts_edge_volume_percent} onInput={event=>onChange('tts_edge_volume_percent',Number(event.currentTarget.value))}/></label>
      <label data-setting="tts_edge_pitch_hz">Pitch (Hz)<input type="number" min="-100" max="100" value={value.tts_edge_pitch_hz} onInput={event=>onChange('tts_edge_pitch_hz',Number(event.currentTarget.value))}/></label>
    </div>
    {error&&<p class="assistant-error" role="alert">{error}</p>}
  </div>
}
