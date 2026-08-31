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
  using_managed:boolean
  managed:{
    status:'not_installed'|'installing'|'ready'|'error';phase?:string|null
    error?:string|null;version?:string|null;python:string;requirement:string
    uv_available:boolean;installed_at?:number|null;updated_at?:number|null
    // Which mechanism would build the environment, or null when neither can.
    // This, not `uv_available`, decides whether the button works: a source
    // install with a real Python and no uv falls back to `venv`.
    install_method?:'uv'|'venv'|null
    last_install_error?:string|null
  }
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

  useEffect(()=>{
    if(provider?.managed.status!=='installing')return
    const timer=setInterval(()=>{
      void api<EdgeProviderStatus>('GET','/api/voice/providers/edge').then(next=>{
        setProvider(next);setCatalog(next.catalog)
        if(next.managed.status!=='installing')setBusy('')
      }).catch(()=>{})
    },1000)
    return()=>clearInterval(timer)
  },[provider?.managed.status])

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

  const install=async()=>{
    setBusy('install');setError('')
    try{
      const response=await fetch('/api/voice/providers/edge/install',{
        method:'POST',headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'edge-tts-install'},body:'{}',
      })
      const next=await response.json() as EdgeProviderStatus&{started?:boolean;error?:string}
      if(!response.ok)throw new Error(next.error||'Managed Edge TTS installation failed to start.')
      setProvider(next);setCatalog(next.catalog)
      if(next.managed.status!=='installing')setBusy('')
    }catch(cause){setBusy('');setError(cause instanceof Error?cause.message:String(cause))}
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
    <div class="edge-managed-install">
      <p aria-live="polite"><span class={`state-dot ${provider?.managed.status==='ready'?'idle':provider?.managed.status==='installing'?'running':'stopped'}`}/> managed::{provider?.managed.status||'loading'}{provider?.managed.phase?` · ${provider.managed.phase.replaceAll('_',' ')}`:''}{provider?.managed.version?` · edge-tts ${provider.managed.version}`:''}{provider?.managed.error?` · ${provider.managed.error}`:''}</p>
      <div class="theme-actions"><button type="button" disabled={!!busy||provider?.managed.status==='installing'||provider?.managed.install_method===null} onClick={()=>void install()}>{provider?.managed.status==='installing'?'Installing…':provider?.managed.status==='ready'?'Repair / reinstall managed Edge TTS':provider?.managed.status==='error'?'Retry managed installation':'Install Edge TTS integration'}</button></div>
      <small>swe-mux asks <code>{provider?.managed.install_method==='venv'?'python -m venv':'uv'}</code> to build an isolated environment and fetch <code>{provider?.managed.requirement||'edge-tts==7.2.8'}</code> directly from PyPI into the environment containing <code>{provider?.managed.python||'<data_dir>/integrations/edge-tts/current/python'}</code>, verifies it, and activates it atomically. The package remains inspectable and replaceable under its <a href="https://github.com/rany2/edge-tts/blob/master/LICENSE" target="_blank" rel="noreferrer">LGPL-3.0 license</a>. Nothing installs merely because this provider is selected.</small>
      {provider?.managed.install_method==='venv'&&<p class="tts-lexicon-hint"><code>uv</code> was not found, so swe-mux will build the environment with this install's own Python (<code>python -m venv</code>). uv is preferred because it can also provide the interpreter; installing it is not required here.</p>}
      {provider?.managed.install_method===null&&<p class="tts-lexicon-hint">Managed installation needs either <code>uv</code> or a real Python interpreter to build an environment with, and this build has neither. Install uv, or use the external interpreter override below.</p>}
      {provider?.managed.status==='ready'&&provider.managed.last_install_error&&<p class="tts-lexicon-hint tts-lexicon-error">The current managed integration is still active, but its last repair failed: {provider.managed.last_install_error}</p>}
      {provider?.managed.status==='ready'&&!!value.tts_edge_python.trim()&&<p class="tts-lexicon-hint">The managed environment is ready, but the external interpreter override is active. Clear the override and Save to use the managed installation.</p>}
    </div>
    <label data-setting="tts_edge_python">External Python interpreter override<input value={value.tts_edge_python} placeholder="Blank uses the managed installation" onInput={event=>onChange('tts_edge_python',event.currentTarget.value)}/><small>Advanced escape hatch for an environment you maintain yourself. Save before checking it.</small></label>
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
