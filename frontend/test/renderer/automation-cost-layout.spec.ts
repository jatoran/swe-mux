import { expect, test } from 'playwright/test'

/**
 * The automation dashboard's figures tables, measured in a real layout.
 *
 * Three regressions live here, and none of them is visible to a unit test.
 *
 * The first is truncation. These tables mix ten-figure token counts with sub-cent costs, and
 * printed at full precision in nowrap columns they overflowed every window; the ten-column
 * telemetry table was the one nobody could read. The second is the phone, where a nowrap table
 * inside a horizontal scroller can never show a column and the header naming it at the same
 * time. The third is the panel's own frame: its child count changes with the view, so the
 * fixed grid rows it used to declare fitted one view and drew the status line over the first
 * heading in the other.
 */

const openTab = async (page: import('playwright/test').Page, tab: string) => {
  await page.goto('/automation-cost-harness.html')
  await page.waitForSelector('.automation-tabs button')
  await page.locator('.automation-tabs button', { hasText: tab }).click()
}

const boxes = (page: import('playwright/test').Page, selector: string) =>
  page.evaluate(target => [...document.querySelectorAll(target)].map(element => {
    const rect = element.getBoundingClientRect()
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, right: rect.right, bottom: rect.bottom }
  }), selector)

test('the spend view answers which automation costs what, ranked', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'spend')

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

test('a cost too small to print never renders as free', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'spend')

  const row = page.locator('.cost-table tbody tr', { hasText: 'Doc drift watch' })
  await expect(row.locator('td').nth(1)).toHaveText('<$0.0001')
  // And the exact figure survives as the cell's title rather than being thrown away.
  await expect(row.locator('td').nth(2)).toHaveAttribute('title', '$0.0004')
})

test('no figures table overflows its panel at desktop width', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  for (const [tab, sub] of [['spend', null], ['attend', 'all-session health']] as const) {
    await openTab(page, tab)
    if (sub) await page.locator('.automation-subtabs button', { hasText: sub }).click()
    const overflow = await page.evaluate(() =>
      [...document.querySelectorAll('.usage-table-scroll')].map(element => ({
        scroll: element.scrollWidth, client: element.clientWidth,
      })))
    expect(overflow.length).toBeGreaterThan(0)
    for (const box of overflow) expect(box.scroll).toBeLessThanOrEqual(box.client + 1)
  }
})

/** The status line used to be drawn over the first section heading in exactly this view. */
test('the panel frame holds every view, sub-tab strip or not', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  for (const [tab, sub] of [['spend', null], ['attend', 'all-session health'], ['configure', null]] as const) {
    await openTab(page, tab)
    if (sub) await page.locator('.automation-subtabs button', { hasText: sub }).click()
    const [progress] = await boxes(page, '.usage-progress')
    const [main] = await boxes(page, '.automation-panel > main')
    const [footer] = await boxes(page, '.automation-panel > footer')
    expect(main.y).toBeGreaterThanOrEqual(progress.bottom - 0.5)
    expect(main.bottom).toBeLessThanOrEqual(footer.y + 0.5)
    // And the body is the part that grew, so it is the part that scrolls.
    expect(main.height).toBeGreaterThan(200)
  }
})

/** Left as an ordinary grid cell the strip took a half-width column and truncated four of its
 *  six labels, then stretched to the height of the section beside it. */
test('the summary strip spans the columns it heads', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'configure')

  const [summary] = await boxes(page, '.usage-tables > .usage-summary')
  const [tables] = await boxes(page, '.usage-tables')
  expect(summary.width).toBeGreaterThan(tables.width - 2)

  const clipped = await page.evaluate(() =>
    [...document.querySelectorAll('.usage-summary article > span')]
      .filter(element => element.scrollWidth > element.clientWidth).length)
  expect(clipped).toBe(0)
})

test('at phone width every table row becomes a labelled card', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await openTab(page, 'spend')

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
  expect(labels).toEqual(['automation', 'today', '7 days', 'calls', 'tokens', 'model'])
  await expect(page.locator('.cost-table thead')).toBeHidden()
})

test('the telemetry table reads at human scale rather than raw units', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'attend')
  await page.locator('.automation-subtabs button', { hasText: 'all-session health' }).click()

  const row = page.locator('.usage-table', { hasText: 'Observed workload telemetry' }).locator('tbody tr').first()
  // 8403 seconds and 9,702,931,354 tokens are both technically present and practically unread.
  await expect(row.locator('td').nth(2)).toHaveText('2h 20m')
  await expect(row.locator('td').nth(6)).toHaveText('9.7B')
  // The exact total is still one hover away rather than discarded by the rounding.
  await expect(row.locator('td').nth(6)).toHaveAttribute('title', /9.702.931.354/)
})
