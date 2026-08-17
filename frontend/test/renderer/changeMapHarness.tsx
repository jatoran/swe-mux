// The real Change Map pane over a stubbed daemon. Two things need a browser and cannot be
// unit tested: Sigma only draws into a container that has real geometry (it appends
// absolutely positioned canvases and reads the client box for its viewport), and the
// mobile fallback is chosen from a media query. Both are layout facts, so they live here.
import { render } from 'preact'
import Sigma from 'sigma'
import { ChangeMapPane } from '../../src/ChangeMapPane'
import type { LayoutResult } from '../../src/changeMap'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

/** Just enough of Sigma for a spec to place the pointer over a node and read back
 *  what the reducers decided to draw.
 *
 *  `framedGraphToViewport`, not `graphToViewport`: the coordinates in the display
 *  data have already been through Sigma's normalization, so the graph-space
 *  converter normalizes them a second time and lands the pointer on empty canvas. */
type SigmaProbe = {
  getNodeDisplayData(key: string): { x: number; y: number; color: string; forceLabel?: boolean } | undefined
  framedGraphToViewport(point: { x: number; y: number }): { x: number; y: number }
}

declare global {
  interface Window {
    changeMapLayoutResults: LayoutResult[]
    changeMapOpened: { path: string; worktree: string | null }[]
    changeMapSigma?: SigmaProbe
  }
}

// Records what the layout worker answers, so a spec can prove ForceAtlas2 actually ran.
// This is the check nothing else can make: if the worker is ever built from a blob URL
// again, the CSP refuses it *silently* and the pane still draws its seeded ring, so the
// map looks fine and is simply never laid out.
window.changeMapLayoutResults = []
const NativeWorker = window.Worker
class RecordingWorker extends NativeWorker {
  constructor(url: string | URL, options?: WorkerOptions) {
    super(url, options)
    this.addEventListener('message', event => {
      window.changeMapLayoutResults.push((event as MessageEvent<LayoutResult>).data)
    })
  }
}
window.Worker = RecordingWorker as typeof Worker

// The pane holds its Sigma instance privately, which is correct — but the highlight
// is drawn by per-frame reducers and lands nowhere in the DOM, so the only way to
// assert it is to read back what Sigma resolved. `refresh` is the one method the pane
// is guaranteed to call, so wrapping it hands the spec the live renderer.
const nativeRefresh = Sigma.prototype.refresh
Sigma.prototype.refresh = function patchedRefresh(this: Sigma, ...args: Parameters<Sigma['refresh']>) {
  window.changeMapSigma = this as unknown as SigmaProbe
  return nativeRefresh.apply(this, args)
}

window.changeMapOpened = []

const MAP = {
  session_id: 'claude-d92695',
  project_id: 'p1',
  baseline_head: '4417166ac1de',
  available: true,
  disabled_reason: null,
  worktree: null,
  // One edited file the graph refuses to index, so the caption that says so is on
  // screen in the same run that asserts the geometry around it.
  excluded: { outside_root: 1, unindexable: 0 },
  nodes: [
    // Mixed case on purpose: `path` is the casefolded graph identity and
    // `display_path` is the only thing that may reach a file endpoint.
    {
      path: 'src/swe_mux/server.py', role: 'seed', sessions: ['claude-d92695'],
      display_path: 'src/swe_mux/Server.py',
    },
    {
      path: 'src/swe_mux/code_graph.py', role: 'seed', sessions: ['claude-d92695'],
      display_path: 'src/swe_mux/code_graph.py',
    },
    { path: 'src/swe_mux/mcp.py', role: 'blast', hop: 1, display_path: 'src/swe_mux/mcp.py' },
    {
      path: 'src/swe_mux/deterministic_consumers.py', role: 'blast', hop: 1,
      display_path: 'src/swe_mux/deterministic_consumers.py',
    },
    { path: 'src/swe_mux/cli.py', role: 'blast', hop: 2, display_path: 'src/swe_mux/cli.py' },
    { path: 'src/swe_mux/layouts.py', role: 'context', display_path: 'src/swe_mux/layouts.py' },
    { path: 'src/swe_mux/config.py', role: 'context', display_path: 'src/swe_mux/config.py' },
    // No `display_path`: written, then deleted. Its button must be dead, not a 404.
    { path: 'src/swe_mux/store.py', role: 'context' },
  ],
  edges: [
    { source: 'src/swe_mux/mcp.py', target: 'src/swe_mux/server.py', kind: 'imports' },
    { source: 'src/swe_mux/mcp.py', target: 'src/swe_mux/server.py', kind: 'calls' },
    { source: 'src/swe_mux/deterministic_consumers.py', target: 'src/swe_mux/code_graph.py', kind: 'imports' },
    { source: 'src/swe_mux/cli.py', target: 'src/swe_mux/mcp.py', kind: 'imports' },
    { source: 'src/swe_mux/server.py', target: 'src/swe_mux/layouts.py', kind: 'imports' },
    { source: 'src/swe_mux/server.py', target: 'src/swe_mux/config.py', kind: 'imports' },
    { source: 'src/swe_mux/code_graph.py', target: 'src/swe_mux/store.py', kind: 'imports' },
  ],
  sessions: [],
  excludes_note: 'Excludes generated, vendored, and test-only files.',
  lower_bound_note: 'A lower bound: only edges the indexer resolved statically are drawn.',
}

window.fetch = (async () =>
  new Response(JSON.stringify(MAP), { status: 200, headers: { 'Content-Type': 'application/json' } })
) as typeof fetch

const SESSION = {
  id: 'claude-d92695', name: 'claude-d92695', project_id: 'p1', state: 'idle', backend: 'claude',
} as unknown as Session
const PROJECT = { id: 'p1', name: 'swe-mux', position: 0 } as unknown as Project

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')
// The drawer column the pane normally lives in: a fixed-height flex box, which is what
// makes `min-height:0` load-bearing all the way down to the canvas.
const root = document.querySelector<HTMLDivElement>('#root')!
root.style.display = 'flex'
root.style.height = '100vh'
root.style.width = '100%'
render(
  <ChangeMapPane
    session={SESSION}
    project={PROJECT}
    onPopOut={() => {}}
    onOpenFile={(path, worktree) => { window.changeMapOpened.push({ path, worktree }) }}
  />,
  root,
)
