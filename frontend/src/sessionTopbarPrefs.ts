import { useEffect, useState } from 'preact/hooks'
import { currentProfile, rawDomain, saveDomain, type SettingsProfile } from './deviceSettings.ts'
import { defaultSessionTopbarConfig, normalizeSessionTopbarConfig, type SessionTopbarConfig } from './sessionTopbarConfig.ts'

/** An absent or cleared mobile domain follows desktop until the first edit. */
export const hasMobileSessionTopbarConfig=():boolean=>
  Object.keys(rawDomain('mobile','sessionTopbar')??{}).length>0

export const loadSessionTopbarConfig=(profile:SettingsProfile=currentProfile()):SessionTopbarConfig=>
  normalizeSessionTopbarConfig(rawDomain(profile==='mobile'&&hasMobileSessionTopbarConfig()?'mobile':'desktop','sessionTopbar'))

let sequence=0
const pageId=Math.random().toString(36).slice(2)
function record(operation:string,profile:SettingsProfile,config:Record<string,unknown>,id:number,failed=false):void {
  const entry={at:new Date().toISOString(),severity:failed?'warning':'info',component:'session-topbar',
    operation,profile,pageId,sequence:id,inheritsDesktop:profile==='mobile'&&!Object.keys(config).length,
    rows:Array.isArray(config.rows)?config.rows.length:0,density:config.density}
  try {
    const key='mux.session-topbar-diagnostics.v1'
    let raw:unknown
    try { raw=JSON.parse(window.localStorage.getItem(key)||'[]') } catch { raw=[] }
    const rows=Array.isArray(raw)?raw.filter(row=>row&&typeof row==='object'
      &&row.component==='session-topbar'&&JSON.stringify(row).length<1024).slice(-63):[]
    window.localStorage.setItem(key,JSON.stringify([...rows,entry]))
  } catch { console.warn('[session-topbar] Diagnostic storage unavailable',entry) }
  if(failed)console.warn('[session-topbar] Layout save failed',entry)
}

async function persist(profile:SettingsProfile,config:Record<string,unknown>):Promise<void> {
  const id=++sequence
  record('save-started',profile,config,id)
  try {
    await saveDomain(profile,'sessionTopbar',config)
    record('save-completed',profile,config,id)
  } catch(error) {
    record('save-failed',profile,config,id,true)
    throw error
  }
}

export const saveSessionTopbarConfig=(config:SessionTopbarConfig,profile:SettingsProfile=currentProfile()):Promise<void>=>
  persist(profile,normalizeSessionTopbarConfig(config) as unknown as Record<string,unknown>)

export const resetSessionTopbarConfig=(profile:SettingsProfile=currentProfile()):Promise<void>=>
  saveSessionTopbarConfig(defaultSessionTopbarConfig(),profile)

export const inheritDesktopSessionTopbarConfig=():Promise<void>=>persist('mobile',{})

export function useSessionTopbarConfig(profile?:SettingsProfile):SessionTopbarConfig {
  const [config,setConfig]=useState(()=>loadSessionTopbarConfig(profile))
  useEffect(()=>{
    const sync=()=>setConfig(loadSessionTopbarConfig(profile))
    sync();window.addEventListener('mux:settings-changed',sync)
    return()=>window.removeEventListener('mux:settings-changed',sync)
  },[profile])
  return config
}
