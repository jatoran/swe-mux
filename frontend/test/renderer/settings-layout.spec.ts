import { expect, test } from 'playwright/test'

/**
 * Settings' two layouts, measured rather than described.
 *
 * `settingsTabs.test.ts` proves the navigation model; none of it can see where anything
 * lands. What is asserted here is the part that is CSS and only CSS:
 *
 *  - narrow, the header is one row — the search box sits between the title and the close
 *    button instead of wrapping onto a line of its own, which is the vertical space this
 *    layout was changed to recover;
 *  - narrow, the section list is a drawer: genuinely off screen and out of the focus
 *    order when closed, over the content and inside the panel when open;
 *  - wide, it is still the docked column, and the drawer chrome is absent.
 *
 * The groups are asserted in both, because "see the categories" is the reason the phone
 * gets the desktop's list instead of the flat rail it used to have.
 */

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1280, height: 900 }

type Page = import('playwright/test').Page

async function chrome(page: Page) {
  return page.evaluate(() => {
    const box = (selector: string) => {
      const element = document.querySelector(selector)
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { x: rect.x, right: rect.right, y: rect.y, bottom: rect.bottom, width: rect.width, height: rect.height }
    }
    const nav = document.querySelector('.settings-tabs') as HTMLElement | null
    return {
      header: box('.settings-panel>header'),
      heading: box('.settings-heading'),
      search: box('.settings-panel>header>div.settings-search'),
      close: box('.settings-panel>header>.settings-close'),
      trigger: box('.settings-panel>header>.settings-nav-trigger'),
      body: box('.settings-body'),
      content: box('.settings-content'),
      nav: box('.settings-tabs'),
      navVisibility: nav ? getComputedStyle(nav).visibility : null,
      isDrawer: !!document.querySelector('.settings-tabs-drawer'),
      scrims: document.querySelectorAll('.settings-nav-scrim').length,
      groups: [...document.querySelectorAll('.settings-tab-group>span')]
        .filter(element => getComputedStyle(element).display !== 'none')
        .map(element => element.textContent),
      headingText: document.querySelector('.settings-heading')?.textContent || '',
      // Asked of the browser rather than inferred: a `visibility:hidden` element keeps its
      // offsetParent and still answers a bounding-box query, and the only thing that
      // settles whether a closed drawer is out of the way is whether focus takes.
      focusableInNav: nav
        ? [...nav.querySelectorAll('button')].filter(button => {
            button.focus()
            return document.activeElement === button
          }).length
        : 0,
    }
  })
}

test('narrow: the header is one row with the search inline, and the section list is a closed drawer', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })
  const g = await chrome(page)

  // One row: every header child shares the header's vertical band. A wrapped search box
  // is what this asserts against — it used to sit on its own line below the title.
  expect(g.trigger).not.toBeNull()
  for (const part of [g.trigger!, g.heading!, g.search!, g.close!]) {
    expect(part.y).toBeGreaterThanOrEqual(g.header!.y - 0.5)
    expect(part.bottom).toBeLessThanOrEqual(g.header!.bottom + 0.5)
  }
  // ...and in that order, left to right.
  expect(g.trigger!.x).toBeLessThan(g.heading!.x)
  expect(g.heading!.x).toBeLessThan(g.search!.x)
  expect(g.search!.right).toBeLessThanOrEqual(g.close!.x + 0.5)
  // The whole header is one row of a two-line title, not three stacked controls.
  expect(g.header!.height).toBeLessThan(64)

  // The title names where you are, not what the panel is.
  expect(g.headingText).toContain('SETTINGS')
  expect(g.headingText).toContain('General')

  // Closed drawer: off to the left of the panel, invisible, and unreachable by Tab.
  expect(g.isDrawer).toBe(true)
  expect(g.navVisibility).toBe('hidden')
  expect(g.nav!.right).toBeLessThanOrEqual(g.body!.x + 0.5)
  expect(g.focusableInNav).toBe(0)
  expect(g.scrims).toBe(0)
  // The content has the full width, because the drawer is out of flow.
  expect(g.content!.width).toBeGreaterThan(PHONE.width - 2)
})

