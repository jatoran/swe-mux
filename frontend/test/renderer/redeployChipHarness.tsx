// The redeploy indicator in each of the two top bars it can live in.
//
// Placement is the whole claim and it is pure CSS, so no unit test can see it. On a phone
// the chip is a control *inside* `.mobile-toolbar`; it used to be a fixed card anchored
// 54px below that bar, which covered the top of the workspace for the whole multi-minute
// build on the screen with the least of it to spare. On desktop it is still the floating
// card under `.app-topbar`, and the bar itself is crowded enough that "does the rest of it
// still fit on one row" is a real question rather than a formality.
//
// `?inline=0` renders the desktop placement; anything else renders the phone's.
import { render } from 'preact'
import { RedeployChip } from '../../src/RedeployChip'
import type { RedeployState } from '../../src/redeployProgress'
import '../../src/style.css'

const params = new URLSearchParams(location.search)
const inline = params.get('inline') !== '0'

// A build two minutes in, with log lines to expand onto. `startedAt` is a fixed epoch and
// the harness freezes nothing else: the elapsed label is allowed to tick, and the spec
// matches its shape rather than its value.
const STATE: RedeployState = {
  phase: params.get('phase') === 'down' ? 'down' : 'building',
  startedAt: Date.now() - 132_000,
  expiresAt: Date.now() + 600_000,
  sawDown: false,
  downProbes: 0,
  logTail: [
    'INFO: PyInstaller: 6.11.1',
    'INFO: collecting submodules for swe_mux',
    'INFO: Building EXE from EXE-00.toc',
  ],
}

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

render(
  inline
    ? <div class="mobile-toolbar">
      <button class="nav-toggle mobile-nav-toggle" aria-label="Open navigation sidebar">☰</button>
      <button class="mobile-project-name" type="button">swe-mux</button>
      <button class="mobile-run-trigger">▶ Run</button>
      <RedeployChip state={STATE} inline />
      <button class="mobile-drawer-toggle" aria-label="Open side panel">▤</button>
    </div>
    : <div class="app-shell">
      <div class="workspace">
        <header class="app-topbar"><div class="app-identity"><strong class="desktop-project-name">swe-mux</strong></div></header>
      </div>
      <RedeployChip state={STATE} />
    </div>,
  document.querySelector('#root')!,
)
