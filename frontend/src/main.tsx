import { render } from 'preact'
import { initialize } from '@continuity-editor/editor'
import wasmUrl from '@continuity-editor/editor/wasm?url'
import { App } from './App'
import { reportContinuityFailure } from './continuityStatus'
import { installClipboardCapture } from './clipboardHistory'
import './style.css'

// Before the app (and before the note editor's WASM engine) can take a reference
// to `navigator.clipboard.writeText`: the capture hooks wrap that method, so they
// have to be in place ahead of the first copy, not on first render.
installClipboardCapture()

// Start the Continuity WebAssembly engine once, from the SPA origin. The note
// editor waits on this same shared promise internally; passing the bundler-
// emitted asset URL guarantees the .wasm is served (and compiled) locally.
export const continuityInitialization = initialize({ wasm: wasmUrl })
continuityInitialization.catch((error: unknown) => {
  reportContinuityFailure(error instanceof Error ? error.message : String(error))
})

render(<App />, document.getElementById('app')!)
