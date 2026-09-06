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

const openDomain = async (page:import('playwright/test').Page,label:string)=>{
  await page.goto('/resources-harness.html?activity')
  await page.getByRole('tab',{name:label,exact:true}).click()
}

test('activity lists identifiable runs with compact and exact token values',async({page})=>{
  await page.setViewportSize({width:1200,height:900})
  await openDomain(page,'Runs')
  await expect(page.locator('.analytics-run-card')).toContainText('Refactor usage')
  await expect(page.locator('.analytics-run-card')).toContainText('swe-mux')
  const tokens=page.locator('.analytics-run-meta .metric-value')
  await expect(tokens).toHaveText('9.7B')
  await tokens.click()
  await expect(tokens).toHaveText('9,702,931,354')
})

test('collection coverage remains collapsed beside tools',async({page})=>{
  await page.setViewportSize({width:1200,height:900})
  await openDomain(page,'Tools')
  await page.locator('.analytics-tool-record').first().waitFor()
  const health=page.locator('.analytics-collection')
  await expect(health).toHaveJSProperty('open',false)
  await health.locator(':scope > summary').click()
  await expect(health).toContainText('Missing measurements are not zero')
})

test('every segment fits the shared frame without overlapping its chrome', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/resources-harness.html')
  await page.waitForSelector('.resources-segmented button')

  const labels = await page.locator('.resources-segmented button').allInnerTexts()
  expect(labels).toEqual(['Processes', 'Network', 'Storage'])

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

test('mobile tool rows expand without widening the screen',async({page})=>{
  await page.setViewportSize({width:390,height:800})
  await openDomain(page,'Tools')
  const tool=page.locator('.analytics-tool-record').first()
  await tool.locator(':scope > summary').click()
  await expect(tool).toContainText('Mean duration')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
})
