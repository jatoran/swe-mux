import { expect, test } from 'playwright/test'

/**
 * The automation dashboard, measured in a real layout.
 *
 * Three regressions live here, and none of them is visible to a unit test.
 *
 * The first is truncation. The spend tables mix ten-figure token counts with sub-cent costs,
 * and printed at full precision in nowrap columns they overflowed every window. The second is
 * the phone, where a nowrap table inside a horizontal scroller can never show a column and the
 * header naming it at the same time. The third is the panel's own frame: its child count
 * changes with the view, so fixed grid rows fitted one view and drew the status line over the
 * first heading in the others.
 */

// Three tabs, and the tab is the question: Policy (what may run, and where),
// Usage (what it costs), Activity (what it did).
const openTab = async (page: import('playwright/test').Page, tab: string) => {
  await page.goto('/automation-cost-harness.html')
  await page.waitForSelector('.automation-tabs button')
  await page.locator('.automation-tabs button', { hasText: tab }).click()
}

/**
 * The spend view fetches when it mounts, which is when its tab is first selected.
 *
 * It is a shared component (`AutomationSpendView`) so the Usage dialog draws the identical
 * table, and a shared component owns its own data. Reading the table without waiting
 * measures the frame before the response lands and reports an empty table as a formatting
 * regression.
 */
const openSpend = async (page: import('playwright/test').Page) => {
  await openTab(page, 'Usage')
  await page.locator('.cost-table tbody tr').first().waitFor()
}

const boxes = (page: import('playwright/test').Page, selector: string) =>
  page.evaluate(target => [...document.querySelectorAll(target)].map(element => {
    const rect = element.getBoundingClientRect()
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, right: rect.right, bottom: rect.bottom }
  }), selector)

test('the spend view answers which automation costs what, ranked', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openSpend(page)

  const names = await page.locator('.cost-table tbody .cost-name strong').allInnerTexts()
  expect(names[0]).toBe('Scan timeline')
  // A feature that bills the observer budget without being a rule, and a rule id nothing on
  // the page can turn off any more, both have to be visible rather than folded into a total.
  expect(names).toContain('Read aloud')
  expect(names).toContain('builtin.removed-triage')

  const cells = await page.locator('.cost-table tbody tr').first().locator('td').allInnerTexts()
  expect(cells[1]).toBe('$0.26')
  expect(cells[2]).toContain('$1.83')
  expect(cells[2]).toContain('97%')

  // The rows reconcile with the headline, which is the whole basis for trusting them.
  const footer = await page.locator('.cost-table tfoot td').allInnerTexts()
  expect(footer[1]).toBe('$0.26')
  const headline = await page.locator('.cost-summary article').first().locator('strong').innerText()
  expect(headline).toBe('$0.0006')
})

/**
 * Caching is measured rather than assumed, and the three readings are different answers.
 *
 * A row with billed prompt tokens and no cached ones reads 0% - a real, actionable "this is
 * not caching". A row with no billed prompt tokens at all reads as a dash, because an unused
 * rule and a daemon predating cache accounting look identical and neither is a broken cache.
 */
test('the prompt-cache hit rate sits beside the spend it explains', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openSpend(page)

  const cached = page.locator('.cost-summary article', { hasText: 'prompt cache' })
  await expect(cached.locator('strong')).toHaveText('77%')
  await expect(cached.locator('small')).toContainText('72% over 7d')

  const scan = page.locator('.cost-table tbody tr', { hasText: 'Scan timeline' })
  await expect(scan.locator('td').nth(5)).toHaveText('77%')
  await expect(scan.locator('td').nth(5)).toHaveAttribute(
    'title', '3,000,000 of 3,900,000 prompt tokens served from cache')

  const titler = page.locator('.cost-table tbody tr', { hasText: 'Session titler' })
  await expect(titler.locator('td').nth(5)).toHaveText('0%')

  const retired = page.locator('.cost-table tbody tr', { hasText: 'builtin.removed-triage' })
  await expect(retired.locator('td').nth(5)).toHaveText('—')
})

