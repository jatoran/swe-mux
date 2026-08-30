/**
 * Settings search index.
 *
 * The index is derived from the same JSX that renders the Settings form, by
 * walking the *vnode* tree rather than the DOM. That matters twice over:
 *
 * - Only one tab is mounted at a time, so a DOM walk could only ever see the tab
 *   you are already looking at. Calling a tab's render function just allocates
 *   plain objects — no DOM, no effects, no child-component bodies run — so every
 *   tab can be indexed for the cost of a few object allocations.
 * - Nothing has to be declared twice. A new `<label>` in Settings.tsx is
 *   searchable the moment it renders, and an edited label re-indexes itself.
 *   There is no registry to forget to update.
 *
 * The trade is that anything rendered *inside* a child component (`<Accounts/>`)
 * is opaque to the walk: a component vnode is a function reference plus props,
 * and calling it would mean running hooks outside a render. Those panels are
 * still reachable through their tab and their own headings.
 *
 * Ranking itself is not local to this file: `fuzzyText.ts` owns the ladder, which
 * the sidebar's Project/session filter scores by too.
 */
import { fieldScore, normalizeSearchText } from './fuzzyText.ts'

export { normalizeSearchText }

/** Structural view of a preact vnode, so the walk is testable without a renderer. */
export type VNodeLike = {
  type?: unknown
  props?: Record<string, unknown> | null
} | string | number | boolean | null | undefined | VNodeLike[]

export type SettingsEntryKind = 'field' | 'action' | 'section'

export type SettingsSearchEntry = {
  tab: string
  tabLabel: string
  tabIndex: number
  /**
   * Enclosing headings, outermost first — `['Keyboard shortcuts', 'View']`. A single
   * slot cannot describe a tab that nests `<h4>` groups under an `<h3>`: whichever
   * heading rendered last wins, so every keyboard-shortcut row filed itself under its
   * category ("View") and lost the section a reader needs to place it. Levels are
   * positional, so setting one drops every deeper heading and a new `<h3>` cannot
   * inherit the previous block's `<h4>`.
   */
  path: string[]
  /** `path` joined for display and for result identity. Derived, never set apart from it. */
  section: string
  /** Display text, as written in the JSX. */
  label: string
  /** `label`, normalized — also the prefix used to find the element in the DOM. */
  key: string
  /** Everything else worth matching on: heading, help text, option labels, placeholders. */
  keywords: string
  kind: SettingsEntryKind
  /** Which same-`key` element of this `kind` in this tab, in document order. */
  occurrence: number
}

/** CSS selector for the elements an entry of each kind could have come from. */
export const kindSelector: Record<SettingsEntryKind, string> = {
  field: 'label',
  action: 'button,summary',
  section: 'h3,h4,strong',
}

// Tags that emit an entry, and the kind they emit. `summary` is both an action
// (it opens something) and a heading, so it is registered as an action and its
// selector covers both.
const EMITS: Record<string, SettingsEntryKind> = {
  h3: 'section', h4: 'section', strong: 'section', label: 'field', button: 'action', summary: 'action',
}
// Which slot of the heading path a tag owns. `strong` emits an entry without
// claiming a level: it marks a labelled block inside a section rather than opening
// one, and letting it truncate the path would file that section's later controls
// under a phrase like "EDITOR::CHORDS".
const HEADING_LEVEL: Record<string, number> = { h3: 1, h4: 2 }
/** How a heading path reads in one line. */
export const SECTION_SEPARATOR = ' · '
// An emitting tag inside one of these is decoration, not a control: `<strong>`
// inside a paragraph is emphasis, `<button>` inside a `<label>` is part of that
// field. Skipping them keeps the index to things a user can actually navigate to.
const SWALLOWS = new Set(['label', 'p', 'li', 'button', 'summary', 'h3', 'h4', 'em', 'code'])
// Prose that describes whatever control precedes it. Folded into that entry's
// keywords so searching for a phrase from the help text still finds the control.
const HELP_TAGS = new Set(['p', 'small'])
// Props worth matching on even though they render no text node.
const TEXT_PROPS = ['placeholder', 'title', 'aria-label', 'alt'] as const

/**
 * A `Dropdown`'s rows live in its `options` prop rather than as `<option>` children, so the
 * walk below cannot see them the way it saw a `<select>`'s. Harvesting the labels keeps every
 * choice searchable: "Tokyo Night" and "DEBUG" and "Kokoro" are exactly the words a person
 * types when hunting for the setting that offers them, and they became unfindable the moment
 * the panel stopped using native selects. Only `label` is taken — `value` is an id, and
 * indexing ids would match a search for "off" against every enum in the panel.
 */
