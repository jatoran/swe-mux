import { t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { hostQuery } from './hostProfile'
import { DEFAULT_KEYMAP_PRESET, type KeymapPreset } from './HarnessSetup'
import type { SetupDraft } from './onboarding'

type Props={draft:SetupDraft;onChange:(patch:Partial<SetupDraft>)=>void;onStart:()=>Promise<void>;onContinue:()=>Promise<void>;onCustomize:()=>Promise<void>;onBusy:(busy:boolean)=>void}
const NOTES:Record<string,string>={get swemux() { return t("SetupKeymap.42f7b62ffa") },get tmux() { return t("SetupKeymap.1fcf4855b6") },get zellij() { return t("SetupKeymap.563ab6f701") },get vscode() { return t("SetupKeymap.694a936124") },get minimal() { return t("SetupKeymap.5695bd0557") }}
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
  return <section><h2 data-heading-message="SetupKeymap.259b7ef403">{t("SetupKeymap.259b7ef403")}</h2><p class="setup-lede">{t("SetupKeymap.f5fef19b85")}</p>
    <div class="setup-cards">{presets.map(preset=><label class={`setup-card ${selected===preset.id?'selected':''}`} key={preset.id}><input type="radio" name="keymap" checked={selected===preset.id} onChange={()=>onChange({keymap:preset.id,keymap_applied:''})}/><span><strong>{preset.title}</strong><small>{NOTES[preset.id] || preset.description.split('. ')[0]}</small>{preset.warning && <small class="setup-warning">{preset.warning}</small>}</span></label>)}</div>
    {selected==='custom' && <p>{t("SetupKeymap.4fdff76df2")}</p>}
    {!loaded&&!error && <p role="status">{t("SetupKeymap.052669daa8")}</p>}
    <button class="link" disabled={busy||!loaded} onClick={()=>void apply(onCustomize)}>{t("SetupKeymap.ce67a96520")}</button>
    <p class="setup-hint">{t("SetupKeymap.38e6ac5460")}</p>
    {error && <p role="alert">{error} <button onClick={reload}>{t("SetupKeymap.942087cc2d")}</button></p>}
    <footer class="setup-step-actions"><button disabled={busy||!loaded} onClick={()=>void apply(onContinue)}>{t("SetupKeymap.cdb8f2c9eb")}</button><button class="primary" disabled={busy||!loaded} onClick={()=>void apply(onStart)}>{t("SetupKeymap.597bf9ec78")}</button></footer>
  </section>
}
