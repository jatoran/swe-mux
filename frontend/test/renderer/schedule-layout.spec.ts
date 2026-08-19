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

  expect(g.rows).toHaveLength(4)
  expect(g.overflow).toBeLessThanOrEqual(1)
  for (const row of g.rows) expect(row.right).toBeLessThanOrEqual(g.columnRight + 0.5)
  // One schedule's label is far longer than the column; it must be the part that clips.
  // Found by its text rather than by position, because the order is the daemon's sort
  // and a new row landing between two others is not this test's subject.
  const long = await page.evaluate(() => {
    const heading = [...document.querySelectorAll('.schedule-heading strong')]
      .find(element => element.textContent?.startsWith('Weekly dependency')) as HTMLElement
    return { clipped: heading.scrollWidth > heading.clientWidth, overflow: getComputedStyle(heading).textOverflow }
  })
  expect(long.clipped).toBe(true)
  expect(long.overflow).toBe('ellipsis')
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

test('the cron field and its presets share one row, and a preset fills the field', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)
  await page.getByRole('button', { name: 'new…' }).click()

  const row = page.locator('.schedule-cron-row')
  const input = row.locator('input')
  const select = row.locator('select')
  // One row means one row: a select that pushed itself onto a second line would take the
  // width the expression needs, which is the whole reason both columns shrink.
  const [inputBox, selectBox] = await Promise.all([input.boundingBox(), select.boundingBox()])
  expect(inputBox!.y).toBeCloseTo(selectBox!.y, 0)
  expect(selectBox!.x).toBeGreaterThan(inputBox!.x)
  const overflow = await page.evaluate(() => {
    const editor = document.querySelector('.schedule-editor') as HTMLElement
    return editor.scrollWidth - editor.clientWidth
  })
  expect(overflow).toBeLessThanOrEqual(1)

  // Choosing one writes the expression into the field, and the hint under the row becomes
  // the piece of syntax that expression demonstrates.
  await select.selectOption('0 13 1,15 * *')
  await expect(input).toHaveValue('0 13 1,15 * *')
  await expect(page.locator('.schedule-editor label', { has: row }).locator('small'))
    .toContainText('A comma is a list')
  // And the daemon is still the only thing that says when it fires.
  await expect(page.locator('.schedule-preview')).toContainText('Next:')

  // A hand-edited expression is no longer any preset, and says so rather than keeping a
  // stale label.
  await input.fill('0 10 * * *')
  await expect(select).toHaveValue('')
  await expect(select.locator('option').first()).toHaveText('Custom')
})

test('a resume row says what it reopens and where that conversation has got to', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)

  // The cadence alone does not say what a schedule does once two actions exist, so the
  // heading carries both.
  const resume = page.locator(ROW).filter({ hasText: 'Pick the migration back up' })
  await expect(resume.locator('.schedule-heading small')).toContainText('continues Storage migration (continued)')

  await resume.locator('.schedule-heading').click()
  const target = resume.locator('.schedule-target')
  await expect(target).toContainText('Wherever this work has got to')
  // Both names, because the pinned row and where the work actually is have diverged -
  // which is the one thing about a rolling target a reader cannot work out themselves.
  await expect(target).toContainText('[claude] Storage migration')
  await expect(target).toContainText('now at Storage migration (continued)')
  await expect(target).toContainText('stops above 70% context')
  const overflow = await page.evaluate(() => {
    const body = document.querySelector('.schedule-body') as HTMLElement
    return body.scrollWidth - body.clientWidth
  })
  expect(overflow).toBeLessThanOrEqual(1)
})

test('editing a resume offers the three target kinds and never a backend', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)
  await page.locator(ROW).filter({ hasText: 'Pick the migration back up' })
    .getByRole('button', { name: 'edit' }).click()

  await expect(page.locator('.schedule-target-summary')).toContainText('[claude] Storage migration')
  // A resume runs the harness the conversation belongs to. Offering an Agent select
  // would offer control the daemon refuses.
  await expect(page.getByLabel('Agent', { exact: true })).toHaveCount(0)
  const kind = page.getByLabel('What to reopen')
  await expect(kind).toHaveValue('latest_of_session')
  await expect(kind.locator('option')).toHaveCount(3)
  // The ceiling belongs to the accumulating kind and to nothing else.
  await expect(page.getByLabel('Stop above')).toHaveValue('70')
  await kind.selectOption('run')
  await expect(page.getByLabel('Stop above')).toHaveCount(0)
})

test('a fork schedule cannot be saved until a legal cut point is chosen', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/schedule-harness.html')
  await page.waitForSelector(ROW)
  await page.locator(ROW).filter({ hasText: 'Pick the migration back up' })
    .getByRole('button', { name: 'edit' }).click()
  await page.getByLabel('What to reopen').selectOption('fork_point')

  const points = page.locator('.schedule-branch-point')
  await expect(points).toHaveCount(3)
  const save = page.getByRole('button', { name: 'Save' })
  await expect(save).toBeDisabled()
  // The daemon decided this one is illegal; the browser renders that answer rather than
  // recomputing it, and an unselectable row is what stops a nightly fork that fails.
  await expect(points.filter({ hasText: 'Reading the remaining migrations' }).locator('input')).toBeDisabled()

  await points.filter({ hasText: 'Migrated the first two tables' }).locator('input').check()
  await expect(save).toBeEnabled()
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
