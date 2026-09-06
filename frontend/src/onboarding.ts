import { useEffect, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api.ts'
import type { Budget } from './types'

export type ProviderSetupDraft = Partial<{
  llm_provider:string;custom_llm_base_url:string;custom_llm_model:string;custom_llm_catalog_url:string
  openrouter_cheap_model:string;openrouter_standard_model:string;automation_daily_budget:Budget
}>

export type SetupStep = 'existing'|'experience'|'provider'|'harnesses'|'projects'|'keymap'|'permissions'|'extras'|'voice'|'phone'|'desktop'|'finish'|'complete'
export type VoiceSetupDraft = {
  step?: 'choices'|'install'|'test'|'provider'; read_aloud?: boolean; dictation?: boolean
  summaries?: boolean; assistant?: boolean; tts_engine?: 'sapi'|'kokoro'; stt_engine?: 'sapi'|'whisper'
}
export type SetupDraft = {
  tier?: 'terminal'|'deterministic'|'automations'; autonomy?: string; overrides?: Record<string,boolean>
  theme?: string; keymap?: string; keymap_applied?: string; fleet_access?: string; harnesses?: Record<string,boolean>
  default_harness?: string; scan_history?: boolean; rail_desktop?: boolean; rail_mobile?: boolean
  core_complete?: boolean; model_features_pending?: boolean
  applied_experience?: 'terminal'|'deterministic'|'automations'
  autonomy_overrides?: Record<string,number>; provider?:ProviderSetupDraft
  project_id?: string; project_path?: string; project_name?: string; project_filter?: string; selected_projects?: string[]
  provider_return?: 'permissions'|'extras'|'voice'; voice?: VoiceSetupDraft
}
export type OnboardingState = {
  version: number; revision: number; step: SetupStep; status: 'active'|'deferred'|'complete'
  hidden: boolean; tour_status: 'pending'|'active'|'deferred'|'complete'; tour_step: string
  dismissed: string[]; completed: string[]; draft: SetupDraft; backup?: string|null; restart_required?: string[]
}
export type OnboardingPatch = Partial<Pick<OnboardingState,'step'|'status'|'hidden'|'tour_status'|'tour_step'|'dismissed'|'completed'|'draft'>> & {action?: 'restart'|'fresh'|'reuse'}
export type SaveOnboarding = (patch: OnboardingPatch | ((state: OnboardingState) => OnboardingPatch)) => Promise<OnboardingState>
export const ONBOARDING_CHANGED = 'mux:onboarding-changed'

/** Failed startup reads stay unknown and retry. A failure is never completion. */
export function useOnboarding() {
  const [state,setState] = useState<OnboardingState|null>(null)
  const [error,setError] = useState('')
  const current = useRef(state)
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const alive = useRef(true)
  const accept = (value: OnboardingState) => {
    if (!alive.current) return
    if (current.current && current.current.revision > value.revision) return
    current.current=value;setState(value);setError('')
  }
  const reload = async () => {
    const value = await api<OnboardingState>('GET','/api/onboarding',undefined,{timeoutMs:10000})
    accept(value);return value
  }
  useEffect(()=>{
    alive.current=true
    let timer: ReturnType<typeof setTimeout>|undefined
    let attempt=0
    const read=()=>void reload().catch(cause=>{
      if (!alive.current) return
      setError(`Setup is waiting for the daemon. ${String((cause as Error).message)}`)
      timer=setTimeout(read,Math.min(10000,1000*2**attempt++))
    })
    const changed=()=>read()
    read();window.addEventListener(ONBOARDING_CHANGED,changed)
    return ()=>{alive.current=false;clearTimeout(timer);window.removeEventListener(ONBOARDING_CHANGED,changed)}
  },[])
  const save: SaveOnboarding = patch => {
    const next=queue.current.catch(()=>{}).then(async()=>{
      const previous=current.current||await reload()
      try {
        const changes=typeof patch==='function'?patch(previous):patch
        const value=await api<OnboardingState>('PATCH','/api/onboarding',{...changes,revision:previous.revision},{timeoutMs:15000})
        accept(value);return value
      } catch(cause) {
        const conflict=(cause as ApiError).detail?.state as OnboardingState|undefined
        if(conflict)accept(conflict)
        if(alive.current)setError((cause as Error).message)
        throw cause
      }
    })
    queue.current=next
    return next
  }
  return {state,error,save,reload}
}

/** Optimistic fields stay visible during serialized writes and merge with other progress edits. */
export function useSetupDraft(state: OnboardingState, save: SaveOnboarding) {
  const [draft,setDraft]=useState(state.draft)
  const edits=useRef<Partial<SetupDraft>>({})
  const timer=useRef<ReturnType<typeof setTimeout>>()
  const [error,setError]=useState('')
  const flush=async()=>{
    clearTimeout(timer.current)
    const patch={...edits.current}
    if(!Object.keys(patch).length)return
    try {
      const next=await save(current=>({draft:{...current.draft,...patch}}))
      for(const key of Object.keys(patch) as (keyof SetupDraft)[]) {
        if(edits.current[key]===patch[key])delete edits.current[key]
      }
      setDraft({...next.draft,...edits.current});setError('')
    } catch(cause) {setError((cause as Error).message);throw cause}
  }
  const update=(patch:Partial<SetupDraft>)=>{
    edits.current={...edits.current,...patch}
    setDraft(current=>({...current,...patch}))
    clearTimeout(timer.current)
    timer.current=setTimeout(()=>void flush().catch(()=>{}),300)
  }
  useEffect(()=>{setDraft({...state.draft,...edits.current})},[state.revision])
  useEffect(()=>()=>clearTimeout(timer.current),[])
  return {draft,update,flush,error}
}
