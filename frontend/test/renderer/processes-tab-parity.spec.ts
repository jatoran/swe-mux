import { expect, test } from 'playwright/test'

/**
 * The drawer's Processes tab is the modal inspector's surface at column width.
 *
 * The regression this pins is the split it replaced: the tab drew one rollup row per session and
 * nothing else, so the surface you actually have open beside a terminal could not answer what is
 * running under it, and every real investigation ended in "now open the other one". The defence
 * was that a destructive confirm in a 300 px column is how the wrong tree gets killed — a claim
 * about layout, which is why these assertions are about layout: the column gets the full tree,
 * the full evidence, and the same guarded actions, and none of it overflows the column.
 *
 * `process-fleet-layout.spec.ts` pins the same rules for the modal. What is only true here is the
 * Project scope and the marking of the focused session.
 */

const ROWS = '.processes-tab .process-row'
const COLUMN = 300

test('the column draws the whole tree, nested, and stays inside its 300 px', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/processes-tab-harness.html')
  await page.waitForSelector(ROWS)

  const geometry = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.processes-tab .process-row')]
    return {
      count: rows.length,
      depths: rows.map(row => {
        let depth = 0
        for (let node = row.parentElement; node; node = node.parentElement) {
          if (node.classList.contains('process-tree')) break
          if (node.tagName === 'UL') depth += 1
        }
        return depth
      }),
      right: rows.map(row => row.getBoundingClientRect().right),
      // Two lines at column width is the trade the modal already makes on a phone.
      heights: rows.map(row => row.getBoundingClientRect().height),
      columnWidth: document.querySelector('#drawer-column')!.getBoundingClientRect().width,
      bodyOverflow: document.body.scrollWidth - document.body.clientWidth,
    }
  })

  // claude.exe, then cmd.exe → node.exe → {vite node.exe, esbuild.exe}. Nothing is rolled up.
  expect(geometry.count).toBe(5)
  expect(geometry.depths).toEqual([0, 0, 1, 2, 2])
  expect(geometry.columnWidth).toBe(COLUMN)
  for (const right of geometry.right) expect(right).toBeLessThanOrEqual(COLUMN + 0.5)
  for (const height of geometry.heights) expect(height).toBeLessThanOrEqual(80)
  expect(geometry.bodyOverflow).toBeLessThanOrEqual(0)
})

test('a process in the column expands to the same evidence and the same guarded actions', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/processes-tab-harness.html')
  await page.waitForSelector(ROWS)

  expect(await page.locator('.process-detail').count()).toBe(0)
  await page.locator(ROWS).first().click()

  const labels = (await page.locator('.process-detail dt').allInnerTexts()).map(label => label.toLowerCase())
  expect(labels).toEqual(['command', 'parent', 'evidence', 'attribution', 'checked', 'seen', 'network'])

  const terminate = page.locator('.process-detail .process-actions button', { hasText: /^Terminate$/ })
  await expect(terminate).toBeVisible()
  await expect(page.locator('.process-detail .process-actions button', { hasText: 'Terminate tree' })).toBeVisible()
  await expect(page.locator('.process-detail .process-actions button', { hasText: 'Interrupt' })).toBeVisible()

  // The confirm is the same two-press confirm the modal uses — one press arms, it does not act.
  await terminate.click()
  await expect(page.locator('.process-detail .process-actions button.confirming')).toHaveCount(1)
})

test('the tab opens scoped to its Project, and the runtime is not filed under one', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/processes-tab-harness.html')
  await page.waitForSelector(ROWS)

  const projects = await page.locator('.processes-tab .process-project-group > h2').allInnerTexts()
  expect(projects).toEqual(['project::swe-mux'])
  // The other Project's session and the swe-mux runtime are both out of scope.
  await expect(page.locator('.processes-tab', { hasText: 'pwsh.exe' })).toHaveCount(0)
  await expect(page.locator('.process-daemon-group')).toHaveCount(0)
  await expect(page.locator('.process-scope-select')).toHaveValue('p1')
})

test('the focused session is pinned first in its Project and says why', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/processes-tab-harness.html')
  await page.waitForSelector(ROWS)

  const headings = await page.locator('.processes-tab .process-session-heading strong').allInnerTexts()
  // `release prep` is the older session, so age order alone would put it first.
  expect(headings).toEqual(['Fix the parser', 'release prep'])

  const groups = page.locator('.processes-tab .process-session-group')
  await expect(groups.first()).toHaveClass(/focused/)
  await expect(groups.first().locator('.process-session-focused')).toBeVisible()
  await expect(groups.nth(1)).not.toHaveClass(/focused/)
})

test('a server bound to both stacks is one previewable row, named by its process', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/processes-tab-harness.html')
  await page.waitForSelector(ROWS)

  const links = page.locator('.processes-tab .process-link-row')
  await expect(links).toHaveCount(1)
  await expect(links.locator('strong')).toHaveText('http://127.0.0.1:5173/')
  await expect(links.locator('button', { hasText: 'preview' })).toBeVisible()
})

test('drilling into a session narrows the tab to it, and back out restores the Project', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/processes-tab-harness.html')
  await page.waitForSelector(ROWS)

  await page.locator('.processes-tab .process-session-heading').nth(1).click()
  await expect(page.locator('.processes-tab .process-session-group')).toHaveCount(1)
  // The one control that only makes sense against a single session comes with it.
  await expect(page.locator('.processes-tab .process-toolbar input')).toBeVisible()
  await expect(page.locator('.processes-tab .process-row')).toHaveCount(4)

  await page.locator('.processes-tab .process-session-heading').first().click()
  await expect(page.locator('.processes-tab .process-session-group')).toHaveCount(2)
})
