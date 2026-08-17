import { expect, test } from 'playwright/test'

/**
 * Two facts about the Change Map that only a browser can establish.
 *
 * Sigma draws into a container by appending absolutely positioned canvases and reading the
 * container's client box for its viewport, so a host that collapses to zero height renders
 * nothing and reports no error at all. That failure is invisible to a unit test and to a
 * screenshot of a small graph, which is why the geometry is asserted here.
 *
 * The mobile projection is the second: below 760px the pane must fall off WebGL entirely and
 * draw lists, because a Sigma canvas strands on a phone's pixel ratio and a force layout read
 * through a 380px viewport is unreadable even when it does draw.
 */

const CANVAS = '.change-map-canvas'

test('the graph host fills the pane and the chrome stays out of its way', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/change-map-harness.html')
  await page.waitForSelector(`${CANVAS} canvas`)

  const geometry = await page.evaluate(() => {
    const box = (selector: string) => {
      const element = document.querySelector(selector)
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, bottom: rect.bottom, top: rect.top }
    }
    return {
      pane: box('.change-map-pane')!,
      canvas: box('.change-map-canvas')!,
      header: box('.change-map-header')!,
      footer: box('.change-map-footer')!,
      canvases: [...document.querySelectorAll('.change-map-canvas canvas')]
        .map(element => ({ width: (element as HTMLCanvasElement).width, height: (element as HTMLCanvasElement).height })),
      legend: document.querySelectorAll('.change-map-legend-item').length,
      footerNotes: [...document.querySelectorAll('.change-map-footer small')].map(note => note.textContent || ''),
    }
  })

  // Sigma layers several canvases; every one of them must have real pixels.
  expect(geometry.canvases.length).toBeGreaterThan(0)
  for (const canvas of geometry.canvases) {
    expect(canvas.width).toBeGreaterThan(0)
    expect(canvas.height).toBeGreaterThan(0)
  }
  // The canvas takes what the header, controls, legend, and footer leave, and no more.
  expect(geometry.canvas.height).toBeGreaterThan(200)
  expect(geometry.canvas.top).toBeGreaterThanOrEqual(geometry.header.bottom - 0.5)
  expect(geometry.canvas.bottom).toBeLessThanOrEqual(geometry.footer.top + 0.5)
  expect(geometry.footer.bottom).toBeLessThanOrEqual(geometry.pane.bottom + 0.5)

  // Three roles, always drawn, and both daemon captions always shown.
  expect(geometry.legend).toBe(3)
  expect(geometry.footerNotes.length).toBe(2)
  expect(geometry.footerNotes[0]).toContain('Excludes')
  expect(geometry.footerNotes[1]).toContain('lower bound')
})

/**
 * The CSP regression guard. `script-src 'self'` with no `worker-src` and no `blob:` means
 * graphology's stock blob-URL worker helper is refused outright — and refused *silently*,
 * because the pane draws its seeded ring first and simply never receives a layout. A map
 * that is never laid out still looks like a map, so only the worker's own answer proves it.
 */
test('ForceAtlas2 runs in the bundled module worker and moves the graph', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('pageerror', error => consoleErrors.push(error.message))

  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/change-map-harness.html')
  await page.waitForFunction(() => window.changeMapLayoutResults.length > 0)

  const result = await page.evaluate(() => window.changeMapLayoutResults[0])
  const positions = Object.entries(result.positions)
  expect(positions.length).toBe(8)
  for (const [path, point] of positions) {
    expect(Number.isFinite(point.x), path).toBe(true)
    expect(Number.isFinite(point.y), path).toBe(true)
  }
  // Every node at the origin, or every node still on its seeded ring, would both mean the
  // layout never ran. Distinct coordinates are what says it did.
  const distinct = new Set(positions.map(([, point]) => `${point.x.toFixed(4)},${point.y.toFixed(4)}`))
  expect(distinct.size).toBe(8)
  expect(consoleErrors.join('\n')).not.toContain('Content Security Policy')
})

