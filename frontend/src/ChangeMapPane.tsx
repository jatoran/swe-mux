import { useEffect, useRef, useState } from 'preact/hooks'
import Graph from 'graphology'
import Sigma from 'sigma'
import { api } from './api'
import { MOBILE_QUERY } from './deviceSettings'
import {
  DEFAULT_ROLE_PALETTE, HOP_CHOICES, ROLE_DESCRIPTIONS, ROLE_LABELS, ROLE_ORDER,
  clampHops, disabledNote, edgeCounts, graphColor, groupNodesByRole, layoutRequest,
  nodeColor, shortPath, usablePositions,
  type ChangeMap, type LayoutResult, type RolePalette,
} from './changeMap'
import type { Project, Session } from './types'

// The Change Map (Phase 7.9): what this session actually edited, and what those edits
// reach. Red seeds are this session's writes, yellow is the blast radius that imports or
// calls them, blue is the forward context the seeds themselves import. The daemon bounds
// the graph — seeds, blast radius, one hop — so the client never holds a whole codebase.
//
// Two renderings, one component, on the same split every other surface here uses:
//
//  * desktop — Sigma over a graphology graph, laid out by ForceAtlas2 in a bundled
//              module worker (see `changeMapLayout.worker.ts` for why it is not the
//              stock blob-URL helper).
//  * mobile  — the same three roles as lists. Not a smaller graph: a WebGL canvas on a
//              high-density phone display strands on the pixel ratio, and a force layout
//              read through a 380px viewport is unreadable even when it does draw. The
//              question "which files did this touch, and what depends on them" survives
//              being answered as a list; it does not survive being answered by a canvas
//              that renders blank.

type Props = {
  session: Session | null
  project?: Project
  /** Drawer only: pop this map out into its own workspace pane. */
  onPopOut?: () => void
}

const isMobile = () =>
  typeof window !== 'undefined' && !!window.matchMedia?.(MOBILE_QUERY).matches

/** The role colours as this theme defines them, reduced to what Sigma's WebGL colour
 *  parser accepts. `--yellow` is not a variable this app defines; the amber it does
 *  define is the yellow every other surface draws. */
function readPalette(): RolePalette {
  if (typeof document === 'undefined') return DEFAULT_ROLE_PALETTE
  const style = getComputedStyle(document.documentElement)
  const pick = (name: string, fallback: string) => graphColor(style.getPropertyValue(name), fallback)
  return {
    seed: pick('--red', DEFAULT_ROLE_PALETTE.seed),
    blast: pick('--amber', DEFAULT_ROLE_PALETTE.blast),
    context: pick('--blue', DEFAULT_ROLE_PALETTE.context),
  }
}

function readChrome(): { text: string; line: string } {
  if (typeof document === 'undefined') return { text: '#e7eaf0', line: '#252b34' }
  const style = getComputedStyle(document.documentElement)
  return {
    text: graphColor(style.getPropertyValue('--text'), '#e7eaf0'),
    line: graphColor(style.getPropertyValue('--line'), '#252b34'),
  }
}

