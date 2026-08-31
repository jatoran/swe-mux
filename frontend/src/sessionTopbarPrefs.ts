import { useEffect, useState } from 'preact/hooks'
import { rawDomain, saveDomain } from './deviceSettings.ts'
import { defaultSessionTopbarConfig, normalizeSessionTopbarConfig, type SessionTopbarConfig } from './sessionTopbarConfig.ts'

const PROFILE='desktop' as const

export const loadSessionTopbarConfig=():SessionTopbarConfig=>
  normalizeSessionTopbarConfig(rawDomain(PROFILE,'sessionTopbar'))

export const saveSessionTopbarConfig=(config:SessionTopbarConfig):Promise<void>=>
  saveDomain(PROFILE,'sessionTopbar',config as unknown as Record<string,unknown>)

export const resetSessionTopbarConfig=():Promise<void>=>saveSessionTopbarConfig(defaultSessionTopbarConfig())

export function useSessionTopbarConfig():SessionTopbarConfig {
  const [config,setConfig]=useState(loadSessionTopbarConfig)
  useEffect(()=>{
    const sync=()=>setConfig(loadSessionTopbarConfig())
    sync();window.addEventListener('mux:settings-changed',sync)
    return()=>window.removeEventListener('mux:settings-changed',sync)
  },[])
  return config
}