function collectOptionLabels(props: Record<string, unknown> | null | undefined, out: string[]): void {
  const options = props?.options
  if (!Array.isArray(options)) return
  for (const option of options) {
    const label = (option as { label?: unknown } | null)?.label
    if (typeof label === 'string' && label.trim()) out.push(label.trim())
  }
}

const isVNode = (node: unknown): node is { type?: unknown; props?: Record<string, unknown> | null } =>
  typeof node === 'object' && node !== null && 'props' in (node as Record<string, unknown>)

const childrenOf = (node: { props?: Record<string, unknown> | null }): unknown =>
  node.props && typeof node.props === 'object' ? (node.props as { children?: unknown }).children : undefined

/** Every text run under `node`, in document order. */
function collectText(node: unknown, out: string[], withProps: boolean): void {
  if (node === null || node === undefined || typeof node === 'boolean') return
  if (Array.isArray(node)) { for (const child of node) collectText(child, out, withProps); return }
  if (typeof node === 'string') { if (node.trim()) out.push(node.trim()); return }
  if (typeof node === 'number') { out.push(String(node)); return }
  if (!isVNode(node)) return
  const props = node.props
  if (withProps && props) {
    for (const name of TEXT_PROPS) {
      const value = props[name]
      if (typeof value === 'string' && value.trim()) out.push(value.trim())
    }
    collectOptionLabels(props, out)
  }
  collectText(childrenOf(node), out, withProps)
}

const textOf = (node: unknown): string => { const out: string[] = []; collectText(node, out, true); return out.join(' ') }

/**
 * The label a human would read for this control: the first *rendered* text run,
 * not the whole subtree. `<label>Theme<Dropdown …/></label>` is "Theme", never
 * "Theme Dark Light System …" — the option labels stay searchable as keywords,
 * which is why they are harvested only under `withProps`. Glyph-only runs are
 * skipped so an icon-prefixed button indexes as
 * "Claude" rather than "▶", and an `aria-label` is a fallback rather than the
 * first choice: a row labelled "Set shortcut for Open command palette" should
 * still index, and be found, as the "Open command palette" a user can see.
 */
function leadText(node: unknown): string {
  const rendered: string[] = []
  collectText(node, rendered, false)
  const visible = rendered.find(run => /[a-z0-9]/i.test(run))
  if (visible) return visible
  const props = isVNode(node) ? node.props : null
  for (const name of TEXT_PROPS) {
    const value = props?.[name]
    if (typeof value === 'string' && /[a-z0-9]/i.test(value)) return value.trim()
  }
  return ''
}

type Harvest = {
  entries: SettingsSearchEntry[]
  counts: Map<string, number>
  path: string[]
  last: SettingsSearchEntry | null
}

/**
 * Open a heading at `level`, closing every deeper one. An empty heading changes
 * nothing rather than blanking the path — a glyph-only `<h3>` is not a new block.
 */
function openHeading(state: Harvest, level: number, text: string): void {
  if (!text) return
  state.path = [...state.path.slice(0, level - 1), text]
}

function emit(state: Harvest, tab: string, tabLabel: string, tabIndex: number, kind: SettingsEntryKind, node: unknown): void {
  const label = leadText(node)
  const key = normalizeSearchText(label)
  // A control with nothing readable behind it — a bare `×` — is not somewhere a
  // search can usefully land.
  if (!key) return
  const countKey = `${kind}:${key}`
  const occurrence = state.counts.get(countKey) || 0
  state.counts.set(countKey, occurrence + 1)
  // Emitted before the node's own heading is opened, so a heading is filed under
  // the block that contains it rather than under itself.
  const path = [...state.path]
  const entry: SettingsSearchEntry = {
    tab, tabLabel, tabIndex, path, section: path.join(SECTION_SEPARATOR), label, key,
    keywords: normalizeSearchText(`${path.join(' ')} ${textOf(node)}`), kind, occurrence,
  }
  state.entries.push(entry)
  state.last = entry
}

function walk(node: unknown, state: Harvest, tab: string, tabLabel: string, tabIndex: number, ancestors: string[]): void {
  if (node === null || node === undefined || typeof node === 'boolean' || typeof node === 'string' || typeof node === 'number') return
  if (Array.isArray(node)) { for (const child of node) walk(child, state, tab, tabLabel, tabIndex, ancestors); return }
  if (!isVNode(node)) return
  const type = node.type
  // Fragments and child components: nothing of our own to emit, but their JSX
  // children (if any were passed in) still belong to this tab.
  if (typeof type !== 'string') { walk(childrenOf(node), state, tab, tabLabel, tabIndex, ancestors); return }
  const tag = type.toLowerCase()
  const swallowed = ancestors.some(name => SWALLOWS.has(name))
  const kind = EMITS[tag]
  if (kind && !swallowed) {
    emit(state, tab, tabLabel, tabIndex, kind, node)
    if (kind === 'section' && HEADING_LEVEL[tag]) openHeading(state, HEADING_LEVEL[tag], leadText(node))
  } else if (HELP_TAGS.has(tag) && !swallowed && state.last) {
    const help = normalizeSearchText(textOf(node))
    if (help) state.last.keywords = `${state.last.keywords} ${help}`.trim()
  }
  // A heading governs what follows it, not what it contains, so nothing but a
  // `<section>` boundary can close one. Every settings block is a `<section>` —
  // including each keyboard-shortcut category — which is what stops that block's
  // `<h4>` from following the walk out and claiming its siblings: without this the
  // "Reserved shortcut policy" disclosure files itself under the last category
  // rendered above it.
  const enclosing = tag === 'section' ? [...state.path] : null
  walk(childrenOf(node), state, tab, tabLabel, tabIndex, [...ancestors, tag])
  if (enclosing) state.path = enclosing
}

