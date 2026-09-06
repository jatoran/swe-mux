import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { hostQuery } from './hostProfile'
import { DEFAULT_KEYMAP_PRESET, type KeymapPreset } from './HarnessSetup'
import type { SetupDraft } from './onboarding'

type Props={draft:SetupDraft;onChange:(patch:Partial<SetupDraft>)=>void;onStart:()=>Promise<void>;onContinue:()=>Promise<void>;onCustomize:()=>Promise<void>;onBusy:(busy:boolean)=>void}
const NOTES:Record<string,string>={swemux:'Common actions on Ctrl+Shift. A leader for the rest.',tmux:'Familiar prefix shortcuts.',zellij:'Mode-based shortcuts.',vscode:'Familiar editor shortcuts.',minimal:'A small set of shortcuts.'}
export function SetupKeymap({draft,onChange,onStart,onContinue,onCustomize,onBusy}:Props) {
  const [presets,setPresets]=useState<KeymapPreset[]>([])
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [loaded,setLoaded]=useState(false)
  const reload=()=>void api<{presets:KeymapPreset[];preset:string}>('GET',`/api/keybindings?${hostQuery()}`,undefined,{timeoutMs:10000}).then(result=>{
    setPresets(result.presets);setLoaded(true);setError('')
    if(!draft.keymap)onChange({keymap:result.preset||DEFAULT_KEYMAP_PRESET,keymap_applied:result.preset||DEFAULT_KEYMAP_PRESET})
  }).catch(cause=>setError(cause.message))
  useEffect(reload,[])
  useEffect(()=>{onBusy(busy);return()=>onBusy(false)},[busy])
  const apply=async(next:()=>Promise<void>)=>{
    setBusy(true);setError('')
    try{
      const preset=draft.keymap||DEFAULT_KEYMAP_PRESET
      if(preset!=='custom'&&preset!==draft.keymap_applied){
        await api('POST',`/api/keymap-preset?${hostQuery()}`,{preset},{timeoutMs:10000})
        onChange({keymap_applied:preset})
      }
      await next()
    }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
  }
  const selected=draft.keymap||DEFAULT_KEYMAP_PRESET
  return <section><h2>Choose familiar shortcuts.</h2><p class="setup-lede">One preset now. Every shortcut stays editable.</p>
    <div class="setup-cards">{presets.map(preset=><label class={`setup-card ${selected===preset.id?'selected':''}`} key={preset.id}><input type="radio" name="keymap" checked={selected===preset.id} onChange={()=>onChange({keymap:preset.id,keymap_applied:''})}/><span><strong>{preset.title}</strong><small>{NOTES[preset.id]||preset.description.split('. ')[0]}</small>{preset.warning&&<small class="setup-warning">{preset.warning}</small>}</span></label>)}</div>
    {selected==='custom'&&<p>Your custom shortcuts are selected.</p>}
    {!loaded&&!error&&<p role="status">Loading presets…</p>}
    <button class="link" disabled={busy||!loaded} onClick={()=>void apply(onCustomize)}>Customize shortcuts…</button>
    <p class="setup-hint">The shortcut editor opens in Settings. Closing it returns here.</p>
    {error&&<p role="alert">{error} <button onClick={reload}>Retry</button></p>}
    <footer class="setup-step-actions"><button disabled={busy||!loaded} onClick={()=>void apply(onContinue)}>Continue optional setup</button><button class="primary" disabled={busy||!loaded} onClick={()=>void apply(onStart)}>Start working</button></footer>
  </section>
}
