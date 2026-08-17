import { expect, test } from 'playwright/test'

/**
 * The Schedule tab lives in the narrowest column in the app and puts a label, a cron
 * expression, a countdown, a verdict and four buttons in it. Everything here is CSS plus
 * one class, which no unit test can see: `schedules.test.ts` proves the strings, this
 * proves they fit and that a schedule which cannot fire says so where it is read.
 */

const ROW = '.schedule-row'

async function geometry(page: import('playwright/test').Page) {
  return page.evaluate(() => {
    const column = document.querySelector('#drawer-column') as HTMLElement
    const body = document.querySelector('.schedule-body') as HTMLElement
    const box = (element: Element) => {
      const rect = element.getBoundingClientRect()
      return { x: rect.x, right: rect.right, height: rect.height }
    }
    return {
      columnRight: column.getBoundingClientRect().right,
      // A body wider than its own scroll box is the horizontal scrollbar this column
      // must never grow.
      overflow: body.scrollWidth - body.clientWidth,
      rows: [...document.querySelectorAll('.schedule-row')].map(box),
      headings: [...document.querySelectorAll('.schedule-heading strong')].map(element => ({
        ...box(element),
        clipped: (element as HTMLElement).scrollWidth > (element as HTMLElement).clientWidth,
      })),
      details: document.querySelectorAll('.schedule-detail').length,
    }
  })
}

test('every row fits the drawer column, and a long label ellipsizes rather than widening it', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)
  const g = await geometry(page)

  expect(g.rows).toHaveLength(3)
  expect(g.overflow).toBeLessThanOrEqual(1)
  for (const row of g.rows) expect(row.right).toBeLessThanOrEqual(g.columnRight + 0.5)
  // The second schedule's label is far longer than the column; it must be the part that clips.
  expect(g.headings[1].clipped).toBe(true)
  const overflow = await page.evaluate(() =>
    getComputedStyle(document.querySelectorAll('.schedule-heading strong')[1]).textOverflow)
  expect(overflow).toBe('ellipsis')
  // Collapsed means collapsed: no prompt body and no run history until asked for.
  expect(g.details).toBe(0)
})

test('a schedule that cannot fire says so on its own row, with the way to fix it', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)

  const blocked = page.locator('.schedule-row.blocked')
  await expect(blocked).toHaveCount(1)
  await expect(blocked.locator('.schedule-blocked')).toContainText('Scheduled runs is off for this Project')
  await expect(blocked.locator('.schedule-blocked button')).toHaveText('Turn it on')
  // The paused one is dimmed but not marked as broken: someone chose that.
  await expect(page.locator('.schedule-row.paused')).toHaveCount(1)
  await expect(page.locator('.schedule-row.paused.blocked')).toHaveCount(0)
})

test('expanding one row reveals its prompt and its run history, and only that row', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)
  await page.locator('.schedule-heading').first().click()

  await expect(page.locator('.schedule-detail')).toHaveCount(1)
  await expect(page.locator('.schedule-prompt')).toContainText('report PASS')
  expect(await page.locator('.schedule-run').count()).toBe(2)
  await expect(page.locator('.schedule-run').nth(1)).toContainText('still live')

  await page.locator('.schedule-heading').first().click()
  await expect(page.locator('.schedule-detail')).toHaveCount(0)
})

test('the editor replaces the list and previews the next fires from the daemon', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)
  await page.getByRole('button', { name: 'new…' }).click()

  await expect(page.locator('.schedule-editor')).toBeVisible()
  await expect(page.locator(ROW)).toHaveCount(0)
  // The preview is the only thing that makes a cron field usable, and it comes from the
  // daemon rather than from a second implementation in the browser.
  await expect(page.locator('.schedule-preview')).toContainText('Next:')
  const overflow = await page.evaluate(() => {
    const editor = document.querySelector('.schedule-editor') as HTMLElement
    return editor.scrollWidth - editor.clientWidth
  })
  expect(overflow).toBeLessThanOrEqual(1)
})

test('at phone width the rows still fit and the controls stay tappable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)
  await page.evaluate(() => {
    const column = document.querySelector('#drawer-column') as HTMLElement
    column.style.width = '100vw'
  })
  const g = await geometry(page)

  expect(g.overflow).toBeLessThanOrEqual(1)
  for (const row of g.rows) expect(row.right).toBeLessThanOrEqual(g.columnRight + 0.5)
  const heights = await page.evaluate(() =>
    [...document.querySelectorAll('.schedule-actions button')].map(button => button.getBoundingClientRect().height))
  for (const height of heights) expect(height).toBeGreaterThanOrEqual(36)
})
