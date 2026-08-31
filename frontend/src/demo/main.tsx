/**
 * Demo entry: install the fake daemon (fetch + WebSocket shims) BEFORE the app
 * modules load, then boot the real, unmodified App against it.
 *
 * The import of `../App` is dynamic on purpose — a static import would be
 * hoisted above the shim installation and the first fetches would race the
 * fake backend. Everything the visitor does here is simulated in-page; the
 * demo build ships to the static marketing site and talks to nothing.
 */
import { render } from 'preact'
import { installFakeFetch } from './fakeApi.ts'
import { installFakeWebSocket } from './fakeSocket.ts'
import '../style.css'

installFakeFetch()
installFakeWebSocket()

// Device-local presentation defaults, seeded only when the visitor has none:
// the drawer shows the demo's key tabs (Notes, Git, Alerts) rather than all
// eleven, and the first-run tour stays out of the way of the embed.
const seedLocal = (key: string, value: string): void => {
  if (localStorage.getItem(key) === null) localStorage.setItem(key, value)
}
seedLocal('mux.drawer.hidden.v1', JSON.stringify([
  'queue', 'transcript', 'activity', 'agent', 'schedule', 'processes', 'actions', 'files',
]))
// The guided tour stays reachable (Help menu → "Take the guided tour") but must
// not auto-open over the embed.
seedLocal('mux.tutorial.v1', '1')

async function boot(): Promise<void> {
  const [{ App }, { initialize }, wasm] = await Promise.all([
    import('../App'),
    import('@continuity-editor/editor'),
    import('@continuity-editor/editor/wasm?url'),
  ])
  const { reportContinuityFailure } = await import('../continuityStatus')
  initialize({ wasm: wasm.default }).catch((error: unknown) => {
    reportContinuityFailure(error instanceof Error ? error.message : String(error))
  })
  render(<App />, document.getElementById('app')!)
}

void boot()
