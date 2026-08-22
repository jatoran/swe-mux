import { render } from 'preact'
import { MobileTerminalDraft } from '../../src/TerminalDraftComposer'
import '../../src/style.css'

// The pane's own layout, with the real components and the real stylesheet. What it exists
// to pin is the geometry contract in `ui.md`: a pane is two rows — header and surface —
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

const pane = <section class="terminal-pane focused">
  <div class="pane-bar agent-pane-bar">
    {/* Deliberately longer than any pane is wide: the header's contract is that a generated
        title ellipsizes rather than taking width from the pane tools. */}
    <div class="pane-identity"><span class="pane-title">claude-1ee230 · refactor the scrollback ring so it keeps bracketed paste mode across replay</span>{fault && <span class="pane-fault" role="img" aria-label="Session fault: observation stale">⚠</span>}</div>
    {/* The real bar's right-aligned group, in its shipped order: the standing `appr:` mode
        first, then the two chips that open a surface, then the overflow menu. Mirrored as
        plain markup rather than mounting `ApprovalChip` because the geometry contract is
        what this harness pins, and the real chip would put a fetch behind every load. */}
    <div class="pane-tools">
      <div class="approval-chip-wrap"><button class="pane-tool-label approval-chip">appr:wait</button></div>
      <button class="pane-tool-label queue-chip">queue</button>
      <button class="pane-tool-label transcript-chip">transcript</button>
      <button>⋯</button>
    </div>
  </div>
  <div class="terminal-surface">
    <div class="terminal-host" />
    {draft&&<MobileTerminalDraft sessionName="harness" text="A persistent message that has not reached the terminal." busy={false} error="" onInput={()=>{}} onInsert={()=>{}} onClear={()=>{}} onClose={()=>{}}/>}
    <div class="terminal-action-rail"><div class="terminal-action-rows" /></div>
  </div>
</section>

const root = document.querySelector('#root')!
root.setAttribute('style', 'width:100%;height:100dvh;display:grid;grid-template-rows:minmax(0,1fr);margin:0')
document.body.setAttribute('style', 'margin:0')
document.documentElement.style.setProperty('--ui-scale', '1')
render(
  <>{mobile
    ? <div class="mobile-unified-active">{pane}</div>
    : <div class="pane-grid count-1">{pane}</div>}
  </>,
  root,
)
