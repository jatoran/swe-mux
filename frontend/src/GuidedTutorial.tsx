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
  const mobileAtStart=useRef(window.matchMedia('(max-width:760px)').matches).current
  const [dragging,setDragging]=useState(false)
  const [targetRect,setTargetRect]=useState<DOMRect|null>(null)
  const cardRef=useRef<HTMLElement>(null)
  const [cardSize,setCardSize]=useState({width:380,height:250})
  const steps=useMemo<Step[]>(()=>[
    {id:'welcome',eyebrow:'WELCOME TO SWE_MUX',title:'Keep every agent and terminal in view.',body:<><p>swe-mux keeps Claude, Codex, shells, files, notes, and local previews alive in one Project workspace—even when this window closes.</p><p>This hands-on tour takes about two minutes. You can exit at any point and start it again from the menu's <strong>Help</strong> row, the command palette, or Settings → General.</p></>},
    {id:'projects',eyebrow:'PROJECTS',title:'Open the Project registry.',selectors:['[data-tutorial="projects"]','[data-tutorial="empty-project"]'],action:{kind:'click',selectors:['[data-tutorial="projects"]','[data-tutorial="empty-project"]'],hint:'Click the highlighted Projects control'},targetLabel:'CLICK PROJECTS',body:<><p>A Project anchors sessions, files, notes, previews, history, and pane layout to one real folder.</p><p>Click the highlighted control to open the real registry. The tour advances when you do.</p></>},
    ...(initialHasProject?[{id:'project-open' as const,eyebrow:'EXISTING PROJECT',title:'Open your existing Project workspace.',selectors:['[data-tutorial="open-project"]'],action:{kind:'click' as const,selectors:['[data-tutorial="open-project"]'],hint:'Click Open workspace'},targetLabel:'OPEN WORKSPACE',body:<><p>A Project is already configured, so the replay will not make you create a duplicate.</p><p>Open it now to continue with the same real workflow a new Project reaches after creation.</p></>}]:[
      {id:'project-add' as const,eyebrow:'FIRST PROJECT',title:'Start adding your first Project.',selectors:['[data-tutorial="add-project"]'],action:{kind:'click' as const,selectors:['[data-tutorial="add-project"]'],hint:'Click + Add project'},targetLabel:'CLICK + ADD PROJECT',body:<><p>Choose a real folder you want Claude, Codex, and terminals to work in.</p><p>Click <strong>+ Add project</strong> to open the creation form.</p></>},
      {id:'project-create' as const,eyebrow:'CREATE PROJECT',title:'Name it, choose its folder, and create it.',selectors:['[data-tutorial="folder-picker"]','[data-tutorial="project-form"]'],action:{kind:'event' as const,gate:{action:'project-created' as const},hint:'Complete the form and click Create project'},targetLabel:'COMPLETE THIS FORM',body:<><p>Enter a name, type or browse to an existing folder, then click <strong>Create project</strong>.</p><p class="tutorial-callout">The tour continues only after the Project is successfully created.</p></>},
    ]),
    // Optional, and said so in the same words the first-run harness panel uses
    // ("sign in to each agent CLI so its account and history appear"). The two surfaces
    // used to contradict each other - the panel called CLI login a later step and this
    // step then demanded one before it would move - which is worse than either answer on
    // its own. Nothing further in the tour needs a provider account: the session it starts
    // is a Shell.
    {id:'accounts',eyebrow:'PROVIDER ACCOUNTS',title:'Sign in to an agent CLI, whenever you are ready.',selectors:['[data-tutorial="provider-account-actions"]'],action:{kind:'event',gate:{action:'account-saved'},hint:'Save an account now, or skip'},targetLabel:'PROVIDER ACCOUNTS',skipLabel:'I’ll do this later',body:<><p>mux reads Claude’s and Codex’s own login files, so this switcher stays empty until each CLI has been signed in. <strong>sign in + save</strong> runs that login here; <strong>save current login</strong> captures one you already have.</p><p class="tutorial-callout">This is not required to carry on — the rest of the tour uses a plain Shell session. Skip it and come back to Settings → Accounts once you have picked a provider.</p></>},
    {id:'run',eyebrow:'START WORK',title:'Open the real Run menu.',selectors:['[data-tutorial="run"]','[data-tutorial="project-run"]'],action:{kind:'click',selectors:['[data-tutorial="run"]','[data-tutorial="project-run"]'],hint:'Click Run'},targetLabel:'CLICK RUN',body:<><p>Run is the Project-level launcher for Claude, Codex, shells, custom terminals, and trusted Project tasks.</p><p>Click it now to open the actual launcher.</p></>},
    {id:'run-choice',eyebrow:'RUN MENU',title:'Start a Shell session.',selectors:['[data-tutorial="run-choice-shell"]'],action:{kind:'event',gate:{action:'session-launched',backend:'shell'},hint:'Choose Shell and wait for it to start'},targetLabel:'SELECT SHELL',body:<><p>Select <strong>Shell</strong> for this walkthrough. It gives us a safe live tab to use while learning the workspace.</p><p>The tour advances after the session is successfully created.</p></>},
    {id:'workspace',eyebrow:'THE WORKSPACE',title:'Panes are regions. Tabs are views.',selectors:['[data-tutorial="workspace"]'],body:<><p>Each pane owns its own tab strip. A tab can be a terminal, note, file, History browser, or live development preview.</p><p>Closing a file, note, or preview tab closes only the view. Closing a live terminal asks for confirmation because it stops the process.</p></>},
    {id:'new-tab',eyebrow:'ADD A TAB',title:'Create a second terminal tab.',selectors:mobileAtStart?['[data-tutorial="run"]','[data-tutorial="project-run"]']:['[data-tutorial="pane-run"]','[data-tutorial="run"]','[data-tutorial="project-run"]'],action:{kind:'event',gate:{action:'session-launched',backend:'shell'},hint:'Open Run, choose Shell, and wait for the terminal to start'},targetLabel:'RUN → SHELL',body:<><p>{mobileAtStart?'Open Run again':'Use the + after the pane tabs'} and choose <strong>Shell</strong>. Every launcher goes through the same Run menu, and a new session joins the focused pane as a tab.</p><p>The new tab must finish starting before the tour continues.</p></>},
    ...(mobileAtStart?[{id:'tabs' as const,eyebrow:'MOBILE TABS',title:'Mobile keeps every view in one tab rail.',selectors:['[data-tutorial="tab-strip"]'],body:<><p>Swipe the unified tab rail to move between terminals and resources. Mobile preserves the desktop pane tree but intentionally omits drag-to-reorder and split geometry.</p><p>Open the same Project on desktop to rearrange tabs or create pane-edge splits.</p></>}]:[
      {id:'tabs' as const,eyebrow:'MOVE TABS',title:dragging?'Drop it in the highlighted tab bar.':'Drag the highlighted tab.',selectors:['[data-tutorial="tab-drag-source"]'],action:{kind:'event' as const,gate:{action:'tab-dropped' as const,zone:'tabs' as const},hint:dragging?'Release inside the highlighted tab bar':'Drag the highlighted tab'},dragTarget:{selectors:['[data-tutorial="tab-strip"]'],label:'DROP IN TAB BAR'},targetLabel:dragging?'DROP IN TAB BAR':'DRAG THIS TAB',body:<><p>{dragging?'The destination is now highlighted. Release inside the tab bar to complete a real tab move.':'Press and hold the highlighted tab, then begin dragging. The destination appears only after the drag starts, keeping the tabs clear and readable.'}</p><p>The insertion marker shows exactly where the tab will land.</p></>},
      {id:'splits' as const,eyebrow:'SPLIT PANES',title:dragging?'Drop it on the highlighted pane edge.':'Drag a tab to make a pane.',selectors:['[data-tutorial="tab-drag-source"]'],action:{kind:'event' as const,gate:{action:'tab-dropped' as const,zone:'split' as const},hint:dragging?'Release on the highlighted right edge':'Drag the highlighted tab'},dragTarget:{selectors:['[data-tutorial="workspace-pane"]'],edge:'right' as const,label:'DROP ON RIGHT EDGE'},targetLabel:dragging?'DROP ON RIGHT EDGE':'DRAG THIS TAB',body:<><p>{dragging?'Release on the bright right-edge zone. The workspace preview shows the pane that will be created.':'Start dragging the highlighted tab. Once the drag is active, the tutorial switches its focus to a clear right-edge drop zone.'}</p><p>The same technique works on the left, top, and bottom edges.</p></>},
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
    {id:'resources',eyebrow:'PROJECT RESOURCES',title:'Open Project notes.',selectors:['[data-tutorial="project-notes"]'],action:{kind:'click',selectors:['[data-tutorial="project-notes"]'],hint:'Open Notes'},targetLabel:'OPEN NOTES',body:<><p>Open <strong>Notes</strong> to create Project-owned working documents. Notes can stay in this panel or open as ordinary workspace tabs beside any terminal.</p><p>The file browser lives in the same panel.</p></>},
    // The tour used to end having never mentioned that most of the drawer's analysis
    // surfaces start switched off, so a new user met them one empty pane at a time and
    // read "off" as "broken". Explanatory rather than click-gated: the gates themselves
    // are the interaction, and they are where the operator will be when it matters.
    {id:'gates',eyebrow:'SWITCHED OFF ON PURPOSE',title:'Anything that costs something starts off.',body:<><p>swe-mux keeps the expensive and interruptive things off until you ask for them: reading transcripts, ranking what needs you, letting an agent act on its own. So a panel that has never been switched on says so, instead of looking empty.</p><p>Every one of those notices turns the thing on where you are standing — it names the scope, says where the change is written, and tells you whether it can cost money before you press it. You never have to go and find a settings page.</p></>},
    {id:'features',eyebrow:'FIND ANYTHING',title:'Open the main menu.',selectors:['[data-tutorial="menu"]'],action:{kind:'click',selectors:['[data-tutorial="menu"]'],hint:'Click menu'},targetLabel:'CLICK MENU',body:<><p>The main menu is the map to every surface that belongs to the whole app rather than to one session.</p><p>Open it now to see those real destinations.</p></>},
    // The copy names rows and groups that exist, and `TUTORIAL_CHROME_CLAIMS` in
    // `tutorial.ts` declares each of those names so `tourChrome.test.ts` can check it
    // against what `App.tsx` renders. This paragraph is why that mechanism exists: it
    // described a `Utilities` group for months after that group was unfolded into the
    // eight plain rows below, and nothing in the suite could tell.
    {id:'feature-menu',eyebrow:'MAIN FEATURES',title:'Everything else stays one click away.',selectors:['[data-tutorial="main-menu"]'],body:<><p>The viewers come first - <strong>Session history</strong>, <strong>Notes</strong>, <strong>Fleet queue</strong>, <strong>Prompt library</strong>, <strong>Clipboard history</strong>, <strong>Resources</strong>, <strong>Usage &amp; spend</strong>, and <strong>Notifications</strong>. Below them sit the things you configure: <strong>Projects</strong>, <strong>Plugins</strong>, <strong>Configure Actions</strong>, <strong>Automation Dashboard</strong>, and <strong>Settings</strong>, with the reload and rebuild commands folded into <strong>Maintenance</strong>. <strong>Help</strong> is the last row, and it can bring you back here.</p><p>Press <kbd>Ctrl Alt P</kbd> for the command palette. <kbd>Ctrl Alt T</kbd> opens a terminal; <kbd>Ctrl Alt ←/→</kbd> moves focus between panes.</p></>},
    // The tour ends by handing over to something that answers questions it did
    // not cover, which is most of them: two minutes cannot explain an install, and
    // the gear in the footer can. Anchored on the real control so the last thing
    // the tour does is point at where help lives afterwards.
    {id:'configurator',eyebrow:'ASK ABOUT SWE-MUX',title:'The gear starts an agent that knows this install.',selectors:['.configurator-trigger'],targetLabel:'THE CONFIGURATOR',body:<><p>Press it and swe-mux opens an agent session pointed at itself. It can read every setting with its real accepted values, the dependency graph behind those switched-off panels, and this machine's health report — and it will change a setting for you once you have agreed to a specific change.</p><p>It is the right place for "what does this do", "why is this panel empty", and "help me set up my phone".</p></>},
    // Ends by naming the surface that answers everything two minutes could not, rather
    // than only by naming the switch that replays these two minutes.
    {id:'ready',eyebrow:'TOUR COMPLETE',title:'You are ready to build.',body:<><p>Your Projects and layouts persist, and long-running sessions survive browser reloads or a hidden desktop window.</p><p><strong>Help</strong> - the last row of the menu, or “open help” in the command palette - explains every panel from the design document that defines it, and offers this walkthrough again. Settings → General has the same replay button.</p></>},
  ].filter(step=>!setupCompleted||step.id!=='accounts') as Step[],[dragging,initialHasProject,mobileAtStart,setupCompleted])
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

  return <div class={`tutorial-layer ${targetRect?'targeted':'centered'} ${dragging?'drag-active':''}`} role="dialog" aria-modal="true" aria-label="Getting started tutorial">
    {hole&&step.action&&!dragging&&<div class="tutorial-blockers" aria-hidden="true"><i style={{left:0,top:0,width:'100%',height:hole.top}}/><i style={{left:0,top:hole.top,width:hole.left,height:hole.bottom-hole.top}}/><i style={{left:hole.right,top:hole.top,width:innerWidth-hole.right,height:hole.bottom-hole.top}}/><i style={{left:0,top:hole.bottom,width:'100%',height:innerHeight-hole.bottom}}/></div>}
    {targetRect&&<div class="tutorial-spotlight" style={{left:targetRect.left-7,top:targetRect.top-7,width:targetRect.width+14,height:targetRect.height+14}} aria-hidden="true"/>}
    {targetRect&&targetLabel&&<div class="tutorial-target-label" style={{left:Math.max(8,Math.min(innerWidth-190,targetRect.left+targetRect.width/2-88)),top:labelTop}}>{targetLabel}</div>}
    <section ref={cardRef} class={`tutorial-card side-${position.side}`} style={{left:position.left,top:position.top}}>
      <header><span>{step.eyebrow}</span><button onClick={onExit} aria-label="Exit tutorial">Exit tour ×</button></header>
      <div class="tutorial-copy"><h2>{step.title}</h2>{step.body}</div>
      {/* An action step used to render its hint *instead of* a control, so the only way
          past one whose anchor was missing - or whose gate the user could not satisfy,
          which for `accounts` meant a real provider login - was to abandon the tour. A
          walk whose sole escape is abandonment is not a walk. The gate and its spotlight
          stay for the user who does want to do the thing now; skipping is the second
          answer, and it is a legitimate one. */}
      <footer><span>{index+1} / {steps.length}</span><div class="tutorial-progress" aria-hidden="true"><i style={{width:`${((index+1)/steps.length)*100}%`}}/></div>{step.action?<><em>{step.action.hint}</em><button class="tutorial-skip" onClick={()=>last?onComplete():setIndex(value=>value+1)}>{step.skipLabel||'Skip step'}</button></>:<button class="primary" onClick={()=>last?onComplete():setIndex(value=>value+1)}>{last?'Finish':'Next'}</button>}</footer>
    </section>
  </div>
}
