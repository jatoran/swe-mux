import { expect, test, type Locator, type Page } from 'playwright/test'

/**
 * The transcript reader's per-message chip row: when it is drawn, and what it covers.
 *
 * Every fact here belongs to the browser. The row's presence is a media query plus a
 * `:hover`, its harmlessness while hidden is `pointer-events` under an `opacity:0` that
 * still hit-tests, and the defect that started this - controls sitting on top of the
 * timestamp - is two boxes overlapping, which only a laid-out page can answer.
 *
 * The touch half runs `hasTouch` + `isMobile` so Chromium reports `(hover: none)`; a
 * `hasTouch` page that still hovers would silently exercise the pointer rules twice, so
 * the first test asserts the emulation itself.
 */

const messages = (page: Page) => page.locator('.transcript-message')
const anchorOf = (message: Locator) => message.locator('.transcript-copy-anchor')
const opacityOf = (locator: Locator) =>
  locator.evaluate(element => getComputedStyle(element).opacity)

const open = async (page: Page, query = '') => {
  await page.goto(`/transcript-actions-harness.html${query}`)
  await expect(messages(page)).toHaveCount(3)
}

test.describe('pointer', () => {
  test.use({ viewport: { width: 900, height: 800 } })

  test('the row is absent from the resting column and cannot be clicked through', async ({ page }) => {
    await open(page)
    const first = messages(page).first()
    expect(await opacityOf(anchorOf(first))).toBe('0')
    // The dangerous half: `opacity:0` still hit-tests, so an unrevealed Copy sitting over
    // the prose would take a click meant for the words underneath it.
    expect(await anchorOf(first).locator('.transcript-copy').first()
      .evaluate(element => getComputedStyle(element).pointerEvents)).toBe('none')
  })

  test('hovering a message reveals that message, and keyboard focus reveals it too', async ({ page }) => {
    await open(page)
    const [first, second] = [messages(page).nth(0), messages(page).nth(1)]
    await first.hover()
    await expect.poll(() => opacityOf(anchorOf(first))).toBe('1')
    expect(await opacityOf(anchorOf(second))).toBe('0')
    expect(await anchorOf(first).locator('.transcript-copy').first()
      .evaluate(element => getComputedStyle(element).pointerEvents)).toBe('auto')

    // A control hidden with `visibility` or `display` cannot take the focus that would
    // reveal it, which is why the hidden state is opacity: a keyboard reader has to be
    // able to tab into a row that is not drawn yet.
    await page.mouse.move(0, 0)
    await expect.poll(() => opacityOf(anchorOf(first))).toBe('0')
    await anchorOf(second).locator('.transcript-copy').first().focus()
    await expect.poll(() => opacityOf(anchorOf(second))).toBe('1')
  })

  test('Copy and Select are marks, and still say what they are', async ({ page }) => {
    await open(page)
    const chips = anchorOf(messages(page).first()).locator('.transcript-copy')
    await expect(chips).toHaveCount(2)
    for (const label of ['Select text from this message', 'Copy this message']) {
      await expect(anchorOf(messages(page).first()).locator(`[aria-label="${label}"]`)).toHaveCount(1)
    }
    // Icon-only: a mark and no words. The words are on the accessible name and the tooltip.
    expect(await chips.first().evaluate(element => element.textContent)).toBe('')
    await expect(chips.first().locator('svg')).toHaveCount(1)
    await expect(chips.nth(1).locator('svg')).toHaveCount(1)
  })

  test('the revealed row clears the timestamp, markers and all', async ({ page }) => {
    // The defect: a four-chip row overran a gutter sized for two, and sat on the stamp.
    await open(page, '?readAloud=1')
    const reply = messages(page).nth(1)
    await expect(reply).toHaveClass(/has-audio/)
    await expect(anchorOf(reply).locator('.transcript-audio')).toHaveCount(2)
    await reply.hover()
    await expect.poll(() => opacityOf(anchorOf(reply))).toBe('1')
    const stamp = await reply.locator('header time').boundingBox()
    const row = await anchorOf(reply).locator('.transcript-copy').first().boundingBox()
    expect(stamp).not.toBeNull()
    expect(row).not.toBeNull()
    expect(stamp!.x + stamp!.width).toBeLessThanOrEqual(row!.x)
  })
})

test.describe('touch', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 780 } })

  test('a tap opens one entry, and tapping it again closes it', async ({ page }) => {
    await open(page)
    // If this fails the rest of the file is testing the pointer rules a second time.
    expect(await page.evaluate(() => matchMedia('(hover: none)').matches)).toBe(true)

    const [first, second] = [messages(page).nth(0), messages(page).nth(1)]
    expect(await opacityOf(anchorOf(first))).toBe('0')
    await first.tap()
    await expect.poll(() => opacityOf(anchorOf(first))).toBe('1')
    // One at a time: a phone has no room for a row of controls above every reply.
    expect(await opacityOf(anchorOf(second))).toBe('0')
    await second.tap()
    await expect.poll(() => opacityOf(anchorOf(second))).toBe('1')
    await expect.poll(() => opacityOf(anchorOf(first))).toBe('0')
    await second.tap()
    await expect.poll(() => opacityOf(anchorOf(second))).toBe('0')
  })

  test('pressing a chip does not close the row it sits in', async ({ page }) => {
    await open(page)
    const first = messages(page).first()
    await first.tap()
    await expect.poll(() => opacityOf(anchorOf(first))).toBe('1')
    // The tap that opened the row is the same event the chips sit inside.
    await anchorOf(first).locator('[aria-label="Select text from this message"]').tap()
    await expect(page.locator('.transcript-select-sheet')).toHaveCount(1)
    await page.locator('.transcript-select-sheet > header > button').tap()
    expect(await opacityOf(anchorOf(first))).toBe('1')
  })

  test('the opened row clears the timestamp', async ({ page }) => {
    await open(page)
    const first = messages(page).first()
    await first.tap()
    await expect.poll(() => opacityOf(anchorOf(first))).toBe('1')
    const stamp = await first.locator('header time').boundingBox()
    const row = await anchorOf(first).locator('.transcript-copy').first().boundingBox()
    expect(stamp!.x + stamp!.width).toBeLessThanOrEqual(row!.x)
  })
})
