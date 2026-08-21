import { migratedDrawerSegment } from './drawerSegments.ts'
import { DRAWER_TABS, type DrawerTabId } from './drawerTabs.ts'
import { browserUuid } from './layout.ts'

export type DrawerSplitDirection = 'horizontal' | 'vertical'
export type DrawerEdge = 'left' | 'right' | 'top' | 'bottom'

export type DrawerStack = {
  type: 'stack'
  id: string
  tabs: DrawerTabId[]
}

export type DrawerSplit = {
  type: 'split'
  id: string
  direction: DrawerSplitDirection
  ratio: number
  first: DrawerNode
  second: DrawerNode
}

export type DrawerNode = DrawerStack | DrawerSplit

export type DrawerLayout = {
  version: 1
  root: DrawerNode
}

export type DrawerProjectPresentation = {
  selected_tabs: Record<string, DrawerTabId>
  /**
   * Which segment each segmented tab is showing, per Project.
   *
   * Keyed by tab rather than by stack: a tab lives in exactly one stack, and keying by
   * stack would lose the choice the moment the tab were dragged to another pane. Stored
   * loosely as `string` rather than validated against `drawerSegments.ts` on purpose —
   * this module stays the layout's own vocabulary, and an unknown id costs nothing
   * because `resolveDrawerSegment` falls back to the first available segment anyway.
   *
   * A *retired* id is the one case that fallback is not good enough for, and it is
   * migrated on read (`migratedDrawerSegment`). Falling back would land a reader who had
   * Land selected on Git's first segment, which is Map today by coincidence and would
   * stop being the right answer the moment the order changed.
   */
  selected_segments: Record<string, string>
  focused_tab: DrawerTabId
  desktop_expanded: boolean
}

export type DrawerProjectPresentationMap = Readonly<Record<string, DrawerProjectPresentation>>

export const DRAWER_LAYOUT_KEY = 'mux.drawer.layout.v1'
// v3 adds `selected_segments`. The key is versioned rather than the payload because a v2
// reader handed a v3 record would drop the segment silently, and a v3 reader handed a v2
// record is exactly the migration below — which is also where a retired tab id becomes the
// tab *and* segment that replaced it, so a saved `context` selection lands on
// Agent → Instructions rather than on Agent's first segment.
export const DRAWER_PROJECT_PRESENTATIONS_KEY = 'mux.drawer.projects.v3'
export const DRAWER_PROJECT_PRESENTATIONS_KEY_V2 = 'mux.drawer.projects.v2'
export const DRAWER_MAX_DEPTH = 24
export const DRAWER_MIN_RATIO = 0.1
export const DRAWER_MAX_RATIO = 0.9

const registeredIds = (): DrawerTabId[] => DRAWER_TABS.map(tab => tab.id)
const isDrawerTab = (value: string): value is DrawerTabId =>
  DRAWER_TABS.some(tab => tab.id === value)
const nodeId = (kind: 'stack' | 'split'): string => `drawer-${kind}-${browserUuid().slice(0, 12)}`
const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)
/**
 * Where a stored tab id lands now, tab *and* segment.
 *
 * Every consolidation the drawer has been through is one row here, and each must stay
 * forever: a saved arrangement is device-local and can be arbitrarily old, and an
 * unrecognised id is dropped rather than repaired, which silently loses whichever pane the
 * user had dragged it into.
 *
 * The `segment` half is what makes a merge non-destructive. Folding Context into Agent
 * without it would land every reader who had Context selected on Agent's *first* segment,
 * which is a different surface than the one they chose.
 */
export function migratedTabTarget(value: unknown): { tab: DrawerTabId; segment?: string } | null {
  // Phase 4 folded two catalogs into the Actions tab.
  if (value === 'commands' || value === 'prompts') return { tab: 'actions' }
  // Phase 7.10 folded the standalone Timeline tab into Insight as a segment.
  if (value === 'timeline') return { tab: 'activity', segment: 'timeline' }
  // The drawer consolidation: Insight was renamed, and three tabs became segments or a
  // section of their neighbours.
  if (value === 'insight') return { tab: 'activity' }
  if (value === 'changemap') return { tab: 'activity', segment: 'changes' }
  if (value === 'context') return { tab: 'agent', segment: 'instructions' }
  // Clipboard became a *section* of Actions, not a segment, so there is no segment to
  // restore — arriving on Actions is arriving, and the section is already on screen.
  if (value === 'clipboard') return { tab: 'actions' }
  return typeof value === 'string' && DRAWER_TABS.some(tab => tab.id === value)
    ? { tab: value as DrawerTabId }
    : null
}

