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

// Four flat views, no group rail: `attend` and `review` are gone, because the surfaces
// under them had homes of their own (Alerts and Activity/Findings) and this dashboard was
// drawing second copies. Nothing here has a sub-tab strip any more.
const openTab = async (page: import('playwright/test').Page, tab: string) => {
  await page.goto('/automation-cost-harness.html')
  await page.waitForSelector('.automation-tabs button')
  await page.locator('.automation-tabs button', { hasText: tab }).click()
}

/**
 * The spend view fetches when it mounts, which is when its tab is first selected.
 *
 * That is new: it used to be drawn from the dashboard's single startup load, so its rows
 * were on screen before any tab could be clicked. It is a shared component now
 * (`AutomationSpendView`) so that Resources can draw the identical table, and a shared
 * component owns its own data. Reading the table without waiting measures the frame
 * before the response lands and reports an empty table as a formatting regression.
 */
const openSpend = async (page: import('playwright/test').Page) => {
  await openTab(page, 'cost breakdown')
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

/** The status line used to be drawn over the first section heading in exactly this view. */
test('the panel frame holds every view', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  for (const tab of ['cost breakdown', 'rules & observers', 'projects', 'learned fixes', 'diagnostics'] as const) {
    await openTab(page, tab)
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
  await openTab(page, 'rules & observers')

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
  expect(labels).toEqual(['automation', 'today', '7 days', 'calls', 'tokens', 'cached', 'model'])
  await expect(page.locator('.cost-table thead')).toBeHidden()
})

test('the projects view answers what runs where, including "nothing"', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openTab(page, 'projects')
  const rows = page.locator('.automation-matrix tbody tr')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toContainText('swe-mux')
  await expect(rows.nth(0)).toContainText('Loop detection')
  // An opted-out Project is a row saying so, never a missing row: silence must read
  // as "off", not as "covered".
  await expect(rows.nth(1)).toContainText('nothing')
  // Read-only: the only control is the link into that Project's own editor.
  await expect(rows.nth(1).locator('.setting-link')).toHaveText('Project settings')
})

test('the dashboard keeps no second copy of the surfaces that moved out', async ({ page }) => {
  await page.goto('/automation-cost-harness.html')
  await page.waitForSelector('.automation-tabs button')
  const tabs = await page.locator('.automation-tabs button').allInnerTexts()
  expect(tabs).toEqual(['rules & observers', 'projects', 'cost breakdown', 'learned fixes', 'diagnostics'])
  await expect(page.locator('.automation-subtabs')).toHaveCount(0)
  // The way back to the two inboxes this dashboard used to duplicate is a permanent row,
  // not an empty-state hint: "where did the attention inbox go" is asked by someone
  // looking at a full one somewhere else.
  //
  // `Usage & spend` is a third kind of entry and rides the same row. It is not a surface
  // this dashboard ever duplicated - it is the other half of the same question, since the
  // spend table here is one of three pots and only Usage draws the other two, so "is this
  // a lot" is answerable only over there.
  const elsewhere = page.locator('.automation-elsewhere button')
  await expect(elsewhere).toHaveText([/Attention inbox/, 'Run notes', 'Usage & spend'])
})
