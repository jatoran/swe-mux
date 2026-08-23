import { render } from 'preact'
import { VoiceControl, VoiceDock, type Conversation } from '../../src/ConversationControl'
import type { Command } from '../../src/commands'
import type { VoiceDockState } from '../../src/voiceDock'
import '../../src/style.css'

/**
 * The voice dock in a real workspace grid, with the real stylesheet.
 *
 * What it exists to pin is the geometry contract the dock inherited from the pane's
 * read-aloud strip (`design/features/ui.md`): the surface floats from a zero-height anchor
 * in the main stage's own grid cell, so a pane's row count - and therefore a live agent's
 * PTY size - is identical at every dock state. It also pins the part of the collapse
 * contract a stylesheet can break on its own: at the chip the workspace is completely
 * clear, and at the peek there is no composer but every open confirmation card is still
 * there with its buttons.
 *
 * Query parameters: `dock` (chip|peek|full), `mode` (talk|chat|read), `talk=0` to render
 * with capture stopped, `card=1` to draw an open confirmation card in the assistant body,
 * and `mobile=1` for the phone toolbar, where the voice control shares a row with nav,
 * quota, the Project name, Run, and the drawer toggle.
 */

const parameters = new URLSearchParams(location.search)
const dock = (parameters.get('dock') || 'full') as VoiceDockState
const modeParameter = parameters.get('mode')
const mode = modeParameter === 'talk' ? 'dictation' : modeParameter === 'read' ? 'read' : 'chat'
const talkActive = parameters.get('talk') !== '0'
const card = parameters.get('card') === '1'
const mobile = parameters.get('mobile') === '1'

const commands: Command[] = []

const conversation: Conversation = {
  target: {kind:'session',id:'dock-harness',label:'Agent · harness',available:()=>true,agentRunId:()=>null,voiceMode:()=>null,voiceContent:()=>null},
  targetAvailable: true, pinned: false, phase: talkActive ? 'listening' : 'off', active: talkActive,
  standby: false, hold: false, holdBuffer: '', deferredTrigger: '', deferredSource: null,
  comms: false, wake: 'mux',
  detail: talkActive ? 'Listening. Say “mux, send” to submit.' : '',
  landedAt: 0, latency: null, detector: 'silero',
  history: [
    {id:'1',role:'you',text:'Mux, list active sessions.',at:1},
    {id:'2',role:'mux',text:'2 matching sessions\n\nSession 1 - Alpha\nStatus: working',at:2},
  ],
  draft: 'refactor the scrollback ring so it keeps bracketed paste mode across replay',
  toggle: () => {}, togglePin: () => {}, stop: () => {}, send: () => {}, append: () => {},
  undo: () => {}, clear: () => {}, clearHistory: () => {}, toggleStandby: () => {},
  toggleComms: () => {}, releaseHold: () => {}, discardHold: () => {}, edit: () => {},
}

// The assistant body is presentational here: the real one owns a daemon dialog, and this
// harness has no daemon. The shapes that matter to layout are the peek line and the card.
const assistantView = dock === 'peek'
  ? <div class="assistant-panel peek">
    <p class="assistant-peek-line"><b>mux</b><span>Two sessions are working; alpha has been on the same turn for eleven minutes.</span></p>
    {card && <aside class="assistant-action reversible">
      <p><strong>about to</strong> queue a message to alpha<span class="assistant-countdown"> · 4s</span></p>
      <div><button class="confirm">run now</button><button class="cancel">cancel</button></div>
    </aside>}
  </div>
  : <div class="assistant-panel">
    <div class="assistant-log" role="log">
      <article class="assistant-message user"><header>you</header><p>What is the fleet doing?</p></article>
      <article class="assistant-message assistant"><header>mux</header><p>Two sessions are working; alpha has been on the same turn for eleven minutes.</p></article>
      {card && <aside class="assistant-action reversible">
        <p><strong>about to</strong> queue a message to alpha<span class="assistant-countdown"> · 4s</span></p>
        <div><button class="confirm">run now</button><button class="cancel">cancel</button></div>
      </aside>}
    </div>
    <footer class="assistant-input-row">
      <textarea class="assistant-input" rows={1} placeholder="Message the assistant…" />
      <button class="assistant-send">send</button>
      <button class="assistant-new">new</button>
    </footer>
  </div>

const pane = <section class="terminal-pane focused">
  <div class="pane-bar agent-pane-bar">
    <div class="pane-identity"><span class="pane-title">claude-1ee230</span></div>
    <div class="pane-tools"><button>⋯</button></div>
  </div>
  <div class="terminal-surface">
    <div class="terminal-host" />
    <div class="terminal-action-rail"><div class="terminal-action-rows" /></div>
  </div>
</section>

