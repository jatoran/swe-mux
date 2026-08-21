import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { Budget, Session } from './types'

type Spend = {
  global_enabled:boolean;project_enabled:boolean;run_enabled:boolean
  daily_budget:Budget;spend_today:{tokens:number;cost_usd:number;unpriced_calls?:number}
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
  // The chip reads whichever axis the budget actually enforces. A dollar figure
  // drawn against a token-only cap would name a limit that cannot stop scanning,
  // and against a provider that reports no cost it would sit at $0.0000 forever.
  const budget=state.daily_budget
  const enforcesUsd=budget.mode!=='tokens'&&budget.usd!=null
  const spent=enforcesUsd
    ?`$${state.spend_today.cost_usd.toFixed(4)}`
    :state.spend_today.tokens.toLocaleString()
  const cap=enforcesUsd
    ?`$${(budget.usd??0).toFixed(2)}`
    :(budget.tokens??0).toLocaleString()
  const unpriced=state.spend_today.unpriced_calls||0
  const title=`Scan timeline ${state.run_enabled?'on for this run':'off for this run'} · ${spent} of ${cap} today${enforcesUsd?` · ${state.spend_today.tokens.toLocaleString()} tokens`:''}${enforcesUsd&&unpriced?` · ${unpriced} of today's calls reported no cost`:''}`
  return <button class={`scan-spend-status ${state.run_enabled?'active':''}`} title={title} aria-label={title} onClick={onOpen}><span>SCAN</span><strong>{spent}</strong><em>/ {cap}</em></button>
}