/** Index one tab's rendered vnode tree. */
export function harvestSettings(node: unknown, tab: string, tabLabel: string, tabIndex: number): SettingsSearchEntry[] {
  const state: Harvest = { entries: [], counts: new Map(), path: [], last: null }
  walk(node, state, tab, tabLabel, tabIndex, [])
  return state.entries
}

/**
 * Adapts a live DOM subtree to the shape the vnode walk consumes, so a mounted
 * tab can be indexed by exactly the same rules. This is how the contents of a
 * child component — opaque as a vnode — become searchable: once a tab has been
 * opened, what it actually rendered is indexed and cached for the page session.
 */
export function domVNode(element: Element): VNodeLike {
  const props: Record<string, unknown> = {}
  for (const name of TEXT_PROPS) {
    const value = element.getAttribute(name)
    if (value) props[name] = value
  }
  props.children = [...element.childNodes].map(node =>
    node.nodeType === 3 ? node.nodeValue : node.nodeType === 1 ? domVNode(node as Element) : null)
  return { type: element.tagName.toLowerCase(), props }
}

/**
 * The tab itself, so searching for its name always reaches it. This is the floor
 * for a tab whose body is one child component and therefore harvests nothing.
 */
export const tabEntry = (tab: string, tabLabel: string, tabIndex: number): SettingsSearchEntry => ({
  tab, tabLabel, tabIndex, path: [], section: '', label: tabLabel, key: normalizeSearchText(tabLabel),
  keywords: normalizeSearchText(tabLabel), kind: 'section', occurrence: 0,
})

const KIND_BONUS: Record<SettingsEntryKind, number> = { field: 50, action: 10, section: 20 }

/**
 * Rank `entries` against `query`. Every whitespace-separated term must match
 * something, so "voice wake" narrows rather than widening.
 */
export function searchSettings(entries: SettingsSearchEntry[], query: string, limit = 12): SettingsSearchEntry[] {
  const terms = normalizeSearchText(query).split(' ').filter(Boolean)
  if (!terms.length) return []
  const scored: Array<{ entry: SettingsSearchEntry; score: number }> = []
  for (const entry of entries) {
    let score = 0
    for (const term of terms) {
      const value = fieldScore(entry.key, entry.keywords, term)
      if (!value) { score = 0; break }
      score += value
    }
    if (!score) continue
    score += KIND_BONUS[entry.kind] + Math.max(0, 30 - entry.key.length / 2)
    scored.push({ entry, score })
  }
  scored.sort((left, right) =>
    right.score - left.score || left.entry.tabIndex - right.entry.tabIndex || left.entry.key.localeCompare(right.entry.key))
  const seen = new Set<string>()
  const results: SettingsSearchEntry[] = []
  for (const { entry } of scored) {
    const identity = `${entry.tab}|${entry.section}|${entry.kind}|${entry.key}`
    if (seen.has(identity)) continue
    seen.add(identity)
    results.push(entry)
    if (results.length >= limit) break
  }
  return results
}

/**
 * Which candidate element an entry points at. Candidates are the elements of the
 * entry's kind, in document order, matched on the entry's label as a prefix
 * rather than by equality, because the DOM's `textContent` also carries the
 * control's own text (a `<select>`'s option labels, for one). Containment is the
 * fallback for a label that does not start the element — an icon span first, say
 * — and occurrence disambiguates repeated labels within the tab.
 */
export function matchIndex(candidateTexts: string[], key: string, occurrence: number): number {
  const normalized = candidateTexts.map(normalizeSearchText)
  const prefix: number[] = []
  const contains: number[] = []
  for (let index = 0; index < normalized.length; index += 1) {
    if (normalized[index].startsWith(key)) prefix.push(index)
    else if (normalized[index].includes(key)) contains.push(index)
  }
  const hits = prefix.length ? prefix : contains
  if (!hits.length) return -1
  return hits[Math.min(occurrence, hits.length - 1)]
}
