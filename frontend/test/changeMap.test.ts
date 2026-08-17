import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  DEFAULT_ROLE_PALETTE, LAYOUT_ITERATIONS, MAX_HOPS, ROLE_ORDER,
  adjacency, clampHops, disabledNote, edgeCounts, excludedNote, focusedPath, graphColor,
  groupNodesByRole, hslToHex, layoutRequest, mixHex, neighborNodes, neighborhood, nodeColor,
  nodeSize, seedPositions, shortPath, usablePositions,
  type ChangeMapEdge, type ChangeMapNode,
} from '../src/changeMap.ts'
import { changeMapLeafId, changeMapLeafSessionId, emptyLayout, leaves, openTab, parseLayout, paneStacks, removeLeaf, resourceLeaf, terminalLeaf } from '../src/layout.ts'

const node = (path: string, role: ChangeMapNode['role'], extra: Partial<ChangeMapNode> = {}): ChangeMapNode =>
  ({ path, role, ...extra })

test('session hues are converted before they reach the renderer', () => {
  // Sigma's colour parser understands `#hex` and `rgb()` and returns *black* for
  // anything else, and the daemon issues session hues as `hsl(...)`. Without this the
  // unify legend renders as N identical black dots, which is the one failure mode that
  // makes unify mode worse than useless.
  assert.equal(hslToHex('hsl(0, 70%, 55%)'), '#dd3c3c')
  assert.equal(hslToHex('hsl(120, 70%, 55%)'), '#3cdd3c')
  assert.equal(hslToHex('hsl(240, 70%, 55%)'), '#3c3cdd')
  assert.equal(hslToHex('hsl(0, 0%, 100%)'), '#ffffff')
  assert.equal(hslToHex('hsl(0, 0%, 0%)'), '#000000')
  // Space-separated and out-of-range hues are still CSS, and still have to survive.
  assert.equal(hslToHex('hsl(480 70% 55%)'), hslToHex('hsl(120, 70%, 55%)'))
  assert.equal(hslToHex('#f07178'), null)

  // Everything else passes through or falls back; nothing silently becomes black.
  assert.equal(graphColor('#f07178', '#000000'), '#f07178')
  assert.equal(graphColor('#abc', '#000000'), '#abc')
  assert.equal(graphColor('rgb(1, 2, 3)', '#000000'), 'rgb(1, 2, 3)')
  assert.equal(graphColor('hsl(240, 70%, 55%)', '#000000'), '#3c3cdd')
  assert.equal(graphColor('  ', '#123456'), '#123456')
  assert.equal(graphColor('chartreuse', '#123456'), '#123456')
  assert.equal(graphColor(undefined, '#123456'), '#123456')
  // A theme variable read back with surrounding whitespace is the normal case.
  assert.equal(graphColor(' #72a7ff ', '#000000'), '#72a7ff')
})

test('role colour and size rank the three roles the same way', () => {
  const palette = DEFAULT_ROLE_PALETTE
  assert.equal(nodeColor(node('a.ts', 'seed'), palette), palette.seed)
  assert.equal(nodeColor(node('b.ts', 'blast', { hop: 1 }), palette), palette.blast)
  assert.equal(nodeColor(node('c.ts', 'context'), palette), palette.context)
  // Unify overrides seeds only: "what this reaches" does not become a per-session
  // question just because several sessions are on screen.
  assert.equal(nodeColor(node('a.ts', 'seed', { hue: 'hsl(120, 70%, 55%)' }), palette), '#3cdd3c')
  assert.equal(nodeColor(node('b.ts', 'blast', { hue: 'hsl(120, 70%, 55%)' }), palette), palette.blast)

  assert.ok(nodeSize(node('a.ts', 'seed')) > nodeSize(node('b.ts', 'blast', { hop: 1 })))
  assert.ok(nodeSize(node('b.ts', 'blast', { hop: 1 })) > nodeSize(node('c.ts', 'context')))
  // Further out is smaller, but never invisible.
  assert.ok(nodeSize(node('b.ts', 'blast', { hop: 1 })) > nodeSize(node('d.ts', 'blast', { hop: 3 })))
  assert.ok(nodeSize(node('d.ts', 'blast', { hop: 9 })) >= 4)
})

test('the hops selector never offers more than the daemon honours', () => {
  assert.equal(clampHops(0), 1)
  assert.equal(clampHops(-3), 1)
  assert.equal(clampHops(9), MAX_HOPS)
  assert.equal(clampHops(Number.NaN), 1)
  assert.equal(clampHops(2.4), 2)
})

