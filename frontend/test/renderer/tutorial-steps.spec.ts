import { expect, test } from 'playwright/test'

/**
 * The tour has a way forward on every step, at both layouts.
 *
 * This is the first-run blocker the 2026-08-20 usability audit measured, and it is only
 * visible in a browser: an action step used to render its hint *instead of* a Next
 * button, so a step whose anchor was not on screen offered `Exit tour ×` and nothing
 * else. On a phone that was real - `resources` anchors on a Notes control the side panel
 * carries, and the phone layout keeps that panel shut - and the tour died at step 10 of
 * 14. The desktop route to the same dead end is hiding the Notes tab.
 *
 * The harness deliberately mounts the tour over a page with *no* anchors, so every step
 * is in its worst case at once. A walk that finishes from there finishes anywhere.
 */

const VIEWPORTS = [
  { name: 'a phone', width: 390, height: 780 },
  { name: 'a desktop', width: 1280, height: 800 },
]

/** Press whatever forward control the current card offers, and say which it was. */
async function stepForward(page: import('playwright/test').Page): Promise<'next' | 'skip'> {
  const skip = page.locator('.tutorial-card > footer .tutorial-skip')
  if (await skip.count()) { await skip.click(); return 'skip' }
  await page.locator('.tutorial-card > footer button.primary').click()
  return 'next'
}

for (const viewport of VIEWPORTS) {
  for (const project of ['0', '1']) {
    test(`the tour walks to the end on ${viewport.name} with${project === '1' ? '' : 'out'} an existing Project`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await page.goto(`/tutorial-harness.html?project=${project}`)
      await expect(page.locator('.tutorial-card')).toBeVisible()

      const total = Number((await page.textContent('.tutorial-card > footer > span'))!.split('/')[1].trim())
      expect(total).toBeGreaterThan(10)

      // One press per step, no more: a step that offered nothing would time out here,
      // and a step that advanced twice would run the counter off the end.
      for (let index = 1; index < total; index++) {
        await expect(page.locator('.tutorial-card > footer > span')).toHaveText(`${index} / ${total}`)
        await stepForward(page)
      }
      await expect(page.locator('.tutorial-card > footer > span')).toHaveText(`${total} / ${total}`)

      // The last step is the only one that ends the tour, and it ends it as *complete*
      // rather than as the abandonment `Exit tour ×` records.
      await page.locator('.tutorial-card > footer button.primary').click()
      await expect(page.locator('#tutorial-ended')).toHaveText('complete')
    })
  }
}

test('every action-gated step offers a skip beside its hint, and the plain steps do not', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/tutorial-harness.html?project=0')
  await expect(page.locator('.tutorial-card')).toBeVisible()

  const total = Number((await page.textContent('.tutorial-card > footer > span'))!.split('/')[1].trim())
  let gated = 0
  for (let index = 1; index < total; index++) {
    const hint = await page.locator('.tutorial-card > footer > em').count()
    const skip = await page.locator('.tutorial-card > footer .tutorial-skip').count()
    const next = await page.locator('.tutorial-card > footer button.primary').count()
    // Exactly one forward control per card, and a hint is never the whole footer.
    expect(hint + next).toBe(1)
    expect(skip).toBe(hint)
    gated += hint
    await stepForward(page)
  }
  // The walk really does contain gated steps; otherwise the assertions above are vacuous.
  expect(gated).toBeGreaterThan(3)
})

test('the skip control fits inside the card on a phone, beside the hint', async ({ page }) => {
  // The footer is a single flex row and the skip button is a fourth thing in it, after
  // the counter, the progress bar, and the hint. A phone card is `100vw - 16px`, which is
  // where that runs out first - and a control pushed outside the card is a control the
  // user cannot press, which is the exact failure this whole change exists to remove.
  await page.setViewportSize({ width: 360, height: 720 })
  await page.goto('/tutorial-harness.html?project=0')
  await expect(page.locator('.tutorial-card')).toBeVisible()
  await stepForward(page)
  const skip = page.locator('.tutorial-card > footer .tutorial-skip')
  await expect(skip).toBeVisible()

  const card = (await page.locator('.tutorial-card').boundingBox())!
  const button = (await skip.boundingBox())!
  const hint = (await page.locator('.tutorial-card > footer > em').boundingBox())!
  expect(button.x).toBeGreaterThanOrEqual(card.x)
  expect(button.x + button.width).toBeLessThanOrEqual(card.x + card.width + 0.5)
  // Beside the hint, not on top of it.
  expect(button.x).toBeGreaterThanOrEqual(hint.x + hint.width - 0.5)
})

for (const viewport of VIEWPORTS) {
  test(`the menu step describes the menu that exists on ${viewport.name}`, async ({ page }) => {
    // `tourChrome.test.ts` proves the names are real by reading `App.tsx`; this proves the
    // card actually renders them, at both layouts, in the browser. The negative half is the
    // regression that started Phase 16's stale-guidance item: this step described a
    // `Utilities` group for months after it was unfolded into the rows below.
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.goto('/tutorial-harness.html?project=1')
    await expect(page.locator('.tutorial-card')).toBeVisible()

    const eyebrow = () => page.textContent('.tutorial-card > header span')
    for (let guard = 0; guard < 20; guard++) {
      if (await eyebrow() === 'MAIN FEATURES') break
      await stepForward(page)
    }
    expect(await eyebrow()).toBe('MAIN FEATURES')

    const copy = (await page.textContent('.tutorial-copy'))!
    expect(copy).not.toContain('Utilities')
    for (const row of ['Session history', 'Fleet queue', 'Usage & spend', 'Automation Dashboard', 'Help']) {
      expect(copy).toContain(row)
    }
    expect(copy).toContain('Maintenance')
  })
}

test('the provider-login step is optional, and says so in the harness panel’s words', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/tutorial-harness.html?project=1')
  await expect(page.locator('.tutorial-card')).toBeVisible()

  // Walk to it by what the card says rather than by index: the step list branches on
  // layout and on whether a Project already exists, so a hard-coded position rots. The
  // card is also the only reading that cannot run ahead of the render - `onNavigate`
  // fires from an effect, so a step recorded there lags the DOM by a frame.
  const eyebrow = () => page.textContent('.tutorial-card > header span')
  for (let guard = 0; guard < 12; guard++) {
    if (await eyebrow() === 'PROVIDER ACCOUNTS') break
    await stepForward(page)
  }
  expect(await eyebrow()).toBe('PROVIDER ACCOUNTS')

  const copy = (await page.textContent('.tutorial-copy'))!
  // The contradiction this closes: the first-run harness panel calls CLI login a later
  // step ("sign in to each agent CLI so its account and history appear"), and this step
  // then refused to move until one was saved. Both surfaces now say the same thing.
  expect(copy).toContain('reads Claude’s and Codex’s own login files')
  expect(copy).toContain('not required')
  // Named answer, not a generic dismissal: "I'll do this later" is a choice.
  await expect(page.locator('.tutorial-card > footer .tutorial-skip')).toHaveText('I’ll do this later')

  await page.locator('.tutorial-card > footer .tutorial-skip').click()
  // Skipping advances the tour rather than ending it.
  await expect(page.locator('.tutorial-card > header span')).not.toHaveText('PROVIDER ACCOUNTS')
  await expect(page.locator('#tutorial-ended')).toHaveCount(0)
})
