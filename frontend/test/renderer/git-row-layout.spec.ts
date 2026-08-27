import { expect, test } from 'playwright/test'

const HARNESS = '/git-row-layout-harness.html'

const expandRow = async (page: import('playwright/test').Page) => {
  const row = page.locator('.git-map-row').filter({ hasText: 'worktree-git-map-latency' })
  await expect(row).toBeVisible()
  await row.locator('.git-map-summary').click()
  return row
}

test('a row\'s two acts sit above the read, and the read sits where the changes will', async ({ page }) => {
  // The defect this places: the row's per-checkout read renders a one-line placeholder,
  // and with that placeholder above the acts, Land jumped down the moment the row opened
  // and back up when the lists arrived - under a pointer already travelling to it.
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto(HARNESS)
  const row = await expandRow(page)

  const reading = row.locator('.git-state', { hasText: 'Reading this worktree' })
  await expect(reading).toBeVisible()

  const before = await page.evaluate(() => {
    const detail = document.querySelector<HTMLElement>('.git-map-detail')!
    const top = (selector: string) =>
      detail.querySelector<HTMLElement>(selector)!.getBoundingClientRect().top
    return { land: top('.git-land-row-section'), remove: top('.git-map-actions'), reading: top('.git-state') }
  })
  // Both acts above the placeholder, and Land above Remove.
  expect(before.land).toBeLessThan(before.remove)
  expect(before.remove).toBeLessThan(before.reading)

  await expect(reading).toBeHidden()
  await expect(row.locator('.git-change-group')).not.toHaveCount(0)

  const after = await page.evaluate(() => {
    const detail = document.querySelector<HTMLElement>('.git-map-detail')!
    const top = (selector: string) =>
      detail.querySelector<HTMLElement>(selector)!.getBoundingClientRect().top
    return { land: top('.git-land-row-section'), remove: top('.git-map-actions'), group: top('.git-change-group') }
  })
  // Neither act moved when the lists arrived, and the first group landed where the
  // placeholder had been - it was replaced in place rather than pushed past.
  expect(after.land).toBeCloseTo(before.land, 0)
  expect(after.remove).toBeCloseTo(before.remove, 0)
  expect(after.group).toBeCloseTo(before.reading, 0)
})

test('the acts stay reachable under an unbounded change list', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto(HARNESS)
  const row = await expandRow(page)
  await expect(row.locator('.git-change-group').first()).toBeVisible()

  const geometry = await page.evaluate(() => {
    const detail = document.querySelector<HTMLElement>('.git-map-detail')!
    const box = (selector: string) => detail.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON()
    return { land: box('.git-land-row-section'), remove: box('.git-map-actions'), group: box('.git-change-group') }
  })
  expect(geometry.land.bottom).toBeLessThanOrEqual(geometry.group.top + 0.5)
  expect(geometry.remove.bottom).toBeLessThanOrEqual(geometry.group.top + 0.5)
})

for (const width of [320, 360, 430]) {
  test(`Log fits its pane at ${width}px, so the session link and the close swipe survive`, async ({ page }) => {
    // One line of CSS produced all three failures: `.git-graph{min-width:max-content}`
    // made the section as wide as an untruncated `nowrap` subject wanted, so the subject
    // never ellipsed, the tab scrolled sideways, and the session-link button - the
    // `max-content` half of `.git-commit-head` - was pushed off the right edge. A
    // scrollable-x ancestor also vetoes the drawer's close swipe by design
    // (`pathOwnsHorizontalScroll`), which is why the gesture died on Log alone.
    await page.setViewportSize({ width, height: 720 })
    await page.goto(HARNESS)
    await page.getByRole('tab', { name: 'Log', exact: true }).click()
    await expect(page.locator('.git-graph-row').first()).toBeVisible()

    const geometry = await page.evaluate(() => {
      const body = document.querySelector<HTMLElement>('.git-review-tab')!
      const graph = document.querySelector<HTMLElement>('.git-graph')!
      const subject = document.querySelector<HTMLElement>('.git-commit-title span')!
      const links = document.querySelector<HTMLElement>('.git-commit-links')
      return {
        bodyOverflow: body.scrollWidth - body.clientWidth,
        graphOverflow: graph.scrollWidth - graph.clientWidth,
        bodyRight: body.getBoundingClientRect().right,
        linksRight: links ? links.getBoundingClientRect().right : null,
        linksLeft: links ? links.getBoundingClientRect().left : null,
        subjectClipped: subject.scrollWidth > subject.clientWidth,
        viewport: window.innerWidth,
      }
    })

    expect(geometry.bodyOverflow).toBeLessThanOrEqual(1)
    expect(geometry.graphOverflow).toBeLessThanOrEqual(1)
    // The subject is the thing that gives, and it gives by ellipsing rather than by
    // widening the row - which is the whole point.
    expect(geometry.subjectClipped).toBe(true)
    expect(geometry.linksRight).not.toBeNull()
    expect(geometry.linksRight!).toBeLessThanOrEqual(geometry.bodyRight + 0.5)
    expect(geometry.linksLeft!).toBeGreaterThanOrEqual(0)
  })
}

test('the lane art survives, and gives up its outermost columns before the subject does', async ({ page }) => {
  // The reason the fix is a clip rather than a scroll: a reader keeps the lanes nearest
  // the commit, which are the ones that carry meaning, and keeps the subject.
  await page.setViewportSize({ width: 320, height: 720 })
  await page.goto(HARNESS)
  await page.getByRole('tab', { name: 'Log', exact: true }).click()
  const glyph = page.locator('.git-graph-glyph').first()
  await expect(glyph).toBeVisible()

  const geometry = await page.evaluate(() => {
    const row = document.querySelector<HTMLElement>('.git-graph-row')!
    const art = row.querySelector<HTMLElement>('.git-graph-glyph')!
    const title = row.querySelector<HTMLElement>('.git-commit-title')!
    return {
      artWidth: art.getBoundingClientRect().width,
      rowWidth: row.getBoundingClientRect().width,
      titleWidth: title.getBoundingClientRect().width,
      rowOverflow: row.scrollWidth - row.clientWidth,
    }
  })
  expect(geometry.artWidth).toBeGreaterThan(0)
  // The art never takes more of the row than the text it annotates.
  expect(geometry.artWidth).toBeLessThan(geometry.titleWidth)
  expect(geometry.rowOverflow).toBeLessThanOrEqual(1)
})
