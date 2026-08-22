import { expect, test } from 'playwright/test'

/**
 * The Resources dialog: four meters behind one dialog instead of four modals.
 *
 * Two things are measured here that no unit test can see.
 *
 * The frame. Its four segments contribute different numbers of rows — Processes is one
 * element, Network and Storage add a toolbar and a footer, Fleet activity adds a domain rail
 * and a footer — so the panel is a flex column rather than the fixed `grid-template-rows`
 * each single-surface modal could afford. A regression there draws the status line over the
 * first heading, which is exactly what the Automation panel once did.
 *
 * The workload table. It has moved twice — out of the Automation dashboard's health view,
 * then out of the Tokens segment when spend became its own dialog — and its whole reason for
 * existing is that ten-figure token counts and raw seconds are technically present and
 * practically unreadable. Those assertions follow the table to each new home.
 */

const openDomain = async (page: import('playwright/test').Page, label: string) => {
  await page.goto('/resources-harness.html')
  await page.waitForSelector('.usage-domain-tabs button')
  await page.locator('.usage-domain-tabs button', { hasText: label }).click()
}

test('the workload table reads at human scale rather than raw units', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openDomain(page, 'runs + workload')
  await page.locator('.usage-table', { hasText: 'Observed workload' }).locator('tbody tr').first().waitFor()

  const row = page.locator('.usage-table', { hasText: 'Observed workload' }).locator('tbody tr').first()
  // 8403 seconds and 9,702,931,354 tokens are both technically present and practically unread.
  await expect(row.locator('td').nth(2)).toHaveText('2h 20m')
  await expect(row.locator('td').nth(6)).toHaveText('9.7B')
  // The exact total is still one hover away rather than discarded by the rounding.
  await expect(row.locator('td').nth(6)).toHaveAttribute('title', /9.702.931.354/)
})

test('collection health is collapsed beneath the metrics it qualifies', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openDomain(page, 'tools + skills')
  await page.locator('.usage-table', { hasText: 'Cross-project tool metrics' }).locator('tbody tr').first().waitFor()

  // Parser coverage does not measure the fleet — it says whether the two tables above it
  // were collectable at all. Drawn open as a third peer table it pushed the real metrics
  // off the first screen, so it ships closed and the tables sit above it.
  const health = page.locator('.telemetry-collection-health')
  await expect(health).toHaveJSProperty('open', false)
  await expect(health.locator('summary')).toContainText('parser v4')

  const metrics = await page.locator('.usage-table', { hasText: 'Cross-project tool metrics' }).boundingBox()
  const collapsed = await health.boundingBox()
  expect(collapsed!.y).toBeGreaterThan(metrics!.y)

  await health.locator('summary').click()
  await expect(health.locator('tbody tr')).toHaveCount(1)
})

test('every segment fits the shared frame without overlapping its chrome', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/resources-harness.html')
  await page.waitForSelector('.resources-segmented button')

  const labels = await page.locator('.resources-segmented button').allInnerTexts()
  expect(labels).toEqual(['Processes', 'Network', 'Storage', 'Fleet activity'])

  for (const label of labels) {
    await page.locator('.resources-segmented button', { hasText: label }).click()
    const geometry = await page.evaluate(() => {
      const box = (selector: string) => {
        const element = document.querySelector<HTMLElement>(selector)
        return element ? element.getBoundingClientRect().toJSON() : null
      }
      const panel = document.querySelector<HTMLElement>('.resources-panel')!
      return {
        header: box('.resources-panel > header')!,
        segmented: box('.resources-segmented')!,
        body: box('.resources-panel > main') || box('.resources-panel > .process-fleet-view')!,
        panel: panel.getBoundingClientRect().toJSON(),
        overflow: panel.scrollHeight - panel.clientHeight,
      }
    })
    expect(geometry.segmented.top, label).toBeGreaterThanOrEqual(geometry.header.bottom - 0.5)
    expect(geometry.body.top, label).toBeGreaterThanOrEqual(geometry.segmented.bottom - 0.5)
    expect(geometry.body.bottom, label).toBeLessThanOrEqual(geometry.panel.bottom + 0.5)
    // The panel itself never scrolls: the body is the part that grew, so it is the part
    // that takes the remaining height and scrolls inside it.
    expect(geometry.overflow, label).toBeLessThanOrEqual(1)
  }
})

test('at phone width the dialog fills the screen without scrolling sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await openDomain(page, 'tools + skills')
  await page.locator('.usage-table', { hasText: 'Cross-project tool metrics' }).locator('tbody tr').first().waitFor()
  const overflow = await page.evaluate(() => ({
    body: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    cells: [...document.querySelectorAll('.usage-table td')]
      .filter(cell => cell.scrollWidth > cell.clientWidth + 1).length,
  }))
  expect(overflow.body).toBe(true)
  // The wide table scrolls inside its own container rather than widening the page.
  expect(overflow.cells).toBe(0)
})
