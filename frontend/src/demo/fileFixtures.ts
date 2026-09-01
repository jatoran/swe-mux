/**
 * A small, real filesystem for the demo's two Projects.
 *
 * The File Explorer used to be the one drawer tab that could not be opened: the demo
 * answered `files/tree` with a flat five-entry stub of the wrong shape (`{root, entries}`
 * where the reader wants `{directories: {...}}`), and had no route at all for a directory
 * or for a file's text. So the tab drew an error, and the walkthrough could not name it.
 *
 * Two rules shape what is in here, and both are the same rule the rest of the demo's
 * fixtures obey:
 *
 * - **The paths are the repository's own.** `gitFixtures.ts` invents commits touching
 *   `src/cart.js`, `src/coupons.js`, `tests/checkout.spec.ts` and the two coupon
 *   fixtures; those files exist here, with contents that match what the commits claim
 *   they did. A tree that disagreed with the Git tab would demonstrate the opposite of
 *   what either surface is for.
 * - **A file opens and reads like a file.** Short, but real enough that a visitor who
 *   clicks `src/coupons.js` after watching a scenario talk about the coupon table sees
 *   the table. Placeholder lorem would make the tab a screenshot of a tab.
 *
 * No harness is named here (`tests/test_harness_name_literals.py` does not allowlist this
 * module), which is why the invented code is about carts and memes rather than about
 * agents.
 */
import { DEMO_PROJECT_ID, DEMO_PROJECT2_ID } from './fixtures.ts'
import { nowSeconds, state } from './store.ts'

/** One project's files, keyed by project-relative path. Directories are derived. */
type FileSet = Record<string, string>

