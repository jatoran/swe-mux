import { render } from 'preact'
import { ConversationToggle, VoiceDock, VoiceDockChip, type Conversation } from '../../src/ConversationControl'
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
 * Query parameters: `dock` (chip|peek|full), `mode` (talk|chat), `talk=0` to render with
 * capture stopped, `card=1` to draw an open confirmation card in the assistant body, and
 * `mobile=1` for the phone toolbar, where the chip is a seventh control on a bar that has
 * to stay one row.
 */

const parameters = new URLSearchParams(location.search)
const dock = (parameters.get('dock') || 'full') as VoiceDockState
const mode = parameters.get('mode') === 'talk' ? 'dictation' : 'chat'
const talkActive = parameters.get('talk') !== '0'
const card = parameters.get('card') === '1'
const mobile = parameters.get('mobile') === '1'

const commands: Command[] = []

const conversation: Conversation = {
  target: {kind:'session',id:'dock-harness',label:'Agent · harness',available:()=>true,agentRunId:()=>null,voiceMode:()=>null,voiceContent:()=>null},
  targetAvailable: true, pinned: false, phase: talkActive ? 'listening' : 'off', active: talkActive,
  standby: false, hold: false, holdBuffer: '', comms: false, wake: 'mux',
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
    <div class="pane-voice"><button class="voice-chip auto">tts:auto</button></div>
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
const chip = <VoiceDockChip state={dock} talkActive={talkActive} pendingActions={card ? 1 : 0} unseen={false} onToggle={() => {}} />

// The phone bar in full, because the risk the chip introduces here is width: it is a
// seventh control on a row that must not wrap, beside nav, quota, the Project name, the
// microphone, Run, and the drawer toggle.
const mobileToolbar = <div class="mobile-toolbar">
  <button class="nav-toggle mobile-nav-toggle">≡</button>
  <button class="mobile-project-name">swe-mux</button>
  <ConversationToggle conversation={conversation} configured={true} />
  {chip}
  <button class="mobile-run-trigger">▶ Run</button>
  <button class="mobile-drawer-toggle">▤</button>
</div>

render(
  <div class="app-shell" style={{height: '100dvh'}}>{mobile ? mobileToolbar : null}<div class="workspace" style={{'--sidebar-width': '254px', '--utility-rail-width': '40px'}}>
    <header class="app-topbar">
      <div class="app-identity">
        <button class="sidebar-collapse">«</button>
        <strong class="desktop-project-name">swe-mux</strong>
        <ConversationToggle conversation={conversation} configured={true} />
        {chip}
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
        dock={dock}
        onDock={() => {}}
      />
    </div>
  </div></div>,
  root,
)
