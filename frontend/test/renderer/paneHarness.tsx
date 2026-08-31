import { render } from 'preact'
import { useState } from 'preact/hooks'
import { MobileTerminalDraft } from '../../src/TerminalDraftComposer'
import { PaneRunTrigger } from '../../src/PaneRunTrigger'
import { OverflowRail } from '../../src/RailScroller'
import { SessionTopbar } from '../../src/SessionTopbar'
import { defaultSessionRowConfig } from '../../src/sessionRowConfig'
import { deriveRowFleetFacts } from '../../src/sessionRowFields'
import { addSessionTopbarRow, defaultSessionTopbarConfig, placeSessionTopbarItem } from '../../src/sessionTopbarConfig'
import type { Session } from '../../src/types'
import '../../src/style.css'

// The pane's own layout, with the real components and the real stylesheet. What it exists
// to pin is the geometry contract in `ui.md`: an intrinsic configured header and one flexible surface,
// and the surface owns every pixel of the second one. Two shipped regressions came from
// breaking that in CSS alone — a phantom track that left dead black space, then a missing
// `grid-column` that auto-placed the terminal into an implicit second column and halved
// it. Both resized the PTY under a live agent, and neither was visible to tsc or to the
// unit suite.
//
// Nothing voice-shaped is mounted here any more. The conversation surface became app-level
// chrome (`voice-dock-harness.html`, `voiceDockHarness.tsx`), and the read-aloud player
// strip — which used to float from a zero-height anchor in this pane — is gone entirely:
// read aloud is operated from the voice dock's `tts` tab, and which sessions speak is
// marked on the sidebar row and the tab strip. The remaining float is the mobile Draft
// composer, which is what the overlay assertions now cover.

const parameters = new URLSearchParams(location.search)
const mobile = parameters.get('mobile') === '1'
const draft = parameters.get('draft') === '1'
// A faulted pane is the rare rendering, so it is opt-in here: the marker must survive beside
// a name too long for the bar, which is the case where a fixed-size glyph is easiest to lose.
const fault = parameters.get('fault') === '1'
const tabs = parameters.get('tabs') === '1'
const topbarRows = Math.max(1,Math.min(3,Number(parameters.get('rows')||1)))

const session={
  id:'pane-harness',project_id:'p1',name:'claude-1ee230 · refactor the scrollback ring so it keeps bracketed paste mode across replay',
  backend:'claude',state:'working',state_since:Date.now()/1000-300,created_at:Date.now()/1000-3600,
  model:'claude-sonnet-5',cwd:'D:/PROJECTS/swe-mux',spawn_cwd:'D:/PROJECTS/swe-mux',runtime_cwd:'D:/PROJECTS/swe-mux',
  git:{branch:'ui-settings',dirty:3,ahead:1,behind:0},provider_account_hashes:{},
} as unknown as Session
let topbarConfig=defaultSessionTopbarConfig()
for(let index=1;index<topbarRows;index++)topbarConfig=addSessionTopbarRow(topbarConfig)
if(topbarRows>1)topbarConfig=placeSessionTopbarItem(topbarConfig,{kind:'metric',id:'model',mode:'always'},1,'left')
if(topbarRows>2)topbarConfig=placeSessionTopbarItem(topbarConfig,{kind:'metric',id:'branch',mode:'always'},2,'left')
const topbarFacts=deriveRowFleetFacts([session],{[session.id]:0})

const pane = <section class="terminal-pane focused">
  <SessionTopbar preview session={session} config={topbarConfig} rowConfig={defaultSessionRowConfig()} facts={topbarFacts}
    title={<div class="pane-identity"><span class="pane-title">{session.name}</span>{fault&&<span class="pane-fault" role="img" aria-label="Session fault: observation stale">⚠</span>}</div>}
    renderAction={id=>id==='approvals'?<div class="approval-chip-wrap"><button class="pane-tool-label approval-chip">appr:wait</button></div>:<button class={`pane-tool-label ${id.slice('drawer:'.length)}-chip`}>{id.slice('drawer:'.length)}</button>}
    menu={<button aria-label="More actions">⋯</button>}/>
  <div class="terminal-surface">
    <div class="terminal-host" />
    {draft&&<MobileTerminalDraft sessionName="harness" text="A persistent message that has not reached the terminal." busy={false} error="" onInput={()=>{}} onInsert={()=>{}} onClear={()=>{}} onClose={()=>{}}/>}
    <div class="terminal-action-rail"><div class="terminal-action-rows" /></div>
  </div>
</section>

function TabRailHarness() {
  const [expanded,setExpanded]=useState(false)
  return <section class="pane-stack focused-pane">
    <OverflowRail className="stack-tabs" wrapperClassName="stack-tabs-rail" activeKey="one" stripProps={{role:'tablist','aria-label':'Workspace tabs'}}>
      <div class="stack-tab-shell" style="order:0"><button role="tab" aria-selected="true" class="tab-main active">first tab</button></div>
      <div class="stack-tab-shell" style="order:1"><button role="tab" aria-selected="false" class="tab-main">second tab</button></div>
      <PaneRunTrigger projectName="swe-mux" mobile={mobile} expanded={expanded} order={2} onOpen={()=>setExpanded(value=>!value)}/>
    </OverflowRail>
    <div class="stack-active">{pane}</div>
  </section>
}

const root = document.querySelector('#root')!
root.setAttribute('style', 'width:100%;height:100dvh;display:grid;grid-template-rows:minmax(0,1fr);margin:0')
document.body.setAttribute('style', 'margin:0')
document.documentElement.style.setProperty('--ui-scale', '1')
render(
  <>{tabs
    ? <TabRailHarness/>
    : mobile
      ? <div class="mobile-unified-active">{pane}</div>
      : <div class="pane-grid count-1">{pane}</div>}
  </>,
  root,
)