const migratedTabId = (value: unknown): DrawerTabId | null => migratedTabTarget(value)?.tab ?? null

export function clampDrawerRatio(value: number): number {
  if (!Number.isFinite(value)) return 0.5
  return Math.max(DRAWER_MIN_RATIO, Math.min(DRAWER_MAX_RATIO, Math.round(value * 10000) / 10000))
}

export function defaultDrawerLayout(order: readonly DrawerTabId[] = registeredIds()): DrawerLayout {
  const known = new Set(registeredIds())
  const tabs = order.filter((id, index) => known.has(id) && order.indexOf(id) === index)
  for (const id of registeredIds()) if (!tabs.includes(id)) tabs.push(id)
  return { version: 1, root: { type: 'stack', id: nodeId('stack'), tabs } }
}

function rawTabs(value: unknown, limit = 10000): DrawerTabId[] {
  const result: DrawerTabId[] = []
  const pending: unknown[] = [value]
  let visited = 0
  while (pending.length && visited < limit) {
    visited += 1
    const current = pending.pop()
    if (!isRecord(current)) continue
    if (current.type === 'stack' && Array.isArray(current.tabs)) {
      for (const value of current.tabs) {
        const tab = migratedTabId(value)
        if (tab && !result.includes(tab)) result.push(tab)
      }
    } else if (current.type === 'split') {
      pending.push(current.second, current.first)
    } else if ('root' in current) {
      pending.push(current.root)
    }
  }
  return result
}

type NormalizeContext = {
  seenTabs: Set<DrawerTabId>
  seenNodeIds: Set<string>
}

function normalizedId(raw: unknown, kind: 'stack' | 'split', context: NormalizeContext): string {
  const candidate = typeof raw === 'string' && raw.trim() ? raw : nodeId(kind)
  let result = candidate
  while (context.seenNodeIds.has(result)) result = nodeId(kind)
  context.seenNodeIds.add(result)
  return result
}

function normalizeNode(
  value: unknown,
  context: NormalizeContext,
  depth: number,
): DrawerNode | null {
  if (!isRecord(value)) return null
  if (depth >= DRAWER_MAX_DEPTH) {
    const tabs = rawTabs(value).filter(tab => {
      if (context.seenTabs.has(tab)) return false
      context.seenTabs.add(tab)
      return true
    })
    return tabs.length ? { type: 'stack', id: normalizedId(value.id, 'stack', context), tabs } : null
  }
  if (value.type === 'stack') {
    const tabs: DrawerTabId[] = []
    if (Array.isArray(value.tabs)) {
      for (const rawTab of value.tabs) {
        const tab = migratedTabId(rawTab)
        if (!tab || context.seenTabs.has(tab)) continue
        context.seenTabs.add(tab)
        tabs.push(tab)
      }
    }
    return tabs.length ? { type: 'stack', id: normalizedId(value.id, 'stack', context), tabs } : null
  }
  if (value.type !== 'split') return null
  const first = normalizeNode(value.first, context, depth + 1)
  const second = normalizeNode(value.second, context, depth + 1)
  if (!first) return second
  if (!second) return first
  return {
    type: 'split',
    id: normalizedId(value.id, 'split', context),
    direction: value.direction === 'vertical' ? 'vertical' : 'horizontal',
    ratio: clampDrawerRatio(typeof value.ratio === 'number' ? value.ratio : 0.5),
    first,
    second,
  }
}

