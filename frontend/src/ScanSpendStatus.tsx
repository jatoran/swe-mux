import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { Session } from './types'

type Spend = {
  global_enabled:boolean;project_enabled:boolean;run_enabled:boolean
  daily_budget_usd:number;spend_today:{tokens:number;cost_usd:number}
}

export function ScanSpendStatus({session,onOpen}:{session:Session|null;onOpen:()=>void}) {
  const [state,setState]=useState<Spend|null>(null)
  const sid=session?.id||''
  const run=session?.agent_run_id||''
  useEffect(()=>{
    let stale=false
    const load=()=>{
      if(!sid){setState(null);return}
      api<Spend>('GET',`/api/sessions/${sid}/scan-timeline`).then(value=>{if(!stale)setState(value)}).catch(()=>{if(!stale)setState(null)})
    }
    load()
    const refresh=()=>load()
    const interval=window.setInterval(load,30_000)
    window.addEventListener('mux:turn-ended',refresh)
    return()=>{stale=true;window.clearInterval(interval);window.removeEventListener('mux:turn-ended',refresh)}
  },[sid,run])
  if(!state?.global_enabled||!state.project_enabled)return null
  const title=`Scan timeline ${state.run_enabled?'on for this run':'off for this run'} · $${state.spend_today.cost_usd.toFixed(4)} of $${state.daily_budget_usd.toFixed(2)} today · ${state.spend_today.tokens.toLocaleString()} tokens`
  return <button class={`scan-spend-status ${state.run_enabled?'active':''}`} title={title} aria-label={title} onClick={onOpen}><span>SCAN</span><strong>${state.spend_today.cost_usd.toFixed(4)}</strong><em>/ ${state.daily_budget_usd.toFixed(2)}</em></button>
}
