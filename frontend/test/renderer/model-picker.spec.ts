import { expect, test } from 'playwright/test'

/**
 * The model picker's option row, and the routing summary that indexes every picker.
 *
 * `modelPricing.test.ts` proves the strings; none of it can see whether they fit. This
 * row is the reason: it carries a model id and a price pair in one 8px line inside a
 * settings column that is 390px wide on a phone, and the failure mode is not an
 * exception but a price that has quietly ellipsized away, or an id that has run under
 * the price and made both unreadable. Both are geometry, and geometry is measured here.
 *
 * The catalog fixture (`settingsHarness.tsx`) is chosen for the same reason: a free
 * model, an unpriced one, a negative-priced auto-router, a sub-cent price beside a
 * double-digit one, and an id long enough to need the ellipsis. A layout that holds for
 * `$0.08 / $0.30` and breaks for `$3.00 / $15.00` is the regression this catches.
 */

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1280, height: 900 }

type Page = import('playwright/test').Page

/**
 * Open the panel at a viewport.
 *
 * The size is set before the navigation rather than after, so the panel renders once
 * in the layout under test. Resizing a mounted panel makes the layout cross the
 * breakpoint mid-transition, and the tab drawer is then briefly both animating and
 * scheduled to close - which reads as a flaky click, not as a layout bug.
 */
const open = async (page: Page, viewport: { width: number; height: number }) => {
  await page.setViewportSize(viewport)
  await page.goto('/settings-harness.html')
  await page.locator('.settings-tabs button').first().waitFor({ state: 'attached' })
}

/**
 * Reach a tab in either layout. Past the breakpoint the tab list is a docked column;
 * narrow, it is a drawer that has to be opened first and closes itself on the pick.
 */
const openTab = async (page: Page, tab: string, subpage?: string) => {
  const trigger = page.locator('.settings-nav-trigger')
  if (await trigger.isVisible()) {
    await trigger.click()
    await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(1)
  }
  await page.locator('.settings-tabs [role="tab"]', { hasText: new RegExp(`^${tab}$`) }).click()
  // Scoped to the tab role: selecting a tab auto-expands its page links, so a bare
  // `button.active` would also match the active page beneath it.
  await expect(page.locator('.settings-tabs [role="tab"].active')).toHaveText(tab)
  if (subpage) await openSubpage(page, subpage)
}

/**
 * Select one of the active tab's pages from its sidebar links. A paged tab shows only
 * the selected page, so a control on another page has no layout box until this runs.
 * Narrow, picking the tab closed the drawer, so it is reopened first.
 */
const openSubpage = async (page: Page, label: string) => {
  const trigger = page.locator('.settings-nav-trigger')
  if (await trigger.isVisible()) {
    await trigger.click()
    await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(1)
  }
  await page.locator('.settings-subtabs button', { hasText: new RegExp(`^${label}$`) }).click()
  await expect(page.locator('.settings-subtabs button.active')).toHaveText(label)
}

/**
 * One picker's own listbox.
 *
 * Scoped to the picker rather than to the page because a picker closes on a pointer
 * press outside itself, and moving focus with the keyboard leaves the previous one
 * open - so an unscoped `.model-picker-options` query counts two listboxes and reads
 * as a row-count regression.
 */
const optionsOf = (page: Page, id: string) =>
  page.locator(`.model-picker:has(#${id}) .model-picker-options [role="option"]`)

/**
 * Focus a picker's input, which is what opens its listbox, and wait for the rows.
 * A click rather than `focus()`: click waits for the input to be visible, and right
 * after a page switch the section is still hidden for a frame — focusing a hidden
 * input is a silent no-op that never opens the list.
 */
const openPicker = async (page: Page, id: string) => {
  await page.locator(`#${id}`).click()
  await optionsOf(page, id).first().waitFor()
}

/** Every option row, with the geometry of its two meta cells. */
const rows = (page: Page) => page.evaluate(() => {
  const box = (element: Element | null | undefined) => {
    if (!element) return null
    const rect = element.getBoundingClientRect()
    return { x: rect.x, right: rect.right, width: rect.width, y: rect.y, bottom: rect.bottom }
  }
  return [...document.querySelectorAll('.model-picker-options [role="option"]')].map(option => {
    const meta = option.querySelector('.model-picker-meta')
    const price = option.querySelector('.model-picker-price')
    const id = meta?.querySelector('span:not(.model-picker-price)')
    return {
      name: option.querySelector('strong')?.textContent || '',
      id: id?.textContent || '',
      price: price?.textContent || '',
      title: option.getAttribute('title') || '',
      // `scrollWidth > clientWidth` is how an ellipsized cell admits it was clipped.
      idClipped: id ? id.scrollWidth > id.clientWidth + 1 : false,
      priceClipped: price ? price.scrollWidth > price.clientWidth + 1 : false,
      idBox: box(id),
      priceBox: box(price),
      optionBox: box(option),
    }
  })
})