const ROCKET_SHOP: FileSet = {
  'README.md': [
    '# rocket-shop',
    '',
    'A storefront that got slow on a Friday and has been getting faster ever since.',
    '',
    '## Running it',
    '',
    '```',
    'npm install',
    'npm run dev      # serves site/ on :4321',
    'npm test         # the checkout and cart specs',
    '```',
    '',
    '## Where things are',
    '',
    '- `src/cart.js` - the request path. Nothing here may touch disk.',
    '- `src/coupons.js` - the coupon table, loaded once at boot.',
    '- `src/checkout.js` - order placement and the badge the checkout test kept losing.',
    '- `tests/` - specs plus the fixtures they read.',
    '',
    'The 40MB coupon file that used to sit in `tests/fixtures/` is gone. It is a sample',
    'now, and the real one is a conversation for a human.',
    '',
  ].join('\n'),
  'package.json': [
    '{',
    '  "name": "rocket-shop",',
    '  "version": "2.4.1",',
    '  "private": true,',
    '  "scripts": {',
    '    "dev": "node scripts/serve.js site --port 4321",',
    '    "test": "node --test tests/",',
    '    "lint": "eslint src tests"',
    '  },',
    '  "engines": { "node": ">=20" }',
    '}',
    '',
  ].join('\n'),
  'src/cart.js': [
    "import { priceFor } from './coupons.js'",
    '',
    '// The request path. Everything it needs is in memory by the time a request',
    '// arrives, which is the whole reason p95 came down from 480ms to 11ms.',
    'export function cartTotal(lines, coupon) {',
    '  let total = 0',
    '  for (const line of lines) {',
    '    total += priceFor(line.sku, line.quantity, coupon)',
    '  }',
    '  return Math.round(total)',
    '}',
    '',
    'export function cartBadge(lines) {',
    '  return lines.reduce((count, line) => count + line.quantity, 0)',
    '}',
    '',
  ].join('\n'),
  'src/coupons.js': [
    '// The coupon table, loaded once at boot and never re-read.',
    '//',
    '// It used to be parsed per request out of a 40MB file, which is the entire story',
    '// of why the cart endpoint was slow. Replacing the file did nothing; not opening',
    '// it did everything.',
    '',
    'const TABLE = new Map([',
    "  ['LAUNCH20', { kind: 'percent', value: 20, stacks: false }],",
    "  ['FREESHIP', { kind: 'shipping', value: 0, stacks: true }],",
    "  ['RETURNING', { kind: 'percent', value: 5, stacks: true }],",
    '])',
    '',
    'export function priceFor(sku, quantity, coupon) {',
    '  const base = catalogPrice(sku) * quantity',
    '  const rule = TABLE.get(coupon)',
    '  if (!rule || rule.kind !== \'percent\') return base',
    '  return base * (1 - rule.value / 100)',
    '}',
    '',
    'export function invalidate() {',
    '  // Owned by whoever replaced the file on disk. Still open; see the queue.',
    '}',
    '',
  ].join('\n'),
  'src/checkout.js': [
    "import { cartBadge } from './cart.js'",
    '',
    'export async function placeOrder(cart, session) {',
    '  const order = await session.post(\'/orders\', { lines: cart.lines })',
    '  // The badge is written after the order resolves, not before. The checkout spec',
    '  // used to read it in between and pass only when the network was slow enough.',
    '  session.setBadge(cartBadge(cart.lines))',
    '  return order',
    '}',
    '',
  ].join('\n'),
  'tests/checkout.spec.ts': [
    "import { test } from 'node:test'",
    "import assert from 'node:assert/strict'",
    "import { placeOrder } from '../src/checkout.js'",
    '',
    "test('the badge is written after the order resolves', async () => {",
    '  const session = fakeSession()',
    '  await placeOrder({ lines: [{ sku: \'A1\', quantity: 2 }] }, session)',
    '  // One await. This was the flake: the assertion read the badge before the',
    '  // request settled, so it passed only on a slow network.',
    '  assert.equal(session.badge, 2)',
    '})',
    '',
  ].join('\n'),
  'tests/cart.spec.ts': [
    "import { test } from 'node:test'",
    "import assert from 'node:assert/strict'",
    "import { cartTotal } from '../src/cart.js'",
    '',
    "test('a percent coupon comes off the line total', () => {",
    "  assert.equal(cartTotal([{ sku: 'A1', quantity: 2 }], 'LAUNCH20'), 3200)",
    '})',
    '',
    "test('an unknown coupon changes nothing', () => {",
    "  assert.equal(cartTotal([{ sku: 'A1', quantity: 2 }], 'NOPE'), 4000)",
    '})',
    '',
  ].join('\n'),
  'tests/fixtures/coupons.sample.json': [
    '{',
    '  "_comment": "Twelve rows, not 1.2 million. The real table lives in the database.",',
    '  "codes": [',
    '    { "code": "LAUNCH20", "kind": "percent", "value": 20 },',
    '    { "code": "FREESHIP", "kind": "shipping", "value": 0 },',
    '    { "code": "RETURNING", "kind": "percent", "value": 5 }',
    '  ]',
    '}',
    '',
  ].join('\n'),
  'site/index.html': [
    '<!doctype html>',
    '<meta charset="utf-8">',
    '<title>rocket-shop</title>',
    '<h1>rocket-shop</h1>',
    '<p>The dev server serves this directory. The preview pane loads it.</p>',
    '',
  ].join('\n'),
}

const MEME_GARDEN: FileSet = {
  'README.md': [
    '# meme-garden',
    '',
    'Memes, on a schedule. Nobody asked for this and it has never gone down.',
    '',
    '- `src/water.js` - the watering loop.',
    '- `src/schema.sql` - two tables, one of which is regretted.',
    '- `memes/index.json` - the catalogue.',
    '',
  ].join('\n'),
  'package.json': [
    '{',
    '  "name": "meme-garden",',
    '  "version": "0.9.0",',
    '  "private": true,',
    '  "scripts": { "water": "node src/water.js" }',
    '}',
    '',
  ].join('\n'),
  'src/water.js': [
    "import { readCatalogue } from './catalogue.js'",
    '',
    '// Runs every six hours. A meme that has not been seen in a month is retired,',
    '// which is the only deletion this project has ever performed on purpose.',
    'export async function water(now = Date.now()) {',
    '  const memes = await readCatalogue()',
    '  const stale = memes.filter(meme => now - meme.last_seen > THIRTY_DAYS)',
    '  for (const meme of stale) await retire(meme)',
    '  return { watered: memes.length, retired: stale.length }',
    '}',
    '',
    'const THIRTY_DAYS = 30 * 24 * 60 * 60 * 1000',
    '',
  ].join('\n'),
  'src/catalogue.js': [
    "import memes from '../memes/index.json' with { type: 'json' }",
    '',
    'export async function readCatalogue() {',
    '  return memes.items',
    '}',
    '',
  ].join('\n'),
  'src/schema.sql': [
    '-- Two tables. The second one is the migration that is still running.',
    'CREATE TABLE meme (',
    '  id      TEXT PRIMARY KEY,',
    '  caption TEXT NOT NULL,',
    '  seen_at INTEGER NOT NULL',
    ');',
    '',
    '-- Added when captions grew a second language. Backfilling it is the open work.',
    'CREATE TABLE meme_caption (',
    '  meme_id TEXT NOT NULL REFERENCES meme(id),',
    '  lang    TEXT NOT NULL,',
    '  text    TEXT NOT NULL,',
    '  PRIMARY KEY (meme_id, lang)',
    ');',
    '',
  ].join('\n'),
  'memes/index.json': [
    '{',
    '  "items": [',
    '    { "id": "m-001", "caption": "it works on my machine", "last_seen": 1756000000000 },',
    '    { "id": "m-002", "caption": "this is fine", "last_seen": 1756400000000 },',
    '    { "id": "m-003", "caption": "ship it", "last_seen": 1756600000000 }',
    '  ]',
    '}',
    '',
  ].join('\n'),
}