test('narrow: the hamburger and the title both open the drawer over the content', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })

  for (const opener of ['.settings-nav-trigger', '.settings-heading-trigger']) {
    await page.click(opener)
    await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(1)
    // The transform has to finish before the geometry means anything.
    await page.waitForTimeout(250)
    const open = await chrome(page)
    expect(open.navVisibility).toBe('visible')
    expect(open.nav!.x).toBeGreaterThanOrEqual(open.body!.x - 0.5)
    // Over the content, not beside it: the content keeps its full width underneath.
    expect(open.content!.width).toBeGreaterThan(PHONE.width - 2)
    expect(open.nav!.right).toBeLessThan(PHONE.width)
    expect(open.scrims).toBe(1)
    expect(open.focusableInNav).toBeGreaterThan(10)
    // The categories are the point of borrowing the desktop list.
    expect(open.groups).toEqual(['Workspace', 'Agents', 'Interface', 'System'])

    // The scrim closes it again, which is how the workspace sidebar behaves.
    // To the right of the drawer: the scrim spans the whole body and the drawer is
    // painted over its left end, exactly as the workspace sidebar sits over its own.
    await page.click('.settings-nav-scrim', { position: { x: PHONE.width - 30, y: 400 } })
    await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(0)
    await page.waitForTimeout(250)
    expect((await chrome(page)).navVisibility).toBe('hidden')
  }
})

test('narrow: picking a section closes the drawer and retitles the header', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })

  await page.click('.settings-nav-trigger')
  await page.click('.settings-tabs button:text-is("Appearance")')
  await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(0)
  await page.waitForTimeout(250)

  const g = await chrome(page)
  expect(g.headingText).toContain('Appearance')
  expect(g.navVisibility).toBe('hidden')
  expect(g.focusableInNav).toBe(0)
  await expect(page.locator('.settings-content h3').first()).toHaveText('Theme')
})

test('narrow: the soft keyboard shortens the panel instead of hiding its footer', async ({ page }) => {
  // The bug this pins: on a phone the panel is sized to `100dvh`, and `100dvh` is the
  // *layout* viewport, which `interactive-widget=resizes-visual` deliberately keeps at full
  // height while the keyboard is up (see `updateAppHeight` in `App.tsx`). So the footer -
  // where Save lives - sat under the keyboard, unreachable by scrolling, because scrolling
  // moves the middle row and not the fixed one below it. It is a fixed panel like the drawer
  // and the sidebar overlay, and it takes the same treatment they do.
  //
  // The keyboard is simulated the way the app publishes it, because that is the whole
  // contract: `--keyboard-inset` and `--visual-offset` on the root, from which the stylesheet
  // derives `--keyboard-cover`. Playwright cannot raise a real one. The rules are ungated by
  // `.soft-keyboard-open`, so the lengths alone are what has to be enough.
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  await page.waitForSelector('.settings-panel>footer button', { state: 'attached' })

  const INSET = 320
  const before = await page.evaluate(() => document.querySelector('.settings-panel>footer')!.getBoundingClientRect().bottom)
  expect(before).toBeGreaterThan(PHONE.height - 4)

  const after = await page.evaluate(inset => {
    document.documentElement.style.setProperty('--keyboard-inset', `${inset}px`)
    document.documentElement.style.setProperty('--visual-offset', '0px')
    const footer = document.querySelector<HTMLElement>('.settings-panel>footer')!
    const save = [...footer.querySelectorAll('button')].pop()!
    return {
      footer: footer.getBoundingClientRect().bottom,
      save: save.getBoundingClientRect().bottom,
      // The scroller is what gives up the height, which is what keeps every control
      // reachable rather than merely keeping the buttons on screen.
      content: document.querySelector('.settings-body')!.getBoundingClientRect().height,
    }
  }, INSET)

  const visible = PHONE.height - INSET
  expect(after.footer).toBeLessThanOrEqual(visible + 1)
  expect(after.save).toBeLessThanOrEqual(visible + 1)
  expect(after.content).toBeGreaterThan(0)
})

test('wide: the section list is the docked column it always was', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })
  const g = await chrome(page)

  expect(g.isDrawer).toBe(false)
  expect(g.trigger).toBeNull()
  expect(g.navVisibility).toBe('visible')
  expect(g.scrims).toBe(0)
  // Docked: the column and the content are side by side and do not overlap.
  expect(g.nav!.right).toBeLessThanOrEqual(g.content!.x + 0.5)
  expect(g.groups).toEqual(['Workspace', 'Agents', 'Interface', 'System'])
  expect(g.headingText).toContain('CONFIG::V6')
  expect(g.headingText).toContain('Settings')
})

