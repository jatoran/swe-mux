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
  await page.waitForSelector('.analytics-primary .analytics-tab-row button')
  await page.locator('.analytics-primary .analytics-tab-row button', { hasText: label }).click()
}

test('the overview shows three pots, each stamped with its basis, and no total', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/usage-harness.html')
  await page.locator('.usage-pot').first().waitFor()

  const pots = page.locator('.usage-pot')
  await expect(pots).toHaveCount(3)
  await expect(pots.locator('header strong')).toHaveText(['Agent usage', 'Automation', 'Quota headroom'])
  // The basis is the whole reason these are three tiles. `$499` read back out of
  // transcripts and `$1.88` billed by the call are not the same kind of claim.
  await expect(pots.locator('header em')).toHaveText([
    'transcript estimate', 'metered · billed', '% of window',
  ])

  // Three tiles on one row, so they are compared rather than read in sequence.
  const boxes = await pots.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().toJSON()))
  expect(boxes[0].top).toBeCloseTo(boxes[1].top, 0)
  expect(boxes[1].top).toBeCloseTo(boxes[2].top, 0)
  expect(boxes[0].right).toBeLessThanOrEqual(boxes[1].left + 1)

  // Nothing anywhere on the surface sums them, and the surface says so.
  //
  // Asserted once, on the segment footer, because that is where the statement now
  // lives: the Settings overhaul retired the separate `.usage-pots-caveat` element
  // and moved the wording into `USAGE_SEGMENTS[].footer` (`usageSegments.ts`), which
  // carries `NEVER_SUMMED` on every segment rather than only under the tiles. The
  // spec kept asserting the retired element and its retired phrasing ("never added
  // together"), so it went red the first time this suite ran on CI - the invariant
  // was intact the whole time and only its rendering had moved.
  await expect(page.locator('.usage-panel > footer')).toContainText('cost basis')
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
  await expect(page.locator('.analytics-primary .analytics-tab-row button[aria-selected="true"]')).toHaveText('Automation')
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
  const subset=page.locator('details', {hasText:'Agent model spend · observed runs only'})
  await expect(subset).toHaveJSProperty('open',false)

})