test('the mobile projection groups by role and drops empty roles', () => {
  const nodes = [node('z.ts', 'context'), node('a.ts', 'seed'), node('m.ts', 'seed')]
  const groups = groupNodesByRole(nodes)
  assert.deepEqual(groups.map(group => group.role), ['seed', 'context'])
  assert.deepEqual(groups[0].nodes.map(item => item.path), ['a.ts', 'm.ts'])
  assert.deepEqual(groupNodesByRole([]), [])
  assert.deepEqual(ROLE_ORDER, ['seed', 'blast', 'context'])
})

test('degree counts each connected pair once, in both directions', () => {
  const edges: ChangeMapEdge[] = [
    { source: 'a.ts', target: 'core.ts', kind: 'imports' },
    { source: 'b.ts', target: 'core.ts', kind: 'calls' },
  ]
  assert.deepEqual(edgeCounts(edges), { 'a.ts': 1, 'b.ts': 1, 'core.ts': 2 })
  assert.deepEqual(edgeCounts([]), {})
  // A file that both imports and calls another is one line on the graph, so reporting
  // two links would name a relationship the picture does not draw.
  assert.deepEqual(edgeCounts([
    { source: 'a.ts', target: 'core.ts', kind: 'imports' },
    { source: 'a.ts', target: 'core.ts', kind: 'calls' },
    { source: 'core.ts', target: 'a.ts', kind: 'calls' },
    { source: 'a.ts', target: 'a.ts', kind: 'calls' },
  ]), { 'a.ts': 1, 'core.ts': 1 })
})

test('adjacency is undirected and drops self-loops', () => {
  // The map draws one line per connected pair, and "what is this file wired to" has
  // no direction: the importers above a node matter as much as the imports below it.
  const links = adjacency([
    { source: 'a.ts', target: 'core.ts', kind: 'imports' },
    { source: 'a.ts', target: 'core.ts', kind: 'calls' },
    { source: 'core.ts', target: 'util.ts', kind: 'imports' },
    { source: 'lonely.ts', target: 'lonely.ts', kind: 'calls' },
  ])
  assert.deepEqual([...(links.get('a.ts') || [])], ['core.ts'])
  assert.deepEqual([...(links.get('core.ts') || [])].sort(), ['a.ts', 'util.ts'])
  assert.deepEqual([...(links.get('util.ts') || [])], ['core.ts'])
  assert.equal(links.has('lonely.ts'), false)
})

test('the highlight set is the focused node plus its links', () => {
  const links = adjacency([
    { source: 'a.ts', target: 'core.ts', kind: 'imports' },
    { source: 'b.ts', target: 'core.ts', kind: 'imports' },
    { source: 'far.ts', target: 'other.ts', kind: 'imports' },
  ])
  assert.deepEqual([...(neighborhood('core.ts', links) || [])].sort(), ['a.ts', 'b.ts', 'core.ts'])
  // Null and empty are different renderings, and the difference is the whole point:
  // null draws the map normally, while a focused node with no links must still dim
  // everything else — that is exactly the reading that says a file is unreferenced.
  assert.equal(neighborhood(null, links), null)
  assert.deepEqual([...(neighborhood('orphan.ts', links) || [])], ['orphan.ts'])
})

test('a hover previews on top of the selection and falls back to it', () => {
  assert.equal(focusedPath('hovered.ts', 'picked.ts'), 'hovered.ts')
  assert.equal(focusedPath(null, 'picked.ts'), 'picked.ts')
  assert.equal(focusedPath('hovered.ts', null), 'hovered.ts')
  assert.equal(focusedPath(null, null), null)
})

test('a focused file lists its links by role, then path', () => {
  const nodes = [
    node('src/core.ts', 'seed'),
    node('src/z.ts', 'blast', { hop: 1 }),
    node('src/a.ts', 'blast', { hop: 1 }),
    node('src/dep.ts', 'context'),
    node('src/unrelated.ts', 'context'),
  ]
  const links = adjacency([
    { source: 'src/z.ts', target: 'src/core.ts', kind: 'imports' },
    { source: 'src/a.ts', target: 'src/core.ts', kind: 'calls' },
    { source: 'src/core.ts', target: 'src/dep.ts', kind: 'imports' },
  ])
  assert.deepEqual(neighborNodes('src/core.ts', links, nodes).map(item => item.path),
    ['src/a.ts', 'src/z.ts', 'src/dep.ts'])
  assert.deepEqual(neighborNodes('src/unrelated.ts', links, nodes), [])
  assert.deepEqual(neighborNodes(null, links, nodes), [])
})

