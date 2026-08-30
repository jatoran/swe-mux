import { expect, test, type Locator } from 'playwright/test'

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

test('several accounts stack into quota columns rather than into sentences of different lengths', async ({ page }) => {
  await page.goto('/account-switcher-harness.html?accounts=multi')
  await page.click('.account-summary > button >> nth=0')

  const rows = page.locator('.account-popover .quota-row')
  await expect(rows).toHaveCount(3)

  // The property, measured rather than asserted about the markup: within each column,
  // every account's figure starts at the same x. This can only be checked in a browser -
  // the headings are a `div` and the figures are in a `small` inside a `button`, so they
  // are separate formatting contexts and no `auto` grid track would ever line them up.
  const left = (target: Locator) => target.evaluateAll(nodes =>
    nodes.map(node => Math.round(node.getBoundingClientRect().left)))
  for (const key of ['session', 'weekly', 'fable']) {
    const columns = await left(page.locator(`.account-popover .quota-cell-${key} b`))
    // The errored account draws no cells at all, so two of the three rows reach here.
    expect(columns.length).toBeGreaterThan(1)
    expect(new Set(columns).size).toBe(1)
  }
  // And the percentages themselves are right-aligned inside their column, so `5%` and
  // `100%` end level instead of starting level and drifting apart.
  const rights = await page.locator('.account-popover .quota-cell-session b').evaluateAll(nodes =>
    nodes.map(node => Math.round(node.getBoundingClientRect().right)))
  expect(new Set(rights).size).toBe(1)

  // The headings sit over the columns they name, which is what makes a bare column of
  // percentages readable at all.
  const headings = page.locator('.account-popover .quota-columns span')
  await expect(headings).toHaveText(['5h', 'weekly', 'fable'])
  const headingLefts = await left(page.locator('.account-popover .quota-columns span'))
  const firstCells = await left(rows.first().locator('.quota-cell'))
  expect(headingLefts).toEqual(firstCells)

  // A failed poll invalidates the whole account rather than one window of it, and says so
  // in place of a stale mix that would read as current.
  await expect(page.locator('.account-popover .quota-row-note')).toHaveText('unavailable')
})

test('the codex section carries two quota columns, not an empty fable one', async ({ page }) => {
  await page.goto('/account-switcher-harness.html?accounts=multi')
  await page.click('.account-summary > button >> nth=0')
  // Claude reports Fable; Codex has no saved account here at all, so no columns are drawn
  // for it. The column set belongs to the section, never to the popover.
  await expect(page.locator('.account-popover .quota-columns')).toHaveCount(1)
  await expect(page.locator('.account-popover .quota-columns.has-fable')).toHaveCount(1)
})

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