const FILES: Record<string, FileSet> = {
  [DEMO_PROJECT_ID]: ROCKET_SHOP,
  [DEMO_PROJECT2_ID]: MEME_GARDEN,
}

/**
 * Edits the visitor made, held for this page only.
 *
 * A Markdown file opened in the drawer autosaves through the same queue notes use, so a
 * demo that refused the write would show a save error the moment somebody typed. Keeping
 * the text in a map means the file reads back what they wrote, and a reset (which reloads
 * the frame) forgets it - which is the same promise every other demo edit makes.
 */
const edits: Record<string, string> = {}

const key = (projectId: string, path: string): string => `${projectId}::${path}`

const setFor = (projectId: string): FileSet => FILES[projectId] ?? {}

const textFor = (projectId: string, path: string): string | null => {
  const edited = edits[key(projectId, path)]
  if (edited !== undefined) return edited
  const found = setFor(projectId)[path]
  return found === undefined ? null : found
}

/** Every directory implied by the file paths, root included. */
function directoriesOf(projectId: string): Set<string> {
  const found = new Set<string>([''])
  for (const path of Object.keys(setFor(projectId))) {
    const parts = path.split('/')
    for (let depth = 1; depth < parts.length; depth += 1) {
      found.add(parts.slice(0, depth).join('/'))
    }
  }
  return found
}

const parentOf = (path: string): string | null =>
  (path === '' ? null : path.split('/').slice(0, -1).join('/'))

/**
 * One directory listing.
 *
 * Directories first and then files, each alphabetically, which is what a real listing
 * from the daemon arrives in - the reader does not sort, so a fixture that came back in
 * insertion order would put `README.md` above `src/` and look wrong in a way no test
 * would catch.
 */
export function demoDirectory(projectId: string, path: string): unknown {
  const folder = path.replace(/\/+$/, '')
  const directories = directoriesOf(projectId)
  if (!directories.has(folder)) return null
  const prefix = folder === '' ? '' : `${folder}/`
  const children = new Map<string, { name: string; path: string; kind: 'directory' | 'file'; size: number | null }>()
  for (const [file, text] of Object.entries(setFor(projectId))) {
    if (!file.startsWith(prefix)) continue
    const rest = file.slice(prefix.length)
    if (!rest) continue
    const cut = rest.indexOf('/')
    const name = cut === -1 ? rest : rest.slice(0, cut)
    const child = `${prefix}${name}`
    if (children.has(child)) continue
    children.set(child, cut === -1
      ? { name, path: child, kind: 'file', size: (edits[key(projectId, file)] ?? text).length }
      : { name, path: child, kind: 'directory', size: null })
  }
  const items = [...children.values()].sort((left, right) =>
    (left.kind === right.kind
      ? left.name.localeCompare(right.name)
      : left.kind === 'directory' ? -1 : 1))
  return { path: folder, parent: parentOf(folder), items, truncated: false }
}

/** The tree reader's one round trip: the root plus every folder it wants restored. */
export function demoFileTree(projectId: string, paths: string[]): unknown {
  const wanted = paths.length ? paths : ['']
  const directories: Record<string, unknown> = {}
  for (const path of wanted) {
    const listing = demoDirectory(projectId, path)
    if (listing) directories[path.replace(/\/+$/, '')] = listing
  }
  return { directories }
}