test('what the map refused to draw is stated, not silently dropped', () => {
  assert.equal(excludedNote({ outside_root: 0, unindexable: 0 }), '')
  assert.equal(excludedNote(undefined), '')
  const outside = excludedNote({ outside_root: 1, unindexable: 0 })
  assert.match(outside, /^1 edited file not shown \(1 outside this checkout\)/)
  const both = excludedNote({ outside_root: 2, unindexable: 3 })
  assert.match(both, /^5 edited files not shown/)
  assert.match(both, /2 outside this checkout, 3 in a generated, vendored, or hidden directory/)
})

test('dimming mixes toward the background rather than toward a fixed grey', () => {
  // Sigma's WebGL parser has no opacity to fall back on: the colour it is handed is
  // the colour it draws, and a fixed grey is either invisible or louder than the
  // highlight across the eleven themes this app ships.
  assert.equal(mixHex('#ffffff', '#000000', 0.5), '#808080')
  assert.equal(mixHex('#f07178', '#090b0e', 0), '#f07178')
  assert.equal(mixHex('#f07178', '#090b0e', 1), '#090b0e')
  assert.equal(mixHex('#fff', '#000', 0.5), '#808080')
  // Clamped, and non-hex passes through untouched rather than being mangled.
  assert.equal(mixHex('#ffffff', '#000000', 5), '#000000')
  assert.equal(mixHex('rgba(1,2,3,0.5)', '#000000', 0.5), 'rgba(1,2,3,0.5)')
})

test('labels shorten to the last two segments', () => {
  assert.equal(shortPath('src/swe_mux/server.py'), 'swe_mux/server.py')
  assert.equal(shortPath('api.ts'), 'api.ts')
  assert.equal(shortPath('src/api.ts'), 'src/api.ts')
})

test('every disabled reason gets a note and a way out', () => {
  for (const reason of ['unsupported', 'no_project', 'automation_disabled'] as const) {
    const { note, hint } = disabledNote(reason)
    assert.ok(note.length > 0, reason)
    assert.ok(hint.length > 0, reason)
  }
  // An unknown reason (or none) must still read as an explanation, not a blank pane.
  assert.ok(disabledNote(null).note.length > 0)
  assert.match(disabledNote('automation_disabled').hint, /Code-structure graph/)
})

test('seeded positions are finite, distinct, and stratified by role', () => {
  const nodes = [
    node('a.ts', 'seed'), node('b.ts', 'seed'),
    node('c.ts', 'blast', { hop: 1 }), node('d.ts', 'blast', { hop: 2 }),
    node('e.ts', 'context'),
  ]
  const positions = seedPositions(nodes)
  const radius = (path: string) => Math.hypot(positions[path].x, positions[path].y)
  for (const item of nodes) {
    assert.ok(Number.isFinite(positions[item.path].x), item.path)
    assert.ok(Number.isFinite(positions[item.path].y), item.path)
  }
  // ForceAtlas2 converges to very different pictures from different starts, and seeds
  // in the middle with their dependents around them is the reading the map is for.
  assert.ok(radius('a.ts') < radius('c.ts'))
  assert.ok(radius('c.ts') < radius('e.ts'))
  // Coincident nodes are a degenerate FA2 start; a single-node ring must still place.
  assert.notDeepEqual(positions['a.ts'], positions['b.ts'])
  assert.deepEqual(Object.keys(seedPositions([node('only.ts', 'seed')])), ['only.ts'])
})

test('the worker payload drops self-loops, duplicates, and dangling edges', () => {
  // graphology throws on a duplicate edge in a non-multi graph rather than ignoring it,
  // so `imports` plus `calls` between the same pair has to collapse before it is posted.
  const nodes = [node('a.ts', 'seed'), node('b.ts', 'blast', { hop: 1 })]
  const request = layoutRequest(7, nodes, [
    { source: 'a.ts', target: 'b.ts', kind: 'imports' },
    { source: 'a.ts', target: 'b.ts', kind: 'calls' },
    { source: 'a.ts', target: 'a.ts', kind: 'calls' },
    { source: 'a.ts', target: 'gone.ts', kind: 'imports' },
  ])
  assert.equal(request.requestId, 7)
  assert.equal(request.iterations, LAYOUT_ITERATIONS)
  assert.deepEqual(request.edges, [{ source: 'a.ts', target: 'b.ts' }])
  assert.deepEqual(request.nodes.map(item => item.id), ['a.ts', 'b.ts'])
  for (const item of request.nodes) {
    assert.ok(Number.isFinite(item.x) && Number.isFinite(item.y), item.id)
    assert.ok(item.size > 0, item.id)
  }
})

