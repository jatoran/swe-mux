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

test('terminal memoization delivers every pane visibility transition', () => {
  // The lightweight visibility effect cannot run if the custom memo comparator
  // swallows the only prop that changed. That leaves a hidden pane registered for
  // PTY geometry and prevents the newly shown pane's redraw.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  const comparator = source.slice(source.indexOf('export const TerminalPane = memo'))
  assert.match(comparator, /a\.visible === b\.visible/)
})

test('restoring a warm pane reflows same-grid renderer dimensions after fitting', () => {
  // FitAddon skips `term.resize` when cols/rows match. That is insufficient after
  // `display:none`: the renderer surface can still occupy the old upper-left area.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  const start = source.indexOf('paneVisibilityRef.current = (nowVisible: boolean) => {')
  const end = source.indexOf('// Chromium device emulation', start)
  assert.ok(start >= 0 && end > start, 'pane visibility handler not found')
  const action = source.slice(start, end)
  const fit = action.indexOf('scheduleFullRedraw()')
  const reflow = action.indexOf('reflowVisibleTerminalRenderer(term, host.current)')
  assert.ok(fit >= 0, 'restored pane must fit and redraw')
  assert.ok(reflow > fit, 'same-grid renderer reflow must follow the scheduled fit')
  assert.match(action, /if \(paneIsHidden\(\)\) return/)
})

test('the visible Resize action measures and registers before claiming input', () => {
  // A claim by the existing owner is intentionally a no-op for geometry. The action
  // must force a fresh viewport frame first, and both buttons must use that path.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  const start = source.indexOf('resizeToPaneRef.current = () => {')
  const end = source.indexOf('const claimOnFocus', start)
  assert.ok(start >= 0 && end > start, 'resizeToPane action not found')
  const action = source.slice(start, end)
  const register = action.indexOf('sendViewport(localFit.cols, localFit.rows, true)')
  const claim = action.indexOf("claimInput('gesture')")
  assert.match(action, /refitVisibleTerminal\(fit, box\)/)
  assert.match(action, /reflowVisibleTerminalRenderer\(term, box\)/)
  assert.ok(register >= 0, 'Resize must force-register the measured viewport')
  assert.ok(claim > register, 'Resize must register the viewport before claiming input')
  assert.equal(source.match(/onClick=\{\(\)=>\{resizeToPaneRef\.current\(\);focusTerminalInputRef\.current\(\)\}\}/g)?.length, 2)
})

test('everything that gates on being looked at asks paneIsHidden, not the document', () => {
  // A warm pane is in a visible document. Any check left on `document.hidden` would
  // treat it as on-screen and let it size the PTY, take the keyboard, or write the
  // system clipboard out from under the tab the user is actually on.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  for (const marker of [
    '() => replaying || paneIsHidden()',
    'if (paneIsHidden()) sendViewport(',
    'const hidden = paneIsHidden()',
    'visible: !paneIsHidden(),',
    'paneHidden: paneIsHidden(),',
    'attachRegistersViewport(fitted, paneIsHidden())',
  ]) {
    assert.ok(source.includes(marker), `TerminalPane no longer contains: ${marker}`)
  }
  // The list above only proves the checks that exist are right; it is blind to a new one
  // added on the document instead. `attach_ready` was exactly that, and it registered a
  // warm pane's unfitted 80x24 as a visible viewport, which — ownership carrying geometry
  // — resized the whole session to it. Outside its own definition and the prose about it,
  // `document.hidden` has no correct use in this file.
  const uses = source
    .split('\n')
    .filter((line: string) => line.includes('document.hidden') && !line.trimStart().startsWith('//'))
  assert.deepEqual(uses.map((line: string) => line.trim()), [
    'const paneIsHidden = () => document.hidden || !visibleRef.current',
  ])
})

test('a pane withdraws its viewport whether or not it recorded a fit', () => {
  // Both deregistration paths used to be gated on a `localFit` the pane may never have
  // taken: a reconnect nulls it, and a pane that is hidden when the socket opens cannot
  // measure one. The registration then outlived the pane's own visibility and held the
  // PTY at a size nobody was looking at.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  assert.ok(!/if \(localFit\) sendViewport\(/.test(source))
  assert.equal(source.match(/sendViewport\(localFit\?\.cols \?\? term\.cols/g)?.length, 2)
})

test('every resize flood is routed through the coalescing scheduler', () => {
  // visualViewport/window resize and the host ResizeObserver are the three triggers
  // that arrive per animation frame. Any one of them left on the eager path puts the
  // per-frame pseudoconsole resize (and the CLI repaint behind it) straight back.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  for (const marker of [
    "new ResizeObserver(scheduleBurstFit)",
    "window.addEventListener('resize', scheduleBurstFit)",
    "window.visualViewport?.addEventListener('resize',scheduleBurstFit)",
  ]) {
    assert.ok(source.includes(marker), `resize trigger no longer coalesced: ${marker}`)
  }
  // The fourth flood is self-inflicted: the daemon answers every viewport registration
  // with a `geometry` frame, so an eager fit on that frame re-registers a still-moving
  // grid and the echo schedules the next pass — a pseudoconsole resize per websocket
  // round-trip for as long as a splitter drag lasts, invisible to all three triggers
  // above. Measured at ~25 resizes/s per pane before it was classed as a burst.
  const geometryStart = source.indexOf("frame.type === 'geometry'")
  const geometryEnd = source.indexOf("frame.type === 'exit'", geometryStart)
  assert.ok(geometryStart >= 0 && geometryEnd > geometryStart, 'geometry handler not found')
  const handlerBody = source.slice(geometryStart, geometryEnd)
  assert.ok(
    handlerBody.includes('scheduleBurstFit()'),
    'the geometry-frame fit is no longer routed through the coalescing scheduler',
  )
  assert.ok(
    !handlerBody.includes(' scheduleFit()'),
    'the geometry-frame handler regained an eager fit',
  )
})

test('a resize restores the tail only for a viewport that was already on it', () => {
  // A ConPTY-backed buffer gains blank rows on a resize rather than pulling
  // scrollback down, so `baseY` moves and the viewport is left above the newest
  // line -- which is the "it scrolls down again" the fix is for. Someone who had
  // deliberately scrolled up must not be yanked to the bottom by their keyboard.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  assert.match(source, /const wasAtTail = !offTailRef\.current/)
  assert.match(source, /if \(wasAtTail\) scrollTerminalToTail\(term\)/)
})

test('the scheduler is fed the measured cost of each pass', () => {
  // Without this the adaptive path never learns, and every pane silently reverts to
  // fitting on every frame. The cost must go through `effectiveViewportCost`: the local
  // clock alone reads microseconds below ConPTY's reflow threshold, so a pass that sent
  // a `resize` frame (a pseudoconsole resize plus a CLI repaint downstream) would keep
  // the scheduler eager through an entire splitter drag — measured at ~22 resizes per
  // second per visible pane before the charge existed.
  const source = readFileSync(join(SRC, 'TerminalPane.tsx'), 'utf8')
  assert.match(
    source,
    /viewportScheduler\.observeCost\(\s*effectiveViewportCost\(performance\.now\(\) - startedAt, viewportResizeSent\),?\s*\)/,
  )
  assert.match(source, /viewportResizeSent = false/)
  assert.match(source, /viewportResizeSent = true/)
})