function insertMissingTabs(root: DrawerNode, missing: readonly DrawerTabId[]): DrawerNode {
  let next = root
  const canonical = registeredIds()
  for (const id of missing) {
    const canonicalIndex = canonical.indexOf(id)
    const predecessor = canonical.slice(0, canonicalIndex).reverse().find(candidate => drawerStackForTab({ version: 1, root: next }, candidate))
    const target = predecessor
      ? drawerStackForTab({ version: 1, root: next }, predecessor)
      : drawerStacks({ version: 1, root: next })[0]
    if (!target) continue
    const insertion = predecessor ? target.tabs.indexOf(predecessor) + 1 : 0
    next = mapDrawerNode(next, node => node.type === 'stack' && node.id === target.id
      ? { ...node, tabs: [...node.tabs.slice(0, insertion), id, ...node.tabs.slice(insertion)] }
      : node)
  }
  return next
}

export function normalizeDrawerLayout(value: unknown, fallbackOrder: readonly DrawerTabId[] = registeredIds()): DrawerLayout {
  const rawRoot = isRecord(value) && 'root' in value ? value.root : value
  const context: NormalizeContext = { seenTabs: new Set(), seenNodeIds: new Set() }
  let root = normalizeNode(rawRoot, context, 0)
  if (!root) {
    const recovered = rawTabs(rawRoot)
    return defaultDrawerLayout(recovered.length ? recovered : fallbackOrder)
  }
  const missing = registeredIds().filter(id => !context.seenTabs.has(id))
  root = insertMissingTabs(root, missing)
  return { version: 1, root }
}

export function parseDrawerLayout(raw: string | null, fallbackOrder?: readonly DrawerTabId[]): DrawerLayout {
  if (!raw) return defaultDrawerLayout(fallbackOrder)
  try { return normalizeDrawerLayout(JSON.parse(raw), fallbackOrder) }
  catch { return defaultDrawerLayout(fallbackOrder) }
}

export function serializeDrawerLayout(layout: DrawerLayout): string {
  return JSON.stringify(normalizeDrawerLayout(layout))
}

export function drawerStacks(layout: DrawerLayout): DrawerStack[] {
  const result: DrawerStack[] = []
  const visit = (node: DrawerNode) => {
    if (node.type === 'stack') result.push(node)
    else { visit(node.first); visit(node.second) }
  }
  visit(layout.root)
  return result
}

export function drawerTabs(layout: DrawerLayout): DrawerTabId[] {
  return drawerStacks(layout).flatMap(stack => stack.tabs)
}

export function drawerStack(layout: DrawerLayout, stackId: string): DrawerStack | null {
  return drawerStacks(layout).find(stack => stack.id === stackId) ?? null
}

export function drawerStackForTab(layout: DrawerLayout, tab: DrawerTabId): DrawerStack | null {
  return drawerStacks(layout).find(stack => stack.tabs.includes(tab)) ?? null
}

/**
 * The stack occupying the drawer's top-right corner.
 *
 * The drawer collapses as a whole, so its collapse control belongs to the drawer rather
 * than to any one pane — but the drawer has no chrome of its own to hang it on, only pane
 * headings. This picks the single heading that sits where a close control is expected.
 * `horizontal` splits lay their branches out as a row (`second` is the right one) and
 * `vertical` splits as a column (`first` is the top one), matching `.drawer-split` in the
 * stylesheet; keep the two in step if that flex direction ever changes.
 */
export function drawerCollapseHostStack(node: DrawerNode): DrawerStack {
  if (node.type === 'stack') return node
  return drawerCollapseHostStack(node.direction === 'horizontal' ? node.second : node.first)
}

export function mapDrawerNode(node: DrawerNode, transform: (node: DrawerNode) => DrawerNode): DrawerNode {
  const mapped = node.type === 'split'
    ? { ...node, first: mapDrawerNode(node.first, transform), second: mapDrawerNode(node.second, transform) }
    : node
  return transform(mapped)
}

function sameJson(first: unknown, second: unknown): boolean {
  return JSON.stringify(first) === JSON.stringify(second)
}

