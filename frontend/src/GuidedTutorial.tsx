import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { mobileLayout } from './deviceMode'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { matchesTutorialAction, placeTutorialCard, TUTORIAL_ACTION_EVENT, type TutorialActionDetail, type TutorialActionGate, type TutorialStepId } from './tutorial'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'

export type { TutorialStepId }

type Props={
  hasProject:boolean
  setupCompleted?:boolean
  initialStep?:TutorialStepId
  onStep?:(step:TutorialStepId)=>void
  onNavigate:(step:TutorialStepId)=>void
  onExit:()=>void
  onComplete:()=>void
}

type Step={
  id:TutorialStepId
  eyebrow:string
  title:string
  body:JSX.Element
  selectors?:string[]
  action?:{kind:'click';selectors:string[];hint:string}|{kind:'event';gate:TutorialActionGate;hint:string}
  dragTarget?:{selectors:string[];edge?:'right';label:string}
  targetLabel?:string
  /** Wording for the skip control on an action step, where the default reads wrongly.
   *  Every action step is skippable (see the footer); only the label varies. */
  skipLabel?:string
}

const findFirst=(selectors:string[]|undefined):HTMLElement|null=>{
  for(const selector of selectors||[]){
    for(const match of document.querySelectorAll<HTMLElement>(selector)){
      const rect=match.getBoundingClientRect()
      if(rect.width>0&&rect.height>0)return match
    }
  }
  return null
}

const rectOf=(element:HTMLElement|null):DOMRect|null=>element?.getBoundingClientRect()||null
const sameRect=(first:DOMRect|null,second:DOMRect|null)=>first===second||Boolean(first&&second&&
  Math.abs(first.left-second.left)<.5&&Math.abs(first.top-second.top)<.5&&
  Math.abs(first.width-second.width)<.5&&Math.abs(first.height-second.height)<.5)

