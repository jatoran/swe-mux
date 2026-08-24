// The two lazily-loaded surfaces, mounted on demand over a stubbed daemon.
//
// What this exists to prove cannot be seen from source text: that neither CodeMirror nor
// Sigma/Graphology is requested until something mounts the surface that needs it, that the
// stand-in shown while the chunk is in flight occupies the box the real thing will (so the
// layout does not jump on a fast load), and that a grammar arrives after the document is
// already readable rather than gating the first paint on it.
//
// Vite serves each module as its own request in dev, so `performance.getEntriesByType`
// answers the "was it fetched" question directly, against the real module graph rather
// than against a build artefact this suite has no way to produce.
import { render } from 'preact'
import { useState } from 'preact/hooks'
import { LazyCodeEditor } from '../../src/LazyCodeEditor'
import { LazyChangeMap } from '../../src/LazyChangeMap'
import '../../src/style.css'

// Nothing here should reach the daemon; the Change Map is mounted with no session, which
// is the branch that renders before it touches WebGL. Anything that does ask gets an
// empty object rather than a rejection, so a stray fetch cannot look like a load failure.
window.fetch = (async () =>
  new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.height = '100%'
document.documentElement.style.setProperty('--ui-scale', '1')

const SOURCE = 'const answer = 42\nexport function main() { return answer }\n'
const RELOADED = 'const answer = 7\nexport function main() { return answer }\n'

declare global {
  interface Window {
    /** The last text the editor emitted, for asserting that typing still round-trips. */
    editorValue: string
  }
}

function Host() {
  const [editor, setEditor] = useState(false)
  const [map, setMap] = useState(false)
  const [value, setValue] = useState(SOURCE)
  // Both surfaces size themselves from a parent that has a height: `.project-resource` is
  // `height:100%` and `.change-map-pane` is `height:100%;flex:1`. Mounted in a page that
  // gives them none, CodeMirror ends up measuring inside a box with no room and thrashes
  // its measure loop on every re-render — which reads as an editor that stops accepting
  // keystrokes and has nothing to do with the code under test. The workspace gives them a
  // real box; so does this.
  return <div id="host" style="height:100dvh;display:grid;grid-template-rows:auto minmax(0,1fr) minmax(0,1fr)">
    <div id="controls">
      <button id="mount-editor" onClick={() => setEditor(true)}>mount editor</button>
      <button id="mount-map" onClick={() => setMap(true)}>mount map</button>
      {/* An external rewrite — reload from disk, or a conflict overwrite. This is the one
          path the last-emitted-string shortcut could plausibly break, so it has a button. */}
      <button id="external-change" onClick={() => setValue(RELOADED)}>external change</button>
    </div>
    <section id="editor-slot" class="project-resource file-editor">
      {editor && <LazyCodeEditor
        value={value}
        filename="demo.ts"
        onChange={next => { window.editorValue = next; setValue(next) }}
        ariaLabel="demo.ts"
      />}
    </section>
    <section id="map-slot" style="min-height:0;display:flex">
      {map && <LazyChangeMap session={null} onOpenFile={() => {}} />}
    </section>
  </div>
}

render(<Host />, document.querySelector('#root')!)
