import { render } from 'preact'
import { useState } from 'preact/hooks'
import { OnboardingFlow } from '../../src/OnboardingFlow'
import { GettingStarted } from '../../src/GettingStarted'
import { GuidedTutorial } from '../../src/GuidedTutorial'
import { useOnboarding } from '../../src/onboarding'
import { api } from '../../src/api'
import type { TutorialStepId } from '../../src/tutorial'
import '../../src/style.css'

function Host(){
  const {state,error,save}=useOnboarding()
  const [customizing,setCustomizing]=useState(false)
  const [launched,setLaunched]=useState(false)
  const launch=async(project?:string,backend='shell')=>{
    const projects=await api<{id:string}[]>('GET','/api/projects')
    if(projects.length)await api('POST','/api/sessions',{project_id:project||projects[0].id,backend})
    setLaunched(true)
  }
  return <div style="display:flex;min-height:100dvh;background:var(--bg);color:var(--text)">
    <aside style="width:220px;flex:0 0 220px;border-right:1px solid var(--line);padding:12px">
      <h3>PROJECTS</h3>
      {state&&<GettingStarted state={state} save={save} completed={[]} tier={state.draft.tier||''} onAction={id=>{if(id==='tour')void save({status:'deferred',tour_status:'active'});else void save({step:id==='provider'?'provider':id==='voice'?'voice':id==='permissions'?'permissions':'experience',status:'active'})}}/>}
      <p>Usage</p><button onClick={()=>void save({hidden:false})}>Show Getting started again</button>
    </aside>
    <main style="padding:20px;min-width:0"><h1>Your Project workspace.</h1>{launched&&<p role="status">Session started</p>}{error&&<p role="alert">{error}</p>}</main>
    {!customizing&&state?.status==='active'&&<OnboardingFlow state={state} save={save} onTour={()=>{}} onLaunch={launch} onDone={()=>{}} onCustomizeKeymap={()=>setCustomizing(true)}/>}
    {customizing&&<div role="dialog" aria-label="Shortcut editor"><button onClick={()=>void api('PUT','/api/keybindings',{preset:'custom',rules:[]}).then(()=>setCustomizing(false))}>Save customized shortcuts</button></div>}
    {state?.status!=='active'&&state?.tour_status==='active'&&<GuidedTutorial hasProject setupCompleted initialStep={state.tour_step as TutorialStepId} onStep={step=>{void save({tour_step:step})}} onNavigate={()=>{}} onExit={()=>{void save({tour_status:'deferred'})}} onComplete={()=>{void save({tour_status:'complete'})}}/>}
  </div>
}
render(<Host/>,document.querySelector('#root')!)