test('at phone width the pane draws lists instead of a WebGL canvas', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/change-map-harness.html')
  await page.waitForSelector('.change-map-group')

  expect(await page.locator(CANVAS).count()).toBe(0)
  const groups = await page.locator('.change-map-group h3').allInnerTexts()
  expect(groups.length).toBe(3)
  expect(groups[0].toLowerCase()).toContain('edited here')

  // Every row is a real touch target, and none of them overflows the phone viewport.
  const rows = await page.evaluate(() =>
    [...document.querySelectorAll('.change-map-group li button')].map(row => {
      const rect = row.getBoundingClientRect()
      return { height: rect.height, right: rect.right }
    }))
  expect(rows.length).toBe(8)
  for (const row of rows) {
    expect(row.height).toBeGreaterThanOrEqual(38)
    expect(row.right).toBeLessThanOrEqual(390.5)
  }
})

test('selecting a file names it, its role, and how many links it has', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/change-map-harness.html')
  await page.waitForSelector('.change-map-group')

  await page.locator('.change-map-group li button', { hasText: 'server.py' }).first().click()
  const detail = page.locator('.change-map-detail')
  await expect(detail).toBeVisible()
  await expect(detail.locator('> code')).toHaveText('src/swe_mux/server.py')
  // `imports` plus `calls` between the same pair is one relationship, counted once each
  // way; server.py has one dependent pair and two forward imports.
  await expect(detail).toContainText('3 links')

  await detail.locator('button', { hasText: 'clear' }).click()
  await expect(detail).toHaveCount(0)
})

/**
 * The list projection has no picture, so the links a node has must be spelled out.
 * This is also where the open-file plumbing is proved end to end, because a graph node
 * has no DOM to click: what reaches the host must be the node's true-cased
 * `display_path`, never the casefolded `path` the graph stores.
 */
test('a selected file lists what it links to and opens by its real path', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/change-map-harness.html')
  await page.waitForSelector('.change-map-group')

  await page.locator('.change-map-group li button', { hasText: 'server.py' }).first().click()
  const detail = page.locator('.change-map-detail')
  const neighbors = detail.locator('.change-map-neighbors code')
  await expect(neighbors).toHaveText([
    'src/swe_mux/mcp.py', 'src/swe_mux/config.py', 'src/swe_mux/layouts.py',
  ])
  // Tapping a link moves the selection there, so the list is navigable rather than
  // only informative.
  await neighbors.filter({ hasText: 'mcp.py' }).click()
  await expect(detail.locator('> code')).toHaveText('src/swe_mux/mcp.py')

  await page.locator('.change-map-group li button', { hasText: 'server.py' }).first().click()
  await detail.locator('button', { hasText: 'open file' }).click()
  expect(await page.evaluate(() => window.changeMapOpened)).toEqual([
    { path: 'src/swe_mux/Server.py', worktree: null },
  ])
})

test('a file that no longer exists offers a dead button, not a broken link', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/change-map-harness.html')
  await page.waitForSelector('.change-map-group')

  await page.locator('.change-map-group li button', { hasText: 'store.py' }).first().click()
  const open = page.locator('.change-map-detail button', { hasText: 'open file' })
  await expect(open).toBeDisabled()
  expect(await page.evaluate(() => window.changeMapOpened.length)).toBe(0)
})

test('the map states what it refused to draw', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/change-map-harness.html')
  await page.waitForSelector(`${CANVAS} canvas`)

  // A dropped file is a file the reader knows they changed. Silently omitting it is
  // the failure this caption exists to prevent.
  await expect(page.locator('.change-map-status')).toContainText('1 edited file not shown')
  await expect(page.locator('.change-map-status')).toContainText('1 outside this checkout')
})

/**
 * The highlight is drawn by Sigma's per-frame reducers and lands nowhere in the DOM,
 * so nothing but the renderer's own resolved display data can prove it happened. The
 * pointer is driven through the real canvas, which means Sigma's hit-testing is under
 * test too — the detail card naming the expected file is what says the click landed.
 */