export function GuidedTutorial({hasProject,setupCompleted=false,initialStep,onStep,onNavigate,onExit,onComplete}:Props){
  const initialHasProject=useRef(hasProject).current
  const mobileAtStart=useRef(mobileLayout()).current
  const [dragging,setDragging]=useState(false)
  const [targetRect,setTargetRect]=useState<DOMRect|null>(null)
  const cardRef=useRef<HTMLElement>(null)
  const [cardSize,setCardSize]=useState({width:380,height:250})
  const steps=useMemo<Step[]>(()=>[
    {id:'welcome',get eyebrow() { return t("GuidedTutorial.223a16dad9") },get title() { return t("GuidedTutorial.efc2ca7bfc") },body:<><p>{t("GuidedTutorial.31a0506b94")}</p><p>{rich("GuidedTutorial.9b7b0f8c4c", { value1: <strong>{t("GuidedTutorial.b79cac926e")}</strong> })}</p></>},
    {id:'projects',eyebrow:'PROJECTS',get title() { return t("GuidedTutorial.f7e1c3a632") },selectors:['[data-tutorial="projects"]','[data-tutorial="empty-project"]'],action:{kind:'click',selectors:['[data-tutorial="projects"]','[data-tutorial="empty-project"]'],get hint() { return t("GuidedTutorial.1b303c742f") }},get targetLabel() { return t("GuidedTutorial.77de0176f9") },body:<><p>{t("GuidedTutorial.651d5ff01a")}</p><p>{t("GuidedTutorial.835f850b9c")}</p></>},
    ...(initialHasProject?[{id:'project-open' as const,get eyebrow() { return t("GuidedTutorial.fcecf1cff4") },get title() { return t("GuidedTutorial.9c9f398040") },selectors:['[data-tutorial="open-project"]'],action:{kind:'click' as const,selectors:['[data-tutorial="open-project"]'],get hint() { return t("GuidedTutorial.a79031a5a0") }},get targetLabel() { return t("GuidedTutorial.9d4fc82f7e") },body:<><p>{t("GuidedTutorial.3becac9ee3")}</p><p>{t("GuidedTutorial.3bbb5b49f6")}</p></>}]:[
      {id:'project-add' as const,get eyebrow() { return t("GuidedTutorial.9c9e4aabab") },get title() { return t("GuidedTutorial.3351f5a892") },selectors:['[data-tutorial="add-project"]'],action:{kind:'click' as const,selectors:['[data-tutorial="add-project"]'],get hint() { return t("GuidedTutorial.8a486c1031") }},get targetLabel() { return t("GuidedTutorial.aff1307b94") },body:<><p>{t("GuidedTutorial.21d3adbf71")}</p><p>{rich("GuidedTutorial.e4886d6fa5", { value1: <strong>{t("GuidedTutorial.cf77ef5338")}</strong> })}</p></>},
      {id:'project-create' as const,get eyebrow() { return t("GuidedTutorial.3f4823e480") },get title() { return t("GuidedTutorial.af8a1f4d93") },selectors:['[data-tutorial="folder-picker"]','[data-tutorial="project-form"]'],action:{kind:'event' as const,gate:{action:'project-created' as const},get hint() { return t("GuidedTutorial.4af289c92b") }},get targetLabel() { return t("GuidedTutorial.507c81b1f8") },body:<><p>{rich("GuidedTutorial.f0435e7d91", { value1: <strong>{t("GuidedTutorial.fa67e4d0da")}</strong> })}</p><p class="tutorial-callout">{t("GuidedTutorial.7c30aa3ab3")}</p></>},
    ]),
    // Optional, and said so in the same words the first-run harness panel uses
    // ("sign in to each agent CLI so its account and history appear"). The two surfaces
    // used to contradict each other - the panel called CLI login a later step and this
    // step then demanded one before it would move - which is worse than either answer on
    // its own. Nothing further in the tour needs a provider account: the session it starts
    // is a Shell.
    {id:'accounts',get eyebrow() { return t("GuidedTutorial.48e156f97d") },get title() { return t("GuidedTutorial.ab547da674") },selectors:['[data-tutorial="provider-account-actions"]'],action:{kind:'event',gate:{action:'account-saved'},get hint() { return t("GuidedTutorial.3299ee7ae4") }},get targetLabel() { return t("GuidedTutorial.48e156f97d") },get skipLabel() { return t("GuidedTutorial.576cf61bb9") },body:<><p>{rich("GuidedTutorial.bf19e86e8b", { value1: <strong>{t("GuidedTutorial.e5e76c0505")}</strong>, value2: <strong>{t("GuidedTutorial.42652d72b0")}</strong> })}</p><p class="tutorial-callout">{t("GuidedTutorial.93b469820f")}</p></>},
    {id:'run',get eyebrow() { return t("GuidedTutorial.f5a9de1fd5") },get title() { return t("GuidedTutorial.3eca25a0f7") },selectors:['[data-tutorial="run"]','[data-tutorial="project-run"]'],action:{kind:'click',selectors:['[data-tutorial="run"]','[data-tutorial="project-run"]'],get hint() { return t("GuidedTutorial.bd9a027e36") }},get targetLabel() { return t("GuidedTutorial.8f12d4b184") },body:<><p>{t("GuidedTutorial.2176022cd0")}</p><p>{t("GuidedTutorial.901eabfe64")}</p></>},
    {id:'run-choice',get eyebrow() { return t("GuidedTutorial.c06b0c5769") },get title() { return t("GuidedTutorial.31f962f67c") },selectors:['[data-tutorial="run-choice-shell"]'],action:{kind:'event',gate:{action:'session-launched',backend:'shell'},get hint() { return t("GuidedTutorial.7a6d30c012") }},get targetLabel() { return t("GuidedTutorial.b6b04c9578") },body:<><p>{rich("GuidedTutorial.84497ba540", { value1: <strong>{t("GuidedTutorial.a733285486")}</strong> })}</p><p>{t("GuidedTutorial.6a0a003f8e")}</p></>},
    {id:'workspace',get eyebrow() { return t("GuidedTutorial.425bcff254") },get title() { return t("GuidedTutorial.0faff908db") },selectors:['[data-tutorial="workspace"]'],body:<><p>{t("GuidedTutorial.ecb70d106e")}</p><p>{t("GuidedTutorial.22505070c2")}</p></>},
    {id:'new-tab',eyebrow:'ADD A TAB',get title() { return t("GuidedTutorial.3075b4debd") },selectors:mobileAtStart?['[data-tutorial="run"]','[data-tutorial="project-run"]']:['[data-tutorial="pane-run"]','[data-tutorial="run"]','[data-tutorial="project-run"]'],action:{kind:'event',gate:{action:'session-launched',backend:'shell'},get hint() { return t("GuidedTutorial.6c6c554136") }},targetLabel:'RUN → SHELL',body:<><p>{rich("GuidedTutorial.9fec7cd529", { value1: (mobileAtStart) ? 'yes' : 'no', value2: <strong>{t("GuidedTutorial.a733285486")}</strong> })}</p><p>{t("GuidedTutorial.83ee22460f")}</p></>},
    ...(mobileAtStart?[{id:'tabs' as const,get eyebrow() { return t("GuidedTutorial.500c218ada") },get title() { return t("GuidedTutorial.148e47bab5") },selectors:['[data-tutorial="tab-strip"]'],body:<><p>{t("GuidedTutorial.f6fe9d071d")}</p><p>{t("GuidedTutorial.f1b1d16680")}</p></>}]:[
      {id:'tabs' as const,get eyebrow() { return t("GuidedTutorial.c8e3962375") },title:dragging?t("GuidedTutorial.57323ad3f3"):t("GuidedTutorial.6fbb53cf31"),selectors:['[data-tutorial="tab-drag-source"]'],action:{kind:'event' as const,gate:{action:'tab-dropped' as const,zone:'tabs' as const},hint:dragging?t("GuidedTutorial.97741ee799"):t("GuidedTutorial.47ce2aa008")},dragTarget:{selectors:['[data-tutorial="tab-strip"]'],get label() { return t("GuidedTutorial.b7ec601d79") }},targetLabel:dragging?t("GuidedTutorial.b7ec601d79"):t("GuidedTutorial.2658631fbb"),body:<><p>{dragging ? t("GuidedTutorial.b044f69357") : t("GuidedTutorial.8f0538a562")}</p><p>{t("GuidedTutorial.90d75ea7e3")}</p></>},
      {id:'splits' as const,get eyebrow() { return t("GuidedTutorial.f43307831f") },title:dragging?t("GuidedTutorial.be11ee16fb"):t("GuidedTutorial.9c3e86872a"),selectors:['[data-tutorial="tab-drag-source"]'],action:{kind:'event' as const,gate:{action:'tab-dropped' as const,zone:'split' as const},hint:dragging?t("GuidedTutorial.5ca3bc47d7"):t("GuidedTutorial.47ce2aa008")},dragTarget:{selectors:['[data-tutorial="workspace-pane"]'],edge:'right' as const,get label() { return t("GuidedTutorial.5e4ca4caa9") }},targetLabel:dragging?t("GuidedTutorial.5e4ca4caa9"):t("GuidedTutorial.2658631fbb"),body:<><p>{dragging ? t("GuidedTutorial.fb41296213") : t("GuidedTutorial.82176c53a4")}</p><p>{t("GuidedTutorial.28c714151d")}</p></>},
    ]),
    // Anchored on the drawer's Notes control rather than a sidebar row. `project-notes` is
    // carried by whichever Notes control is on screen: the launcher rail while the desktop
    // drawer is closed, the drawer's own pane strip while it is open, and the mobile
    // overlay's flattened strip. Desktop therefore always has exactly one — never zero, and
    // never two, which matters because an ambiguous anchor spotlights the wrong control.
    // Mobile has none while the panel is shut, so `navigateTutorial` opens the side panel
    // for this step the way it opens the navigation sidebar for the others. A Notes tab
    // the user has *hidden* still leaves no anchor on either layout; that one is answered
    // by the footer's skip control rather than by an anchor, because unhiding a tab is not
    // something the tour should do behind the user's back.
    {id:'resources',get eyebrow() { return t("GuidedTutorial.1684ad97fc") },get title() { return t("GuidedTutorial.2806830332") },selectors:['[data-tutorial="project-notes"]'],action:{kind:'click',selectors:['[data-tutorial="project-notes"]'],get hint() { return t("GuidedTutorial.0f88625ffd") }},get targetLabel() { return t("GuidedTutorial.6b6539ba76") },body:<><p>{rich("GuidedTutorial.25231ce6d4", { value1: <strong>{t("GuidedTutorial.8a7525b149")}</strong> })}</p><p>{t("GuidedTutorial.bc93fff11a")}</p></>},
    // The tour used to end having never mentioned that most of the drawer's analysis
    // surfaces start switched off, so a new user met them one empty pane at a time and
    // read "off" as "broken". Explanatory rather than click-gated: the gates themselves
    // are the interaction, and they are where the operator will be when it matters.
    {id:'gates',get eyebrow() { return t("GuidedTutorial.c897458d27") },get title() { return t("GuidedTutorial.a3421c8f69") },body:<><p>{t("GuidedTutorial.833c2c1edd")}</p><p>{t("GuidedTutorial.0aeaa1c162")}</p></>},
    {id:'features',get eyebrow() { return t("GuidedTutorial.6caa2eea08") },get title() { return t("GuidedTutorial.d1fd4d79b4") },selectors:['[data-tutorial="menu"]'],action:{kind:'click',selectors:['[data-tutorial="menu"]'],get hint() { return t("GuidedTutorial.b709643169") }},get targetLabel() { return t("GuidedTutorial.3797e00f2f") },body:<><p>{t("GuidedTutorial.7cbc79f395")}</p><p>{t("GuidedTutorial.44280015b3")}</p></>},
    // The copy names rows and groups that exist, and `TUTORIAL_CHROME_CLAIMS` in
    // `tutorial.ts` declares each of those names so `tourChrome.test.ts` can check it
    // against what `App.tsx` renders. This paragraph is why that mechanism exists: it
    // described a `Utilities` group for months after that group was unfolded into the
    // eight plain rows below, and nothing in the suite could tell.
    {id:'feature-menu',get eyebrow() { return t("GuidedTutorial.c6b65850d9") },get title() { return t("GuidedTutorial.ab23e3d6ff") },selectors:['[data-tutorial="main-menu"]'],body:<><p>{rich("GuidedTutorial.889a929a77", { value1: <strong>{t("GuidedTutorial.c1c80b0378")}</strong>, value2: <strong>{t("GuidedTutorial.8a7525b149")}</strong>, value3: <strong>{t("GuidedTutorial.60c2b52761")}</strong>, value4: <strong>{t("GuidedTutorial.a9e479fe98")}</strong>, value5: <strong>{t("GuidedTutorial.7ca62c6a45")}</strong>, value6: <strong>{t("GuidedTutorial.e89b30aa1d")}</strong>, value7: <strong>{t("GuidedTutorial.d96a9b9cc5")}</strong>, value8: <strong>{t("GuidedTutorial.788011833a")}</strong>, value9: <strong>{t("GuidedTutorial.04e2a9728a")}</strong>, value10: <strong>{t("GuidedTutorial.9514b7ff48")}</strong>, value11: <strong>{t("GuidedTutorial.a440d64bd0")}</strong>, value12: <strong>{t("GuidedTutorial.dc7824ba7c")}</strong>, value13: <strong>{t("GuidedTutorial.74a883a037")}</strong>, value14: <strong>{t("GuidedTutorial.17ccfa5b68")}</strong>, value15: <strong>{t("GuidedTutorial.b79cac926e")}</strong> })}</p><p>{rich("GuidedTutorial.879b018c4f", { value1: <kbd>{t("GuidedTutorial.3bc3eeed28")}</kbd>, value2: <kbd>{t("GuidedTutorial.911381f2e8")}</kbd>, value3: <kbd>{t("GuidedTutorial.f873119f0c")}</kbd> })}</p></>},
    // The tour ends by handing over to something that answers questions it did
    // not cover, which is most of them: two minutes cannot explain an install, and
    // the gear in the footer can. Anchored on the real control so the last thing
    // the tour does is point at where help lives afterwards.
    {id:'configurator',get eyebrow() { return t("GuidedTutorial.392e45251a") },get title() { return t("GuidedTutorial.c0efdae791") },selectors:['.configurator-trigger'],get targetLabel() { return t("GuidedTutorial.0b36e50d8f") },body:<><p>{t("GuidedTutorial.38137f4e06")}</p><p>{t("GuidedTutorial.bde67bc4df")}</p></>},
    // Ends by naming the surface that answers everything two minutes could not, rather
    // than only by naming the switch that replays these two minutes.
    {id:'ready',get eyebrow() { return t("GuidedTutorial.8b12f71b06") },get title() { return t("GuidedTutorial.4b6e8797cf") },body:<><p>{t("GuidedTutorial.0bbbbac8ae")}</p><p>{rich("GuidedTutorial.68ea42952a", { value1: <strong>{t("GuidedTutorial.b79cac926e")}</strong> })}</p></>},
  ].filter(step=>!setupCompleted||step.id!=='accounts') as Step[], [dragging, initialHasProject, mobileAtStart, setupCompleted, uiLocale.value, catalogRevision.value])
  const [index,setIndex]=useState(()=>Math.max(0,steps.findIndex(item=>item.id===initialStep)))
  const step=steps[index]

  useEffect(()=>{setDragging(false);onNavigate(step.id);onStep?.(step.id)},[step.id])

  useEffect(()=>{
    const advance=()=>setIndex(value=>Math.min(steps.length-1,value+1))
    const click=(event:MouseEvent)=>{
      if(step.action?.kind!=='click')return
      const target=event.target instanceof Element?event.target:null
      if(target&&step.action.selectors.some(selector=>target.closest(selector)))queueMicrotask(advance)
    }
    const action=(event:Event)=>{
      const detail=(event as CustomEvent<TutorialActionDetail>).detail
      if(!detail)return
      if((step.id==='tabs'||step.id==='splits')&&detail.action==='tab-drag-started'){setDragging(true);return}
      if((step.id==='tabs'||step.id==='splits')&&detail.action==='tab-drag-cancelled'){setDragging(false);return}
      if(step.action?.kind==='event'&&matchesTutorialAction(step.action.gate,detail))advance()
      else if(detail.action==='tab-dropped')setDragging(false)
    }
    document.addEventListener('click',click,true)
    window.addEventListener(TUTORIAL_ACTION_EVENT,action)
    return()=>{document.removeEventListener('click',click,true);window.removeEventListener(TUTORIAL_ACTION_EVENT,action)}
  },[step,steps.length])

  useEffect(()=>{
    let frame=0
    const measure=()=>{
      cancelAnimationFrame(frame)
      frame=requestAnimationFrame(()=>{
        const activeSelectors=dragging&&step.dragTarget?step.dragTarget.selectors:step.selectors
        const target=findFirst(activeSelectors)
        let nextTarget=rectOf(target)
        if(nextTarget&&dragging&&step.dragTarget?.edge==='right')nextTarget=new DOMRect(nextTarget.right-Math.max(72,nextTarget.width*.22),nextTarget.top+Math.min(34,nextTarget.height*.12),Math.max(72,nextTarget.width*.22),nextTarget.height-Math.min(34,nextTarget.height*.12))
        const visibleTarget=nextTarget&&nextTarget.width>0&&nextTarget.height>0?nextTarget:null
        setTargetRect(current=>sameRect(current,visibleTarget)?current:visibleTarget)
        if(cardRef.current){
          const nextSize={width:cardRef.current.offsetWidth,height:cardRef.current.offsetHeight}
          setCardSize(current=>current.width===nextSize.width&&current.height===nextSize.height?current:nextSize)
        }
      })
    }
    measure()
    const observer=new MutationObserver(measure)
    observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['class','style']})
    window.addEventListener('resize',measure)
    window.addEventListener('scroll',measure,true)
    return()=>{cancelAnimationFrame(frame);observer.disconnect();window.removeEventListener('resize',measure);window.removeEventListener('scroll',measure,true)}
  },[dragging,step])

  // The tutorial coaches over the live app rather than covering it, and it opens before
  // the surfaces it points at, so it correctly sits *under* them: back closes whatever
  // the tutorial talked you into opening, and exits the tutorial only once that is gone.
  useDismissLevel(onExit,true,'tutorial')
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();dismissStack.pop()}
      if(event.key==='ArrowRight'&&!step.action){event.preventDefault();setIndex(value=>Math.min(steps.length-1,value+1))}
    }
    window.addEventListener('keydown',key,true)
    return()=>window.removeEventListener('keydown',key,true)
  },[step.action,steps.length])

  const position=placeTutorialCard(targetRect,{width:innerWidth,height:innerHeight},cardSize)
  const last=index===steps.length-1
  const hole=targetRect?{left:Math.max(0,targetRect.left-9),top:Math.max(0,targetRect.top-9),right:Math.min(innerWidth,targetRect.right+9),bottom:Math.min(innerHeight,targetRect.bottom+9)}:null
  const targetLabel=dragging&&step.dragTarget?step.dragTarget.label:step.targetLabel
  const labelTop=targetRect?(targetRect.top>34?targetRect.top-31:Math.min(innerHeight-30,targetRect.bottom+9)):0

  return <div class={`tutorial-layer ${targetRect?'targeted':'centered'} ${dragging?'drag-active':''}`} role="dialog" aria-modal="true" aria-label={t("GuidedTutorial.12f2e0cbaf")}>
    {hole&&step.action&&!dragging && <div class="tutorial-blockers" aria-hidden="true"><i style={{left:0,top:0,width:'100%',height:hole.top}}/><i style={{left:0,top:hole.top,width:hole.left,height:hole.bottom-hole.top}}/><i style={{left:hole.right,top:hole.top,width:innerWidth-hole.right,height:hole.bottom-hole.top}}/><i style={{left:0,top:hole.bottom,width:'100%',height:innerHeight-hole.bottom}}/></div>}
    {targetRect && <div class="tutorial-spotlight" style={{left:targetRect.left-7,top:targetRect.top-7,width:targetRect.width+14,height:targetRect.height+14}} aria-hidden="true"/>}
    {targetRect&&targetLabel && <div class="tutorial-target-label" style={{left:Math.max(8,Math.min(innerWidth-190,targetRect.left+targetRect.width/2-88)),top:labelTop}}>{targetLabel}</div>}
    <section ref={cardRef} class={`tutorial-card side-${position.side}`} style={{left:position.left,top:position.top}}>
      <header><span>{step.eyebrow}</span><button onClick={onExit} aria-label={t("GuidedTutorial.f224be99f5")}>{t("GuidedTutorial.fd0510f004")}</button></header>
      <div class="tutorial-copy"><h2>{step.title}</h2>{step.body}</div>
      {/* An action step used to render its hint *instead of* a control, so the only way
          past one whose anchor was missing - or whose gate the user could not satisfy,
          which for `accounts` meant a real provider login - was to abandon the tour. A
          walk whose sole escape is abandonment is not a walk. The gate and its spotlight
          stay for the user who does want to do the thing now; skipping is the second
          answer, and it is a legitimate one. */}
      <footer><span>{index+1} / {steps.length}</span><div class="tutorial-progress" aria-hidden="true"><i style={{width:`${((index+1)/steps.length)*100}%`}}/></div>{step.action ? <><em>{step.action.hint}</em><button class="tutorial-skip" onClick={()=>last?onComplete():setIndex(value=>value+1)}>{step.skipLabel || t("GuidedTutorial.7d44d39712")}</button></> : <button class="primary" onClick={()=>last?onComplete():setIndex(value=>value+1)}>{last ? t("GuidedTutorial.a6c7a84baa") : t("GuidedTutorial.1ff57a29d7")}</button>}</footer>
    </section>
  </div>
}