export function normalizeDrawerProjectPresentation(
  value: unknown,
  layout: DrawerLayout,
  fallbackFocused?: DrawerTabId,
): DrawerProjectPresentation {
  const record = isRecord(value) ? value : {}
  const selectedValue = isRecord(record.selected_tabs) ? record.selected_tabs : {}
  const segmentValue = isRecord(record.selected_segments) ? record.selected_segments : {}
  const focusedTarget = migratedTabTarget(record.focused_tab)
  const focusedCandidate = focusedTarget?.tab ?? fallbackFocused
  const focused = focusedCandidate && drawerStackForTab(layout, focusedCandidate)
    ? focusedCandidate
    : drawerTabs(layout)[0]
  const focusedStack = drawerStackForTab(layout, focused)
  const selected: Record<string, DrawerTabId> = {}
  // A retired tab id that migrated *with* a segment seeds that segment, so the reader who
  // had Change Map selected lands on Activity → Changes rather than on Activity's first
  // segment. An explicitly stored segment always wins over a migration hint: the hint is
  // only reconstructing what the old flat id meant.
  const segments: Record<string, string> = {}
  const seed = (target: { tab: DrawerTabId; segment?: string } | null) => {
    if (target?.segment && segments[target.tab] === undefined) segments[target.tab] = target.segment
  }
  for (const [tab, stored] of Object.entries(segmentValue)) {
    // A retired segment id lands on whatever absorbed it, by record rather than by the
    // first-available fallback `resolveDrawerSegment` would otherwise apply: that
    // fallback happens to be right for Land → Map today only because Map is Git's first
    // segment, which is not something a retirement should depend on.
    if (typeof stored === 'string' && stored) {
      segments[tab] = isDrawerTab(tab) ? migratedDrawerSegment(tab, stored) : stored
    }
  }
  for (const stack of drawerStacks(layout)) {
    const target = migratedTabTarget(selectedValue[stack.id])
    const stored = target?.tab
    selected[stack.id] = stack.id === focusedStack?.id
      ? focused
      : stored && stack.tabs.includes(stored) ? stored : stack.tabs[0]
    if (stored && selected[stack.id] === stored) seed(target)
  }
  seed(focusedTarget)
  return {
    selected_tabs: selected,
    selected_segments: segments,
    focused_tab: focused,
    desktop_expanded: record.desktop_expanded === true || record.desktopExpanded === true,
  }
}

export function activateDrawerTab(
  presentation: DrawerProjectPresentation,
  layout: DrawerLayout,
  tab: DrawerTabId,
  /** Also select a segment of that tab, for a palette entry or voice phrase that named one. */
  segment?: string,
): DrawerProjectPresentation {
  const stack = drawerStackForTab(layout, tab)
  if (!stack) return normalizeDrawerProjectPresentation(presentation, layout)
  const normalized = normalizeDrawerProjectPresentation(presentation, layout)
  const next = {
    ...normalized,
    focused_tab: tab,
    selected_tabs: { ...normalized.selected_tabs, [stack.id]: tab },
    selected_segments: segment
      ? { ...normalized.selected_segments, [tab]: segment }
      : normalized.selected_segments,
  }
  return sameJson(next, presentation) ? presentation : next
}

/** Remember which segment a tab is showing, without moving the selection to that tab. */
export function selectDrawerSegment(
  presentation: DrawerProjectPresentation,
  layout: DrawerLayout,
  tab: DrawerTabId,
  segment: string,
): DrawerProjectPresentation {
  const normalized = normalizeDrawerProjectPresentation(presentation, layout)
  if (normalized.selected_segments[tab] === segment) return presentation
  const next = { ...normalized, selected_segments: { ...normalized.selected_segments, [tab]: segment } }
  return sameJson(next, presentation) ? presentation : next
}

export function updateDrawerProjectPresentation(
  presentation: DrawerProjectPresentation,
  layout: DrawerLayout,
  patch: Partial<Pick<DrawerProjectPresentation, 'focused_tab' | 'desktop_expanded'>>,
): DrawerProjectPresentation {
  const merged = normalizeDrawerProjectPresentation({ ...presentation, ...patch }, layout, patch.focused_tab)
  return sameJson(merged, presentation) ? presentation : merged
}

