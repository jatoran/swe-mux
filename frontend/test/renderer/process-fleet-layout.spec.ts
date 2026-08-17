import { expect, test } from 'playwright/test'

/**
 * The inspector's density is pure CSS plus one `open` class, so nothing else can catch it.
 *
 * The regression this pins: every process printed its command, its evidence chain, its
 * attribution, its first/last seen, and a four-button action row unconditionally — six lines and
 * ~120 px per process, so a single session filled the panel and a phone showed one process at a
 * time. The rule now is one line collapsed, everything still reachable expanded.
 */

const ROW_LIMIT = 34
/** Session trees only: the daemon group draws the same rows but answers different questions. */
const SESSION_ROWS = '.process-project-group:not(.process-daemon-group) .process-row'

async function rowGeometry(page: import('playwright/test').Page) {
  return page.evaluate(() => {
    const box = (element: Element | null) => {
      if (!element) return null
      const rect = (element as HTMLElement).getBoundingClientRect()
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, right: rect.right, bottom: rect.bottom }
    }
    const rows = [...document.querySelectorAll('.process-project-group:not(.process-daemon-group) .process-row')]
    return {
      count: rows.length,
      boxes: rows.map(row => box(row)!),
      // Smaller than its content means the arguments are ellipsizing, which is the trade a
      // one-line row has to make rather than wrapping.
      args: rows.map(row => {
        const element = row.querySelector('.process-args') as HTMLElement | null
        return element ? { client: element.clientWidth, scroll: element.scrollWidth, box: box(element)! } : null
      }),
      metrics: rows.map(row => box(row.querySelector('.process-metrics'))),
      details: document.querySelectorAll('.process-detail').length,
      actions: document.querySelectorAll('.process-tree .process-actions').length,
    }
  })
}

test('every process is one line, and nothing is expanded until it is asked for', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/process-fleet-harness.html')
  await page.waitForSelector(SESSION_ROWS)
  const g = await rowGeometry(page)

  expect(g.count).toBe(5)
  for (const row of g.boxes) expect(row.height).toBeLessThanOrEqual(ROW_LIMIT)
  // Collapsed means collapsed: no detail block and no destructive button on screen.
  expect(g.details).toBe(0)
  expect(g.actions).toBe(0)
})

test('the line yields its arguments before it yields its numbers', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/process-fleet-harness.html')
  await page.waitForSelector(SESSION_ROWS)
  const g = await rowGeometry(page)

  // The Claude row's command is far longer than any panel, so it must be the part that clips.
  const claude = g.args[0]!
  expect(claude.scroll).toBeGreaterThan(claude.client)
  const overflow = await page.evaluate(() =>
    getComputedStyle(document.querySelector('.process-args')!).textOverflow)
  expect(overflow).toBe('ellipsis')

  // And the metrics stay whole, on the same line, inside the row.
  for (const [index, metrics] of g.metrics.entries()) {
    expect(metrics!.width).toBeGreaterThan(0)
    expect(metrics!.right).toBeLessThanOrEqual(g.boxes[index].right + 0.5)
    expect(metrics!.bottom).toBeLessThanOrEqual(g.boxes[index].bottom + 0.5)
  }
})

test('expanding a row reveals the evidence and the actions it guards', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/process-fleet-harness.html')
  await page.waitForSelector(SESSION_ROWS)
  await page.locator(SESSION_ROWS).first().click()

  // Rendered uppercase by `text-transform`, so compared case-insensitively.
  const labels = (await page.locator('.process-detail dt').allInnerTexts()).map(label => label.toLowerCase())
  expect(labels).toEqual(['command', 'parent', 'evidence', 'attribution', 'checked', 'seen', 'network'])
  await expect(page.locator('.process-detail .process-actions button', { hasText: 'Terminate tree' })).toBeVisible()
  // One row expanded, not all of them.
  expect(await page.locator('.process-detail').count()).toBe(1)

  await page.locator(SESSION_ROWS).first().click()
  expect(await page.locator('.process-detail').count()).toBe(0)
})

/** Nesting is the reason the tree exists; density must not flatten it. */
test('the tree still draws its parent-child edges', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/process-fleet-harness.html')
  await page.waitForSelector(SESSION_ROWS)

  const depths = await page.evaluate(() =>
    [...document.querySelectorAll('.process-project-group:not(.process-daemon-group) .process-row')].map(row => {
      let depth = 0
      for (let node = row.parentElement; node; node = node.parentElement) {
        if (node.classList.contains('process-tree')) break
        if (node.tagName === 'UL') depth += 1
      }
      return depth
    }))
  // cmd.exe → node.exe → {vite, esbuild}
  expect(depths).toEqual([0, 0, 1, 2, 2])

  const indents = await page.evaluate(() =>
    [...document.querySelectorAll('.process-project-group:not(.process-daemon-group) .process-row')].map(row => row.getBoundingClientRect().x))
  expect(indents[2]).toBeGreaterThan(indents[1])
  expect(indents[3]).toBeGreaterThan(indents[2])
})

/**
 * A heading reading `1 proc · CPU 2.5% · 571.0 MiB` above a row reading `2.5% · 571.0 MiB`, under
 * a Project heading that already said `swe-mux`, is the same fact printed three times.
 */
test('a session heading never restates its Project or its only row', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/process-fleet-harness.html')
  await page.waitForSelector(SESSION_ROWS)

  const headings = await page.evaluate(() =>
    [...document.querySelectorAll('.process-session-group > .process-session-heading')].map(heading => ({
      title: (heading.querySelector('strong') as HTMLElement).innerText.trim(),
      rollup: (heading.querySelector('small') as HTMLElement | null)?.innerText.trim() || '',
    })))

  // Sessions are named the way the sidebar names them: a generated title while a session is
  // still auto-named, the hand-given name once it has been renamed.
  expect(headings.map(heading => heading.title)).toEqual(['swe-mux runtime', 'Fix the parser', 'release prep'])
  expect(headings[1].rollup).toBe('')
  // The multi-process session keeps its rollup, because there it aggregates.
  expect(headings[2].rollup).toContain('4 proc')
})

/** The width where a wrapped row is acceptable and a truncated number is not. */
test('at phone width a row stays within two lines and keeps its numbers', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/process-fleet-harness.html')
  await page.waitForSelector(SESSION_ROWS)
  const g = await rowGeometry(page)

  for (const row of g.boxes) expect(row.height).toBeLessThanOrEqual(ROW_LIMIT * 2)
  for (const [index, metrics] of g.metrics.entries()) {
    expect(metrics!.width).toBeGreaterThan(0)
    expect(metrics!.right).toBeLessThanOrEqual(g.boxes[index].right + 0.5)
  }
})
