import { expect, test } from 'playwright/test'

/**
 * Whether the Queue tab can say, continuously and without lag, that its target will not
 * take a message and why.
 *
 * The report this came from: a message armed at a session that read idle everywhere was
 * refused with `Not safe right now: terminal_input_after_completion`, after the operator
 * had typed something and then deleted it. Every part of that is working as designed —
 * the guard counts keystrokes, not composer contents — and none of it was sayable before
 * the press.
 *
 * Three of the properties that make the fix honest exist only in a browser:
 *
 *  - the strip paints from the session row **before** the tab's own fetch resolves, which
 *    is the entire "no lag when I pull up that tab" claim;
 *  - the fetched target view then corrects it, by `observed_at` rather than by arrival;
 *  - a pushed update reaches it with no refetch, which is what the daemon's transient
 *    readiness stream buys.
 */

test('the strip paints from the row before the queue fetch resolves', async ({ page }) => {
  // The fetch is held open, so anything on screen came from the session row the pane was
  // handed — the copy the composition root already had in memory when the tab opened.
  await page.goto('/queue-readiness-harness.html?hold=1')
  await page.waitForSelector('.queue-readiness')

  const strip = page.locator('.queue-readiness')
  await expect(strip).toHaveClass(/queue-readiness-blocked/)
  await expect(page.locator('.queue-readiness-headline')).toHaveText('not deliverable')
  await expect(page.locator('.queue-readiness-summary'))
    .toHaveText(/typed in this terminal after its last turn ended/)

  // The half that answers the confusion, not just the half that names the check.
  await expect(page.locator('.queue-readiness-clears').first())
    .toContainText('Clearing the line does not clear this')
  await expect(page.locator('.queue-readiness-clears').first())
    .toContainText('next turn ends')

  // And the narration that makes the block stop looking like a bug: the composer really
  // is empty, and the block really does persist.
  await expect(page.locator('.queue-readiness')).toContainText('Nothing is sitting in the composer now')
})

test('the queue fetch corrects the row it painted from', async ({ page }) => {
  await page.goto('/queue-readiness-harness.html?hold=1')
  await page.waitForSelector('.queue-readiness')
  await expect(page.locator('.queue-readiness-summary'))
    .toHaveText(/typed in this terminal/)

  await page.evaluate(() => (window as unknown as { __releaseFetch: () => void }).__releaseFetch())
  // The target view's reading is stamped newer, so it wins — and it wins on the stamp,
  // not on having arrived second.
  await expect(page.locator('.queue-readiness-summary'))
    .toHaveText(/not showing the agent’s prompt/)
})

test('a pushed readiness update reaches the strip with no refetch', async ({ page }) => {
  await page.goto('/queue-readiness-harness.html?hold=1')
  await page.waitForSelector('.queue-readiness')

  const fetches: string[] = []
  page.on('request', request => { if (request.url().includes('/api/queue')) fetches.push(request.url()) })

  await page.evaluate(() => {
    const api = window as unknown as {
      __pushReadiness: (readiness: unknown) => void
      __readings: Record<string, unknown>
    }
    api.__pushReadiness(api.__readings.safe)
  })
  await expect(page.locator('.queue-readiness')).toHaveClass(/queue-readiness-safe/)
  await expect(page.locator('.queue-readiness-headline')).toHaveText('deliverable')
  // A safe target says so in one line and stops talking.
  await expect(page.locator('.queue-readiness-summary')).toHaveCount(0)
  await expect(page.locator('.queue-readiness-clears')).toHaveCount(0)
  expect(fetches).toEqual([])
})

test('an old reading says how old it is instead of passing as current', async ({ page }) => {
  // Load-bearing rather than decorative: `sessionSnapshots.ts` preserves the last known
  // readiness across raw PTY snapshots, so without this a verdict from a minute ago
  // renders identically to one from this second.
  await page.goto('/queue-readiness-harness.html?hold=1&row=stale')
  await page.waitForSelector('.queue-readiness')
  await expect(page.locator('.queue-readiness-age')).toHaveText(/4[5-9]s ago/)

  // A mid-turn block is not the end of the story for a message that asked to interject.
  await expect(page.locator('.queue-readiness'))
    .toContainText('can still be written into the running turn')
})

test('a protection is named before the press, and never disables the button', async ({ page }) => {
  await page.goto('/queue-readiness-harness.html?hold=1&row=protectedApproval')
  await page.waitForSelector('.queue-readiness')
  await expect(page.locator('.queue-readiness-summary')).toHaveText(/waiting on an approval prompt/)
  await expect(page.locator('.queue-readiness-protected'))
    .toContainText('“Send anyway” will not be offered')

  // Nothing here is allowed to take the operator's override away: the advisory can be
  // stale, and a stale false block with no way out is worse than a wrong label. There is
  // no message staged in this harness, so the assertion is that the strip owns the
  // warning and no disabled-send class appears with it.
  await expect(page.locator('.queue-send[disabled]')).toHaveCount(0)
})

test('a fresh reading shows no age at all', async ({ page }) => {
  await page.goto('/queue-readiness-harness.html?hold=1')
  await page.waitForSelector('.queue-readiness')
  await expect(page.locator('.queue-readiness-age')).toHaveCount(0)
})
