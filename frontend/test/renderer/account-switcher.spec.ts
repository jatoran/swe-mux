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
  // At the default scale the row has room for every qualifier, so all three are drawn.
  await expect(rows.first().locator('.quota-window-label')).toHaveCount(3)
  for (const label of await rows.first().locator('.quota-window-label').all()) await expect(label).toBeVisible()
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
  // the account, not a warning about it.
  const counts = page.locator('.account-popover .account-session-count')
  await expect(counts).toHaveText(['5×', '2×'])

  // What a switch did to them is the daemon's per-provider fact, and this harness
  // declares Claude Code as a CLI that follows the switch - so no sentence is drawn
  // under these counts. The paragraph only exists for a provider whose CLI keeps the
  // login it started with.
  await expect(page.locator('.account-popover .account-session-notice')).toHaveCount(0)

  // The badge sits on the name line, between the name and the refresh age, so the quota
  // line below keeps the row's full width and a percentage never moves for it.
  const placement = await page.locator('.account-popover section > button').first().evaluate(row => {
    const box = (selector: string) => row.querySelector(selector)!.getBoundingClientRect()
    const name = box(':scope > strong'), count = box(':scope > .account-session-count')
    const age = box(':scope > .account-refresh-age'), quota = box('.quota-row')
    return {
      afterName: count.left - name.right,
      beforeAge: age.left - count.right,
      onNameLine: count.top < name.bottom && count.bottom > name.top,
      aboveQuota: count.bottom <= quota.top + 1,
    }
  })
  expect(placement.afterName).toBeGreaterThanOrEqual(0)
  expect(placement.beforeAge).toBeGreaterThan(0)
  expect(placement.onNameLine).toBe(true)
  expect(placement.aboveQuota).toBe(true)
  await expect(page.locator('.account-popover .quota-row .account-session-count')).toHaveCount(0)
  const cells = await page.locator('.account-popover .quota-cell-session b').evaluateAll(nodes =>
    nodes.map(node => Math.round(node.getBoundingClientRect().left)))
  expect(new Set(cells).size).toBe(1)
})

test('a Fable row fits the popover at a large UI scale and stays in columns with every other row', async ({ page }) => {
  // The popover is a fixed width while its text scales with the chrome, so at 1.4x the
  // full Fable row (38ch) no longer fits the ~34ch it is given. A Codex account with the
  // widest weekly countdown sits in the other section, because the columns have to agree
  // across sections and not only within one.
  await page.goto('/account-switcher-harness.html?accounts=multi&codex=1&scale=1.4')
  await page.click('.account-summary > button >> nth=0')
  const popover = page.locator('.account-popover')
  await expect(popover.locator('.quota-row')).toHaveCount(4)

  // Nothing overruns: not a row, not the popover, not the Fable label inside its track.
  const overflow = await popover.evaluate(node => ({
    popover: node.scrollWidth - node.clientWidth,
    rows: [...node.querySelectorAll('section > button')].map(row => row.scrollWidth - row.clientWidth),
    fable: [...node.querySelectorAll('.quota-cell-fable .quota-window-label')].map(label => label.scrollWidth - label.clientWidth),
  }))
  expect(overflow.popover).toBeLessThanOrEqual(0)
  for (const row of overflow.rows) expect(row).toBeLessThanOrEqual(0)
  // Both readable Claude rows carry the column, the one without a Fable reading included.
  expect(overflow.fable.length).toBe(2)
  for (const label of overflow.fable) expect(label).toBeLessThanOrEqual(0)
  await expect(popover.locator('.quota-cell-fable .quota-window-label').first()).toBeVisible()

  // The compact tier drops the 5h and weekly qualifiers, on every row in both sections.
  for (const label of await popover.locator(':is(.quota-cell-session,.quota-cell-weekly) .quota-window-label').all()) await expect(label).toBeHidden()

  // And it is one tier for the whole popover: Codex's columns sit under Claude's.
  const lefts = (selector: string) => popover.locator(selector).evaluateAll(nodes =>
    nodes.map(node => Math.round(node.getBoundingClientRect().left)))
  for (const key of ['session', 'weekly']) {
    const columns = await lefts(`.quota-cell-${key} b`)
    expect(columns.length).toBe(3)
    expect(new Set(columns).size).toBe(1)
  }
  // The widest countdown still reads whole in the compact weekly column.
  await expect(popover.locator('section').nth(1).locator('.quota-cell-weekly .quota-reset')).toHaveText('29d23h')
  const reset = await popover.locator('section').nth(1).locator('.quota-cell-weekly .quota-reset').evaluate(node => node.scrollWidth - node.clientWidth)
  expect(reset).toBeLessThanOrEqual(0)
})