test('every priced model states input and output cost, and an unpriced one states nothing', async ({ page }) => {
  await open(page, DESKTOP)
  await openTab(page, 'Accounts', 'Models')
  await openPicker(page, 'openrouter_cheap_model-picker')

  const options = await rows(page)
  const byId = new Map(options.filter(row => row.id).map(row => [row.id, row]))

  // Per *million* tokens. The catalog quotes per token, and $0.00000008 is not a
  // figure anyone chooses a model on.
  expect(byId.get('deepseek/deepseek-v4-flash')?.price).toBe('1M · $0.08 / $0.30 per M')
  expect(byId.get('anthropic/claude-sonnet-5')?.price).toBe('200K · $3.00 / $15.00 per M')
  expect(byId.get('meta-llama/llama-4-scout:free')?.price).toBe('128K · free')
  expect(byId.get('openrouter/auto')?.price).toBe('2M · variable / variable per M')
  // The one rendering that actively misinforms would be `$0.00` here.
  expect(byId.get('somevendor/model-without-published-pricing')?.price).toBe('')

  // The row cannot say which figure is input and which is output; the title must.
  expect(byId.get('deepseek/deepseek-v4-flash')?.title).toContain('Input $0.08 per million tokens')
  expect(byId.get('deepseek/deepseek-v4-flash')?.title).toContain('Output $0.30 per million tokens')
})

test('the exact id stays on the row beside the price, because the filter ranks on it', async ({ page }) => {
  await open(page, DESKTOP)
  await openTab(page, 'Accounts', 'Models')
  await openPicker(page, 'openrouter_cheap_model-picker')

  // Typing a vendor path must be explainable by what the rows show: the two catalog
  // matches, plus the type-exact-id escape row the query itself creates.
  await page.locator('#openrouter_cheap_model-picker').fill('openai/')
  await expect(optionsOf(page, 'openrouter_cheap_model-picker')).toHaveCount(3)
  const filtered = (await rows(page)).filter(row => row.price !== '')
  expect(filtered.map(row => row.id)).toEqual(['openai/gpt-5.6-luna', 'openai/gpt-5.6-terra'])
})

const LONG_ID = 'averylongvendorname/an-extremely-long-model-identifier-preview-2026-08-01'

test('wide: the id yields to the price, and prices align down one right edge', async ({ page }) => {
  await open(page, DESKTOP)
  await openTab(page, 'Accounts', 'Models')
  await openPicker(page, 'openrouter_cheap_model-picker')

  const priced = (await rows(page)).filter(row => row.priceBox && row.idBox)
  expect(priced.length).toBeGreaterThan(4)

  for (const row of priced) {
    // The id yields; the price does not. That is the whole point of giving the price
    // its own auto-width column instead of letting one nowrap line hold both.
    expect(row.idBox!.right, `${row.id} id overlaps its price`)
      .toBeLessThanOrEqual(row.priceBox!.x + 0.5)
    expect(row.priceClipped, `${row.id} price is ellipsized`).toBe(false)
    expect(row.priceBox!.right, `${row.id} price leaves its row`)
      .toBeLessThanOrEqual(row.optionBox!.right + 0.5)
    // One line, not two: the meta row is the picker's second line and its last.
    expect(Math.round(row.priceBox!.y)).toBe(Math.round(row.idBox!.y))
  }

  // Right-aligned in a shared column: the figures are read against each other.
  expect(new Set(priced.map(row => Math.round(row.priceBox!.right))).size,
    'prices are not aligned to one right edge').toBe(1)

  // No ordinary id may clip; only the deliberately long one is allowed to ellipsize
  // (whether it needs to depends on the panel width, so its clipping is not asserted).
  expect(priced.filter(row => row.idClipped).map(row => row.id).filter(id => id !== LONG_ID)).toEqual([])
})

test('narrow: the price takes its own line rather than erasing the id', async ({ page }) => {
  await open(page, PHONE)
  await openTab(page, 'Accounts', 'Models')
  await openPicker(page, 'openrouter_cheap_model-picker')

  const priced = (await rows(page)).filter(row => row.priceBox && row.idBox)
  expect(priced.length).toBeGreaterThan(4)

  for (const row of priced) {
    // Side by side in a ~216px control column, the price wants 155-192px and the id
    // is left with almost nothing. On a touch device there is no hover to recover the
    // id from the title, so the two cells stack instead of competing.
    expect(row.priceBox!.y, `${row.id} price did not stack below its id`)
      .toBeGreaterThan(row.idBox!.bottom - 0.5)
    expect(row.priceClipped, `${row.id} price is ellipsized`).toBe(false)
    expect(row.priceBox!.right).toBeLessThanOrEqual(row.optionBox!.right + 0.5)
    expect(row.idBox!.right).toBeLessThanOrEqual(row.optionBox!.right + 0.5)
  }

  // Stacking is what buys this: only the genuinely long id still needs the ellipsis,
  // where side by side every single one of them was truncated.
  expect(priced.filter(row => row.idClipped).map(row => row.id)).toEqual([LONG_ID])
})