export function parseDrawerProjectPresentations(
  raw: string | null,
  layout: DrawerLayout,
): DrawerProjectPresentationMap {
  if (!raw) return {}
  let value: unknown
  try { value = JSON.parse(raw) } catch { return {} }
  if (!isRecord(value)) return {}
  const result: Record<string, DrawerProjectPresentation> = {}
  for (const [projectId, presentation] of Object.entries(value)) {
    if (!projectId || !isRecord(presentation)) continue
    result[projectId] = normalizeDrawerProjectPresentation(presentation, layout)
  }
  return result
}

/**
 * Read the newest stored presentation map, falling back through every older shape.
 *
 * Three tiers now, newest first: v3 (this shape), v2 (the same shape without
 * `selected_segments`), and the original flat per-Project `{tab, desktopExpanded}` record.
 * v2 needs no conversion beyond parsing — `normalizeDrawerProjectPresentation` fills the
 * missing segment map, and the retired tab ids inside it migrate to tab *and* segment on
 * the way through — so it is passed straight to the parser rather than hand-converted.
 */
export function migrateDrawerProjectPresentations(
  currentRaw: string | null,
  legacyRaw: string | null,
  layout: DrawerLayout,
  legacyGlobalTab: string | null = null,
  initiallyActiveProject = '',
  v2Raw: string | null = null,
): DrawerProjectPresentationMap {
  if (currentRaw !== null) return parseDrawerProjectPresentations(currentRaw, layout)
  if (v2Raw !== null) return parseDrawerProjectPresentations(v2Raw, layout)
  let legacy: unknown = {}
  try { legacy = legacyRaw ? JSON.parse(legacyRaw) : {} } catch { legacy = {} }
  const result: Record<string, DrawerProjectPresentation> = {}
  if (isRecord(legacy)) {
    for (const [projectId, state] of Object.entries(legacy)) {
      if (!projectId || !isRecord(state)) continue
      const tab = migratedTabId(state.tab) ?? drawerTabs(layout)[0]
      result[projectId] = normalizeDrawerProjectPresentation({
        focused_tab: tab,
        desktop_expanded: state.desktopExpanded === true,
        selected_tabs: {},
      }, layout, tab)
    }
  }
  const migratedGlobalTab = migratedTabId(legacyGlobalTab)
  if (initiallyActiveProject && !result[initiallyActiveProject] && migratedGlobalTab) {
    result[initiallyActiveProject] = normalizeDrawerProjectPresentation({
      focused_tab: migratedGlobalTab,
      desktop_expanded: false,
      selected_tabs: {},
    }, layout, migratedGlobalTab)
  }
  return result
}

export function drawerProjectPresentationFor(
  map: DrawerProjectPresentationMap,
  projectId: string,
  layout: DrawerLayout,
): DrawerProjectPresentation {
  return normalizeDrawerProjectPresentation(map[projectId], layout)
}

export function serializeDrawerProjectPresentations(
  map: DrawerProjectPresentationMap,
  layout: DrawerLayout,
): string {
  return JSON.stringify(Object.fromEntries(Object.entries(map).map(([id, presentation]) =>
    [id, normalizeDrawerProjectPresentation(presentation, layout)])))
}

export function reconcileDrawerProjectPresentations(
  map: DrawerProjectPresentationMap,
  layout: DrawerLayout,
): DrawerProjectPresentationMap {
  let changed = false
  const result: Record<string, DrawerProjectPresentation> = {}
  for (const [id, presentation] of Object.entries(map)) {
    const normalized = normalizeDrawerProjectPresentation(presentation, layout, presentation.focused_tab)
    result[id] = normalized
    if (!sameJson(normalized, presentation)) changed = true
  }
  return changed ? result : map
}

export function pruneDrawerProjectPresentations(
  map: DrawerProjectPresentationMap,
  knownProjectIds: readonly string[],
): DrawerProjectPresentationMap {
  const known = new Set(knownProjectIds)
  if (Object.keys(map).every(id => known.has(id))) return map
  return Object.fromEntries(Object.entries(map).filter(([id]) => known.has(id)))
}

export function setDrawerProjectPresentation(
  map: DrawerProjectPresentationMap,
  projectId: string,
  presentation: DrawerProjectPresentation,
  layout: DrawerLayout,
): DrawerProjectPresentationMap {
  if (!projectId) return map
  const normalized = normalizeDrawerProjectPresentation(presentation, layout, presentation.focused_tab)
  if (sameJson(map[projectId], normalized)) return map
  return { ...map, [projectId]: normalized }
}