test('the qualifiers stay at a large UI scale when no account reports Fable', async ({ page }) => {
  // The tier answers whether the row fits, not what the scale is: without a Fable column
  // the same 1.4x row has room for every qualifier.
  await page.goto('/account-switcher-harness.html?saved=1&codex=1&scale=1.4')
  await page.click('.account-summary > button >> nth=0')
  const popover = page.locator('.account-popover')
  await expect(popover.locator('.quota-cell-fable')).toHaveCount(0)
  const labels = popover.locator('.quota-window-label')
  await expect(labels).toHaveCount(4)
  for (const label of await labels.all()) await expect(label).toBeVisible()
})

test('a stranded login is one collapsed line that expands, dismisses, and can be silenced for good', async ({ page }) => {
  await page.goto('/account-switcher-harness.html?accounts=stranded')
  await page.click('.account-summary > button >> nth=0')

  // Codex keeps the login it started with, so each login still being spent is a notice:
  // the saved account the switch left, and the login mux never saved. Claude follows the
  // switch and draws nothing, whatever its counts.
  const notices = page.locator('.account-popover .account-session-notice')
  await expect(notices).toHaveCount(2)
  const summaries = page.locator('.account-popover .account-session-notice-summary')
  await expect(summaries).toHaveText([
    /17 live sessions still on gorskovich\.tony@example\.com/,
    /1 live session still on a login that is not saved here/,
  ])
  // Collapsed: one line, clamped rather than wrapped, and none of the explanation.
  await expect(page.locator('.account-popover .account-session-notice-body')).toHaveCount(0)
  await expect(summaries.first()).toHaveAttribute('aria-expanded', 'false')
  const clamp = await summaries.first().locator('span').evaluate(node => {
    const style = getComputedStyle(node)
    return { whiteSpace: style.whiteSpace, overflow: style.textOverflow, lines: node.getBoundingClientRect().height / parseFloat(style.lineHeight) }
  })
  expect(clamp.whiteSpace).toBe('nowrap')
  expect(clamp.overflow).toBe('ellipsis')
  expect(clamp.lines).toBeLessThan(1.5)

  // Expanded: the whole sentence, the checkbox unticked, and the button.
  await summaries.first().click()
  await expect(summaries.first()).toHaveAttribute('aria-expanded', 'true')
  const body = notices.first().locator('.account-session-notice-body')
  await expect(body).toHaveText(/17 live sessions on gorskovich\.tony@example\.com\. Codex reads its login at startup, so they keep spending gorskovich\.tony@example\.com until restarted\./)
  await expect(body.getByRole('checkbox', { name: 'never show this again' })).not.toBeChecked()

  // Dismiss without the box: this notice goes, its sibling stays, and it stays gone
  // when the popover is reopened - a dismissal is not the lifetime of a portal.
  await body.getByRole('button', { name: 'dismiss' }).click()
  await expect(notices).toHaveCount(1)
  await expect(summaries).toHaveText([/1 live session still on a login that is not saved here/])
  await page.keyboard.press('Escape')
  await expect(page.locator('.account-popover')).toHaveCount(0)
  await page.click('.account-summary > button >> nth=0')
  await expect(notices).toHaveCount(1)
  // Nothing was persisted: no settings write for a dismissal that is only for now.
  const before = await page.evaluate(() => (window as unknown as { __calls: { method: string; url: string }[] }).__calls.filter(call => call.url.startsWith('/api/settings')))
  expect(before).toEqual([])

  // Tick the box, dismiss: the last notice goes, and the choice is written to the
  // shared store under the canonical desktop profile rather than remembered by this tab.
  await summaries.first().click()
  await notices.first().getByRole('checkbox', { name: 'never show this again' }).check()
  await notices.first().getByRole('button', { name: 'dismiss' }).click()
  await expect(notices).toHaveCount(0)
  const after = await page.evaluate(() => (window as unknown as { __calls: { method: string; url: string }[] }).__calls.filter(call => call.url.startsWith('/api/settings')))
  expect(after).toEqual([{ method: 'PUT', url: '/api/settings/desktop' }])
  await page.keyboard.press('Escape')
  await page.click('.account-summary > button >> nth=0')
  await expect(page.locator('.account-popover .account-section-head h4')).toHaveText(['claude', 'codex'])
  await expect(notices).toHaveCount(0)
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

for (const width of [1280, 360]) {
  for (const outcome of ['succeeded', 'failed']) {
    test(`sign-in messages keep a readable text column at ${width}px after ${outcome}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 })
      await page.goto(`/account-switcher-harness.html?saved=1&outcome=${outcome}`)
      await page.click('.account-summary > button >> nth=0')
      await page.click('.account-section-head .account-signin >> nth=0')

      for (const state of ['running', outcome]) {
        const progress = page.locator(`.account-login.${state}`).first()
        await expect(progress).toBeVisible()
        const layout = await progress.evaluate(node => {
          const box = node.getBoundingClientRect()
          const title = node.querySelector('strong')!.getBoundingClientRect()
          const detail = node.querySelector('small')!.getBoundingClientRect()
          const button = node.querySelector('button')!.getBoundingClientRect()
          return {
            textFraction: detail.width / box.width,
            aligned: Math.abs(title.left - detail.left),
            textGap: detail.top - title.bottom,
            buttonGap: button.left - Math.max(title.right, detail.right),
            overflow: node.scrollWidth - node.clientWidth,
          }
        })
        // Auto-placing the row-spanning button can steal the text column and
        // leave the explanation wrapping one character at a time.
        expect(layout.textFraction).toBeGreaterThan(0.5)
        expect(layout.aligned).toBeLessThan(1)
        expect(layout.textGap).toBeGreaterThanOrEqual(0)
        expect(layout.buttonGap).toBeGreaterThan(0)
        expect(layout.overflow).toBeLessThanOrEqual(1)
      }

      await page.locator('.account-login').getByRole('button', { name: 'dismiss' }).click()
      await expect(page.locator('.account-login')).toHaveCount(0)
    })
  }
}

test('the footer refreshes in place and otherwise leaves, and only leaving closes the popover', async ({ page }) => {
  await page.goto('/account-switcher-harness.html?accounts=multi')
  await page.click('.account-summary > button >> nth=0')

  // Refresh acts on what is on screen, so it keeps the popover open. It is an icon, which
  // is only legible if its name is somewhere a screen reader and a tooltip can reach.
  const refresh = page.locator('.account-popover > footer .account-refresh')
  await expect(refresh).toHaveAttribute('aria-label', 'Refresh quotas')
  await expect(refresh.locator('svg')).toBeVisible()
  await refresh.click()
  await expect(page.locator('.account-popover')).toHaveCount(1)
  await expect(refresh).not.toHaveClass(/spinning/)

  // The other two are destinations, and both hand off through the host rather than
  // navigating themselves - so the popover has to be gone once either is taken.
  await page.click('.account-popover > footer button:has-text("view usage")')
  await expect(page.locator('.account-popover')).toHaveCount(0)

  const calls = await page.evaluate(() =>
    (window as unknown as { __calls: { method: string; url: string }[] }).__calls)
  expect(calls.filter(call => call.url === '/api/provider-accounts/refresh')).toHaveLength(1)
  expect(calls.filter(call => call.url === 'view-usage')).toHaveLength(1)
  expect(calls.filter(call => call.url === 'manage')).toHaveLength(0)
})
