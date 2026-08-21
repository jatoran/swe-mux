import { expect, test } from 'playwright/test'

/**
 * What a gate promises, checked where the promise is actually kept.
 *
 * The gate replaced a link that opened an overlay, staged a Save, and left the reader to
 * walk back to the pane they started on - for the scan timeline, twice, because the
 * second switch only revealed itself after the first was on. Three of the properties that
 * make the replacement honest live entirely in the browser:
 *
 *  - it is **one** request, so a mixed-scope grant cannot half-land;
 *  - the disclosure is read from the live registry, so "also switches on…" and "free to
 *    run" cannot drift from what the daemon will actually do;
 *  - the gate clears on the grant resolving, not on a websocket round trip.
 *
 * A failure keeps the gate up and says why, because nothing changed.
 */

test('a gate discloses scope, closure, and cost before the button', async ({ page }) => {
  await page.goto('/grant-gate-harness.html')
  await page.waitForSelector('.grant-gate-confirm')

  // Both rungs, outermost first, each with the scope it changes and where it is written.
  const steps = await page.$$eval('.grant-gate-steps li', rows => rows.map(row => ({
    scope: row.querySelector('.grant-gate-scope')!.textContent,
    what: row.querySelector('.grant-gate-what')!.textContent,
    writtenTo: row.querySelector('small')!.textContent,
  })))
  expect(steps).toHaveLength(2)
  expect(steps[0].scope).toBe('all projects')
  expect(steps[1].scope).toBe('this Project')
  // A Project opt-in is committed repository content and reaches every clone. That is the
  // consequence most easily missed and it is stated on the row, every time.
  expect(steps[1].writtenTo).toContain('travels with the checkout')

  // The closure is named rather than discovered afterwards: asking for the timeline also
  // switches on the two substrate automations it reads from.
  const closure = await page.textContent('.grant-gate-closure')
  expect(closure).toContain('Deterministic fact capture')
  expect(closure).toContain('Raw transcript store')

  // ...and the cost, from the registry's own `spends`, not a claim this surface makes.
  await expect(page.locator('.grant-gate-cost')).toHaveClass(/spends/)
  expect(await page.textContent('.grant-gate-cost')).toContain('spend')

  // The owning overlay stays reachable as the secondary control. A gate answers the
  // question in front of you; the overlay is the only place it can be undone.
  await expect(page.locator('.grant-gate-actions .setting-link')).toBeVisible()
})

test('a mixed-scope grant is one request, and the gate clears when it resolves', async ({ page }) => {
  await page.goto('/grant-gate-harness.html')
  await page.click('.grant-gate-confirm')
  await page.waitForSelector('#surface-live')

  const posts = await page.evaluate(() =>
    (window as unknown as { __calls: { method: string; url: string; body: Record<string, unknown> }[] })
      .__calls.filter(call => call.method === 'POST'))
  // Exactly one. Sequencing the install write and the Project write from the browser
  // would mean two revisions, two failure modes, and a half-granted state whenever the
  // second lost - which is the thing a gate exists to spare someone.
  expect(posts).toHaveLength(1)
  expect(posts[0].url).toBe('/api/grants')
  expect(posts[0].body).toMatchObject({
    install: { scan_timeline_enabled: true },
    automations: ['scan_timeline'],
    project_id: 'p1',
  })
  // The closure is the daemon's to compute: the browser names it for the reader and does
  // not send it, so the two cannot disagree about what was written.
  expect(posts[0].body.automations).toEqual(['scan_timeline'])
})

test('a refused grant keeps the gate up and says why', async ({ page }) => {
  await page.goto('/grant-gate-harness.html?fail=1')
  await page.click('.grant-gate-confirm')
  await page.waitForSelector('.grant-gate-error')

  expect(await page.textContent('.grant-gate-error')).toContain('read-only')
  // Nothing changed, so the gate must not clear: a surface that cleared on a refusal
  // would claim a permission the daemon does not have.
  await expect(page.locator('.grant-gate-confirm')).toBeVisible()
  await expect(page.locator('#surface-live')).toHaveCount(0)
})
