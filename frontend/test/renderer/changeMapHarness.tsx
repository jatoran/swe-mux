// The real Change Map pane over a stubbed daemon. Two things need a browser and cannot be
// unit tested: Sigma only draws into a container that has real geometry (it appends
// absolutely positioned canvases and reads the client box for its viewport), and the
// mobile fallback is chosen from a media query. Both are layout facts, so they live here.
import { render } from 'preact'
import { ChangeMapPane } from '../../src/ChangeMapPane'
import type { LayoutResult } from '../../src/changeMap'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

declare global {
  interface Window { changeMapLayoutResults: LayoutResult[] }
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

const MAP = {
  session_id: 'claude-d92695',
  project_id: 'p1',
  baseline_head: '4417166ac1de',
  available: true,
  disabled_reason: null,
  nodes: [
    { path: 'src/swe_mux/server.py', role: 'seed', sessions: ['claude-d92695'] },
    { path: 'src/swe_mux/code_graph.py', role: 'seed', sessions: ['claude-d92695'] },
    { path: 'src/swe_mux/mcp.py', role: 'blast', hop: 1 },
    { path: 'src/swe_mux/deterministic_consumers.py', role: 'blast', hop: 1 },
    { path: 'src/swe_mux/cli.py', role: 'blast', hop: 2 },
    { path: 'src/swe_mux/layouts.py', role: 'context' },
    { path: 'src/swe_mux/config.py', role: 'context' },
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
render(<ChangeMapPane session={SESSION} project={PROJECT} onPopOut={() => {}} />, root)