const root = document.querySelector('#root')!
// `.app-shell` is the flex column `.workspace` sizes itself against (`flex:1 1 0`);
// without it the workspace lands in an implicit grid row and collapses to its content,
// which reads as "the terminal surface is not visible" rather than as a broken harness.
root.setAttribute('style', 'width:100%;height:100dvh;margin:0')
document.body.setAttribute('style', 'margin:0')
document.documentElement.style.setProperty('--ui-scale', '1')
// One control, both jobs: click opens the panel, ctrl+click or a hold starts capture.
// It also carries what is waiting behind a collapsed panel, being the only way back to it.
const control = <VoiceControl
  conversation={conversation}
  configured={true}
  dock={dock}
  pendingActions={card ? 1 : 0}
  unseen={false}
  onToggleDock={() => {}}
/>

// A presentational stand-in, like the assistant body: the real panel fetches clips from a
// daemon this harness does not have. The shapes that matter to layout are the control row,
// the transport, and a clip list long enough to scroll.
//
// This is read aloud's only control surface now - the per-pane player strip is retired -
// so the row it grew (`↻ speak`) and the transport it absorbed are mirrored here.
const readView = <div class="voice-read">
  <div class="voice-read-controls">
    <div class="voice-read-session"><span>session</span><strong>claude-1ee230</strong>
      <button class="voice-read-mode auto">tts:auto</button>
      <button class="voice-read-content verbatim">verbatim</button>
      <button class="voice-read-speak">↻ speak</button>
    </div>
    <button class="voice-read-autoplay active">🔊 this device</button>
    <button class="voice-read-held">▶ 2 held</button>
    <a class="voice-read-master" href="#">master: on</a>
    <button class="dictation-settings">⚙</button>
  </div>
  {/* The transport, drawn because this device has a clip loaded. It is the half of the
      retired pane strip that had no other home, and the reason the panel is a flex column
      rather than a two-row grid: with a third conditional child the fixed template gave the
      flexible row to whichever child happened to be second. */}
  <div class="voice-read-now" role="group" aria-label="Playback">
    <button class="voice-read-now-play">⏸</button>
    <input class="voice-read-seek" type="range" min="0" max="41" step="0.1" value="12" aria-label="Seek within the clip" />
    <span class="voice-read-now-time">0:12/0:41</span>
    <span class="voice-read-now-text">The scrollback ring keeps bracketed paste across replay now.</span>
  </div>
  <div class="voice-read-clips" role="list">
    {[
      { id: 'a', kind: 'summary', state: 'held', text: 'The scrollback ring keeps bracketed paste across replay now.' },
      { id: 'b', kind: 'verbatim', state: 'played', text: 'Landed on master and the gate is green.' },
      { id: 'c', kind: 'summary', state: 'synthesizing', text: 'Working through the migration.' },
    ].map(clip => <div key={clip.id} role="listitem" class={`voice-read-clip ${clip.state}`}>
      <button class="voice-read-play">▶</button>
      <span class="voice-read-kind">{clip.kind}</span>
      <span class="voice-read-when">10:41</span>
      <span class="voice-read-text">{clip.text}</span>
      <span class={`voice-read-state ${clip.state}`}>{clip.state}</span>
    </div>)}
  </div>
</div>

// The phone bar in full, because the risk the chip introduces here is width: it is a
// seventh control on a row that must not wrap, beside nav, quota, the Project name, the
// microphone, Run, and the drawer toggle.
const mobileToolbar = <div class="mobile-toolbar">
  <button class="nav-toggle mobile-nav-toggle">≡</button>
  <button class="mobile-project-name">swe-mux</button>
  {control}
  <button class="mobile-run-trigger">▶ Run</button>
  <button class="mobile-drawer-toggle">▤</button>
</div>

render(
  <div class="app-shell" style={{height: '100dvh'}}>{mobile ? mobileToolbar : null}<div class="workspace" style={{'--sidebar-width': '254px', '--utility-rail-width': '40px'}}>
    <header class="app-topbar">
      <div class="app-identity">
        <button class="sidebar-collapse">«</button>
        <strong class="desktop-project-name">swe-mux</strong>
        {control}
      </div>
    </header>
    <aside class="sidebar" />
    <div class="sidebar-resizer" />
    <main class="main-stage">
      <div class="project-workspace unified-workspace">
        <div class="terminal-workspace">
          {/* A bare pane under the tree, not a `.pane-stack`: a stack's first grid row is
              its tab strip, and a harness that omits the strip drops the pane into that
              31px track and reports a terminal with no height. */}
          <div class="pane-tree">{pane}</div>
        </div>
      </div>
    </main>
    <div class="voice-dock-anchor">
      <VoiceDock
        conversation={conversation}
        commands={commands}
        configuredCommands={[{action:'send',phrases:['mux send']},{action:'append',phrases:['mux append']}]}
        onOpenSettings={() => {}}
        mode={mode}
        onMode={() => {}}
        assistantView={assistantView}
        readView={readView}
        captureConfigured={true}
        dock={dock}
        onDock={() => {}}
      />
    </div>
  </div></div>,
  root,
)
