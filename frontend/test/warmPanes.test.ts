import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { WARM_TERMINAL_PANES, recordPaneVisits, warmPaneIds } from '../src/warmPanes.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

test('the most recently shown pane is the first one kept warm', () => {
  let history = recordPaneVisits([], ['a'])
  history = recordPaneVisits(history, ['b'])
  history = recordPaneVisits(history, ['c'])
  assert.deepEqual(warmPaneIds(history, ['c'], ['a', 'b', 'c']), ['b', 'a'])
})

test('revisiting a pane moves it back to the front rather than duplicating it', () => {
  let history = recordPaneVisits([], ['a'])
  history = recordPaneVisits(history, ['b'])
  history = recordPaneVisits(history, ['a'])
  history = recordPaneVisits(history, ['c'])
  assert.deepEqual(history.filter(id => id === 'a').length, 1)
  assert.deepEqual(warmPaneIds(history, ['c'], ['a', 'b', 'c']), ['a', 'b'])
})

test('a layout that activates several panes at once records them all', () => {
  // Restoring a workspace or closing a split makes more than one stack change its
  // active child in the same update; recording only one would lose the others.
  const history = recordPaneVisits(['old'], ['a', 'b'])
  assert.deepEqual(history, ['b', 'a', 'old'])
})

test('warm panes are bounded, evicting least-recently-shown first', () => {
  let history: string[] = []
  const all = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5']
  for (const id of all) history = recordPaneVisits(history, [id])
  const warm = warmPaneIds(history, ['p5'], all)
  assert.equal(warm.length, WARM_TERMINAL_PANES)
  assert.deepEqual(warm, ['p4', 'p3', 'p2'])
  assert.ok(!warm.includes('p0'), 'the oldest pane is evicted, not retained')
})

test('panes already on screen never spend the warm budget', () => {
  // They are mounted because they are visible. Counting them would let a split with
  // several visible terminals evict every hidden one this exists to keep.
  let history: string[] = []
  for (const id of ['a', 'b', 'c', 'd', 'e']) history = recordPaneVisits(history, [id])
  const warm = warmPaneIds(history, ['e', 'd'], ['a', 'b', 'c', 'd', 'e'])
  assert.ok(!warm.includes('e') && !warm.includes('d'))
  assert.deepEqual(warm, ['c', 'b', 'a'])
})

test('a pane the layout no longer has cannot hold a slot', () => {
  // Closed tabs stay in the recency list (it is never pruned on close), so without
  // this the budget would be spent on sessions that are not rendered at all.
  let history: string[] = []
  for (const id of ['gone1', 'gone2', 'gone3', 'live']) history = recordPaneVisits(history, [id])
  assert.deepEqual(warmPaneIds(history, ['other'], ['live', 'other']), ['live'])
})

test('a zero budget keeps nothing warm', () => {
  const history = recordPaneVisits([], ['a', 'b'])
  assert.deepEqual(warmPaneIds(history, ['c'], ['a', 'b', 'c'], 0), [])
})

test('recency is capped so a long session cannot grow it without bound', () => {
  let history: string[] = []
  for (let index = 0; index < 500; index += 1) history = recordPaneVisits(history, [`p${index}`])
  assert.ok(history.length <= 64, `history grew to ${history.length}`)
  assert.equal(history[0], 'p499')
})

test('a warm pane is hidden from layout, pointer, and assistive tech', () => {
  // The whole point is that it stays live while another tab is on screen, so a
  // partial hide (opacity, z-index) would leave a background session focusable and
  // still measuring layout — which is what lets it reshape the shared PTY.
  const css = readFileSync(join(SRC, 'style.css'), 'utf8')
  assert.match(css, /\.terminal-pane\.pane-warm\s*\{[^}]*display:\s*none/)
  const app = readFileSync(join(SRC, 'App.tsx'), 'utf8')
  assert.match(app, /aria-hidden=\{paneVisible\?undefined:'true'\}/)
})

test('the terminal reads pane visibility from a ref, never from its mount deps', () => {
  // Listing `visible` as a mount dependency would dispose xterm and reconnect the
  // socket on every tab switch, which is exactly the cost warm panes remove.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  const deps = source.match(/\}, \[session\.id,[^\]]*\]\)/)
  assert.ok(deps, 'could not find the mount effect dependency list')
  assert.ok(!deps[0].includes('visible'), `mount deps must not include visible: ${deps[0]}`)
  assert.match(source, /const paneIsHidden = \(\) => document\.hidden \|\| !visibleRef\.current/)
})

test('everything that gates on being looked at asks paneIsHidden, not the document', () => {
  // A warm pane is in a visible document. Any check left on `document.hidden` would
  // treat it as on-screen and let it size the PTY, take the keyboard, or write the
  // system clipboard out from under the tab the user is actually on.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  for (const marker of [
    '() => replaying || paneIsHidden()',
    'if (paneIsHidden() && localFit) sendViewport(',
    'const hidden = paneIsHidden()',
    'visible: !paneIsHidden(),',
  ]) {
    assert.ok(source.includes(marker), `TerminalPane no longer contains: ${marker}`)
  }
})