function replaceNode(node: DrawerNode, id: string, replacement: DrawerNode): DrawerNode {
  if (node.id === id) return replacement
  if (node.type === 'stack') return node
  const first = replaceNode(node.first, id, replacement)
  const second = replaceNode(node.second, id, replacement)
  return first === node.first && second === node.second ? node : { ...node, first, second }
}

function removeDrawerTab(node: DrawerNode, tab: DrawerTabId): DrawerNode | null {
  if (node.type === 'stack') {
    if (!node.tabs.includes(tab)) return node
    const tabs = node.tabs.filter(item => item !== tab)
    return tabs.length ? { ...node, tabs } : null
  }
  const first = removeDrawerTab(node.first, tab)
  const second = removeDrawerTab(node.second, tab)
  if (!first) return second
  if (!second) return first
  return first === node.first && second === node.second ? node : { ...node, first, second }
}

export function reorderDrawerStack(
  layout: DrawerLayout,
  stackId: string,
  tabs: readonly DrawerTabId[],
): DrawerLayout {
  const stack = drawerStack(layout, stackId)
  if (!stack || tabs.length !== stack.tabs.length) return layout
  const expected = [...stack.tabs].sort().join('\0')
  const actual = [...new Set(tabs)].sort().join('\0')
  if (expected !== actual || stack.tabs.every((tab, index) => tab === tabs[index])) return layout
  return { ...layout, root: replaceNode(layout.root, stackId, { ...stack, tabs: [...tabs] }) }
}

export function moveDrawerTabToStack(
  layout: DrawerLayout,
  tab: DrawerTabId,
  targetStackId: string,
  insertionIndex = Number.POSITIVE_INFINITY,
): DrawerLayout {
  const source = drawerStackForTab(layout, tab)
  const target = drawerStack(layout, targetStackId)
  if (!source || !target) return layout
  if (source.id === target.id) {
    const without = source.tabs.filter(item => item !== tab)
    const index = Math.max(0, Math.min(without.length, Number.isFinite(insertionIndex) ? insertionIndex : without.length))
    const tabs = [...without.slice(0, index), tab, ...without.slice(index)]
    return reorderDrawerStack(layout, source.id, tabs)
  }
  const root = removeDrawerTab(layout.root, tab)
  if (!root) return layout
  const remainingTarget = drawerStack({ version: 1, root }, targetStackId)
  if (!remainingTarget) return layout
  const index = Math.max(0, Math.min(remainingTarget.tabs.length, Number.isFinite(insertionIndex) ? insertionIndex : remainingTarget.tabs.length))
  const nextRoot = replaceNode(root, targetStackId, {
    ...remainingTarget,
    tabs: [...remainingTarget.tabs.slice(0, index), tab, ...remainingTarget.tabs.slice(index)],
  })
  return { version: 1, root: nextRoot }
}

function nodeDepth(node: DrawerNode, id: string, depth = 0): number | null {
  if (node.id === id) return depth
  if (node.type === 'stack') return null
  return nodeDepth(node.first, id, depth + 1) ?? nodeDepth(node.second, id, depth + 1)
}

export function moveDrawerTabToSplit(
  layout: DrawerLayout,
  tab: DrawerTabId,
  targetStackId: string,
  edge: DrawerEdge,
): DrawerLayout {
  const source = drawerStackForTab(layout, tab)
  const target = drawerStack(layout, targetStackId)
  if (!source || !target || (source.id === target.id && source.tabs.length === 1)) return layout
  const depth = nodeDepth(layout.root, targetStackId)
  if (depth === null || depth + 1 >= DRAWER_MAX_DEPTH) return layout
  const root = removeDrawerTab(layout.root, tab)
  if (!root) return layout
  const remainingTarget = drawerStack({ version: 1, root }, targetStackId)
  if (!remainingTarget) return layout
  const added: DrawerStack = { type: 'stack', id: nodeId('stack'), tabs: [tab] }
  const before = edge === 'left' || edge === 'top'
  const split: DrawerSplit = {
    type: 'split',
    id: nodeId('split'),
    direction: edge === 'left' || edge === 'right' ? 'horizontal' : 'vertical',
    ratio: 0.5,
    first: before ? added : remainingTarget,
    second: before ? remainingTarget : added,
  }
  return { version: 1, root: replaceNode(root, targetStackId, split) }
}