test('a cost too small to print never renders as free', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openSpend(page)

  const row = page.locator('.cost-table tbody tr', { hasText: 'Doc drift watch' })
  await expect(row.locator('td').nth(1)).toHaveText('<$0.0001')
  // And the exact figure survives as the cell's title rather than being thrown away.
  await expect(row.locator('td').nth(2)).toHaveAttribute('title', '$0.0004')
})

test('no figures table overflows its panel at desktop width', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  // The spend view is the one that mixes ten-figure token counts with sub-cent costs, so
  // it is the one this measures. Resources draws the same component and is covered by
  // `resources-layout.spec.ts` at both widths.
  await openSpend(page)
  const overflow = await page.evaluate(() =>
    [...document.querySelectorAll('.usage-table-scroll')].map(element => ({
      scroll: element.scrollWidth, client: element.clientWidth,
    })))
  expect(overflow.length).toBeGreaterThan(0)
  for (const box of overflow) expect(box.scroll).toBeLessThanOrEqual(box.client + 1)
})

/** The status line used to be drawn over the first section heading. */
test('the panel frame holds every view', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  for (const tab of ['Policy', 'Usage', 'Activity'] as const) {
    await openTab(page, tab)
    const [progress] = await boxes(page, '.usage-progress')
    const [main] = await boxes(page, '.automation-panel > main')
    const [panel] = await boxes(page, '.automation-panel')
    expect(main.y).toBeGreaterThanOrEqual(progress.bottom - 0.5)
    expect(main.bottom).toBeLessThanOrEqual(panel.bottom + 0.5)
    // And the body is the part that grew, so it is the part that scrolls.
    expect(main.height).toBeGreaterThan(200)
  }
})

test('at phone width every table row becomes a labelled card', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await openSpend(page)

  // No horizontal scroll anywhere: the stacked layout replaces the scroller rather than
  // living inside it.
  const overflow = await page.evaluate(() => ({
    body: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    cells: [...document.querySelectorAll('.data-table td')]
      .filter(cell => cell.scrollWidth > cell.clientWidth + 1).length,
  }))
  expect(overflow.body).toBe(true)
  expect(overflow.cells).toBe(0)

  // Each value cell carries its own header, since there is no header row to align to.
  const labels = await page.evaluate(() =>
    [...document.querySelectorAll('.cost-table tbody tr')][0]
      ? [...document.querySelectorAll('.cost-table tbody tr:first-child td')]
        .map(cell => cell.getAttribute('data-label'))
      : [])
  expect(labels).toEqual(
    ['automation', 'today', '7 days', 'calls', 'tokens', 'cached', 'cache $', 'model'])
  await expect(page.locator('.cost-table thead')).toBeHidden()
})

test('at phone width the tab row moves to the bottom of the panel', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/automation-cost-harness.html')
  await page.waitForSelector('.automation-tabs button')
  const [tabs] = await boxes(page, '.automation-panel > .automation-tabs')
  const [main] = await boxes(page, '.automation-panel > main')
  expect(tabs.y).toBeGreaterThan(main.y)
})

test('the policy matrix draws Global and Project side by side, grouped by dependency', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'Policy')
  await page.waitForSelector('.automation-matrix-grid')
  // The Project selector is always visible on this view.
  await expect(page.locator('.automation-masterbar .dropdown-trigger')).toBeVisible()
  // The dependency map is grouped; the structure is the "needs X" story, so rows
  // carry no per-row dependency prose.
  await expect(page.locator('.automation-matrix-grid .project-automation-group')).toContainText([/Foundations/, /Deterministic checks/])
  // Two switches per row: the install-wide ceiling and this Project's opt-in.
  const row = page.locator('.automation-matrix-row', { hasText: 'Loop detection' })
  await expect(row.locator('input[type=checkbox]')).toHaveCount(2)
})