test('the active Settings tab adds no marker or indentation', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  const general=page.locator('.settings-tabs [role="tab"]',{hasText:'General'}).first()
  const projects=page.locator('.settings-tabs [role="tab"]',{hasText:'Projects'}).first()
  await expect(general).toHaveAttribute('aria-selected','true')

  const measure=async()=>page.locator('.settings-tabs [role="tab"]').evaluateAll(buttons=>buttons.slice(0,2).map(button=>{
    const element=button as HTMLElement
    const text=element.firstChild
    const range=document.createRange()
    if(text)range.selectNodeContents(text)
    return {
      marker:getComputedStyle(element,'::before').content,
      textLeft:Math.round(range.getBoundingClientRect().left),
    }
  }))

  const before=await measure()
  expect(before[0].marker).toBe('none')
  expect(before[1].marker).toBe('none')
  expect(before[0].textLeft).toBe(before[1].textLeft)
  await projects.click()
  await expect(projects).toHaveAttribute('aria-selected','true')
  const after=await measure()
  expect(after[0].marker).toBe('none')
  expect(after[1].marker).toBe('none')
  expect(after[0].textLeft).toBe(after[1].textLeft)
})

test('every paged Settings tab exposes working page tabs in the content pane', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  const pagedTabs=['General','Projects','Terminals','Processes','Harnesses','Accounts','Prompt queue','Appearance','Input','Text editor','Voice','Remote','Diagnostics']

  for(const tab of pagedTabs){
    await page.getByRole('tab',{name:tab,exact:true}).click()
    const nav=page.locator('.settings-subpage-nav')
    await expect(nav).toBeVisible()
    const pages=await nav.locator('button').allTextContents()
    expect(pages.length,`${tab} must expose more than one page`).toBeGreaterThan(1)
    for(const label of pages){
      await nav.getByRole('button',{name:label,exact:true}).click()
      await expect(nav.locator('button.active')).toHaveText(label)
      const visible=page.locator('.settings-content section[data-settings-subpage]:visible')
      expect(await visible.count(),`${tab} > ${label} rendered no page`).toBeGreaterThan(0)
      expect((await visible.first().innerText()).trim(),`${tab} > ${label} rendered blank`).not.toBe('')
    }
  }
})

test('every tab renders, and every marked control is really in its DOM', async ({ page }) => {
  // `settingsCoverage.test.ts` walks from `config.py` to a control, and it reads *source*:
  // it cannot tell a control that renders from one sitting behind a condition that is never
  // true, or from a tab that throws before it gets there. This is the half only a browser
  // can answer, and it is worth one pass because the coverage pass that added thirty
  // controls added them across seven tabs at once.
  const errors: string[] = []
  let visiting = ''
  // Attributed to the tab being opened, because "something threw" is unactionable across
  // seventeen of them and the stack from a bundled build names no section.
  page.on('pageerror', error => errors.push(`${visiting}: ${error}`))
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })

  const tabs = await page.locator('.settings-tabs [role="tab"]').allTextContents()
  expect(tabs.length).toBe(17)

  const marked = new Set<string>()
  for (const label of tabs) {
    visiting = label
    await page.locator('.settings-tabs [role="tab"]', { hasText: label }).first().click()
    // Every tab renders at least one section heading; a tab that threw renders none.
    await expect(page.locator('.settings-content h3').first()).toBeVisible()
    for (const value of await page.locator('.settings-content [data-setting]').evaluateAll(
      nodes => nodes.map(node => node.getAttribute('data-setting') || ''),
    )) marked.add(value)
  }
  expect(errors).toEqual([])

  // A sample from each tab this pass touched, so a control that stops rendering fails here
  // rather than only when someone follows a link to it.
  for (const setting of [
    'attach_replay_bytes', 'session_recovery_checkpoint_bytes', 'ghost_window_sweep_enabled',
    'status_timeline_retention_days', 'agent_interject_enabled', 'agent_message_max_chars',
    'request_spawn_enabled', 'session_watch_max_minutes', 'prompt_queue_retention_days',
    'openrouter_request_timeout_seconds',
    'assistant_stream_replies', 'log_level',
  ]) expect(marked, `${setting} is marked in the source but never rendered`).toContain(setting)
})
