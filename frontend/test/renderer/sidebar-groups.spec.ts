import { expect, test } from 'playwright/test'

/**
 * A Group has to look like a container, and the rows outside one have to look outside it.
 *
 * The sidebar draws Groups and ungrouped Projects as sibling sections in one flat run, and
 * for a long time the only thing between them was an 8px margin. A Group holding two
 * Projects with ungrouped Projects beneath it therefore read as one list of four — the gap
 * is real, but a gap between two runs of identical rows is not a statement about which run
 * is inside anything.
 *
 * Every cue here is CSS on markup the components already emitted, so this spec is the only
 * place any of it can be checked.
 */

const box = (page: import('playwright/test').Page, selector: string, index = 0) =>
  page.evaluate(([target, at]) => {
    const element = [...document.querySelectorAll(target as string)][at as number] as HTMLElement
    const rect = element.getBoundingClientRect()
    const style = getComputedStyle(element)
    return {
      left: rect.left, top: rect.top, bottom: rect.bottom,
      borderLeft: Number.parseFloat(style.borderLeftWidth),
      paddingLeft: Number.parseFloat(style.paddingLeft),
      background: style.backgroundColor,
    }
  }, [selector, index] as const)

test('grouped Projects sit inside something and ungrouped Projects do not', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 620 })
  await page.goto('/sidebar-groups-harness.html')
  await page.waitForSelector('.sidebar-project-bucket')

  const bucket = await box(page, '.sidebar-project-bucket')
  const ungrouped = await box(page, '.sidebar-ungrouped-projects')

  // The load-bearing cue is the indent, because it moves the rows themselves. A ground
  // shade and a hairline both wash out at sidebar contrast; a row that starts further
  // right does not, and "further right" is the fact being communicated.
  expect(bucket.borderLeft).toBeGreaterThanOrEqual(2)
  expect(bucket.paddingLeft).toBeGreaterThanOrEqual(6)
  expect(ungrouped.borderLeft).toBe(0)
  expect(ungrouped.paddingLeft).toBe(0)

  const grouped = await box(page, '.sidebar-project-bucket .project-row')
  const loose = await box(page, '.sidebar-ungrouped-projects .project-row')
  expect(grouped.left).toBeGreaterThan(loose.left + 4)
})

test('a Group heading is the lid of its box, and spans it', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 620 })
  await page.goto('/sidebar-groups-harness.html')
  await page.waitForSelector('.sidebar-project-bucket')

  const geometry = await page.evaluate(() => {
    const bucket = document.querySelector<HTMLElement>('.sidebar-project-bucket')!
    const header = bucket.querySelector<HTMLElement>('header')!
    const row = bucket.querySelector<HTMLElement>('.project-row')!
    return {
      // The *padding* box, not the border box: the heading cancels the body's indent, it
      // does not paint over the rail that marks the box as a box.
      bucketLeft: bucket.getBoundingClientRect().left + Number.parseFloat(getComputedStyle(bucket).borderLeftWidth),
      headerLeft: header.getBoundingClientRect().left,
      headerBottom: header.getBoundingClientRect().bottom,
      rowTop: row.getBoundingClientRect().top,
      rule: Number.parseFloat(getComputedStyle(header).borderBottomWidth),
    }
  })
  // The heading cancels the body's indent so it reads as the box's lid rather than as its
  // first indented child, and carries a rule so the first Project under it is content.
  expect(geometry.headerLeft).toBeLessThanOrEqual(geometry.bucketLeft + 0.5)
  expect(geometry.rule).toBeGreaterThanOrEqual(1)
  expect(geometry.rowTop).toBeGreaterThanOrEqual(geometry.headerBottom - 0.5)
})

test('a folded Group is a heading alone, with no body left to indent', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 620 })
  await page.goto('/sidebar-groups-harness.html')
  await page.waitForSelector('.sidebar-project-bucket.collapsed')

  const collapsed = await box(page, '.sidebar-project-bucket.collapsed')
  expect(collapsed.paddingLeft).toBe(0)
  // No rule under a heading with nothing beneath it, and no gap held open for rows that
  // are not being drawn.
  expect(await page.evaluate(() => {
    const header = document.querySelector<HTMLElement>('.sidebar-project-bucket.collapsed > header')!
    return getComputedStyle(header).borderBottomColor
  })).toMatch(/rgba\(.*0\)$/)

  // And the ungrouped run directly beneath it is still visibly not part of it, which is the
  // exact arrangement that used to read as one list.
  const ungroupedRow = await box(page, '.sidebar-ungrouped-projects .project-row')
  expect(ungroupedRow.top).toBeGreaterThan(collapsed.bottom - 0.5)
  const groupedRow = await box(page, '.sidebar-project-bucket .project-row')
  expect(ungroupedRow.left).toBeLessThan(groupedRow.left - 4)
})
