import { expect, test } from 'playwright/test'

/**
 * The Usage dialog: the three pots of spend, in their own dialog rather than as the sixth
 * tab of a segment of the dialog about processes and disk.
 *
 * What is measured here is the claim the Overview makes. Three figures of three different
 * kinds sit side by side and are never added together, and whether a reader can actually
 * tell them apart is a layout question rather than a logic one: each basis label has to
 * survive beside its figure at both widths, the tiles have to read as three things rather
 * than as a row of cells that should total, and the caveat that explains the arrangement
 * has to stay on the first screen.
 *
 * The spend table is here too. It is the *same component* the Automation dashboard draws,
 * and its human-scale formatting assertions moved with it from the Resources harness. If
 * the two surfaces ever disagree, the mirroring has been re-implemented as a copy.
 */

const openSegment = async (page: import('playwright/test').Page, label: string) => {
  await page.goto('/usage-harness.html')
  await page.waitForSelector('.usage-segmented button')
  await page.locator('.usage-segmented button', { hasText: label }).click()
}

test('the overview shows three pots, each stamped with its basis, and no total', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/usage-harness.html')
  await page.locator('.usage-pot').first().waitFor()

  const pots = page.locator('.usage-pot')
  await expect(pots).toHaveCount(3)
  await expect(pots.locator('header strong')).toHaveText(['Agents', 'Automation', 'Quota headroom'])
  // The basis is the whole reason these are three tiles. `$499` read back out of
  // transcripts and `$1.88` billed by the call are not the same kind of claim.
  await expect(pots.locator('header em')).toHaveText([
    'subscription · estimated', 'metered · billed', '% of window',
  ])

  // Three tiles on one row, so they are compared rather than read in sequence.
  const boxes = await pots.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().toJSON()))
  expect(boxes[0].top).toBeCloseTo(boxes[1].top, 0)
  expect(boxes[1].top).toBeCloseTo(boxes[2].top, 0)
  expect(boxes[0].right).toBeLessThanOrEqual(boxes[1].left + 1)

  // Nothing anywhere on the surface sums them, and the surface says so.
  await expect(page.locator('.usage-pots-caveat')).toContainText('never added together')
  await expect(page.locator('.usage-panel > footer')).toContainText('never summed')
})

test('the quota tile names the tightest window rather than an average', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/usage-harness.html')
  await page.locator('.usage-pot').first().waitFor()

  // The fixture's tightest reading is claude's 5h window at 91.4%. Weekly windows at 38%
  // and 44% sit beside it, and an average across them would report comfortable headroom on
  // an account that is about to be cut off.
  const quota = page.locator('.usage-pot').nth(2)
  await expect(quota.locator('b')).toHaveText('9%')
  await expect(quota.locator('b')).toHaveClass(/warn/)
  await expect(quota.locator('span')).toContainText('claude 5h')
})

test('a pot opens the segment that explains it', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/usage-harness.html')
  await page.locator('.usage-pot').first().waitFor()

  await page.locator('.usage-pot').nth(1).click()
  await expect(page.locator('.usage-segmented button[aria-selected="true"]')).toHaveText('Automation')
  await page.locator('.cost-table tbody tr').first().waitFor()
})

test('the spend view is the same table here as on the Automation dashboard', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openSegment(page, 'Automation')
  await page.locator('.cost-table tbody tr').first().waitFor()

  // Same component, so the same ranked rows and the same sub-cent guard.
  const names = await page.locator('.cost-table tbody .cost-name strong').allInnerTexts()
  expect(names[0]).toBe('Scan timeline')
  const row = page.locator('.cost-table tbody tr', { hasText: 'Doc drift watch' })
  await expect(row.locator('td').nth(1)).toHaveText('<$0.0001')

  // The agent figure beside it is labelled by its denominator, never as the agent total:
  // it covers only runs mux observed, while the Agents segment reads every transcript.
  await expect(page.locator('.cost-summary article').nth(4).locator('span')).toHaveText('agents · observed runs')
  await expect(page.locator('.usage-table h3', { hasText: 'Agent model spend' }))
    .toHaveText('Agent model spend · observed runs only')
})

test('every segment fits the shared frame without overlapping its chrome', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/usage-harness.html')
  await page.waitForSelector('.usage-segmented button')

  const labels = await page.locator('.usage-segmented button').allInnerTexts()
  expect(labels).toEqual(['Overview', 'Agents', 'Automation', 'Quota'])

  for (const label of labels) {
    await page.locator('.usage-segmented button', { hasText: label }).click()
    const geometry = await page.evaluate(() => {
      const box = (selector: string) => {
        const element = document.querySelector<HTMLElement>(selector)
        return element ? element.getBoundingClientRect().toJSON() : null
      }
      const panel = document.querySelector<HTMLElement>('.resources-panel')!
      return {
        header: box('.resources-panel > header')!,
        segmented: box('.usage-segmented')!,
        body: box('.resources-panel > main')!,
        panel: panel.getBoundingClientRect().toJSON(),
        overflow: panel.scrollHeight - panel.clientHeight,
      }
    })
    expect(geometry.segmented.top, label).toBeGreaterThanOrEqual(geometry.header.bottom - 0.5)
    expect(geometry.body.top, label).toBeGreaterThanOrEqual(geometry.segmented.bottom - 0.5)
    expect(geometry.body.bottom, label).toBeLessThanOrEqual(geometry.panel.bottom + 0.5)
    expect(geometry.overflow, label).toBeLessThanOrEqual(1)
  }
})

test('at phone width the pots stack and the basis labels survive', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/usage-harness.html')
  await page.locator('.usage-pot').first().waitFor()

  const boxes = await page.locator('.usage-pot').evaluateAll(
    nodes => nodes.map(node => node.getBoundingClientRect().toJSON()),
  )
  // One column, so a narrow tile never squeezes its basis label onto a second line under
  // the figure, which is where it stops reading as a qualifier at all.
  expect(boxes[1].top).toBeGreaterThanOrEqual(boxes[0].bottom - 1)
  const clipped = await page.locator('.usage-pot header em').evaluateAll(
    nodes => nodes.filter(node => node.scrollWidth > node.clientWidth + 1).length,
  )
  expect(clipped).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true)
})
