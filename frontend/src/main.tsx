import { render } from 'preact'
import { initialize } from '@continuity-editor/editor'
import wasmUrl from '@continuity-editor/editor/wasm?url'
import { App } from './App'
import { reportContinuityFailure } from './continuityStatus'
import './style.css'

// Start the Continuity WebAssembly engine once, from the SPA origin. The note
// editor waits on this same shared promise internally; passing the bundler-
// emitted asset URL guarantees the .wasm is served (and compiled) locally.
export const continuityInitialization = initialize({ wasm: wasmUrl })
continuityInitialization.catch((error: unknown) => {
  reportContinuityFailure(error instanceof Error ? error.message : String(error))
})

render(<App />, document.getElementById('app')!)