test('focusing a node highlights its links and dims the rest', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/change-map-harness.html')
  await page.waitForSelector(`${CANVAS} canvas`)
  await page.waitForFunction(() => window.changeMapLayoutResults.length > 0)

  // Sigma's display data is already normalized, so `framedGraphToViewport` is the
  // converter that lands on the node; the graph-space one normalizes a second time
  // and puts the pointer on empty canvas.
  const seat = async (node: string) => await page.evaluate(target => {
    const sigma = window.changeMapSigma!
    const data = sigma.getNodeDisplayData(target)!
    const point = sigma.framedGraphToViewport({ x: data.x, y: data.y })
    const box = document.querySelector('.change-map-canvas')!.getBoundingClientRect()
    return { x: box.x + point.x, y: box.y + point.y }
  }, node)

  // Sigma fits the laid-out graph inside the viewport with padding, so the extreme
  // corner is the one place guaranteed to hold no node — which is what makes it a
  // reliable "pointer is over nothing" position.
  const empty = await page.evaluate(() => {
    const box = document.querySelector('.change-map-canvas')!.getBoundingClientRect()
    return { x: box.x + 2, y: box.y + 2 }
  })

  const shades = async () => await page.evaluate(() => {
    const sigma = window.changeMapSigma!
    const read = (key: string) => {
      const data = sigma.getNodeDisplayData(key)
      return { color: data?.color || '', forced: !!data?.forceLabel }
    }
    return {
      focused: read('src/swe_mux/server.py'),
      neighbor: read('src/swe_mux/mcp.py'),
      stranger: read('src/swe_mux/code_graph.py'),
    }
  })

  const plain = await shades()
  expect(plain.focused.forced).toBe(false)

  // Hovering previews without selecting: no detail card, but the picture changes.
  const target = await seat('src/swe_mux/server.py')
  await page.mouse.move(target.x, target.y)
  await expect.poll(async () => (await shades()).focused.forced).toBe(true)
  const hovered = await shades()
  expect(hovered.neighbor.forced).toBe(true)
  expect(hovered.stranger.forced).toBe(false)
  // Dimming is a mix toward the pane background, so an out-of-focus node's colour
  // must actually change — a highlight that leaves every colour alone is not one.
  expect(hovered.stranger.color).not.toBe(plain.stranger.color)
  expect(hovered.neighbor.color).toBe(plain.neighbor.color)

  // Leaving releases it, because nothing is selected yet.
  await page.mouse.move(empty.x, empty.y)
  await expect.poll(async () => (await shades()).stranger.color).toBe(plain.stranger.color)

  // Clicking pins it, and the highlight now survives the pointer leaving the node —
  // which is the whole difference between a hover preview and a selection.
  await page.mouse.click(target.x, target.y)
  await expect(page.locator('.change-map-detail > code')).toHaveText('src/swe_mux/server.py')
  await page.mouse.move(empty.x, empty.y)
  await expect.poll(async () => (await shades()).focused.forced).toBe(true)
  expect((await shades()).stranger.forced).toBe(false)

  // Clicking the pinned node again releases it, the way the list rows do. Two things
  // this needs to be a second *click* rather than a second half of a double-click:
  // the seat is recomputed, because opening the detail card reflows the pane and
  // resizes the canvas so every node has moved; and the wait clears Sigma's
  // double-click window (300ms), inside which a second click is a zoom gesture and
  // no `clickNode` is emitted at all.
  await page.waitForTimeout(450)
  const pinned = await seat('src/swe_mux/server.py')
  await page.mouse.click(pinned.x, pinned.y)
  await expect(page.locator('.change-map-detail')).toHaveCount(0)
  await page.mouse.move(empty.x, empty.y)
  await expect.poll(async () => (await shades()).stranger.color).toBe(plain.stranger.color)
})