test('a ceiling-blocked row greys the Project switch and keeps the Project choice', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'Policy')
  await page.waitForSelector('.automation-matrix-grid')
  const row = page.locator('.automation-matrix-row', { hasText: 'Doc-debt ledger' })
  await expect(row).toHaveClass(/globally-off/)
  // The Global cell stays operable (it is the switch that turns this back on);
  // the Project cell is disabled but still shows the retained opt-in.
  await expect(row.locator('input[type=checkbox]').nth(0)).toBeEnabled()
  await expect(row.locator('input[type=checkbox]').nth(1)).toBeDisabled()
  await expect(row.locator('input[type=checkbox]').nth(1)).toBeChecked()
})

test('a fresh install opens on the starting-set presets; a returning one gets the button', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'Policy')
  // The renderer context has no localStorage history, so this IS the first run.
  await expect(page.locator('.automation-presets h3')).toContainText('Welcome')
  await expect(page.locator('.automation-preset')).toHaveCount(3)
  await page.locator('.automation-preset-dismiss').click()
  await expect(page.locator('.automation-presets')).toHaveCount(0)
  await expect(page.locator('.automation-preset-toggle')).toContainText('Choose preset')
  // Dismissal is durable: a reload lands on the matrix, not the welcome.
  await openTab(page, 'Policy')
  await expect(page.locator('.automation-presets')).toHaveCount(0)
})

test('the dashboard opens on the Project it was launched from', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/automation-cost-harness.html?project=p2')
  await page.waitForSelector('.automation-matrix-grid')
  // Without the threaded Project this would land on p1, the first Project with
  // anything enabled — which is how you ended up editing the wrong Project's policy.
  await expect(page.locator('.automation-matrix-head span').nth(2)).toHaveText('orca')
})

test('the install-wide limits live behind one disclosure on the Policy tab', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await openTab(page, 'Policy')
  const drawer = page.locator('.automation-limits-drawer')
  await drawer.locator('summary').click()
  await expect(drawer.locator('.automation-policy-view')).toBeVisible()
  await expect(drawer.locator('h3').first()).toHaveText('Budgets & ceilings')
  const geometry = await drawer.locator('.automation-policy-view').evaluate(node => ({
    width: node.getBoundingClientRect().width,
    scrollWidth: node.scrollWidth,
  }))
  expect(geometry.scrollWidth).toBeLessThanOrEqual(Math.ceil(geometry.width) + 1)
})

test('the dashboard keeps no second copy of the surfaces that moved out', async ({ page }) => {
  await page.goto('/automation-cost-harness.html')
  await page.waitForSelector('.automation-tabs button')
  const tabs = await page.locator('.automation-tabs button').allInnerTexts()
  // Activity may carry its unread badge in the same button, so the label is a
  // prefix rather than the whole text.
  expect(tabs).toHaveLength(3)
  for (const [index, label] of ['Policy', 'Usage', 'Activity'].entries()) {
    expect(tabs[index].trim().startsWith(label)).toBe(true)
  }
  await expect(page.locator('.automation-subtabs')).toHaveCount(0)
  // The escape-link row is gone: alerts and spend are mirrored inside (the same
  // AttentionInbox and AutomationSpendView components), and the footer motto with it.
  await expect(page.locator('.automation-elsewhere')).toHaveCount(0)
  await expect(page.locator('.automation-panel > footer')).toHaveCount(0)
})

test('the Activity tab mirrors the attention inbox and holds the diagnostics', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'Activity')
  await expect(page.locator('.automation-activity-alerts h3')).toHaveText('Needs you')
  // The same component the Alerts drawer mounts, over the same endpoints.
  await expect(page.locator('.automation-activity-alerts .attention-inbox, .automation-activity-alerts .grant-gate, .automation-activity-alerts .attention-empty').first()).toBeVisible()
  await expect(page.locator('.automation-diagnostics h3').first()).toHaveText('Diagnostics')
  await expect(page.getByText('Historical event dry-run')).toBeVisible()
})
