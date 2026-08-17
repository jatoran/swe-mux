// Pure helpers behind the Change Map (Phase 7.9): the daemon's response shape, the
// role palette, the layout worker's protocol, and the geometry that worker starts
// from. JSX-free and DOM-free so it runs under plain `node --experimental-strip-types`
// and can also be imported by the module worker, which has no DOM at all.
//
// The three roles are the whole legend. Red seeds are the source files *this* session
// wrote; yellow is their blast radius (what imports or calls them, so what this change
// can break); blue is the immediate forward context (what the seeds themselves import).
// Unify mode replaces the red with one hue per session, because then the question is
// "who touched what", not "what did my change reach".

export type ChangeMapRole = 'seed' | 'blast' | 'context'
export type ChangeMapEdgeKind = 'imports' | 'calls'
export type ChangeMapDisabledReason = 'unsupported' | 'no_project' | 'automation_disabled'

export type ChangeMapNode = {
  path: string
  role: ChangeMapRole
  /** Blast-radius distance from the nearest seed. Absent on seeds and context. */
  hop?: number
  /** Seeds only: which sessions wrote this file. */
  sessions?: string[]
  /** Unify mode, seeds only: the writing session's legend colour. */
  hue?: string
}
export type ChangeMapEdge = { source: string; target: string; kind: ChangeMapEdgeKind }
export type ChangeMapSession = { id: string; name: string; hue: string }

export type ChangeMap = {
  session_id: string
  project_id: string | null
  baseline_head: string | null
  /** False means the graph cannot be built at all; `disabled_reason` says why. */
  available: boolean
  disabled_reason: ChangeMapDisabledReason | null
  /** Present with `available: true` when the session has simply not edited source yet. */
  empty_reason?: string
  nodes: ChangeMapNode[]
  edges: ChangeMapEdge[]
  /** Legend, populated only in unify mode. */
  sessions: ChangeMapSession[]
  /** The blast radius overflowed the node cap; `totals` says by how much. */
  truncated?: boolean
  totals?: { shown: number; blast: number; context: number }
  excludes_note: string
  lower_bound_note: string
}

/** The daemon clamps `hops` to this; the selector must not offer more than it honours. */
export const MAX_HOPS = 4
export const HOP_CHOICES: number[] = [1, 2, 3, 4]
export const clampHops = (value: number): number =>
  !Number.isFinite(value) ? 1 : Math.max(1, Math.min(MAX_HOPS, Math.round(value)))

/** ForceAtlas2 passes the worker runs. High enough to untangle a few hundred nodes,
 *  low enough that the pane is never blank for long — the seeded ring renders first
 *  and this only improves it. */
export const LAYOUT_ITERATIONS = 220

// --------------------------------------------------------------------------- //
// Worker protocol                                                              //
// --------------------------------------------------------------------------- //

export type LayoutNode = { id: string; x: number; y: number; size: number }
export type LayoutRequest = {
  requestId: number
  nodes: LayoutNode[]
  edges: { source: string; target: string }[]
  iterations: number
}
export type LayoutPositions = Record<string, { x: number; y: number }>
export type LayoutResult = { requestId: number; positions: LayoutPositions }

// --------------------------------------------------------------------------- //
// Palette                                                                      //
// --------------------------------------------------------------------------- //

export type RolePalette = Record<ChangeMapRole, string>

/** Theme-independent fallbacks, used verbatim in a worker/test context and only when
 *  the document has not defined the matching custom property. */
export const DEFAULT_ROLE_PALETTE: RolePalette = {
  seed: '#f07178',
  blast: '#e7c768',
  context: '#72a7ff',
}

export const ROLE_ORDER: ChangeMapRole[] = ['seed', 'blast', 'context']
export const ROLE_LABELS: Record<ChangeMapRole, string> = {
  seed: 'Edited here',
  blast: 'Blast radius',
  context: 'Imports',
}
export const ROLE_DESCRIPTIONS: Record<ChangeMapRole, string> = {
  seed: 'Source files this session wrote since the baseline commit.',
  blast: 'Files that import or call an edited file, so what this change can reach.',
  context: 'What the edited files themselves import, one hop out.',
}

const HEX = (value: number): string => Math.round(value).toString(16).padStart(2, '0')