test('the listbox never makes the settings column scroll sideways', async ({ page }) => {
  await open(page, PHONE)
  await openTab(page, 'Accounts', 'Models')
  await openPicker(page, 'openrouter_cheap_model-picker')

  const overflow = await page.evaluate(() => {
    const content = document.querySelector('.settings-content') as HTMLElement
    const list = document.querySelector('.model-picker-options') as HTMLElement
    return {
      content: content.scrollWidth - content.clientWidth,
      list: list.scrollWidth - list.clientWidth,
    }
  })
  expect(overflow.content).toBeLessThanOrEqual(1)
  expect(overflow.list).toBeLessThanOrEqual(1)
})

test('a pinned model offers no way to clear itself, an override does', async ({ page }) => {
  await open(page, DESKTOP)

  // Every model setting is edited in Accounts → Models now, one picker per route.
  await openTab(page, 'Accounts', 'Models')

  // The assistant's model is rejected by the daemon when blank, so the control must
  // not be able to produce a blank: no clear-the-setting row.
  await openPicker(page, 'assistant_model-picker')
  await expect(optionsOf(page, 'assistant_model-picker')).toHaveCount(8)
  await expect(optionsOf(page, 'assistant_model-picker').filter({ hasText: 'Use the cheap model' })).toHaveCount(0)

  // The spoken summary's model is an override: clearing it is how you say "follow
  // the cheap model", so that row has to exist, above the eight catalog entries.
  await openPicker(page, 'tts_summary_model-picker')
  await expect(optionsOf(page, 'tts_summary_model-picker')).toHaveCount(9)
  await expect(optionsOf(page, 'tts_summary_model-picker').first()).toHaveText('Use the cheap model…')
})

test('the routing summary resolves what each feature will actually call', async ({ page }) => {
  await open(page, DESKTOP)
  await openTab(page, 'Accounts', 'Models')
  await page.locator('.model-routing li').first().waitFor()

  const summary = await page.evaluate(() => [...document.querySelectorAll('.model-routing li')].map(row => ({
    feature: row.querySelector('strong')?.textContent || '',
    kind: row.querySelector('.model-routing-kind')?.textContent || '',
    model: row.querySelector('.model-routing-model code')?.textContent || row.querySelector('.model-routing-model em')?.textContent || '',
    inherited: !!row.querySelector('.model-routing-inherited'),
    price: row.querySelector('.model-routing-price')?.textContent || '',
    // Membership of `MODEL_ROUTES` is what makes each row a real control: one
    // picker per route, marked for the deep links the feature tabs carry.
    editable: !!row.querySelector('.model-picker input'),
    mark: row.getAttribute('data-setting') || '',
  })))

  expect(summary.map(row => row.feature)).toEqual([
    'Cheap model', 'Standard model', 'Scan timeline', 'Attention narration',
    'Spoken summary', 'Mux assistant', 'Project context card',
  ])
  expect(summary.every(row => row.editable)).toBe(true)
  expect(summary.every(row => row.mark)).toBe(true)

  // An unset override reports what it falls through to, not an empty cell: "blank"
  // and "not configured" are opposite answers to what this feature costs.
  const narration = summary.find(row => row.feature === 'Attention narration')!
  expect(narration.kind).toBe('override')
  expect(narration.model).toBe('deepseek/deepseek-v4-flash')
  expect(narration.inherited).toBe(true)
  expect(narration.price).toBe('1M · $0.08 / $0.30 per M')

  // A pin has nothing to inherit, and reads as a requirement rather than a preference.
  const assistant = summary.find(row => row.feature === 'Mux assistant')!
  expect(assistant.kind).toBe('pinned')
  expect(assistant.inherited).toBe(false)
  expect(assistant.model).toBe('openai/gpt-5.6-terra')
  expect(assistant.price).toBe('400K · $1.25 / $10.00 per M')

  const card = summary.find(row => row.feature === 'Project context card')!
  expect(card.inherited).toBe(true)
})

test("a feature tab's read-only row opens the control that decides it, in Accounts → Models", async ({ page }) => {
  await open(page, DESKTOP)
  // The Voice tab no longer edits the assistant's model; it shows the resolved value
  // and links back to the one editor.
  await openTab(page, 'Voice', 'Mux assistant')
  const readout = page.locator('.model-routing-elsewhere[data-setting="assistant_model"]')
  await expect(readout).toBeVisible()
  await expect(readout.locator('code')).toHaveText('openai/gpt-5.6-terra')

  await readout.locator('button', { hasText: 'Edit in Accounts → Models' }).click()

  // Landing on the right tab is not arriving: the owning page is selected and the
  // control is scrolled to and flashed exactly as a deep link from outside would be.
  await expect(page.locator('.settings-tabs [role="tab"].active')).toHaveText('Accounts')
  const control = page.locator('.model-routing li[data-setting="assistant_model"]')
  await expect(control).toBeVisible()
  await expect(control).toHaveClass(/setting-flash/)
})