export function setDrawerSplitRatio(layout: DrawerLayout, splitId: string, ratio: number): DrawerLayout {
  let changed = false
  const next = mapDrawerNode(layout.root, node => {
    if (node.type !== 'split' || node.id !== splitId) return node
    const normalized = clampDrawerRatio(ratio)
    if (normalized === node.ratio) return node
    changed = true
    return { ...node, ratio: normalized }
  })
  return changed ? { ...layout, root: next } : layout
}

export function resetDrawerLayout(): DrawerLayout {
  return defaultDrawerLayout()
}

export function isDefaultDrawerLayout(layout: DrawerLayout): boolean {
  const stacks = drawerStacks(layout)
  const expected = registeredIds()
  return stacks.length === 1
    && stacks[0].tabs.length === expected.length
    && stacks[0].tabs.every((tab, index) => tab === expected[index])
}

export type DrawerStackRect = {
  stackId: string
  left: number
  top: number
  right: number
  bottom: number
}

export function drawerStackRects(layout: DrawerLayout): DrawerStackRect[] {
  const result: DrawerStackRect[] = []
  const visit = (node: DrawerNode, left: number, top: number, right: number, bottom: number) => {
    if (node.type === 'stack') {
      result.push({ stackId: node.id, left, top, right, bottom })
      return
    }
    if (node.direction === 'horizontal') {
      const middle = left + (right - left) * node.ratio
      visit(node.first, left, top, middle, bottom)
      visit(node.second, middle, top, right, bottom)
    } else {
      const middle = top + (bottom - top) * node.ratio
      visit(node.first, left, top, right, middle)
      visit(node.second, left, middle, right, bottom)
    }
  }
  visit(layout.root, 0, 0, 1, 1)
  return result
}

export function adjacentDrawerStack(
  layout: DrawerLayout,
  stackId: string,
  edge: DrawerEdge,
): DrawerStack | null {
  const rects = drawerStackRects(layout)
  const source = rects.find(rect => rect.stackId === stackId)
  if (!source) return null
  const horizontal = edge === 'left' || edge === 'right'
  const candidates = rects.filter(rect => {
    if (rect.stackId === stackId) return false
    const overlap = horizontal
      ? Math.min(source.bottom, rect.bottom) - Math.max(source.top, rect.top)
      : Math.min(source.right, rect.right) - Math.max(source.left, rect.left)
    if (overlap <= 1e-9) return false
    if (edge === 'left') return rect.right <= source.left + 1e-9
    if (edge === 'right') return rect.left >= source.right - 1e-9
    if (edge === 'top') return rect.bottom <= source.top + 1e-9
    return rect.top >= source.bottom - 1e-9
  })
  candidates.sort((first, second) => {
    const firstDistance = edge === 'left' ? source.left - first.right
      : edge === 'right' ? first.left - source.right
        : edge === 'top' ? source.top - first.bottom : first.top - source.bottom
    const secondDistance = edge === 'left' ? source.left - second.right
      : edge === 'right' ? second.left - source.right
        : edge === 'top' ? source.top - second.bottom : second.top - source.bottom
    return firstDistance - secondDistance || first.stackId.localeCompare(second.stackId)
  })
  return candidates[0] ? drawerStack(layout, candidates[0].stackId) : null
}

export function moveDrawerTabDirection(
  layout: DrawerLayout,
  tab: DrawerTabId,
  edge: DrawerEdge,
): DrawerLayout {
  const source = drawerStackForTab(layout, tab)
  if (!source) return layout
  const adjacent = adjacentDrawerStack(layout, source.id, edge)
  if (adjacent) return moveDrawerTabToStack(layout, tab, adjacent.id, adjacent.tabs.length)
  return moveDrawerTabToSplit(layout, tab, source.id, edge)
}
