// The note editor as the utility drawer actually hosts it: mounted inside a
// `.drawer-note-host` that is `hidden` until its tab is selected.
//
// Continuity positions its inline-code copy affordances off `span.offsetParent`, which is
// null for every node inside a `display:none` subtree, so an editor whose first render lands
// while hidden throws out of the SDK's async start and rejects its `ready` promise. Nothing
// short of a real browser reproduces that: it needs real layout, a real shadow tree, and the
// real WASM engine. The note text below therefore carries inline code on purpose.
import { render } from 'preact'
import { useState } from 'preact/hooks'
import { ContinuityMarkdownEditor } from '../../src/ProjectNoteEditor'
import '../../src/style.css'

const NOTE = [
  '# Hidden mount',
  '',
  'A paragraph with `inline code` in it, which is what makes the affordance pass run.',
  '',
  'Another `span` and one more `token` so several buttons are positioned.',
].join('\n')

// Every failure the SDK can raise out of a hidden first render: the rejected `ready` promise
// arrives as an unhandled rejection, a throw inside a scheduled render as an error event.
const failures: string[] = []
;(window as unknown as { harnessFailures: string[] }).harnessFailures = failures
window.addEventListener('unhandledrejection', event => {
  const reason = event.reason
  const cause = reason instanceof Error && reason.cause ? ` (cause: ${String(reason.cause)})` : ''
  failures.push(`unhandledrejection: ${reason instanceof Error ? reason.message : String(reason)}${cause}`)
})
window.addEventListener('error', event => failures.push(`error: ${event.message}`))

function Harness() {
  const [visible, setVisible] = useState(new URLSearchParams(location.search).get('visible') === '1')
  return <div class="utility-drawer" style="width:420px;height:520px;display:flex;flex-direction:column">
    <button id="reveal" onClick={() => setVisible(true)}>reveal</button>
    <div class="drawer-note-host" hidden={!visible}>
      <section class="project-resource file-editor">
        <header><div class="autosave-resource-heading"><strong>Hidden mount</strong></div></header>
        <ContinuityMarkdownEditor initialText={NOTE} label="Hidden mount" onCommit={() => {}} />
      </section>
    </div>
  </div>
}

render(<Harness />, document.querySelector('#root')!)
