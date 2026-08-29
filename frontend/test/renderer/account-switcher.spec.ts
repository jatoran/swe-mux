import { expect, test } from 'playwright/test'

/**
 * The switcher as a way *in*, not only a way to switch between what already exists.
 *
 * With nothing saved the popover used to print "No saved accounts" beside a `manage…`
 * button into Settings - which is the one screen a new install always lands on, and the
 * one with no path forward on it.
 *
 * Three properties here live only in the browser:
 *
 *  - the empty state is itself the control, and it starts a *login* rather than a capture;
 *  - a run started on the daemon keeps reporting itself across a dismissal and a reopen,
 *    because the state is polled rather than owned by the request that started it;
 *  - a failure carries its reason, which is the only copy of it that exists.
 */

test('an empty popover offers the sign-in it used to only describe the absence of', async ({ page }) => {
  await page.goto('/account-switcher-harness.html')
  await page.click('.account-summary > button >> nth=0')

  const cta = page.locator('.account-empty-cta').first()
  await expect(cta).toBeVisible()
  await expect(cta).toContainText('sign in to claude')
  // The old dead end, gone: a sentence reporting emptiness with no control on it.
  await expect(page.locator('.account-popover section > p', { hasText: /^No saved accounts$/ })).toHaveCount(0)

  await cta.click()
  await expect(page.locator('.account-login.running').first()).toBeVisible()

  const posts = await page.evaluate(() =>
    (window as unknown as { __calls: { method: string; url: string }[] }).__calls
      .filter(call => call.method === 'POST'))
  expect(posts).toHaveLength(1)
  // A login, not a capture: capturing here would save whatever credential happened to be
  // on the host, which for an empty install is nothing at all.
  expect(posts[0].url).toBe('/api/provider-accounts/claude/login')
})

test('a running sign-in survives dismissing the popover, because it is the daemon that owns it', async ({ page }) => {
  await page.goto('/account-switcher-harness.html?saved=1')
  await page.click('.account-summary > button >> nth=0')
  await page.click('.account-section-head .account-signin >> nth=0')
  await expect(page.locator('.account-login.running').first()).toBeVisible()

  // Close it the ordinary way, then come back. While this was one blocked HTTP request,
  // the outcome belonged to whoever held it and this reopen showed nothing at all.
  await page.keyboard.press('Escape')
  await expect(page.locator('.account-popover')).toHaveCount(0)
  await page.click('.account-summary > button >> nth=0')

  await expect(page.locator('.account-login.succeeded').first()).toContainText('Signed in as work@example.com')
})

test('a failed sign-in keeps its reason until it is dismissed', async ({ page }) => {
  await page.goto('/account-switcher-harness.html?saved=1&outcome=failed')
  await page.click('.account-summary > button >> nth=0')
  await page.click('.account-section-head .account-signin >> nth=0')

  const failure = page.locator('.account-login.failed').first()
  await expect(failure).toBeVisible()
  // The request that started the run returned minutes ago; this is where the reason lives.
  await expect(failure).toContainText('browser login was cancelled')

  await failure.locator('button').click()
  await expect(page.locator('.account-login')).toHaveCount(0)
  const dismissals = await page.evaluate(() =>
    (window as unknown as { __calls: { method: string; url: string }[] }).__calls
      .filter(call => call.url.includes('/login/dismiss')))
  expect(dismissals).toHaveLength(1)
})
