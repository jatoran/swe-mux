import { expect, test } from 'playwright/test'

/**
 * The Timeline tab is a sequence, and a sequence is only readable if one entry costs about
 * one entry's worth of screen. Every record used to render its asked / intent / claim /
 * blocked / evidence stack at once, so eight records filled several screens and the tab
 * read as a wall of detail rather than a timeline.
 *
 * None of that is checkable below the browser: it is row height, what a closed row still
 * says, and whether the enablement block stayed above the list. The source-level assertions
 * in `test/scanTimeline.test.ts` prove the markup exists; these prove it lays out.
 */

const HARNESS = '/scan-timeline-harness.html'

test('a closed record costs about a row, and the whole run fits in a screen', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(HARNESS)
  await page.waitForSelector('.scan-record')

  const rows = page.locator('.scan-record')
  await expect(rows).toHaveCount(8)
  // Nothing is expanded on arrival.
  await expect(page.locator('.scan-record-detail')).toHaveCount(0)

  const heights = await rows.evaluateAll(list => list.map(row => row.getBoundingClientRect().height))
  // Two short lines plus padding: ~44px measured. The same record opened is 164-265px,
  // which is what every record used to cost — these eight totalled 1898px, five screens
  // of this drawer, and now total ~361px.
  for (const height of heights) expect(height).toBeLessThan(56)
  // Uniform: no row is half again another, which is what an unbounded summary produces.
  expect(Math.max(...heights)).toBeLessThan(Math.min(...heights) * 1.5)
  const total = heights.reduce((sum, height) => sum + height, 0)
  expect(total).toBeLessThan(420)
})

test('a closed record still says which record it is', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(HARNESS)
  await page.waitForSelector('.scan-record')

  const first = page.locator('.scan-record').first()
  // Time, phase, lifecycle state, and one line of what happened.
  await expect(first.locator('time')).toHaveText(/\d{1,2}:\d{2}/)
  await expect(first.locator('.scan-record-line strong')).toHaveText('implementing')
  await expect(first.locator('.scan-record-state')).toHaveText('active')
  await expect(first.locator('.scan-record-gist')).toContainText('Record 1')
  // A record whose model produced nothing still identifies itself rather than rendering blank.
  await expect(page.locator('.scan-record').nth(5).locator('.scan-record-gist'))
    .toHaveText('No semantic change recorded.')

  // The gist is clamped rather than truncated in the data: the full text is available to
  // the reader who opens the row, and to a hover before they do.
  const gist = first.locator('.scan-record-gist')
  const clipped = await gist.evaluate(node => node.scrollHeight > node.clientHeight + 1)
  expect(clipped).toBe(true)
  await expect(first.locator('.scan-record-head')).toHaveAttribute('title', /moved the evidence disclosures/)
})

test('the flags that say a run stalled survive the collapse', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(HARNESS)
  await page.waitForSelector('.scan-record')

  const flagsOf = (index: number) => page.locator('.scan-record').nth(index).locator('.scan-record-flag')
  await expect(flagsOf(0)).toHaveCount(0)
  await expect(flagsOf(1)).toHaveText(['blocked'])
  await expect(flagsOf(2)).toHaveText(['behind'])
  await expect(flagsOf(3)).toHaveText(['repaired'])
  await expect(flagsOf(4)).toHaveText(['dead end'])
})

test('opening one record opens only that record, and closing it returns the row', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(HARNESS)
  await page.waitForSelector('.scan-record')

  const first = page.locator('.scan-record').first()
  const closed = await first.evaluate(row => row.getBoundingClientRect().height)

  await first.locator('.scan-record-head').click()
  await expect(first.locator('.scan-record-detail')).toHaveCount(1)
  await expect(page.locator('.scan-record-detail')).toHaveCount(1)
  await expect(first.locator('.scan-record-head')).toHaveAttribute('aria-expanded', 'true')
  // Detail is detail: the fields the compact row does not carry.
  await expect(first.locator('dt')).toHaveText(['Asked', 'Intent', 'Claim'])
  await expect(first.locator('.scan-record-targets summary')).toContainText('Evidence targets')
  await expect(first.locator('footer button')).toHaveText('View source')
  // The summary stops being clamped once there is room for it.
  const gist = first.locator('.scan-record-gist')
  expect(await gist.evaluate(node => node.scrollHeight > node.clientHeight + 1)).toBe(false)
  expect(await first.evaluate(row => row.getBoundingClientRect().height)).toBeGreaterThan(closed * 2)

  await first.locator('.scan-record-head').click()
  await expect(first.locator('.scan-record-detail')).toHaveCount(0)
  expect(await first.evaluate(row => row.getBoundingClientRect().height)).toBe(closed)
})

test('an opened record reaches its source, and the caret follows the state', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(HARNESS)
  await page.waitForSelector('.scan-record')

  const first = page.locator('.scan-record').first()
  const caret = first.locator('.scan-record-caret')
  const shut = await caret.evaluate(node => getComputedStyle(node).transform)
  await first.locator('.scan-record-head').click()
  await expect.poll(() => caret.evaluate(node => getComputedStyle(node).transform)).not.toBe(shut)

  await first.locator('footer button').click()
  await expect(first.locator('.scan-source')).toContainText('rehydrated transcript message')
  await expect(first.locator('footer button')).toHaveText('Hide source')
})

test('collapsing records never collapses the boundary or the liveness block', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(HARNESS)
  await page.waitForSelector('.scan-record')

  // A conversation boundary is a landmark in the sequence, not an entry to open.
  const boundary = page.locator('.scan-boundary')
  await expect(boundary).toHaveCount(1)
  await expect(boundary).toContainText('New conversation')
  await expect(boundary.locator('button')).toHaveCount(0)

  // Why a quiet timeline is quiet stays in the panel chrome, above the scroller, where no
  // record collapse can reach it: budget-stopped and genuinely idle look identical from an
  // empty tail, and only this line separates them.
  const stopped = page.locator('.scan-gate.scan-stopped')
  await expect(stopped).toContainText('the hourly call cap for scan timeline is spent')
  await expect(page.locator('.scan-budget summary em')).toContainText('/')
  const [gateBottom, listTop] = await page.evaluate(() => [
    document.querySelector('.scan-gate.scan-stopped')!.getBoundingClientRect().bottom,
    document.querySelector('.scan-timeline-list')!.getBoundingClientRect().top,
  ])
  expect(gateBottom).toBeLessThanOrEqual(listTop + 1)
})

test('on a phone the compact row is a tap target', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto(`${HARNESS}?viewport=phone`)
  await page.waitForSelector('.scan-record')

  const heads = page.locator('.scan-record-head')
  const boxes = await heads.evaluateAll(list => list.map(head => head.getBoundingClientRect()))
  for (const box of boxes) expect(box.height).toBeGreaterThanOrEqual(44)
  // Full-bleed, so the target is the row rather than a caret at its edge.
  const width = await page.evaluate(() => document.querySelector('.scan-timeline-list')!.clientWidth)
  for (const box of boxes) expect(box.width).toBeGreaterThan(width - 2)

  await heads.first().click()
  await expect(page.locator('.scan-record-detail')).toHaveCount(1)
  // The list scrolls; the page does not.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)
  expect(overflow).toBe(true)
})