export function ChangeMapPane({ session, project, onPopOut }: Props) {
  const [data, setData] = useState<ChangeMap | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [unify, setUnify] = useState(false)
  const [hops, setHops] = useState(1)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [mobile, setMobile] = useState(isMobile)
  // Bumped when the WebGL context is lost, which is the one failure Sigma cannot
  // recover from in place: every program, texture, and buffer it holds is dead.
  const [rebuildToken, setRebuildToken] = useState(0)
  const [renderError, setRenderError] = useState('')

  const containerRef = useRef<HTMLDivElement | null>(null)
  const graphRef = useRef<Graph | null>(null)
  const rendererRef = useRef<Sigma | null>(null)
  const workerRef = useRef<Worker | null>(null)
  const workerFailedRef = useRef(false)
  const requestIdRef = useRef(0)

  const sid = session?.id || ''
  const run = session?.agent_run_id || ''
  const projectId = project?.id || session?.project_id || ''

  const load = async () => {
    if (!sid) { setData(null); return }
    setLoading(true)
    try {
      setData(await api<ChangeMap>('GET', `/api/sessions/${sid}/change-map?unify=${unify}&hops=${hops}`))
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setData(null)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { void load() }, [sid, run, unify, hops])
  // The map only changes when an agent writes a file, and a turn boundary is where a
  // write becomes a recorded fact. Event-driven rather than polled for the same reason
  // the timeline is: nothing here moves between turns.
  useEffect(() => {
    const refresh = () => void load()
    window.addEventListener('mux:turn-ended', refresh)
    window.addEventListener('mux:transcript-changed', refresh)
    return () => {
      window.removeEventListener('mux:turn-ended', refresh)
      window.removeEventListener('mux:transcript-changed', refresh)
    }
  }, [sid, run, unify, hops])
  // A selection naming a file the newest map no longer holds must not stick.
  useEffect(() => {
    if (selectedPath && !data?.nodes.some(node => node.path === selectedPath)) setSelectedPath(null)
  }, [data])
  useEffect(() => {
    const query = window.matchMedia?.(MOBILE_QUERY)
    if (!query) return
    const update = () => setMobile(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  useEffect(() => () => {
    workerRef.current?.terminate()
    workerRef.current = null
  }, [])

  const nodes = data?.nodes || []
  const edges = data?.edges || []
  const showGraph = !mobile && !!data?.available && nodes.length > 0

  // The renderer's lifetime, kept apart from the data it draws so a refresh does not
  // reset the camera the reader just panned. Recreated only when the projection
  // changes or the WebGL context dies.
  useEffect(() => {
    if (!showGraph) return
    const host = containerRef.current
    if (!host) return
    const chrome = readChrome()
    const graph = new Graph({ type: 'directed', multi: false, allowSelfLoops: false })
    let renderer: Sigma
    try {
      renderer = new Sigma(graph, host, {
        // The drawer column can be collapsed to zero width while this mounts; an
        // invalid container is a transient geometry state, not a reason to throw.
        allowInvalidContainer: true,
        renderLabels: true,
        renderEdgeLabels: false,
        enableEdgeEvents: false,
        labelFont: "'Cascadia Mono',Consolas,monospace",
        labelSize: 11,
        labelWeight: '400',
        labelColor: { color: chrome.text },
        labelDensity: 0.5,
        labelRenderedSizeThreshold: 3,
        defaultEdgeColor: chrome.line,
        minEdgeThickness: 1,
        zIndex: true,
        minCameraRatio: 0.08,
        maxCameraRatio: 12,
      })
    } catch (cause) {
      setRenderError(cause instanceof Error ? cause.message : String(cause))
      return
    }
    setRenderError('')
    graphRef.current = graph
    rendererRef.current = renderer
    renderer.on('clickNode', ({ node }) => setSelectedPath(node))
    renderer.on('clickStage', () => setSelectedPath(null))

    // Sigma has no resize observation of its own and only watches the window, so a
    // drawer drag or a pane split would otherwise leave the canvas at its old size.
    const observer = new ResizeObserver(() => renderer.resize())
    observer.observe(host)

    // Same resilience TerminalPane's WebGL renderer has, for the same reason: a lost
    // context is silent, and everything simply stops drawing. `preventDefault` is what
    // makes the context restorable at all; the rebuild is what puts a picture back.
    const canvases = Object.values(renderer.getCanvases())
    const onContextLoss = (event: Event) => {
      event.preventDefault()
      setRebuildToken(token => token + 1)
    }
    for (const canvas of canvases) canvas.addEventListener('webglcontextlost', onContextLoss)

    return () => {
      for (const canvas of canvases) canvas.removeEventListener('webglcontextlost', onContextLoss)
      observer.disconnect()
      renderer.kill()
      if (rendererRef.current === renderer) rendererRef.current = null
      if (graphRef.current === graph) graphRef.current = null
    }
  }, [showGraph, rebuildToken])

  /** A worker, or null once one has failed to start. Never retried per render: a
   *  browser that refuses the worker will refuse it every time, and the seeded ring
   *  is already on screen either way. */
  const ensureWorker = (): Worker | null => {
    if (workerRef.current) return workerRef.current
    if (workerFailedRef.current) return null
    try {
      const worker = new Worker(new URL('./changeMapLayout.worker.ts', import.meta.url), { type: 'module' })
      worker.onerror = () => {
        workerFailedRef.current = true
        worker.terminate()
        if (workerRef.current === worker) workerRef.current = null
      }
      workerRef.current = worker
      return worker
    } catch {
      workerFailedRef.current = true
      return null
    }
  }

  // The data sync: repopulate the live graph, draw the seeded arrangement immediately,
  // then swap in ForceAtlas2's answer when the worker returns it.
  useEffect(() => {
    const graph = graphRef.current
    const renderer = rendererRef.current
    if (!graph || !renderer || !showGraph) return
    const palette = readPalette()
    const request = layoutRequest(requestIdRef.current + 1, nodes, edges)
    requestIdRef.current = request.requestId
    const byPath = new Map(nodes.map(node => [node.path, node]))
    graph.clear()
    for (const item of request.nodes) {
      const node = byPath.get(item.id)
      if (!node) continue
      graph.addNode(item.id, {
        x: item.x, y: item.y, size: item.size,
        color: nodeColor(node, palette),
        label: shortPath(item.id),
        zIndex: node.role === 'seed' ? 2 : node.role === 'blast' ? 1 : 0,
      })
    }
    for (const edge of request.edges) graph.addEdge(edge.source, edge.target)
    renderer.refresh()

    const worker = ensureWorker()
    if (!worker) return
    const handler = (event: MessageEvent<LayoutResult>) => {
      const positions = usablePositions(event.data, request.requestId, nodes)
      if (!positions) return
      worker.removeEventListener('message', handler)
      if (graphRef.current !== graph) return
      for (const [path, point] of Object.entries(positions)) {
        if (graph.hasNode(path)) graph.mergeNodeAttributes(path, point)
      }
      rendererRef.current?.refresh()
    }
    worker.addEventListener('message', handler)
    worker.postMessage(request)
    return () => worker.removeEventListener('message', handler)
  }, [data, showGraph, rebuildToken])

  const controls = <div class="change-map-controls">
    <label class="change-map-unify" title="Show every session's edits since the baseline, one hue per session">
      <input type="checkbox" checked={unify} disabled={!sid} onChange={event => setUnify(event.currentTarget.checked)} />
      <span>unify sessions</span>
    </label>
    <label class="change-map-hops" title="How many dependency steps of blast radius to include">
      <span>hops</span>
      <select value={String(hops)} disabled={!sid}
        onChange={event => setHops(clampHops(Number(event.currentTarget.value)))}>
        {HOP_CHOICES.map(choice => <option key={choice} value={String(choice)}>{choice}</option>)}
      </select>
    </label>
  </div>

  const header = <header class="change-map-header">
    <div>
      <strong>Change Map</strong>
      <small>{data?.baseline_head ? `since ${data.baseline_head.slice(0, 8)}` : 'no baseline commit'}</small>
    </div>
    {onPopOut && <button type="button" class="change-map-popout"
      title="Open this change map as a workspace tab"
      aria-label="Open this change map as a workspace tab"
      onClick={onPopOut}>↗</button>}
  </header>

  const footer = data && <footer class="change-map-footer">
    <small>{data.excludes_note}</small>
    <small>{data.lower_bound_note}</small>
  </footer>

  if (!session) return <p class="drawer-empty">Focus a session to see what it changed.</p>
  if (!data) return <section class="change-map-pane">
    {header}
    <p class="change-map-note">{error || (loading ? 'Loading change map…' : 'No change map for this session.')}</p>
  </section>

  if (!data.available) {
    const { note, hint } = disabledNote(data.disabled_reason)
    return <section class="change-map-pane">
      {header}
      <div class="change-map-off">
        <p>{note}</p>
        <p class="change-map-off-hint">{hint}</p>
        {!projectId && <p class="change-map-off-hint">This session is not attached to a registered Project.</p>}
      </div>
      {footer}
    </section>
  }

  if (!nodes.length) return <section class="change-map-pane">
    {header}
    {controls}
    <div class="change-map-off">
      <p>{data.empty_reason === 'no_edits'
        ? 'No source edits in this session yet.'
        : 'Nothing to map for this session yet.'}</p>
      <p class="change-map-off-hint">The map fills in on the turn after this session writes a source file.</p>
    </div>
    {footer}
  </section>

  const legend = unify && data.sessions.length
    ? <div class="change-map-legend" role="list" aria-label="Sessions">
      {data.sessions.map(item => <span key={item.id} role="listitem" class="change-map-legend-item">
        <b style={{ background: graphColor(item.hue, DEFAULT_ROLE_PALETTE.seed) }} aria-hidden="true" />
        {item.name}
      </span>)}
    </div>
    : <div class="change-map-legend" role="list" aria-label="Node roles">
      {ROLE_ORDER.map(role => <span key={role} role="listitem" class={`change-map-legend-item role-${role}`}
        title={ROLE_DESCRIPTIONS[role]}>
        <b aria-hidden="true" />{ROLE_LABELS[role]}
      </span>)}
    </div>

  const degrees = edgeCounts(edges)
  const selected = selectedPath ? nodes.find(node => node.path === selectedPath) || null : null
  const detail = selected && <div class="change-map-detail" role="status">
    <code>{selected.path}</code>
    <div>
      <span class={`change-map-role role-${selected.role}`}>{ROLE_LABELS[selected.role]}</span>
      {selected.role === 'blast' && selected.hop ? <span>{selected.hop} hop{selected.hop > 1 ? 's' : ''} out</span> : null}
      <span>{degrees[selected.path] || 0} link{(degrees[selected.path] || 0) === 1 ? '' : 's'}</span>
    </div>
    {!!selected.sessions?.length && <small>edited by {selected.sessions
      .map(id => data.sessions.find(item => item.id === id)?.name || id.slice(0, 8))
      .join(', ')}</small>}
    <button type="button" onClick={() => setSelectedPath(null)}>clear</button>
  </div>

  const body = mobile
    ? <div class="change-map-list">
      {groupNodesByRole(nodes).map(group => <section key={group.role} class={`change-map-group role-${group.role}`}>
        <h3><b aria-hidden="true" />{group.label}<span>{group.nodes.length}</span></h3>
        <p>{ROLE_DESCRIPTIONS[group.role]}</p>
        <ul>
          {group.nodes.map(node => <li key={node.path}>
            <button type="button" class={selectedPath === node.path ? 'active' : ''}
              onClick={() => setSelectedPath(selectedPath === node.path ? null : node.path)}>
              <code>{node.path}</code>
              <span>{degrees[node.path] || 0}</span>
            </button>
          </li>)}
        </ul>
      </section>)}
    </div>
    : <div class="change-map-canvas" ref={containerRef} role="img"
      aria-label={`Change map: ${nodes.length} files, ${edges.length} dependencies`} />

  return <section class="change-map-pane">
    {header}
    {controls}
    {legend}
    {error && <p class="usage-error">{error}</p>}
    {renderError && <p class="usage-error">The graph could not be drawn: {renderError}</p>}
    {body}
    {detail}
    {footer}
  </section>
}
