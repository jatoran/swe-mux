import { useState } from 'preact/hooks'
import type { OnboardingState, SaveOnboarding } from './onboarding'

const TASKS = [
  {id:'project',title:'Add your first Project'},
  {id:'session',title:'Launch your first session'},
  {id:'provider',title:'Finish model provider setup'},
  {id:'desktop',title:'Set up the desktop and tray'},
  {id:'phone',title:'Connect your phone'},
  {id:'voice',title:'Set up voice'},
] as const

export function GettingStarted({state,save,completed,onAction,tier}:{state:OnboardingState;save:SaveOnboarding;completed:string[];onAction:(id:string)=>void;tier:string}) {
  const [expanded,setExpanded]=useState(false)
  const [error,setError]=useState('')
  const persist=(patch:Parameters<SaveOnboarding>[0])=>void save(patch).catch(cause=>setError((cause as Error).message))
  if(state.hidden)return null
  const done=new Set([...state.completed,...completed])
  const tasks=TASKS.filter(task=>!state.dismissed.includes(task.id)&&(task.id!=='provider'||state.draft.tier==='automations'||state.draft.overrides?.automation_enabled||state.draft.overrides?.scan_timeline_enabled))
  return <section class="getting-started" aria-label="Getting started">
    <button class="getting-started-toggle" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}><span>{expanded?'▾':'▸'} Getting started</span><small>{state.status==='complete'?`${tasks.filter(task=>done.has(task.id)).length}/${tasks.length}`:'Setup paused'}</small></button>
    {expanded&&<div class="getting-started-body">
      {state.status!=='complete'&&<button class="primary" onClick={()=>persist({status:'active',hidden:false})}>Continue setup</button>}
      <button onClick={()=>onAction('experience')}>Experience: {tier||'Choose a tier'}</button>
      <button onClick={()=>onAction('tour')}>{state.tour_status==='complete'?'Replay UI tour':state.tour_step!=='welcome'?'Resume UI tour':'Take the UI tour'}</button>
      {tasks.map(task=><div class="getting-started-task" key={task.id}>
        <button onClick={()=>onAction(task.id)}><span aria-label={done.has(task.id)?'Completed':'Pending'}>{done.has(task.id)?'✓':'○'}</span> {task.title}</button>
        {!done.has(task.id)&&<button class="link" aria-label={`Dismiss ${task.title}`} onClick={()=>persist({dismissed:[...state.dismissed,task.id]})}>×</button>}
      </div>)}
      <details><summary>Explore more</summary><button onClick={()=>onAction('worktrees')}>Try an isolated worktree</button><a href="https://swemux.dev/docs/" target="_blank" rel="noreferrer">Documentation</a><a href="https://swemux.dev/demo/" target="_blank" rel="noreferrer">Live website demo</a></details>
      <button class="link" onClick={()=>persist({dismissed:[]})}>Restore dismissed steps</button>
      <button class="link" onClick={()=>persist({hidden:true})}>Hide Getting started</button>
      <small>Restore this section from Help at any time.</small>
      {error&&<p role="alert">{error}</p>}
    </div>}
  </section>
}
