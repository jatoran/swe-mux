import { expect, test } from 'playwright/test'

/**
 * Settings' two layouts, measured rather than described.
 *
 * `settingsTabs.test.ts` proves the navigation model; none of it can see where anything
 * lands. What is asserted here is the part that is CSS and only CSS:
 *
 *  - narrow, the header is one row — the search box sits between the title and the close
 *    button instead of wrapping onto a line of its own, which is the vertical space this
 *    layout was changed to recover;
 *  - narrow, the section list is a drawer: genuinely off screen and out of the focus
 *    order when closed, over the content and inside the panel when open;
 *  - wide, it is still the docked column, and the drawer chrome is absent.
 *
 * The groups are asserted in both, because "see the categories" is the reason the phone
 * gets the desktop's list instead of the flat rail it used to have.
 */

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1280, height: 900 }

type Page = import('playwright/test').Page

async function chrome(page: Page) {
  return page.evaluate(() => {
    const box = (selector: string) => {
      const element = document.querySelector(selector)
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { x: rect.x, right: rect.right, y: rect.y, bottom: rect.bottom, width: rect.width, height: rect.height }
    }
    const nav = document.querySelector('.settings-tabs') as HTMLElement | null
    return {
      header: box('.settings-panel>header'),
      heading: box('.settings-heading'),
      search: box('.settings-panel>header>div.settings-search'),
      close: box('.settings-panel>header>.settings-close'),
      trigger: box('.settings-panel>header>.settings-nav-trigger'),
      body: box('.settings-body'),
      content: box('.settings-content'),
      nav: box('.settings-tabs'),
      navVisibility: nav ? getComputedStyle(nav).visibility : null,
      isDrawer: !!document.querySelector('.settings-tabs-drawer'),
      scrims: document.querySelectorAll('.settings-nav-scrim').length,
      groups: [...document.querySelectorAll('.settings-tab-group>span')]
        .filter(element => getComputedStyle(element).display !== 'none')
        .map(element => element.textContent),
      headingText: document.querySelector('.settings-heading')?.textContent || '',
      // Asked of the browser rather than inferred: a `visibility:hidden` element keeps its
      // offsetParent and still answers a bounding-box query, and the only thing that
      // settles whether a closed drawer is out of the way is whether focus takes.
      focusableInNav: nav
        ? [...nav.querySelectorAll('button')].filter(button => {
            button.focus()
            return document.activeElement === button
          }).length
        : 0,
    }
  })
}

test('narrow: the header is one row with the search inline, and the section list is a closed drawer', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })
  const g = await chrome(page)

  // One row: every header child shares the header's vertical band. A wrapped search box
  // is what this asserts against — it used to sit on its own line below the title.
  expect(g.trigger).not.toBeNull()
  for (const part of [g.trigger!, g.heading!, g.search!, g.close!]) {
    expect(part.y).toBeGreaterThanOrEqual(g.header!.y - 0.5)
    expect(part.bottom).toBeLessThanOrEqual(g.header!.bottom + 0.5)
  }
  // ...and in that order, left to right.
  expect(g.trigger!.x).toBeLessThan(g.heading!.x)
  expect(g.heading!.x).toBeLessThan(g.search!.x)
  expect(g.search!.right).toBeLessThanOrEqual(g.close!.x + 0.5)
  // The whole header is one row of a two-line title, not three stacked controls.
  expect(g.header!.height).toBeLessThan(64)

  // The title names where you are, not what the panel is.
  expect(g.headingText).toContain('SETTINGS')
  expect(g.headingText).toContain('General')

  // Closed drawer: off to the left of the panel, invisible, and unreachable by Tab.
  expect(g.isDrawer).toBe(true)
  expect(g.navVisibility).toBe('hidden')
  expect(g.nav!.right).toBeLessThanOrEqual(g.body!.x + 0.5)
  expect(g.focusableInNav).toBe(0)
  expect(g.scrims).toBe(0)
  // The content has the full width, because the drawer is out of flow.
  expect(g.content!.width).toBeGreaterThan(PHONE.width - 2)
})

test('narrow: the hamburger and the title both open the drawer over the content', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })

  for (const opener of ['.settings-nav-trigger', '.settings-heading-trigger']) {
    await page.click(opener)
    await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(1)
    // The transform has to finish before the geometry means anything.
    await page.waitForTimeout(250)
    const open = await chrome(page)
    expect(open.navVisibility).toBe('visible')
    expect(open.nav!.x).toBeGreaterThanOrEqual(open.body!.x - 0.5)
    // Over the content, not beside it: the content keeps its full width underneath.
    expect(open.content!.width).toBeGreaterThan(PHONE.width - 2)
    expect(open.nav!.right).toBeLessThan(PHONE.width)
    expect(open.scrims).toBe(1)
    expect(open.focusableInNav).toBeGreaterThan(10)
    // The categories are the point of borrowing the desktop list.
    expect(open.groups).toEqual(['Workspace', 'Agents', 'Interface', 'System'])

    // The scrim closes it again, which is how the workspace sidebar behaves.
    // To the right of the drawer: the scrim spans the whole body and the drawer is
    // painted over its left end, exactly as the workspace sidebar sits over its own.
    await page.click('.settings-nav-scrim', { position: { x: PHONE.width - 30, y: 400 } })
    await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(0)
    await page.waitForTimeout(250)
    expect((await chrome(page)).navVisibility).toBe('hidden')
  }
})

test('narrow: picking a section closes the drawer and retitles the header', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })

  await page.click('.settings-nav-trigger')
  await page.click('.settings-tabs button:text-is("Appearance")')
  await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(0)
  await page.waitForTimeout(250)

  const g = await chrome(page)
  expect(g.headingText).toContain('Appearance')
  expect(g.navVisibility).toBe('hidden')
  expect(g.focusableInNav).toBe(0)
  await expect(page.locator('.settings-content h3').first()).toHaveText('Theme')
})

test('wide: the section list is the docked column it always was', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })
  const g = await chrome(page)

  expect(g.isDrawer).toBe(false)
  expect(g.trigger).toBeNull()
  expect(g.navVisibility).toBe('visible')
  expect(g.scrims).toBe(0)
  // Docked: the column and the content are side by side and do not overlap.
  expect(g.nav!.right).toBeLessThanOrEqual(g.content!.x + 0.5)
  expect(g.groups).toEqual(['Workspace', 'Agents', 'Interface', 'System'])
  expect(g.headingText).toContain('CONFIG::V6')
  expect(g.headingText).toContain('Settings')
})
