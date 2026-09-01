/**
 * Demo entry: install the fake daemon (fetch + WebSocket shims) BEFORE the app
 * modules load, then boot the real, unmodified App against it.
 *
 * The import of `../App` is dynamic on purpose - a static import would be
 * hoisted above the shim installation and the first fetches would race the
 * fake backend. Everything the visitor does here is simulated in-page; the
 * demo build ships to the static marketing site and talks to nothing.
 */
// First, and the order is load-bearing rather than stylistic: deterministic mode replaces
// `Math.random` and rebases the clock, and `fixtures.ts` reads `Date.now()` at module
// evaluation. An import below this line that pulled the store in first would seed the
// whole fleet off the wall clock before the switch had been thrown.
import './determinism.ts'
import { Component, render, type ComponentChildren } from 'preact'
import { installFakeFetch } from './fakeApi.ts'
import { installFakeWebSocket } from './fakeSocket.ts'
import { installViewMirror } from './mirror.ts'
import { installDirector } from './director.ts'
import { DemoBar } from './DemoBar.tsx'
import { DemoDirector } from './DemoDirector.tsx'
import '../style.css'
import './demoDirector.css'
import './demoShow.css'
import './demoBar.css'

installFakeFetch()
installFakeWebSocket()
// The fleet already mirrors across frames through the demo store; this mirrors what the
// store cannot see - which modal is open, which panel and tab, which session is focused
// - so the desktop and phone shown side by side behave as one app rather than two.
installViewMirror()
// One thing drives the interface: the walkthrough is its first scenario, and the mirror
// above is what carries the winning frame's acts to the other one.
installDirector()

/**
 * Device-local presentation defaults for the embed.
 *
 * Versioned by a demo-owned marker rather than by the presence of each app key,
 * because a returning visitor already has those keys from an older demo and would
 * never see a tab this build added. Bump `SEED_VERSION` when the seed changes; the
 * visitor's own later edits within one version are still theirs to keep.
 */
const SEED_KEY = 'swemux-demo-seed'
const SEED_VERSION = '3'

function seedPresentation(): void {
  try {
    if (localStorage.getItem(SEED_KEY) === SEED_VERSION) return
    localStorage.setItem(SEED_KEY, SEED_VERSION)
    // The drawer shows the tabs this demo actually populates rather than all eleven.
    // Queue came off this list when the prompt queue became state rather than a constant
    // (`controlPlane.ts`): two scenarios drive it, and a tab a scenario opens has to be
    // one the visitor can find again afterwards.
    localStorage.setItem('mux.drawer.hidden.v1', JSON.stringify([
      'agent', 'schedule', 'actions', 'files',
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
 * Inside the embed, nothing may raise the phone's soft keyboard.
 *
 * The app's own policy is right and stays: on a coarse pointer, tapping a terminal pane
 * is typing intent, so the keyboard comes up, and a rail press preserves whatever state
 * it found. What breaks here is the premise underneath it. The landing page sizes this
 * iframe itself, so the OS keyboard never changes the frame's `visualViewport`: the inset
 * stays zero, the pane never shrinks, the rail never lifts, and the keyboard-peek button
 * never appears. Every part of swe-mux's soft-keyboard handling is unreachable in here,
 * and what is left is a keyboard covering a demo that cannot react to it.
 *
 * So the embed refuses the keyboard outright, and the real thing is one deliberate tap
 * away: the landing page's "open full screen" link loads this same page at the top level,
 * where `visualViewport` does shrink, the app measures it, and the whole adaptation is not
 * only correct but worth watching.
 *
 * The refusal is a property shadow rather than a listener that puts the attribute back,
 * because the app writes `inputMode` immediately before it focuses: anything reacting
 * *after* focus gets a keyboard that opens and then dismisses, which is worse than either
 * outcome. Shadowing makes the app's write a no-op and leaves `inputmode="none"` standing.
 * Only the terminal's own live input is touched - the Draft composer, the palette and the
 * settings fields keep their keyboards, because there the keyboard is the point and the
 * surface is one the app draws above it.
 */
function refuseSoftKeyboard(): void {
  if (window.top === window.self) return
  const pinned = new WeakSet<HTMLElement>()
  const pin = (field: HTMLElement): void => {
    if (pinned.has(field)) return
    pinned.add(field)
    field.setAttribute('inputmode', 'none')
    Object.defineProperty(field, 'inputMode', {
      configurable: true,
      get: () => 'none',
      set: () => { /* the app may ask for a keyboard; in the embed it cannot have one */ },
    })
  }
  const sweep = (): void => {
    for (const field of document.querySelectorAll<HTMLElement>('.mobile-terminal-live-input')) {
      pin(field)
    }
  }
  sweep()
  new MutationObserver(sweep).observe(document.body, { childList: true, subtree: true })
}

refuseSoftKeyboard()

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
  // Full screen the demo is the whole page, so it has to carry the way back, the scenarios
  // menu and the reset that the landing page carries above the embed. Inside the frame
  // that page already has them, and a second copy would be chrome over chrome.
  const framed = window.top === window.self
  if (framed) document.body.classList.add('demo-framed')
  render(
    <DemoBoundary>
      {framed && <DemoBar />}
      <App />
      {/* Beside the app rather than inside it: the director points at the real chrome
          and must never be something the product build could accidentally ship. */}
      <DemoDirector />
    </DemoBoundary>,
    document.getElementById('app')!,
  )
}

void boot()
