import { render } from 'preact'
import { VoicePlayer } from '../../src/VoicePlayer'
import { MobileTerminalDraft } from '../../src/TerminalDraftComposer'
import type { Command } from '../../src/commands'
import type { Session, VoiceStatus } from '../../src/types'
import '../../src/style.css'

// The pane's own layout, with the real components and the real stylesheet. What it exists
// to pin is the geometry contract in `ui.md`: a pane is two rows, and the read-aloud strip
// floats rather than taking one. Two shipped regressions came from breaking that in CSS
// alone — a phantom track that left dead black space, then a missing `grid-column` that
// auto-placed the terminal into an implicit second column and halved it. Both resized the
// PTY under a live agent, and neither was visible to tsc or to the unit suite.
//
// The conversation surface used to float from this same anchor. It is app-level chrome
// now (`voice-dock-harness.html`, `voiceDockHarness.tsx`) and no longer touches a pane.

const parameters = new URLSearchParams(location.search)
const overlay = parameters.get('overlay') !== '0'
const mobile = parameters.get('mobile') === '1'
const draft = parameters.get('draft') === '1'
// A faulted pane is the rare rendering, so it is opt-in here: the marker must survive beside
// a name too long for the bar, which is the case where a fixed-size glyph is easiest to lose.
const fault = parameters.get('fault') === '1'

const session = { id: 'pane-harness', name: 'harness', backend: 'claude', state: 'running', cwd: 'D:\\PROJECTS\\swe-mux' } as Session
// `commands` is the configured capture-action list (`{action,phrases}[]`), the shape the
// voice-command catalog reads. Populated so the catalog draws its configured section; an
// empty object here silently crashed the whole overlay render, and `test/` is outside
// `tsconfig.json`'s `include`, so tsc never caught it.
const status = {
  enabled: true, stt_enabled: true, stt_available: true, engine: 'sapi', content: 'summary',
  default_mode: 'auto', wake_words: ['mux'],
  commands: [
    { action: 'send', phrases: ['mux send'] },
    { action: 'append', phrases: ['mux append'] },
  ],
} as unknown as VoiceStatus

// The live command registry the catalog groups. Empty is a valid presentational fixture:
// the panel and the voice-command dialog render their fixed grammar (including
// "append without sending") with no registry commands, which is what these tests pin.
const commands: Command[] = []

// The strip lists clips on mount. The harness has no daemon; VoicePlayer already treats a
// failed load as "no clips", which is the state the layout has to hold anyway.
globalThis.fetch = async () => new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })

const pane = <section class="terminal-pane focused">
  <div class="pane-bar agent-pane-bar">
    {/* Deliberately longer than any pane is wide: the header's contract is that a generated
        title ellipsizes rather than taking width from the voice chips or the pane tools. */}
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
  {overlay && <div class="voice-overlay-anchor"><div class="voice-overlay">
    <VoicePlayer session={session} status={status} mode="auto" commands={commands} onSession={() => {}} onOpenSettings={() => {}} />
  </div></div>}
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
