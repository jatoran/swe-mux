/**
 * Demo entry: install the fake daemon (fetch + WebSocket shims) BEFORE the app
 * modules load, then boot the real, unmodified App against it.
 *
 * The import of `../App` is dynamic on purpose - a static import would be
 * hoisted above the shim installation and the first fetches would race the
 * fake backend. Everything the visitor does here is simulated in-page; the
 * demo build ships to the static marketing site and talks to nothing.
 */
import { Component, render, type ComponentChildren } from 'preact'
import { installFakeFetch } from './fakeApi.ts'
import { installFakeWebSocket } from './fakeSocket.ts'
import { installViewMirror } from './mirror.ts'
import { DemoCoach } from './DemoCoach.tsx'
import '../style.css'
import './demoCoach.css'

installFakeFetch()
installFakeWebSocket()
// The fleet already mirrors across frames through the demo store; this mirrors what the
// store cannot see - which modal is open, which panel and tab, which session is focused
// - so the desktop and phone shown side by side behave as one app rather than two.
installViewMirror()

/**
 * Device-local presentation defaults for the embed.
 *
 * Versioned by a demo-owned marker rather than by the presence of each app key,
 * because a returning visitor already has those keys from an older demo and would
 * never see a tab this build added. Bump `SEED_VERSION` when the seed changes; the
 * visitor's own later edits within one version are still theirs to keep.
 */
const SEED_KEY = 'swemux-demo-seed'
const SEED_VERSION = '2'

function seedPresentation(): void {
  try {
    if (localStorage.getItem(SEED_KEY) === SEED_VERSION) return
    localStorage.setItem(SEED_KEY, SEED_VERSION)
    // The drawer shows the tabs this demo actually populates rather than all eleven.
    localStorage.setItem('mux.drawer.hidden.v1', JSON.stringify([
      'queue', 'agent', 'schedule', 'actions', 'files',
    ]))
    // An empty presentation map rather than no map at all. The app treats a missing
    // `mux.drawer.projects.v3` as "a legacy layout still needs migrating" and rewrites
    // the presentation once its config lands - which, on a fresh visitor, arrived just
    // after the first click on a drawer tab and put the panel back on Notes. Writing
    // the key retires the migration without pinning any Project to a tab.
    localStorage.setItem('mux.drawer.projects.v3', '{}')
    // A layout with a *stable* stack id. Without a stored one the app mints a fresh
    // random id on boot, and the presentation written by the first tab click is keyed
    // to an id nothing else recognises - so the drawer opened on Notes however many
    // times a visitor pressed Git. Writing it once makes the first click behave like
    // the second.
    localStorage.setItem('mux.drawer.layout.v1', JSON.stringify({
      version: 1,
      root: {
        type: 'stack',
        id: 'drawer-stack-demo',
        tabs: [
          'notes', 'transcript', 'activity', 'git', 'processes', 'notifications',
          'queue', 'agent', 'schedule', 'actions', 'files',
        ],
      },
    }))
    // Claim each Project's Notes selection up front. The Notes body is the one drawer
    // pane kept mounted across tab switches, and on a device with no claim it opens the
    // Project's first note *and selects its own tab* - so the very first press of Git,
    // Transcript or Alerts opened the panel on Notes instead. Seeding the claim removes
    // the auto-open without changing what any tab does afterwards.
    localStorage.setItem('mux.drawer.note.v1', JSON.stringify({
      'p-rocket': 'note:n-launch',
      'p-garden': 'note:n-ideas',
    }))
    // The product's own first-run tour stays reachable (Help menu -> "Take the guided
    // tour") but must not auto-open over the embed; the demo runs its own coach.
    localStorage.setItem('mux.tutorial.v1', '1')
  } catch {
    // Private mode: the demo simply runs with the app's own defaults.
  }
}

seedPresentation()

/**
 * A last line of defence around the real App.
 *
 * A render that throws tears a Preact tree down and leaves the page frozen, with
 * whatever overlay was open stuck and no way to close it - which is precisely what a
 * demo visitor cannot recover from, because there is no daemon to restart and the
 * only exit anyone found was the page's own "reset demo". Every known cause of that
 * is now a route with a correct shape (`supportPayloads.ts`), and this is here for
 * the next one: it names the failure, keeps it out of the way, and offers the reload
 * that actually fixes it.
 */
class DemoBoundary extends Component<{ children: ComponentChildren }, { failed: string }> {
  state = { failed: '' }

  static getDerivedStateFromError(error: unknown): { failed: string } {
    return { failed: error instanceof Error ? error.message : String(error) }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return <div class="demo-crash" role="alert">
      <h1>This part of the demo broke.</h1>
      <p>The demo runs the real interface against a simulated daemon, and this surface
      asked it for something it does not fake. Nothing on your machine is affected.</p>
      <pre>{this.state.failed}</pre>
      <button onClick={() => location.reload()}>Reload the demo</button>
    </div>
  }
}

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
  render(
    <DemoBoundary>
      <App />
      {/* Beside the app rather than inside it: the coach points at the real chrome
          and must never be something the product build could accidentally ship. */}
      <DemoCoach />
    </DemoBoundary>,
    document.getElementById('app')!,
  )
}

void boot()