test('every segment fits the shared frame without overlapping its chrome', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/usage-harness.html')
  await page.waitForSelector('.analytics-primary .analytics-tab-row button')

  const labels = await page.locator('.analytics-primary .analytics-tab-row button').allInnerTexts()
  expect(labels).toEqual(['Overview', 'Agent usage', 'Quota', 'Activity', 'Automation'])

  for (const label of labels) {
    await page.locator('.analytics-primary .analytics-tab-row button', { hasText: label }).click()
    const geometry = await page.evaluate(() => {
      const box = (selector: string) => {
        const element = document.querySelector<HTMLElement>(selector)
        return element ? element.getBoundingClientRect().toJSON() : null
      }
      const panel = document.querySelector<HTMLElement>('.resources-panel')!
      return {
        header: box('.resources-panel > header')!,
        segmented: box('.analytics-primary .analytics-tab-row')!,
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


test('mobile usage expands exact totals without widening the dialog', async ({page})=>{
  await page.setViewportSize({width:390,height:800})
  await page.goto('/usage-harness.html')
  await page.locator('.analytics-section-picker select').selectOption('agents')
  const tokens=page.locator('.analytics-headlines .metric-value').first()
  await expect(tokens).toHaveText('15.1B')
  await tokens.click()
  await expect(tokens).toHaveText('15,140,545,000')
  const row=page.locator('.analytics-record').first()
  await row.locator('summary').click()
  await expect(row).toContainText('Cache read')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.locator('.analytics-section-picker select').selectOption('quota')
  await expect(page.locator('.quota-account-card')).toHaveCount(2)
  await page.getByRole('tab',{name:'History',exact:true}).click()
  await expect(page.locator('.quota-account-card')).toHaveCount(0)
  await expect(page.locator('.quota-chart')).toHaveCount(1)
  await expect(page.locator('.quota-readout tbody tr')).toHaveCount(2)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
})

test('quota history opens on the weekly window over the last 7 days and says what each line meant when', async ({page})=>{
  await page.setViewportSize({width:1200,height:900})
  await openSegment(page,'Quota')
  await page.getByRole('tab',{name:'History',exact:true}).click()
  const toolbar=page.locator('.analytics-toolbar')
  await expect(toolbar.getByLabel('Range')).toHaveValue('7')
  await expect(toolbar.getByLabel('Window')).toHaveValue('weekly')
  const chart=page.locator('.quota-chart')
  await expect(chart.locator('h3')).toHaveText('Weekly window · % of limit used')
  await expect(chart.locator('.quota-chart-header small')).toContainText('(UTC days)')

  // The axis names days, not just its two ends, and the readout starts on the newest reading.
  await expect.poll(()=>chart.locator('.quota-tick-label').count()).toBeGreaterThanOrEqual(4)
  const readout=page.locator('.quota-readout')
  await expect(readout.locator('.analytics-badge')).toHaveText('Latest')
  await expect(readout.locator('tbody tr')).toHaveCount(2)
  await expect(readout.locator('.quota-value').first()).toHaveText(/^\d+(\.\d)?% used$/)
  await expect(readout.locator('.quota-detail').first()).toContainText('reading')
  const latest=await readout.locator('header strong').innerText()
  expect(latest).toContain('(UTC day)')

  // Pointing at the start of the axis reads an earlier day, and the way back is labelled.
  const plot=chart.locator('svg')
  const box=(await plot.boundingBox())!
  await page.mouse.move(box.x+60,box.y+box.height/2)
  await expect(readout.locator('header strong')).not.toHaveText(latest)
  await readout.getByRole('button',{name:'Show latest'}).click()
  await expect(readout.locator('header strong')).toHaveText(latest)

  // The keyboard reaches the same readings.
  await chart.locator('.quota-plot').focus()
  await page.keyboard.press('Home')
  await expect(readout.locator('header strong')).not.toHaveText(latest)
  await page.keyboard.press('End')
  await expect(readout.locator('header strong')).toHaveText(latest)

  // Every reading is labelled by local time and says when the window was next due to reset.
  await toolbar.getByLabel('Points').selectOption('raw')
  await expect(chart.locator('.quota-chart-header small')).toContainText('(local time)')
  await expect(readout.locator('header strong')).not.toContainText('UTC')
  await expect(readout.locator('.quota-detail').first()).toContainText('next reset')
  const readings=page.locator('.quota-readings')
  await readings.locator('summary').click()
  await expect(readings.locator('thead th')).toHaveText(['Time','Account','Weekly used','5-hour used'])
})

test('each account gets a source lane, a range split, and a source in the readout', async ({page})=>{
  await page.setViewportSize({width:1200,height:900})
  await openSegment(page,'Quota')
  await page.getByRole('tab',{name:'History',exact:true}).click()
  const chart=page.locator('.quota-chart')
  // One lane per account under the axis, drawn in the fills the legend names.
  await expect(chart.locator('.quota-lane-track')).toHaveCount(2)
  await expect(chart.locator('.quota-source-legend li')).toHaveText(['swe-mux sessions','not swe-mux','used elsewhere'])
  expect(await chart.locator('svg rect.quota-source-elsewhere').count()).toBeGreaterThan(0)
  const totals=chart.locator('.quota-sources tbody tr')
  await expect(totals).toHaveCount(2)
  await expect(totals.first()).toContainText(/\d+% swe-mux sessions/)
  await expect(totals.first()).toContainText('used elsewhere')
  await expect(chart.locator('.quota-readout .quota-detail').first()).toContainText(/rose \d+% this day: /)

  // Every reading names where the rise that ended at it came from. The harness's 5-hour
  // window rises at every poll; its weekly window moves a fraction of a point per poll.
  await page.locator('.analytics-toolbar').getByLabel('Points').selectOption('raw')
  await page.locator('.analytics-toolbar').getByLabel('Window').selectOption('session')
  await expect(chart.locator('.quota-chart-header small')).toContainText('(local time)')
  expect(await chart.locator('svg rect.quota-source-swe_mux').count()).toBeGreaterThan(0)
  await expect(chart.locator('.quota-readout .quota-detail').first()).toContainText(/rose \d+% since the reading before: \d+% /)
})

test('the attribution list reads the stored intervals through a well-formed request', async ({page})=>{
  await page.setViewportSize({width:1200,height:900})
  await openSegment(page,'Quota')
  await page.getByRole('tab',{name:'Attribution',exact:true}).click()
  const items=page.locator('.attribution-log details')
  await expect(items.first()).toBeVisible()
  await expect(items.first().locator('summary')).toContainText(/(Weekly|5-hour) \+\d+%/)
  await items.first().locator('summary').click()
  await expect(items.first()).toContainText('This account')
  await expect(page.locator('.attribution-log')).toContainText('Correlation does not establish who used an account')
  // The shared path carries a query of its own; a second `?` made every load a 400.
  const requested=await page.evaluate(()=>(window as unknown as {requested:string[]}).requested)
  const operational=requested.filter(url=>url.startsWith('/api/telemetry/operational'))
  expect(operational.length).toBeGreaterThan(0)
  for(const url of operational)expect(url.split('?').length).toBe(2)
  expect(new URL(operational[0],'http://x').searchParams.get('limit')).toBe('500')
})