/** `hsl(h, s%, l%)` to `#rrggbb`.
 *
 *  Sigma's own colour parser understands `#hex` and `rgb()/rgba()` and *silently
 *  returns black for anything else*, so the daemon's `hsl(...)` session hues have to
 *  be converted before they ever reach a node attribute. A unify legend rendering as
 *  twelve identical black dots is the failure this exists to prevent. */
export function hslToHex(value: string): string | null {
  const match = /^hsla?\(\s*([-\d.]+)(?:deg)?\s*[, ]\s*([\d.]+)%\s*[, ]\s*([\d.]+)%/i.exec(value.trim())
  if (!match) return null
  const hue = ((Number(match[1]) % 360) + 360) % 360
  const saturation = Math.max(0, Math.min(100, Number(match[2]))) / 100
  const lightness = Math.max(0, Math.min(100, Number(match[3]))) / 100
  if (!Number.isFinite(hue) || !Number.isFinite(saturation) || !Number.isFinite(lightness)) return null
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation
  const second = chroma * (1 - Math.abs(((hue / 60) % 2) - 1))
  const base = lightness - chroma / 2
  const sextant = Math.floor(hue / 60) % 6
  const rgb = [
    [chroma, second, 0], [second, chroma, 0], [0, chroma, second],
    [0, second, chroma], [second, 0, chroma], [chroma, 0, second],
  ][sextant]
  return `#${rgb.map(channel => HEX((channel + base) * 255)).join('')}`
}

/** Any CSS colour the app can produce, reduced to something Sigma's WebGL parser
 *  actually understands. Unrecognised values fall back rather than render black. */
export function graphColor(value: string | undefined | null, fallback: string): string {
  const raw = (value || '').trim()
  if (!raw) return fallback
  if (raw.startsWith('#')) return raw.length === 4 || raw.length === 7 || raw.length === 9 ? raw : fallback
  if (/^rgba?\(/i.test(raw)) return raw
  return hslToHex(raw) || fallback
}

/** The colour a node is drawn in. Unify mode overrides seeds with their writer's hue;
 *  blast and context keep their meaning in both modes, because "what this reaches" does
 *  not become a per-session question just because several sessions are on screen. */
export function nodeColor(node: ChangeMapNode, palette: RolePalette): string {
  if (node.role === 'seed' && node.hue) return graphColor(node.hue, palette.seed)
  return palette[node.role] || DEFAULT_ROLE_PALETTE[node.role]
}

/** Seeds read first, blast next, context last — size carries the same ranking the
 *  colour does, so the picture survives being seen without the legend. */
export function nodeSize(node: ChangeMapNode): number {
  if (node.role === 'seed') return 10
  if (node.role === 'blast') return Math.max(4, 7 - (node.hop || 1))
  return 3.5
}

// --------------------------------------------------------------------------- //
// Presentation                                                                 //
// --------------------------------------------------------------------------- //

/** Last two path segments: enough to tell `api.ts` from `queue/api.ts` on a graph
 *  label without spending the width a full repository path costs. */
export function shortPath(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.length <= 2 ? path : parts.slice(-2).join('/')
}

export type RoleGroup = { role: ChangeMapRole; label: string; nodes: ChangeMapNode[] }

/** The mobile projection: the same three roles, as lists rather than a graph.
 *  Empty roles are dropped, so an all-seed map reads as one list rather than two
 *  empty headings. */
export function groupNodesByRole(nodes: ChangeMapNode[]): RoleGroup[] {
  return ROLE_ORDER
    .map(role => ({
      role,
      label: ROLE_LABELS[role],
      nodes: nodes.filter(node => node.role === role).sort((left, right) => left.path.localeCompare(right.path)),
    }))
    .filter(group => group.nodes.length > 0)
}

/** Degree per node, both directions, counting each connected *pair* once.
 *
 *  A file that both imports and calls another is one relationship on screen — the graph
 *  draws one line for it — so counting the two edge kinds separately would report a
 *  number the picture does not show. Drives the detail card and the list's link column:
 *  a seed nothing depends on is a very different read from one twelve files depend on. */
export function edgeCounts(edges: ChangeMapEdge[]): Record<string, number> {
  const counts: Record<string, number> = {}
  const seen = new Set<string>()
  for (const edge of edges) {
    const key = edge.source < edge.target
      ? `${edge.source} ${edge.target}`
      : `${edge.target} ${edge.source}`
    if (edge.source === edge.target || seen.has(key)) continue
    seen.add(key)
    counts[edge.source] = (counts[edge.source] || 0) + 1
    counts[edge.target] = (counts[edge.target] || 0) + 1
  }
  return counts
}

const DISABLED_NOTES: Record<ChangeMapDisabledReason, string> = {
  unsupported: 'This daemon has no code-structure index, so there is no change map to draw.',
  no_project: 'This session has no registered Project, and the map is built per Project.',
  automation_disabled: 'The “Code-structure graph” automation is off for this Project.',
}
const DISABLED_HINTS: Record<ChangeMapDisabledReason, string> = {
  unsupported: 'Restart the daemon on a build that carries the code graph.',
  no_project: 'Register this session’s working directory as a Project to build one.',
  automation_disabled: 'Turn on “Code-structure graph” in this Project’s settings and the map fills in on the next turn.',
}

export function disabledNote(reason: ChangeMapDisabledReason | null): { note: string; hint: string } {
  if (reason && reason in DISABLED_NOTES) return { note: DISABLED_NOTES[reason], hint: DISABLED_HINTS[reason] }
  return {
    note: 'The change map is unavailable for this session.',
    hint: 'Enable the “Code-structure graph” automation for this Project to build one.',
  }
}

// --------------------------------------------------------------------------- //
// Layout seeding                                                               //
// --------------------------------------------------------------------------- //

const RING_RADIUS: Record<ChangeMapRole, number> = { seed: 12, blast: 42, context: 78 }

/**
 * Deterministic starting positions, stratified by role.
 *
 * Two jobs. ForceAtlas2 refuses a graph whose nodes carry no `x`/`y`, and it converges
 * to very different pictures from different starts — seeds in the middle with their
 * dependents around them is the reading the map is for, so the layout starts already
 * arranged that way instead of hoping the forces discover it.
 *
 * It is also the render that ships first: the pane draws these immediately and swaps in
 * the worker's result when it arrives, so a slow layout (or a worker that never starts,
 * on a browser that blocks it) still leaves a legible graph rather than a blank canvas.
 */
export function seedPositions(nodes: ChangeMapNode[]): LayoutPositions {
  const positions: LayoutPositions = {}
  for (const role of ROLE_ORDER) {
    const ring = nodes.filter(node => node.role === role)
    const radius = RING_RADIUS[role]
    ring.forEach((node, index) => {
      // The half-turn offset keeps successive rings from lining up on one spoke,
      // which is what makes an unlaid-out map look like a single crowded line.
      const angle = (2 * Math.PI * index) / Math.max(1, ring.length) + (radius / 100)
      positions[node.path] = { x: radius * Math.cos(angle), y: radius * Math.sin(angle) }
    })
  }
  return positions
}

/** The worker payload for one map: seeded geometry plus the deduplicated edge list. */
export function layoutRequest(
  requestId: number,
  nodes: ChangeMapNode[],
  edges: ChangeMapEdge[],
  iterations = LAYOUT_ITERATIONS,
): LayoutRequest {
  const seeded = seedPositions(nodes)
  const known = new Set(nodes.map(node => node.path))
  const seen = new Set<string>()
  const payloadEdges: { source: string; target: string }[] = []
  for (const edge of edges) {
    // `imports` and `calls` between the same pair are one line on screen, and a
    // duplicate edge in a non-multi graphology graph is a throw, not a no-op.
    const key = `${edge.source} ${edge.target}`
    if (edge.source === edge.target || seen.has(key)) continue
    if (!known.has(edge.source) || !known.has(edge.target)) continue
    seen.add(key)
    payloadEdges.push({ source: edge.source, target: edge.target })
  }
  return {
    requestId,
    nodes: nodes.map(node => ({ id: node.path, ...seeded[node.path], size: nodeSize(node) })),
    edges: payloadEdges,
    iterations,
  }
}

/** Accept a worker reply only when it answers the request still on screen and every
 *  coordinate is finite — a diverged layout must not replace a readable ring with NaN. */
export function usablePositions(
  result: LayoutResult | null,
  requestId: number,
  nodes: ChangeMapNode[],
): LayoutPositions | null {
  if (!result || result.requestId !== requestId || !result.positions) return null
  const positions: LayoutPositions = {}
  for (const node of nodes) {
    const point = result.positions[node.path]
    if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null
    positions[node.path] = point
  }
  return positions
}
