import { useEffect, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api.ts'

export type SetupStep = 'existing'|'experience'|'provider'|'harnesses'|'projects'|'desktop'|'finish'|'complete'
export type SetupDraft = {
  tier?: 'terminal'|'deterministic'|'automations'; autonomy?: string; overrides?: Record<string,boolean>
  theme?: string; keymap?: string; fleet_access?: string; harnesses?: Record<string,boolean>
  default_harness?: string; scan_history?: boolean; rail_desktop?: boolean; rail_mobile?: boolean
}
export type OnboardingState = {
  version: number; revision: number; step: SetupStep; status: 'active'|'deferred'|'complete'
  hidden: boolean; tour_status: 'pending'|'active'|'deferred'|'complete'; tour_step: string
  dismissed: string[]; completed: string[]; draft: SetupDraft; backup?: string|null; restart_required?: string[]
}
export type OnboardingPatch = Partial<Pick<OnboardingState,'step'|'status'|'hidden'|'tour_status'|'tour_step'|'dismissed'|'completed'|'draft'>> & {action?: 'restart'|'fresh'|'reuse'}
export type SaveOnboarding = (patch: OnboardingPatch) => Promise<OnboardingState>
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
        const value=await api<OnboardingState>('PATCH','/api/onboarding',{...patch,revision:previous.revision},{timeoutMs:15000})
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