/**
 * One file's text.
 *
 * `revision` is derived from the content rather than counted, so a reload of the same
 * bytes is the same revision and the editor's stale-read guard has nothing to complain
 * about. A missing path is a 404 rather than empty text, because an editor opened on
 * empty text looks like a file somebody truncated.
 */
export function demoFile(projectId: string, path: string): unknown | null {
  const text = textFor(projectId, path)
  if (text === null) return null
  return {
    // `ready` and a `text` presentation together are what make the editor render: the
    // reader treats any other status as "no safe viewer" and draws "this resource is
    // read-only" over an empty pane, which is what a plausible-looking `ok` produced.
    revision: `r${text.length}-${hash(text)}`,
    status: 'ready',
    presentation: { kind: 'text' },
    path,
    size: text.length,
    text,
  }
}

/** Accept a save, so a Markdown file opened in the drawer can be edited like a note. */
export function demoFileSave(projectId: string, path: string, text: string): unknown | null {
  if (textFor(projectId, path) === null) return null
  edits[key(projectId, path)] = text
  return demoFile(projectId, path)
}

/**
 * The Recent view, which is Git-derived in the product and therefore here too.
 *
 * Every row names a file the tree actually has and the Git tab actually mentions, so the
 * three surfaces cannot tell different stories about one repository.
 */
export function demoRecentFiles(projectId: string): unknown {
  const rows: Array<{ path: string; status: string | null; origin: 'working' | 'committed'; age: number }> = projectId === DEMO_PROJECT_ID
    ? [
      { path: 'src/coupons.js', status: 'M', origin: 'working', age: 0 },
      { path: 'tests/checkout.spec.ts', status: 'M', origin: 'working', age: 0 },
      { path: 'src/cart.js', status: null, origin: 'committed', age: 2 * 3600 },
      { path: 'tests/fixtures/coupons.sample.json', status: null, origin: 'committed', age: 5 * 3600 },
      { path: 'README.md', status: null, origin: 'committed', age: 30 * 86400 },
    ]
    : [
      { path: 'src/water.js', status: 'M', origin: 'working', age: 0 },
      { path: 'src/schema.sql', status: null, origin: 'committed', age: 6 * 3600 },
    ]
  const now = nowSeconds()
  return {
    available: true,
    items: rows.map(row => ({
      name: row.path.split('/').pop() ?? row.path,
      path: row.path,
      kind: 'file' as const,
      origin: row.origin,
      status: row.origin === 'working' ? row.status : null,
      committed_at: row.origin === 'committed' ? now - row.age : null,
    })),
  }
}

/**
 * Name and content search over the same set.
 *
 * Real rather than refused, because the field is the first thing a visitor types into on
 * that tab and "search is not available in the demo" is a worse answer than three hits.
 * Content matches carry the line they were found on, which is what the reader draws.
 */
export function demoFileSearch(projectId: string, query: string, mode: string): unknown {
  const needle = query.trim().toLowerCase()
  if (!needle) return { items: [], truncated: false, truncated_reason: null, stopped_at: null }
  const names = mode !== 'contents'
  const contents = mode !== 'names'
  const items: Array<Record<string, unknown>> = []
  for (const path of Object.keys(setFor(projectId)).sort()) {
    const name = path.split('/').pop() ?? path
    if (names && path.toLowerCase().includes(needle)) {
      items.push({ name, path, match: 'name', line: null, snippet: null })
      continue
    }
    if (!contents) continue
    const lines = (textFor(projectId, path) ?? '').split('\n')
    const index = lines.findIndex(line => line.toLowerCase().includes(needle))
    if (index === -1) continue
    items.push({ name, path, match: 'content', line: index + 1, snippet: lines[index].trim() })
  }
  return { items, truncated: false, truncated_reason: null, stopped_at: null }
}

/** Whether a Project has a tree at all, for the routes that answer per Project. */
export const demoHasFiles = (projectId: string): boolean =>
  Object.keys(setFor(projectId)).length > 0 || state.projects.some(item => item.id === projectId)

/** A short, stable digest. Not a checksum - a revision only has to differ when the text
 *  does, and be the same string twice for the same bytes. */
function hash(text: string): string {
  let value = 5381
  for (let index = 0; index < text.length; index += 1) {
    value = ((value << 5) + value + text.charCodeAt(index)) >>> 0
  }
  return value.toString(36)
}
