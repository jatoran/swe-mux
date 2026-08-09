import { render } from 'preact'
import { DictationPanel, type Conversation } from '../../src/ConversationControl'
import { VoicePlayer } from '../../src/VoicePlayer'
import type { Session, VoiceStatus } from '../../src/types'
import '../../src/style.css'

// The pane's own layout, with the real components and the real stylesheet. What it exists
// to pin is the geometry contract in `ui.md`: a pane is two rows, and the voice surfaces
// float rather than taking one. Two shipped regressions came from breaking that in CSS
// alone — a phantom track that left dead black space, then a missing `grid-column` that
// auto-placed the terminal into an implicit second column and halved it. Both resized the
// PTY under a live agent, and neither was visible to tsc or to the unit suite.

const parameters = new URLSearchParams(location.search)
const overlay = parameters.get('overlay') !== '0'
const mobile = parameters.get('mobile') === '1'

const session = { id: 'pane-harness', name: 'harness', backend: 'claude', state: 'running', cwd: 'D:\\PROJECTS\\swe-mux' } as Session
const status = {
  enabled: true, stt_enabled: true, stt_available: true, engine: 'edge', content: 'summary',
  default_mode: 'auto', wake_words: ['mux'], commands: {},
} as unknown as VoiceStatus

// Enough of the controller for the panel to render every state it draws; the panel is
// presentational, so no capture is started here.
const conversation: Conversation = {
  sessionId: session.id, phase: 'listening', active: true, standby: false, wake: 'mux',
  detail: 'Listening. Say “mux, send” to submit.', landedAt: 0,
  draft: 'refactor the scrollback ring so it keeps bracketed paste mode across replay',
  toggle: () => {}, stop: () => {}, send: () => {}, undo: () => {}, clear: () => {},
  toggleStandby: () => {}, edit: () => {},
}

// The strip lists clips on mount. The harness has no daemon; VoicePlayer already treats a
// failed load as "no clips", which is the state the layout has to hold anyway.
globalThis.fetch = async () => new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })

const pane = <section class="terminal-pane focused">
  <div class="pane-bar agent-pane-bar">
    <div><span class="pane-state running">running</span></div>
    <div class="pane-voice">
      <button class="voice-chip auto">tts:auto</button>
      <button class="conversation-chip listening">talk:on</button>
    </div>
    <div class="pane-tools"><button>⋯</button></div>
  </div>
  {overlay && <div class="voice-overlay-anchor"><div class="voice-overlay">
    <VoicePlayer session={session} status={status} mode="auto" onSession={() => {}} onOpenSettings={() => {}} />
    <DictationPanel conversation={conversation} onOpenSettings={() => {}} />
  </div></div>}
  <div class="terminal-surface">
    <div class="terminal-host" />
    <div class="terminal-action-rail"><div class="terminal-action-rows" /></div>
  </div>
</section>

const root = document.querySelector('#root')!
root.setAttribute('style', 'width:100%;height:100dvh;display:grid;grid-template-rows:minmax(0,1fr);margin:0')
document.body.setAttribute('style', 'margin:0')
document.documentElement.style.setProperty('--ui-scale', '1')
render(
  mobile
    ? <div class="mobile-unified-active">{pane}</div>
    : <div class="pane-grid count-1">{pane}</div>,
  root,
)
