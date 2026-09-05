import { render } from 'preact'
import { useState } from 'preact/hooks'
import { OnboardingFlow } from '../../src/OnboardingFlow'
import { GettingStarted } from '../../src/GettingStarted'
import { GuidedTutorial } from '../../src/GuidedTutorial'
import { ConnectPhone } from '../../src/ConnectPhone'
import { useOnboarding } from '../../src/onboarding'
import type { TutorialStepId } from '../../src/tutorial'
import '../../src/style.css'

function Host(){
  const {state,error,save}=useOnboarding()
  const [phone,setPhone]=useState(false)
  return <div style="display:flex;min-height:100dvh;background:var(--bg);color:var(--text)">
    <aside style="width:260px;border-right:1px solid var(--line);padding-top:20px">
      <h3 style="padding:10px">PROJECTS</h3>
      {state&&<GettingStarted state={state} save={save} completed={[]} tier={state.draft.tier||''} onAction={id=>{if(id==='phone')setPhone(true);else if(id==='tour')void save({tour_status:'active'});else void save({step:'experience',status:'active'})}}/>}
      <p style="padding:10px">Usage</p>
      <button onClick={()=>void save({hidden:false})}>Show Getting started again</button>
    </aside>
    <main style="padding:30px"><h1>Your Project workspace.</h1><p>Your terminals, notes, and files live here.</p>{error&&<p role="alert">{error}</p>}</main>
    {state?.status==='active'&&<OnboardingFlow state={state} save={save} onTour={()=>{}} onLaunch={()=>{}} onDone={()=>{}}/>}
    {state?.status!=='active'&&state?.tour_status==='active'&&<GuidedTutorial hasProject setupCompleted initialStep={state.tour_step as TutorialStepId} onStep={step=>{void save({tour_step:step})}} onNavigate={()=>{}} onExit={()=>{void save({tour_status:'deferred'})}} onComplete={()=>{void save({tour_status:'complete'})}}/>}
    {phone&&<ConnectPhone onClose={()=>setPhone(false)} onComplete={()=>{void save({completed:['phone']});setPhone(false)}}/>}
  </div>
}
render(<Host/>,document.querySelector('#root')!)
