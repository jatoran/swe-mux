import { render } from 'preact'
import { useState } from 'preact/hooks'
import { PluginPopup } from '../../src/PluginPopup'
import '../../src/style.css'

function Harness() {
  const [docked,setDocked]=useState(false)
  if(docked)return <p role="status">Docked as a Project tab</p>
  return <PluginPopup title="Worktree Health" docking={false} onDock={()=>setDocked(true)} onClose={()=>undefined}>
    <section class="pane-stack singleton-stack plugin-popup-stack">
      <div class="stack-tabs-rail"><div class="stack-tabs"><button role="tab" aria-selected="true">Worktree Health</button></div></div>
      <div class="stack-active"><section class="terminal-pane plugin-utility-pane focused"><div class="terminal-surface">Healthy worktrees</div></section></div>
    </section>
  </PluginPopup>
}

render(<Harness/>,document.querySelector('#root')!)
