// ForceAtlas2 for the Change Map, off the main thread.
//
// A bundled Vite module worker, not graphology's `graphology-layout-forceatlas2/worker`
// helper. That helper builds its worker from a blob URL, and this app's CSP is
// `script-src 'self' 'wasm-unsafe-eval'` with no `worker-src` and no `blob:` — a blob
// worker is refused outright there. `new Worker(new URL('./changeMapLayout.worker.ts',
// import.meta.url), {type:'module'})` emits a same-origin chunk instead, which 'self'
// covers. Do not swap this for the stock helper, and do not widen the CSP to make the
// helper work.
//
// It runs a fixed number of iterations and answers once, rather than supervising a
// live simulation: the graph is bounded server-side (seeds, blast radius, one hop) and
// redrawn on turn boundaries, so a settled picture per turn is the whole requirement
// and a running simulation would only cost a thread.

import Graph from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import { LAYOUT_ITERATIONS, type LayoutRequest, type LayoutResult } from './changeMap'

// The base tsconfig ships the DOM lib rather than WebWorker (one `lib` serves the whole
// app), so `self` is typed as a Window here and its `postMessage` carries the wrong
// signature. This is the narrow shape a dedicated worker actually has.
type DedicatedWorkerScope = {
  onmessage: ((event: { data: LayoutRequest }) => void) | null
  postMessage: (message: LayoutResult) => void
}
const scope = self as unknown as DedicatedWorkerScope

function layout(request: LayoutRequest): LayoutResult {
  const graph = new Graph({ type: 'directed', multi: false, allowSelfLoops: false })
  for (const node of request.nodes) {
    if (graph.hasNode(node.id)) continue
    graph.addNode(node.id, { x: node.x, y: node.y, size: node.size })
  }
  for (const edge of request.edges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue
    if (edge.source === edge.target || graph.hasEdge(edge.source, edge.target)) continue
    graph.addEdge(edge.source, edge.target)
  }
  const iterations = Number.isFinite(request.iterations) && request.iterations > 0
    ? Math.min(1000, Math.round(request.iterations))
    : LAYOUT_ITERATIONS
  const positions = forceAtlas2(graph, {
    iterations,
    settings: {
      ...forceAtlas2.inferSettings(graph),
      // Sizes are meaningful here (seeds are drawn large), so the layout must not let
      // a hub's label sit under the node it belongs to.
      adjustSizes: true,
      barnesHutOptimize: graph.order > 300,
      gravity: 1,
      scalingRatio: 12,
    },
  })
  return { requestId: request.requestId, positions }
}

scope.onmessage = event => {
  const request = event.data
  if (!request || !Array.isArray(request.nodes)) return
  if (!request.nodes.length) {
    scope.postMessage({ requestId: request.requestId, positions: {} })
    return
  }
  try {
    scope.postMessage(layout(request))
  } catch {
    // A layout that throws must not strand the pane: an empty answer is discarded by
    // `usablePositions`, which leaves the seeded ring already on screen standing.
    scope.postMessage({ requestId: request.requestId, positions: {} })
  }
}