test('a stale or diverged layout answer is discarded rather than drawn', () => {
  const nodes = [node('a.ts', 'seed'), node('b.ts', 'context')]
  const good = { requestId: 3, positions: { 'a.ts': { x: 1, y: 2 }, 'b.ts': { x: 3, y: 4 } } }
  assert.deepEqual(usablePositions(good, 3, nodes), good.positions)
  // An answer to the previous map must not land on the current one.
  assert.equal(usablePositions(good, 4, nodes), null)
  assert.equal(usablePositions(null, 3, nodes), null)
  // A diverged layout produces NaN, which would erase a readable seeded ring.
  assert.equal(usablePositions({ requestId: 3, positions: { 'a.ts': { x: Number.NaN, y: 0 }, 'b.ts': { x: 1, y: 1 } } }, 3, nodes), null)
  // A partial answer is not usable either: half the graph would stack on the origin.
  assert.equal(usablePositions({ requestId: 3, positions: { 'a.ts': { x: 1, y: 1 } } }, 3, nodes), null)
})

test('a change-map pane leaf survives a layout round trip', () => {
  const resourceId = changeMapLeafId('sess-a')
  assert.equal(resourceId, 'changemap:sess-a')
  assert.equal(changeMapLeafSessionId(resourceId), 'sess-a')
  assert.equal(changeMapLeafSessionId('queue:sess-a'), null)
  assert.equal(changeMapLeafSessionId('changemap:'), null)
  // The prefix is what stops a session's map colliding with its own terminal leaf,
  // which focus tracking resolves by bare id.
  assert.notEqual(resourceId, 'sess-a')
  // Ids that need encoding round-trip exactly.
  assert.equal(changeMapLeafSessionId(changeMapLeafId('a b/c')), 'a b/c')

  const opened = openTab(openTab(emptyLayout(), null, terminalLeaf('sess-a')), 'sess-a', resourceLeaf('changemap', resourceId))
  assert.deepEqual(leaves(opened).map(leaf => leaf.kind), ['terminal', 'changemap'])
  assert.equal(paneStacks(opened)[0].active_child_id, resourceId)

  // A persisted layout carrying one must parse back rather than being pruned as an
  // unknown kind - the client and `layouts.py` have to agree on the leaf kind set.
  const persisted = parseLayout(JSON.parse(JSON.stringify(opened)))
  assert.deepEqual(leaves(persisted).map(leaf => leaf.id), ['sess-a', resourceId])
  assert.deepEqual(leaves(persisted, 'changemap').map(leaf => leaf.id), [resourceId])
  assert.deepEqual(leaves(removeLeaf(persisted, 'changemap', resourceId)).map(leaf => leaf.id), ['sess-a'])
})

test('the daemon accepts the same leaf kind the client persists', () => {
  // The Python validator is the other half of the round trip above: a kind it does not
  // know is stripped server-side, and the popped-out tab silently disappears on reload.
  const layouts = readFileSync(join(import.meta.dirname, '..', '..', 'src', 'swe_mux', 'layouts.py'), 'utf8')
  assert.match(layouts, /LEAF_KINDS = \{[^}]*"changemap"[^}]*\}/)
})

test('the layout worker is bundled, not built from a blob URL', () => {
  // The app's CSP is `script-src 'self' 'wasm-unsafe-eval'` with no `worker-src` and no
  // `blob:`, so graphology's stock `/worker` helper - which constructs its worker from a
  // blob URL - is refused outright. A Vite module worker is same-origin and allowed.
  const pane = readFileSync(join(import.meta.dirname, '..', 'src', 'ChangeMapPane.tsx'), 'utf8')
  const worker = readFileSync(join(import.meta.dirname, '..', 'src', 'changeMapLayout.worker.ts'), 'utf8')
  assert.ok(pane.includes("new Worker(new URL('./changeMapLayout.worker.ts', import.meta.url), { type: 'module' })"))
  assert.doesNotMatch(pane, /createObjectURL/)
  assert.doesNotMatch(worker, /from ['"]graphology-layout-forceatlas2\/worker['"]/)
  assert.doesNotMatch(worker, /createObjectURL/)
})
