import { rich, t } from './localization/index.ts'
import { useState } from 'preact/hooks'
import type { OnboardingState, SaveOnboarding } from './onboarding'

const TASKS = [
  {id:'project',get title() { return t("GettingStarted.771aef9224") }},
  {id:'session',get title() { return t("GettingStarted.8b1ccaf2c7") }},
  {id:'provider',get title() { return t("GettingStarted.d064a478a2") }},
  {id:'permissions',get title() { return t("GettingStarted.5e29baf284") }},
  {id:'desktop',get title() { return t("GettingStarted.ecd70d2c22") }},
  {id:'phone',get title() { return t("GettingStarted.1d3326aa87") }},
  {id:'voice',get title() { return t("GettingStarted.35caa7c79c") }},
] as const

export function GettingStarted({state,save,completed,onAction,tier}:{state:OnboardingState;save:SaveOnboarding;completed:string[];onAction:(id:string)=>void;tier:string}) {
  const [expanded,setExpanded]=useState(false)
  const [error,setError]=useState('')
  const persist=(patch:Parameters<SaveOnboarding>[0])=>void save(patch).catch(cause=>setError((cause as Error).message))
  if(state.hidden)return null
  const done=new Set([...state.completed,...completed])
  const tasks=TASKS.filter(task=>!state.dismissed.includes(task.id)&&(task.id!=='provider'||state.draft.model_features_pending||state.draft.tier==='automations'||state.draft.overrides?.automation_enabled||state.draft.overrides?.scan_timeline_enabled)&&(task.id!=='permissions'||state.draft.tier!=='terminal'))
  return <section class="getting-started" aria-label={t("GettingStarted.831d0f72d2")}>
    <button class="getting-started-toggle" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}><span>{t("GettingStarted.c8281a379b", { value1: (expanded) ? 'yes' : 'no' })}</span><small>{state.status==='complete' ? t("GettingStarted.8a8e4772b5", { length: tasks.filter(task=>done.has(task.id)).length, value2: tasks.length }) : t("GettingStarted.7b8649ebeb")}</small></button>
    {expanded && <div class="getting-started-body">
      {state.status!=='complete' && <button class="primary" onClick={()=>persist({status:'active',hidden:false})}>{t("GettingStarted.c5702c19da")}</button>}
      <button onClick={()=>onAction('experience')}>{rich("GettingStarted.365020d1a0", { value1: ({terminal:t("GettingStarted.4220e2080e"),deterministic:t("GettingStarted.9f6f919dc1"),automations:t("GettingStarted.78a79112df")} as Record<string,string>)[state.draft.tier||tier] || t("GettingStarted.a136fdd982") })}</button>
      <button onClick={()=>onAction('tour')}>{state.tour_status==='complete' ? t("GettingStarted.a0eddd6771") : state.tour_step!=='welcome' ? t("GettingStarted.f6c9398ba3") : t("GettingStarted.be14e09c8f")}</button>
      {tasks.map(task=><div class="getting-started-task" key={task.id}>
        <button onClick={()=>onAction(task.id)}><span aria-label={done.has(task.id) ? t("GettingStarted.22a970d2e5") : t("GettingStarted.331551b0de")}>{done.has(task.id) ? '✓' : '○'}</span> {task.title}</button>
        {!done.has(task.id) && <button class="link" aria-label={t("GettingStarted.d4d093af8c", { title: task.title })} onClick={()=>persist({dismissed:[...state.dismissed,task.id]})}>×</button>}
      </div>)}
      <details><summary>{t("GettingStarted.6fac593fa3")}</summary><button onClick={()=>onAction('worktrees')}>{t("GettingStarted.ea6b05aeec")}</button><a href="https://swemux.dev/docs/" target="_blank" rel="noreferrer">{t("GettingStarted.c205924de0")}</a><a href="https://swemux.dev/demo/" target="_blank" rel="noreferrer">{t("GettingStarted.d4171c6edc")}</a></details>
      <button class="link" onClick={()=>persist({dismissed:[]})}>{t("GettingStarted.bc4860da63")}</button>
      <button class="link" onClick={()=>persist({hidden:true})}>{t("GettingStarted.504c12162e")}</button>
      <small>{t("GettingStarted.56af7a804f")}</small>
      {error && <p role="alert">{error}</p>}
    </div>}
  </section>
}
