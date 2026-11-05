import { codeLabel } from './localization/codeLabels.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
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
      if(!response.ok)throw new Error(next.error||t("EdgeTtsSettings.4b397179b2"))
      setProvider(next);setCatalog(next.catalog)
      if(next.managed.status!=='installing')setBusy('')
    }catch(cause){setBusy('');setError(cause instanceof Error?cause.message:String(cause))}
  }

  const locales=useMemo(()=>[...new Set((catalog?.voices||[]).map(voice=>voice.locale))].sort(), [catalog, uiLocale.value, catalogRevision.value])
  const filtered=useMemo(()=>{
    const needle=query.trim().toLowerCase()
    return (catalog?.voices||[]).filter(voice=>(!locale||voice.locale===locale)&&(
      !needle||`${voice.id} ${voice.name} ${voice.locale} ${voice.gender} ${voice.categories.join(' ')} ${voice.personalities.join(' ')}`.toLowerCase().includes(needle)
    ))
  }, [catalog, locale, query, uiLocale.value, catalogRevision.value])
  const selected=(catalog?.voices||[]).find(voice=>voice.id===value.tts_edge_voice)

  const preview=()=>{
    audioRef.current?.pause()
    const audio=new Audio(`/api/voice/providers/edge/preview?voice=${encodeURIComponent(value.tts_edge_voice)}`)
    audioRef.current=audio
    void audio.play().catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
  }

  return <div class="edge-tts-settings">
    <div class="provider-disclosure">
      <strong>{t("EdgeTtsSettings.c66e240d37")}</strong>
      <p>{t("EdgeTtsSettings.f6341e0840")}</p>
      <label class="check" data-setting="tts_edge_risk_ack_version"><span>{t("EdgeTtsSettings.a1194e2da8")}</span><input type="checkbox" checked={value.tts_edge_risk_ack_version>=1} onChange={event=>onChange('tts_edge_risk_ack_version',event.currentTarget.checked?1:0)}/></label>
    </div>
    <div class="edge-managed-install">
      <p aria-live="polite">{rich("EdgeTtsSettings.b94d4324b5", { value1: <span class={`state-dot ${provider?.managed.status==='ready'?'idle':provider?.managed.status==='installing'?'running':'stopped'}`}/>, value2: provider?.managed.status || t("EdgeTtsSettings.b4a070a2d3"), value3: provider?.managed.phase ? t("EdgeTtsSettings.f6b53f9c8a", { value1: provider.managed.phase.replaceAll('_',' ') }) : '', value4: provider?.managed.version ? t("EdgeTtsSettings.38a6695039", { version: provider.managed.version }) : '', value5: provider?.managed.error ? t("EdgeTtsSettings.f3b99994b4", { error: provider.managed.error }) : '' })}</p>
      <div class="theme-actions"><button type="button" disabled={!!busy||provider?.managed.status==='installing'||provider?.managed.install_method===null} onClick={()=>void install()}>{provider?.managed.status==='installing' ? t("EdgeTtsSettings.530bcc355f") : provider?.managed.status==='ready' ? t("EdgeTtsSettings.2737c05fce") : provider?.managed.status==='error' ? t("EdgeTtsSettings.aa0059593e") : t("EdgeTtsSettings.98ca60a5f4")}</button></div>
      <small>{rich("EdgeTtsSettings.adff565c68", { value1: <code>{provider?.managed.install_method==='venv' ? t("EdgeTtsSettings.18c6c46cc8") : t("EdgeTtsSettings.e6184ce10e")}</code>, value2: <code>{provider?.managed.requirement || t("EdgeTtsSettings.ca4a6ce65a")}</code>, value3: <code>{provider?.managed.python || t("EdgeTtsSettings.53f5be6c49")}</code>, value4: <a href="https://github.com/rany2/edge-tts/blob/master/LICENSE" target="_blank" rel="noreferrer">{t("EdgeTtsSettings.6045bd9812")}</a> })}</small>
      {provider?.managed.install_method==='venv' && <p class="tts-lexicon-hint">{rich("EdgeTtsSettings.4f8928685f", { value1: <code>{t("EdgeTtsSettings.e6184ce10e")}</code>, value2: <code>{t("EdgeTtsSettings.18c6c46cc8")}</code> })}</p>}
      {provider?.managed.install_method===null && <p class="tts-lexicon-hint">{rich("EdgeTtsSettings.ccb248fa48", { value1: <code>{t("EdgeTtsSettings.e6184ce10e")}</code> })}</p>}
      {provider?.managed.status==='ready'&&provider.managed.last_install_error && <p class="tts-lexicon-hint tts-lexicon-error">{rich("EdgeTtsSettings.3b51dc21db", { last_install_error: provider.managed.last_install_error })}</p>}
      {provider?.managed.status==='ready'&&!!value.tts_edge_python.trim() && <p class="tts-lexicon-hint">{t("EdgeTtsSettings.b7ff427442")}</p>}
    </div>
    <label data-setting="tts_edge_python">{rich("EdgeTtsSettings.8e909d2db2", { value1: <input value={value.tts_edge_python} placeholder={t("EdgeTtsSettings.53cecde8d2")} onInput={event=>onChange('tts_edge_python',event.currentTarget.value)}/>, value2: <small>{t("EdgeTtsSettings.1399c678fc")}</small> })}</label>
    <p aria-live="polite">{rich("EdgeTtsSettings.220ced15c9", { value1: <span class={`state-dot ${provider?.integration==='ready'?'idle':busy?'running':'stopped'}`}/>, value2: provider?.integration || t("EdgeTtsSettings.b23a6a8439"), value3: provider?.package_version ? t("EdgeTtsSettings.7b30b65d9d", { package_version: provider.package_version, value2: (provider.tested_version) ? 'yes' : 'no' }) : '', value4: provider?.diagnostic ? t("EdgeTtsSettings.2a54e33e60", { diagnostic: provider.diagnostic }) : '' })}</p>
    <div class="theme-actions">
      <button type="button" disabled={!!busy} onClick={()=>void run('probe')}>{busy==='probe' ? t("EdgeTtsSettings.ec963ffc91") : t("EdgeTtsSettings.43a5e0bd47")}</button>
      <button type="button" disabled={!!busy} onClick={()=>void run('refresh')}>{busy==='refresh' ? t("EdgeTtsSettings.1c0def7be0") : catalog?.voices.length ? t("EdgeTtsSettings.f84d095fb7") : t("EdgeTtsSettings.cf69437569")}</button>
    </div>
    {catalog?.voices.length ? <>
      <p>{rich("EdgeTtsSettings.dc97f875b7", { status: codeLabel(catalog.status), value2: (catalog.stale) ? 'yes' : 'no', length: catalog.voices.length, value4: catalog.package_version ? t("EdgeTtsSettings.847afecc00", { package_version: catalog.package_version }) : '', value5: catalog.error ? t("EdgeTtsSettings.5590b95d2a", { error: catalog.error }) : '' })}</p>
      <div class="edge-voice-filters">
        <label>{rich("EdgeTtsSettings.75a81dcfce", { value1: <input value={query} placeholder={t("EdgeTtsSettings.3a30e1a624")} onInput={event=>setQuery(event.currentTarget.value)}/> })}</label>
        <label>{rich("EdgeTtsSettings.c49ed82bf3", { value1: <select value={locale} onChange={event=>setLocale(event.currentTarget.value)}><option value="">{t("EdgeTtsSettings.a9f854d579")}</option>{locales.map(item=><option value={item}>{item}</option>)}</select> })}</label>
      </div>
      <label data-setting="tts_edge_voice">{rich("EdgeTtsSettings.609b506230", { value1: <select size={Math.min(9,Math.max(3,filtered.length))} value={value.tts_edge_voice} onChange={event=>onChange('tts_edge_voice',event.currentTarget.value)}>
        {!filtered.some(voice=>voice.id===value.tts_edge_voice) && <option value={value.tts_edge_voice}>{selected ? t("EdgeTtsSettings.af3f15095e", { locale: selected.locale, name: selected.name }) : t("EdgeTtsSettings.5ed135a143", { tts_edge_voice: value.tts_edge_voice })}</option>}
        {filtered.map(voice=><option value={voice.id}>{voice.locale} · {voice.name} · {voice.gender}{voice.status&&voice.status!=='GA' ? t("EdgeTtsSettings.b27ab680e4", { status: codeLabel(voice.status) }) : ''}</option>)}
      </select>, value2: <small>{selected ? t("EdgeTtsSettings.fb25f6f59c", { id: selected.id, value2: selected.personalities.length?` · ${selected.personalities.join(', ')}`:'' }) : t("EdgeTtsSettings.4f2486b005", { tts_edge_voice: value.tts_edge_voice })}</small> })}</label>
      <button type="button" title={!provider?.risk_acknowledged ? t("EdgeTtsSettings.2943c228c0") : undefined} disabled={!provider?.risk_acknowledged||provider.integration!=='ready'||!value.tts_edge_voice} onClick={preview}>{t("EdgeTtsSettings.d99fbe7902")}</button>
    </> : <p>{t("EdgeTtsSettings.0db05c8e03")}</p>}
    <div class="edge-prosody-grid">
      <label data-setting="tts_edge_rate_percent">{rich("EdgeTtsSettings.6f111d0ca6", { value1: <input type="number" min="-100" max="100" value={value.tts_edge_rate_percent} onInput={event=>onChange('tts_edge_rate_percent',Number(event.currentTarget.value))}/> })}</label>
      <label data-setting="tts_edge_volume_percent">{rich("EdgeTtsSettings.e84d42b977", { value1: <input type="number" min="-100" max="100" value={value.tts_edge_volume_percent} onInput={event=>onChange('tts_edge_volume_percent',Number(event.currentTarget.value))}/> })}</label>
      <label data-setting="tts_edge_pitch_hz">{rich("EdgeTtsSettings.06bca263eb", { value1: <input type="number" min="-100" max="100" value={value.tts_edge_pitch_hz} onInput={event=>onChange('tts_edge_pitch_hz',Number(event.currentTarget.value))}/> })}</label>
    </div>
    {error && <p class="assistant-error" role="alert">{error}</p>}
  </div>
}
