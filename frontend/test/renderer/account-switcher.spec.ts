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

test('each account names its quota periods inline without a detached heading row', async ({ page }) => {
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

  await expect(page.locator('.account-popover .quota-columns')).toHaveCount(0)
  await expect(rows.first().locator('.quota-cell')).toHaveText(['5% 4h3m/5h', '63% 3d1h/7d', '30% fable'])
  // A hairline between columns, drawn rather than typed, and a breath between the
  // percentage and what follows it. Both measured: the separator has width, the gap
  // between the figure's box and the reset text is under one character, and the two
  // columns sit closer than the 2ch they used to.
  const geometry = await rows.first().evaluate(row => {
    const box = (selector: string) => row.querySelector(selector)!.getBoundingClientRect()
    const percent = box('.quota-cell-session b')
    // The figure's box is `flex:0 0 4ch`, so it is the row's own ruler for `ch`.
    const ch = percent.width / 4
    const reset = box('.quota-cell-session .quota-reset')
    const separator = box('.quota-cell-weekly .quota-separator')
    const session = box('.quota-cell-session')
    const weekly = box('.quota-cell-weekly b')
    return {
      separatorWidth: separator.width,
      separatorHeight: separator.height,
      breath: (reset.left - percent.right) / ch,
      columnGap: (weekly.left - session.right) / ch,
    }
  })
  expect(geometry.separatorWidth).toBeGreaterThan(0)
  expect(geometry.separatorHeight).toBeGreaterThan(0)
  expect(geometry.breath).toBeGreaterThan(0.2)
  expect(geometry.breath).toBeLessThan(0.6)
  expect(geometry.columnGap).toBeLessThan(1.5)
  const tones = await rows.first().evaluate(row => {
    const style = (selector: string) => getComputedStyle(row.querySelector(selector)!)
    return {
      percent: style('.quota-cell b').color,
      reset: style('.quota-reset').color,
      resetOpacity: Number(style('.quota-reset').opacity),
      qualifier: style('.quota-window-label').color,
      qualifierOpacity: Number(style('.quota-window-label').opacity),
    }
  })
  expect(tones.percent).not.toBe(tones.reset)
  expect(tones.qualifier).toBe(tones.reset)
  expect(tones.qualifierOpacity).toBeLessThan(tones.resetOpacity)

  const refreshAge = page.locator('.account-popover section > button > .account-refresh-age').first()
  await expect(refreshAge).toHaveText('now')
  const identityTop = await page.locator('.account-popover section > button > strong').first().evaluate(node => Math.round(node.getBoundingClientRect().top))
  expect(await refreshAge.evaluate(node => Math.round(node.getBoundingClientRect().top))).toBe(identityTop)

  // A failed poll invalidates the whole account rather than one window of it, and says so
  // in place of a stale mix that would read as current.
  await expect(page.locator('.account-popover .quota-row-note')).toHaveText('unavailable')
})

test('only providers with a credential on this host draw a row', async ({ page }) => {
  // `providers` is two entries in every payload; `accounts` here holds Claude only,
  // and Codex is signed out. One row, not two, and no `—` for the one nobody uses.
  await page.goto('/account-switcher-harness.html?saved=1')
  await expect(page.locator('.account-summary > button')).toHaveCount(1)
  await expect(page.locator('.account-summary .provider-glyph.claude')).toHaveCount(1)
  await expect(page.locator('.account-summary .provider-glyph.codex')).toHaveCount(0)
  await expect(page.locator('.account-prompt')).toHaveCount(0)

  // The popover still offers both, because a provider with no credential is exactly
  // the one you might be opening this to add.
  await page.click('.account-summary > button >> nth=0')
  await expect(page.locator('.account-popover .account-section-head h4')).toHaveText(['claude', 'codex'])
})

test('the popover counts live sessions per account and names the ones a switch left behind', async ({ page }) => {
  await page.goto('/account-switcher-harness.html?accounts=multi')
  await page.click('.account-summary > button >> nth=0')

  // The selected account carries its count like any other; the badge is a fact about
  // the row, not a warning about it.
  const counts = page.locator('.account-popover .account-session-count')
  await expect(counts).toHaveText(['5×', '2×'])

  // What a switch did to them is the daemon's per-provider fact, and this harness
  // declares Claude Code as a CLI that follows the switch - so no sentence is drawn
  // under these counts. The paragraph only exists for a provider whose CLI keeps the
  // login it started with.
  await expect(page.locator('.account-popover .account-session-notice')).toHaveCount(0)

  // The badge rides in the row's own `small`, so the quota columns above it keep the
  // geometry `quota-row` depends on: adding it must not move a percentage.
  const cells = await page.locator('.account-popover .quota-cell-session b').evaluateAll(nodes =>
    nodes.map(node => Math.round(node.getBoundingClientRect().left)))
  expect(new Set(cells).size).toBe(1)
})

test('the codex section carries two quota columns, not an empty fable one', async ({ page }) => {
  await page.goto('/account-switcher-harness.html?accounts=multi')
  await page.click('.account-summary > button >> nth=0')
  // Claude reports Fable, so its rows carry the implicit third value. Codex has no saved
  // account here at all, and no detached heading survives for either provider.
  await expect(page.locator('.account-popover .quota-row.has-fable')).toHaveCount(3)
  await expect(page.locator('.account-popover .quota-columns')).toHaveCount(0)
})

test('a machine signed in to nothing is invited, not reported at', async ({ page }) => {
  await page.goto('/account-switcher-harness.html')

  // No provider has a credential on this host, so there are no rows: two entries
  // reading "signed out" is a feature advertising itself to a user who may never
  // adopt it. What is here instead is one way in and one way out.
  await expect(page.locator('.account-summary')).toHaveCount(0)
  const prompt = page.locator('.account-prompt')
  await expect(prompt).toBeVisible()
  await expect(prompt.locator('button')).toHaveText(['add provider', 'hide'])

  // `hide` goes through the host, because the flag is machine config rather than
  // anything this component remembers.
  await prompt.locator('button', { hasText: 'hide' }).click()
  await expect(page.locator('.account-prompt')).toHaveCount(0)
  await expect(page.locator('.account-summary')).toHaveCount(0)
  const ui = await page.evaluate(() =>
    (window as unknown as { __calls: { method: string; url: string }[] }).__calls
      .filter(call => call.method === 'UI'))
  expect(ui).toEqual([{ method: 'UI', url: 'dismiss-prompt' }])
})

test('an empty popover offers the sign-in it used to only describe the absence of', async ({ page }) => {
  await page.goto('/account-switcher-harness.html')
  // The invitation is the way in now that there are no provider rows to click.
  await page.click('.account-prompt button >> nth=0')

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
