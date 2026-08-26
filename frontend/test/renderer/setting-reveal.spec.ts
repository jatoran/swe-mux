import { expect, test, type Page } from 'playwright/test'

// What a deep link from a gated surface has to actually do, measured against the shipped
// settings geometry: land the control inside the scroller, clear of any sticky chrome at
// the top of it, on a desktop panel and on a phone-width one alike.

const DESKTOP = { width: 1280, height: 800 }
const MOBILE = { width: 393, height: 844 }

type Placement = {
  found: boolean
  insideViewport: boolean
  belowRail: boolean
  flashed: boolean
  focused: string
}

const placement = (page: Page, setting: string) => page.evaluate((id: string) => {
  const target = document.querySelector<HTMLElement>(`[data-setting="${id}"]`)
  const scroller = document.querySelector<HTMLElement>('.settings-content')
  const rail = document.querySelector<HTMLElement>('.settings-section-rail')
  if (!target || !scroller) return { found: false, insideViewport: false, belowRail: false, flashed: false, focused: '' }
  const box = target.getBoundingClientRect()
  const view = scroller.getBoundingClientRect()
  const railBottom = rail ? rail.getBoundingClientRect().bottom : view.top
  const active = document.activeElement as HTMLElement | null
  return {
    found: true,
    insideViewport: box.top >= view.top && box.bottom <= view.bottom,
    belowRail: box.top >= railBottom,
    flashed: target.classList.contains('setting-flash'),
    focused: active ? `${active.tagName}:${(active as HTMLInputElement).type || ''}` : '',
  } satisfies Placement
}, setting)

const reveal = (page: Page, setting: string, coarse = false) =>
  page.evaluate(([id, coarsePointer]) => {
    const harness = window as unknown as {
      revealHarness: (setting: string, behavior: ScrollBehavior, coarsePointer: boolean) => () => void
    }
    harness.revealHarness(id as string, 'auto', coarsePointer as boolean)
  }, [setting, coarse] as [string, boolean])

for (const [label, viewport] of [['desktop', DESKTOP], ['mobile', MOBILE]] as const) {
  test(`a revealed setting lands in view on ${label}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.goto('/setting-reveal-harness.html')

    // It starts below the fold: without the reveal, arriving here shows none of it.
    const before = await placement(page, 'auto_delivery_enabled')
    expect(before.found).toBe(true)
    expect(before.insideViewport).toBe(false)

    await reveal(page, 'auto_delivery_enabled')
    await expect.poll(async () => (await placement(page, 'auto_delivery_enabled')).insideViewport).toBe(true)

    const after = await placement(page, 'auto_delivery_enabled')
    expect(after.belowRail).toBe(true)
    expect(after.flashed).toBe(true)
    // A checkbox takes focus on every device: it raises no keyboard and makes the control
    // operable straight from the link.
    expect(after.focused).toBe('INPUT:checkbox')

    // The cue is brief by design; a permanent outline would read as a validation error.
    await expect.poll(
      async () => (await placement(page, 'auto_delivery_enabled')).flashed,
      { timeout: 4000 },
    ).toBe(false)
  })
}

test('a control that renders after the request is still revealed', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/setting-reveal-harness.html?defer=1')

  expect((await placement(page, 'auto_delivery_enabled')).found).toBe(false)
  await reveal(page, 'auto_delivery_enabled')

  // The panel's own data lands after the request on a cold open, so the reveal waits for
  // the control rather than firing once into an empty tab.
  await expect.poll(
    async () => (await placement(page, 'auto_delivery_enabled')).insideViewport,
    { timeout: 5000 },
  ).toBe(true)
  expect((await placement(page, 'auto_delivery_enabled')).flashed).toBe(true)
})

// Settings sections are allowed to fold reference material away behind a `<details>`
// (`.settings-disclosure`, Settings → Voice), so the reveal has to arrive inside a closed one
// rather than stopping at its summary. Pinned here because the alternative is a rule nobody
// can check — "never mark a control inside a disclosure" — enforced by remembering it.
test('a control folded inside a closed disclosure is opened, not waited out', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/setting-reveal-harness.html?collapsed=1')

  const closed = await placement(page, 'auto_delivery_enabled')
  expect(closed.found).toBe(true)
  expect(closed.insideViewport).toBe(false)
  expect(await page.locator('details.settings-disclosure').evaluate(node => (node as HTMLDetailsElement).open)).toBe(false)

  await reveal(page, 'auto_delivery_enabled')
  await expect.poll(async () => (await placement(page, 'auto_delivery_enabled')).insideViewport).toBe(true)

  const after = await placement(page, 'auto_delivery_enabled')
  expect(after.belowRail).toBe(true)
  expect(after.flashed).toBe(true)
  expect(after.focused).toBe('INPUT:checkbox')
  expect(await page.locator('details.settings-disclosure').evaluate(node => (node as HTMLDetailsElement).open)).toBe(true)
})

test('a text field is not focused on a touch device, where the keyboard would cover it', async ({ page }) => {
  await page.setViewportSize(MOBILE)
  await page.goto('/setting-reveal-harness.html')

  await reveal(page, 'scan_timeline_model', true)
  await expect.poll(async () => (await placement(page, 'scan_timeline_model')).insideViewport).toBe(true)
  const touch = await placement(page, 'scan_timeline_model')
  expect(touch.flashed).toBe(true)
  expect(touch.focused).not.toBe('INPUT:text')

  // The same field on a fine pointer does take focus: there is no keyboard to get in the way.
  await page.goto('/setting-reveal-harness.html')
  await reveal(page, 'scan_timeline_model', false)
  await expect.poll(async () => (await placement(page, 'scan_timeline_model')).focused).toBe('INPUT:text')
})
