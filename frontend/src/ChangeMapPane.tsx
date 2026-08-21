import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import Graph from 'graphology'
import Sigma from 'sigma'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { GrantGate } from './GrantGate'
import { MOBILE_QUERY } from './deviceSettings'
import {
  DEFAULT_ROLE_PALETTE, HOP_CHOICES, ROLE_DESCRIPTIONS, ROLE_LABELS, ROLE_ORDER,
  SCOPE_DESCRIPTIONS, SCOPE_LABELS, UNINDEXED_MARK,
  adjacency, checkoutNote, clampHops, disabledNote, edgeCounts, excludedNote, focusedPath,
  graphColor, groupNodesByRole, layoutRequest, markUnindexed, mixHex, neighborNodes,
  neighborhood, nodeColor, nodeLabel, unindexedCount, unindexedNote, usablePositions,
  type ChangeMap, type ChangeMapNode, type ChangeMapScope, type LayoutResult, type RolePalette,
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
  /** Open a mapped file as a workspace pane. `path` is a node's `display_path`
   *  (true-cased), and `worktree` is the map's checkout when it is not the
   *  Project root — a worktree session's files are not in the primary checkout. */
  onOpenFile?: (path: string, worktree: string | null) => void
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

type Chrome = { text: string; line: string; bg: string }

function readChrome(): Chrome {
  const fallback: Chrome = { text: '#e7eaf0', line: '#252b34', bg: '#090b0e' }
  if (typeof document === 'undefined') return fallback
  const style = getComputedStyle(document.documentElement)
  return {
    text: graphColor(style.getPropertyValue('--text'), fallback.text),
    line: graphColor(style.getPropertyValue('--line'), fallback.line),
    bg: graphColor(style.getPropertyValue('--bg'), fallback.bg),
  }
}

/** How far an out-of-focus node or edge is blended into the background. High
 *  enough that the highlighted neighbourhood reads at a glance, low enough that
 *  the rest of the map stays visible as context rather than disappearing. */
const DIM_NODE = 0.82
const DIM_EDGE = 0.55

export function ChangeMapPane({ session, project, onPopOut, onOpenFile }: Props) {
  const [data, setData] = useState<ChangeMap | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  // Empty means "let the daemon decide", which is what makes a worktree default to
  // its branch without the client having to know it is in one. A reader's explicit
  // pick is remembered for as long as the pane is mounted.
  const [scope, setScope] = useState<ChangeMapScope | ''>('')
  const [hops, setHops] = useState(1)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [mobile, setMobile] = useState(isMobile)
  // True while the worker is settling ForceAtlas2. The seeded ring is already on
  // screen, so this drives an unobtrusive "laying out…" badge, not a blank pane.
  const [layoutPending, setLayoutPending] = useState(false)
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

  // The highlight lives in refs, not state. Sigma's reducers run per frame and read
  // whatever these hold, so a hover costs one WebGL repaint instead of re-rendering
  // the whole Preact subtree — which, on this pane, would re-run the data effect and
  // re-seed the graph on every pointer move across the canvas.
  const hoverRef = useRef<string | null>(null)
  const selectedRef = useRef<string | null>(null)
  const focusRef = useRef<{ path: string; nodes: Set<string> } | null>(null)
  const linksRef = useRef<Map<string, Set<string>>>(new Map())
  const chromeRef = useRef<Chrome>(readChrome())

  const sid = session?.id || ''
  const run = session?.agent_run_id || ''
  const projectId = project?.id || session?.project_id || ''

  const load = async () => {
    if (!sid) { setData(null); return }
    setLoading(true)
    try {
      const query = `hops=${hops}${scope ? `&scope=${scope}` : ''}`
      setData(await api<ChangeMap>('GET', `/api/sessions/${sid}/change-map?${query}`))
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setData(null)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { void load() }, [sid, run, scope, hops])
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
  }, [sid, run, scope, hops])
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

  // Undirected links, rebuilt only when the map itself changes. Both projections
  // read it: the graph's highlight through `linksRef`, and the mobile detail card's
  // neighbour list directly.
  const links = useMemo(() => adjacency(edges), [data])
  useEffect(() => { linksRef.current = links }, [links])

  /** Recompute what the reducers draw and repaint once.
   *
   *  A hover previews on top of the selection and falls back to it on leave, so
   *  the pinned highlight survives the pointer crossing the pane. `schedule`
   *  coalesces to the next animation frame, which is what keeps dragging the
   *  pointer across a dense cluster from queueing one full repaint per node. */
  const applyFocus = () => {
    const path = focusedPath(hoverRef.current, selectedRef.current)
    const linked = neighborhood(path, linksRef.current)
    focusRef.current = path && linked ? { path, nodes: linked } : null
    rendererRef.current?.refresh({ schedule: true })
  }
  // The selection is state (it drives the detail card) and the reducers are not
  // re-run by a Preact render, so the two are joined here rather than in the click
  // handler — which would miss the mobile rows and the map-changed reset.
  useEffect(() => {
    selectedRef.current = selectedPath
    applyFocus()
  }, [selectedPath, showGraph, links])

  // The renderer's lifetime, kept apart from the data it draws so a refresh does not
  // reset the camera the reader just panned. Recreated only when the projection
  // changes or the WebGL context dies.
  useEffect(() => {
    if (!showGraph) return
    const host = containerRef.current
    if (!host) return
    const chrome = readChrome()
    chromeRef.current = chrome
    const graph = new Graph({ type: 'directed', multi: false, allowSelfLoops: false })
    let renderer: Sigma
    try {
      renderer = new Sigma(graph, host, {
        // The highlight, applied per frame. A null `focusRef` means nothing is
        // focused and the map draws exactly as it did before, so the common case
        // costs one comparison per node and allocates nothing.
        nodeReducer: (node, attributes) => {
          const focus = focusRef.current
          if (!focus) return attributes
          if (focus.nodes.has(node)) return { ...attributes, forceLabel: true, zIndex: 3 }
          return {
            ...attributes,
            color: mixHex(String(attributes.color || ''), chromeRef.current.bg, DIM_NODE),
            // Dropped rather than dimmed: a faint label at this size reads as noise,
            // and the point of the highlight is that the neighbourhood's labels are
            // the only ones left standing.
            label: '',
            zIndex: 0,
          }
        },
        edgeReducer: (edge, attributes) => {
          const focus = focusRef.current
          if (!focus) return attributes
          const [source, target] = graph.extremities(edge)
          // Only edges *incident to the focused node* light up. An edge between two
          // of its neighbours has both ends inside the highlighted set but is not a
          // link the focused file has, and drawing it would overstate the reach.
          if (source === focus.path || target === focus.path) {
            return { ...attributes, color: chromeRef.current.text, size: 2, zIndex: 2 }
          }
          return {
            ...attributes,
            color: mixHex(
              String(attributes.color || chromeRef.current.line), chromeRef.current.bg, DIM_EDGE),
          }
        },
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
    // Clicking the focused node again releases it, the way the mobile list rows do.
    renderer.on('clickNode', ({ node }) => setSelectedPath(current => current === node ? null : node))
    renderer.on('clickStage', () => setSelectedPath(null))
    renderer.on('enterNode', ({ node }) => { hoverRef.current = node; applyFocus() })
    renderer.on('leaveNode', () => { hoverRef.current = null; applyFocus() })

    // Sigma has no resize observation of its own and only watches the window, so a
    // drawer drag or a pane split would otherwise leave the canvas at its old size.
    // The explicit refresh is load-bearing: selecting a node opens the detail panel,
    // which reflows the pane and resizes the canvas, and a bare resize() leaves the
    // WebGL surface blank until the next camera move (the "click a node and it goes
    // black until you pan" bug). Repainting on every resize keeps a picture up.
    const observer = new ResizeObserver(() => {
      renderer.resize()
      renderer.refresh()
    })
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
        label: nodeLabel(node),
        zIndex: node.role === 'seed' ? 2 : node.role === 'blast' ? 1 : 0,
      })
    }
    for (const edge of request.edges) graph.addEdge(edge.source, edge.target)
    renderer.refresh()

    const worker = ensureWorker()
    if (!worker) { setLayoutPending(false); return }
    setLayoutPending(true)
    const handler = (event: MessageEvent<LayoutResult>) => {
      const positions = usablePositions(event.data, request.requestId, nodes)
      if (!positions) return
      worker.removeEventListener('message', handler)
      setLayoutPending(false)
      if (graphRef.current !== graph) return
      for (const [path, point] of Object.entries(positions)) {
        if (graph.hasNode(path)) graph.mergeNodeAttributes(path, point)
      }
      rendererRef.current?.refresh()
    }
    worker.addEventListener('message', handler)
    worker.postMessage(request)
    return () => {
      worker.removeEventListener('message', handler)
      setLayoutPending(false)
    }
  }, [data, showGraph, rebuildToken])

  // Offered scopes come from the daemon, because only it knows whether this
  // checkout has a base to measure a branch against. The served scope is what the
  // control shows, so a request that fell back never leaves the selector lying.
  const offered = data?.scopes?.length ? data.scopes : (['session', 'project'] as ChangeMapScope[])
  const activeScope = data?.scope || scope || 'session'
  const controls = <div class="change-map-controls">
    <label class="change-map-scope" title={SCOPE_DESCRIPTIONS[activeScope]}>
      <span>show</span>
      <Dropdown value={activeScope} disabled={!sid}
        onChange={value => setScope(value as ChangeMapScope)}
        options={offered.map(choice => ({ value: choice, label: SCOPE_LABELS[choice] }))}/>
    </label>
    <label class="change-map-hops" title="How many dependency steps of blast radius to include">
      <span>hops</span>
      <Dropdown value={String(hops)} disabled={!sid}
        onChange={value => setHops(clampHops(Number(value)))}
        options={HOP_CHOICES.map(choice => ({ value: String(choice), label: String(choice) }))}/>
    </label>
  </div>

  // What this map is measured from. The branch scope says which checkout and
  // against what, because "since <sha>" is not enough to tell one worktree of
  // several apart — and that is exactly when a reader has several open.
  const measuredFrom = data
    ? checkoutNote(data) || (data.baseline_head ? `since ${data.baseline_head.slice(0, 8)}` : 'no baseline commit')
    : 'no baseline commit'
  const header = <header class="change-map-header">
    <div>
      <strong>Change Map</strong>
      <small>{measuredFrom}</small>
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
    // Only the automation case has a switch behind it. `unsupported` needs a different
    // daemon build and `no_project` needs a Project, so neither gets a link that would
    // land on a control that cannot fix them.
    const gated = data.disabled_reason === 'automation_disabled' && !!projectId
    return <section class="change-map-pane">
      {header}
      {gated
        ? <GrantGate ids={['project.codeGraph']} projectId={projectId} heading={note}
            onGranted={() => void load()}>
            <p class="change-map-off-hint">{hint}</p>
          </GrantGate>
        : <div class="change-map-off">
            <p>{note}</p>
            <p class="change-map-off-hint">{hint}</p>
            {!projectId && <p class="change-map-off-hint">This session is not attached to a registered Project.</p>}
          </div>}
      {footer}
    </section>
  }

  const excluded = excludedNote(data.excluded)

  if (!nodes.length) return <section class="change-map-pane">
    {header}
    {controls}
    <div class="change-map-off">
      {/* "Wrote nothing mappable" is a different answer from "wrote nothing", and
          a session that only touched scratch files deserves the first one. */}
      <p>{data.empty_reason === 'excluded'
        ? 'Every source file edited here sits outside the indexed project tree.'
        : data.scope === 'branch'
          ? 'This branch has not changed a source file the index covers.'
          : data.empty_reason === 'no_edits'
            ? 'No source edits in this session yet.'
            : 'Nothing to map for this session yet.'}</p>
      {excluded
        ? <p class="change-map-off-hint">{excluded}</p>
        : <p class="change-map-off-hint">The map fills in on the turn after this session writes a source file.</p>}
    </div>
    {footer}
  </section>

  const legend = data.scope === 'project' && data.sessions.length
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
  // Never `node.path`: that is a casefolded graph identity, and opening it would
  // both fail outright on a case-sensitive host and, where it did not, claim a
  // second pane identity for a file the Files browser already has open.
  const openFile = (node: ChangeMapNode) =>
    node.display_path && onOpenFile?.(node.display_path, data.worktree)
  // Mobile only: on desktop the graph already draws this, and computing it there
  // would be work whose result is never rendered.
  const neighbors = mobile && selected ? neighborNodes(selected.path, links, nodes) : []
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
    {/* Without this, an unindexed file's empty neighbourhood reads as "nothing
        depends on it" — a conclusion, where the truth is an absence of data. */}
    {selected.indexed === false && <small class="change-map-unindexed">
      {UNINDEXED_MARK} not in the code index yet — new on this branch, so nothing
      here can link to it until it lands.
    </small>}
    {/* The graph draws this; a list cannot, so the neighbours are spelled out where
        there is no picture to read them from. */}
    {!!neighbors.length && <ul class="change-map-neighbors" aria-label="Linked files">
      {neighbors.map(node => <li key={node.path}>
        <button type="button" class={`change-map-role role-${node.role}`}
          onClick={() => setSelectedPath(node.path)}>
          <b aria-hidden="true" />
          <code>{markUnindexed(node, node.path)}</code>
        </button>
      </li>)}
    </ul>}
    <div class="change-map-detail-actions">
      {onOpenFile && <button type="button" class="change-map-open"
        disabled={!selected.display_path}
        title={selected.display_path
          ? `Open ${selected.display_path} as a workspace tab`
          : 'This file no longer exists in the checkout'}
        onClick={() => openFile(selected)}>open file</button>}
      <button type="button" onClick={() => setSelectedPath(null)}>clear</button>
    </div>
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
              <code>{markUnindexed(node, node.path)}</code>
              <span>{degrees[node.path] || 0}</span>
            </button>
          </li>)}
        </ul>
      </section>)}
    </div>
    : <div class="change-map-stage">
      <div class="change-map-canvas" ref={containerRef} role="img"
        aria-label={`Change map: ${nodes.length} files, ${edges.length} dependencies`} />
      {(loading || layoutPending) && <div class="change-map-busy" role="status">
        <span class="change-map-spinner" aria-hidden="true" />
        <span>{loading ? 'Loading change map…' : `Laying out ${nodes.length} nodes…`}</span>
      </div>}
    </div>

  // The captions a reader needs to read the picture correctly: what the node cap
  // dropped, what the indexer's admission rules dropped, which nodes the index has
  // never seen, whether a branch request could not be served, and the "seeds but no
  // links" case. Without them, a ring of disconnected dots — or a missing file you
  // know you changed — is a puzzle.
  const unindexed = unindexedNote(unindexedCount(nodes))
  const status = <div class="change-map-status">
    {data.truncated && data.totals && <small class="change-map-truncated">
      Showing {data.totals.shown} of {data.totals.blast + data.totals.context + nodes.filter(n => n.role === 'seed').length} reachable files — blast radius capped for legibility.
    </small>}
    {data.checkout?.truncated && <small class="change-map-truncated">
      This branch changes more files than the map will seed — showing the first of them.
    </small>}
    {data.scope_fallback === 'no_comparison_base' && <small class="change-map-truncated">
      This checkout has no comparison base, so the branch view is unavailable — showing this session's own edits.
    </small>}
    {!!excluded && <small class="change-map-note-inline">{excluded}</small>}
    {!!unindexed && <small class="change-map-note-inline">{unindexed}</small>}
    {!mobile && nodes.length > 0 && edges.length === 0 && <small class="change-map-note-inline">
      No dependency links found for these files. Nothing in the indexed project tree imports or calls them yet.
    </small>}
  </div>

  return <section class="change-map-pane">
    {header}
    {controls}
    {legend}
    {status}
    {error && <p class="usage-error">{error}</p>}
    {renderError && <p class="usage-error">The graph could not be drawn: {renderError}</p>}
    {body}
    {detail}
    {footer}
  </section>
}
